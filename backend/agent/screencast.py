"""CDP Screencast manager — streams live browser frames to the frontend.

Uses Chrome DevTools Protocol Page.startScreencast to get JPEG frames
whenever the page content changes, then publishes them as SSE events.
Only streams for jobs marked as "visible" by the frontend.
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Page, CDPSession

from backend.routes.events import publish_event, publish_global

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from backend.config import (
    SCREENCAST_QUALITY as _CFG_QUALITY,
    SCREENCAST_MAX_WIDTH as _CFG_MAX_W,
    SCREENCAST_MAX_HEIGHT as _CFG_MAX_H,
)

SCREENCAST_FORMAT = "jpeg"
SCREENCAST_QUALITY = _CFG_QUALITY
SCREENCAST_MAX_WIDTH = _CFG_MAX_W
SCREENCAST_MAX_HEIGHT = _CFG_MAX_H

# ---------------------------------------------------------------------------
# Active screencast sessions: job_id -> ScreencastSession
# ---------------------------------------------------------------------------


class ScreencastSession:
    """Manages a CDP screencast for a single browser page/job."""

    def __init__(self, page: Page, job_id: str, run_id: str) -> None:
        self.page = page
        self.job_id = job_id
        self.run_id = run_id
        self._cdp: CDPSession | None = None
        self._active = False
        self._paused = False
        self._frame_count = 0

    async def start(self) -> None:
        """Start CDP screencast on the page."""
        if self._active:
            return
        try:
            self._cdp = await self.page.context.new_cdp_session(self.page)
            self._cdp.on("Page.screencastFrame", self._on_frame)
            await self._cdp.send("Page.startScreencast", {
                "format": SCREENCAST_FORMAT,
                "quality": SCREENCAST_QUALITY,
                "maxWidth": SCREENCAST_MAX_WIDTH,
                "maxHeight": SCREENCAST_MAX_HEIGHT,
            })
            self._active = True
            print(f"[Screencast] Started for job {self.job_id}")
        except Exception as e:
            print(f"[Screencast] Failed to start for job {self.job_id}: {e}")

    def _on_frame(self, params: dict[str, Any]) -> None:
        """Handle incoming screencast frame from CDP."""
        if self._paused or not self._active:
            # Still need to ack the frame even if paused
            self._ack_frame(params.get("sessionId", 0))
            return

        frame_data = params.get("data", "")  # base64 JPEG
        session_id = params.get("sessionId", 0)
        self._frame_count += 1

        # Publish frame via SSE (fire-and-forget)
        asyncio.create_task(self._publish_frame(frame_data))

        # Acknowledge frame so Chrome sends the next one
        self._ack_frame(session_id)

    def _ack_frame(self, session_id: int) -> None:
        """Acknowledge a screencast frame to Chrome."""
        if self._cdp:
            asyncio.create_task(
                self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            )

    async def _publish_frame(self, frame_data: str) -> None:
        """Send frame to frontend via SSE (both run-specific and global)."""
        payload = {
            "job_id": self.job_id,
            "frame": frame_data,
        }
        try:
            await publish_event(self.run_id, "job.frame", payload)
            await publish_global("job.frame", {**payload, "run_id": self.run_id})
        except Exception:
            pass  # Non-fatal

    async def pause(self) -> None:
        """Pause frame streaming (page actions continue, just no frames sent)."""
        self._paused = True

    async def resume(self) -> None:
        """Resume frame streaming."""
        self._paused = False

    async def stop(self) -> None:
        """Stop screencast and clean up CDP session."""
        if not self._active:
            return
        self._active = False
        try:
            if self._cdp:
                await self._cdp.send("Page.stopScreencast")
                await self._cdp.detach()
        except Exception:
            pass
        self._cdp = None
        print(f"[Screencast] Stopped for job {self.job_id} ({self._frame_count} frames sent)")


# ---------------------------------------------------------------------------
# Global session registry
# ---------------------------------------------------------------------------

_sessions: dict[str, ScreencastSession] = {}
_visible_jobs: dict[str, set[str]] = {}  # run_id -> set of visible job_ids


async def start_screencast(page: Page, job_id: str, run_id: str) -> None:
    """Start streaming frames for a job's page."""
    session = ScreencastSession(page, job_id, run_id)
    _sessions[job_id] = session

    # Only start streaming if the job is in the visible set (or if no visibility info yet)
    visible = _visible_jobs.get(run_id)
    if visible is None or job_id in visible:
        await session.start()
    else:
        # Register but don't start — will be started when visibility changes
        pass


async def stop_screencast(job_id: str) -> None:
    """Stop and remove a screencast session."""
    session = _sessions.pop(job_id, None)
    if session:
        await session.stop()


async def set_visible_jobs(run_id: str, job_ids: list[str]) -> None:
    """Update which jobs should be actively streaming frames.
    
    Jobs not in the list get paused. Jobs in the list get started/resumed.
    """
    visible_set = set(job_ids)
    _visible_jobs[run_id] = visible_set

    # Snapshot to avoid "dictionary changed size during iteration"
    snapshot = list(_sessions.items())
    for jid, session in snapshot:
        if session.run_id != run_id:
            continue
        try:
            if jid in visible_set:
                if not session._active:
                    await session.start()
                elif session._paused:
                    await session.resume()
            else:
                if session._active and not session._paused:
                    await session.pause()
        except Exception:
            pass  # Session may have been cleaned up concurrently


async def cleanup_run(run_id: str) -> None:
    """Stop all screencasts for a run."""
    _visible_jobs.pop(run_id, None)
    to_remove = [jid for jid, s in list(_sessions.items()) if s.run_id == run_id]
    for jid in to_remove:
        await stop_screencast(jid)
