"""Demo recording routes — record a browser workflow and synthesize into reusable procedure."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend import database as db
from backend.config import BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID
from backend.services.demo_recording import (
    fetch_session_recording,
    normalize_recording_to_actions,
    actions_to_summary_text,
)
from backend.agent.llm import synthesize_demo_actions_to_procedure

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/demo/start — create Browserbase session for recording
# ---------------------------------------------------------------------------

@router.post("/start")
async def demo_start():
    """Create a Browserbase session for the user to browse; recording is captured by Browserbase.

    Reuses the shared Browserbase context (from kv_store) so that cookies
    accumulated during demos are available to all future agent sessions.
    If no context exists yet, creates one and persists it.
    """
    from browserbase import Browserbase

    if not BROWSERBASE_API_KEY or not BROWSERBASE_PROJECT_ID:
        raise HTTPException(status_code=400, detail="Browserbase not configured")

    # Check for an existing shared context so cookies accumulate across demos
    existing_context_id = await db.kv_get("browserbase_context_id")

    def _create(ctx_id: str | None):
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)
        if ctx_id:
            context_id = ctx_id
        else:
            context = bb.contexts.create(project_id=BROWSERBASE_PROJECT_ID)
            context_id = context.id

        session = bb.sessions.create(
            project_id=BROWSERBASE_PROJECT_ID,
            browser_settings={
                "context": {"id": context_id, "persist": True},
                "solveCaptchas": True,
                "fingerprint": {
                    "browsers": ["chrome"],
                    "devices": ["desktop"],
                    "operatingSystems": ["macos"],
                },
            },
            proxies=[{"type": "browserbase", "geolocation": {"country": "US"}}],
        )
        debug_info = bb.sessions.debug(session.id)
        return {
            "session_id": session.id,
            "live_view_url": debug_info.debugger_fullscreen_url,
            "context_id": context_id,
        }

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _create, existing_context_id)

    # Persist the context so BrowserAgent sessions reuse these cookies
    await db.kv_set("browserbase_context_id", result["context_id"])

    return {
        "session_id": result["session_id"],
        "live_view_url": result["live_view_url"],
    }


# ---------------------------------------------------------------------------
# POST /api/demo/stop — stop recording, synthesize, and save
# ---------------------------------------------------------------------------

@router.post("/stop")
async def demo_stop(body: dict):
    """Stop demo: fetch recording, normalize, synthesize to procedure, store demo."""
    session_id = (body.get("session_id") or "").strip()
    name = (body.get("name") or "").strip()

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    if not BROWSERBASE_API_KEY:
        raise HTTPException(status_code=400, detail="Browserbase not configured")

    try:
        events = await fetch_session_recording(session_id, BROWSERBASE_API_KEY)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch session recording. It may not be ready yet; try again in a few seconds. {e!s}",
        )

    print(f"[Demo] Fetched {len(events)} rrweb events from session {session_id}")
    actions = normalize_recording_to_actions(events)
    actions_text = actions_to_summary_text(actions)
    print(f"[Demo] Normalized to {len(actions)} actions. Summary:\n{actions_text[:1000]}")
    instruction_summary = await synthesize_demo_actions_to_procedure(actions_text)

    raw_events_json = json.dumps(events[:500]) if events else "[]"
    demo = await db.create_task_demo(
        name=name or f"Demo {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
        instruction_summary=instruction_summary,
        session_id=session_id,
        raw_events=raw_events_json,
    )
    return {
        "id": demo["id"],
        "name": demo["name"],
        "instruction_summary": demo["instruction_summary"],
        "created_at": demo["created_at"],
    }


# ---------------------------------------------------------------------------
# GET /api/demo/list — list all saved demos
# ---------------------------------------------------------------------------

@router.get("/list")
async def list_demos():
    """List all saved task demos."""
    demos = await db.list_task_demos()
    return [
        {
            "id": d["id"],
            "name": d["name"],
            "instruction_summary": d["instruction_summary"],
            "created_at": d["created_at"],
        }
        for d in demos
    ]


# ---------------------------------------------------------------------------
# GET /api/demo/{demo_id} — get a single demo
# ---------------------------------------------------------------------------

@router.get("/{demo_id}")
async def get_demo(demo_id: str):
    """Get a single task demo by id."""
    demo = await db.get_task_demo(demo_id)
    if not demo:
        raise HTTPException(status_code=404, detail="Demo not found")
    return {
        "id": demo["id"],
        "name": demo["name"],
        "instruction_summary": demo["instruction_summary"],
        "created_at": demo["created_at"],
    }


# ---------------------------------------------------------------------------
# DELETE /api/demo/{demo_id} — delete a demo
# ---------------------------------------------------------------------------

@router.delete("/{demo_id}")
async def delete_demo(demo_id: str):
    """Delete a saved task demo."""
    existing = await db.get_task_demo(demo_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Demo not found")
    await db.delete_task_demo(demo_id)
    return {"deleted": True, "id": demo_id}
