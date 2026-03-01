"""SSE (Server-Sent Events) endpoint for real-time agent updates."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

# ---------------------------------------------------------------------------
# Global event bus — simple pub/sub via asyncio.Queue per subscriber
# ---------------------------------------------------------------------------

_subscribers: dict[str, list[asyncio.Queue]] = {}  # run_id -> list of queues


def _get_subs(run_id: str) -> list[asyncio.Queue]:
    if run_id not in _subscribers:
        _subscribers[run_id] = []
    return _subscribers[run_id]


async def publish_event(run_id: str, event: str, data: dict[str, Any]) -> None:
    """Push an event to all subscribers of a given run."""
    payload = json.dumps(data)
    for q in _get_subs(run_id):
        try:
            q.put_nowait({"event": event, "data": payload})
        except asyncio.QueueFull:
            pass  # Drop if subscriber is too slow


# Also publish to a global "all" channel for the dashboard
async def publish_global(event: str, data: dict[str, Any]) -> None:
    await publish_event("__global__", event, data)


# ---------------------------------------------------------------------------
# SSE endpoint: GET /api/events/{run_id}
# ---------------------------------------------------------------------------

@router.get("/{run_id}")
async def event_stream(run_id: str):
    queue: asyncio.Queue = asyncio.Queue(maxsize=2000)  # Larger for frame events
    subs = _get_subs(run_id)
    subs.append(queue)

    async def _generator():
        try:
            while True:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield msg
        except asyncio.TimeoutError:
            # Send keepalive
            yield {"event": "keepalive", "data": "{}"}
        except asyncio.CancelledError:
            pass
        finally:
            subs.remove(queue)

    return EventSourceResponse(_generator())


# ---------------------------------------------------------------------------
# Global SSE endpoint: GET /api/events/global/stream
# ---------------------------------------------------------------------------

@router.get("/global/stream")
async def global_event_stream():
    queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
    subs = _get_subs("__global__")
    subs.append(queue)

    async def _generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield {"event": "keepalive", "data": "{}"}
        except asyncio.CancelledError:
            pass
        finally:
            subs.remove(queue)

    return EventSourceResponse(_generator())
