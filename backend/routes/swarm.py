"""Browser swarm routes — launch, list, get, and cancel browser agent swarms."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend import database as db
from backend.models import SwarmCreate, SwarmOut, SwarmAgentOut
from backend.config import BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID
from backend.agent.browser_swarm import (
    start_swarm,
    cancel_swarm,
    get_active_agents,
    get_active_agent,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory auth session tracking (for the login flow)
# ---------------------------------------------------------------------------
_auth_session: dict | None = None  # {session_id, context_id, live_view_url}


# ---------------------------------------------------------------------------
# POST /api/swarm — launch a new browser swarm
# ---------------------------------------------------------------------------

@router.post("", response_model=SwarmOut)
async def create_swarm(body: SwarmCreate):
    if not body.tasks:
        raise HTTPException(status_code=400, detail="At least one task is required")

    swarm = await db.create_swarm()

    # Convert Pydantic models to dicts for the engine
    tasks = [t.model_dump() for t in body.tasks]

    # Launch the swarm in the background
    asyncio.create_task(start_swarm(swarm["id"], tasks))

    return SwarmOut(**swarm)


# ---------------------------------------------------------------------------
# GET /api/swarm — list all swarms
# ---------------------------------------------------------------------------

@router.get("", response_model=list[SwarmOut])
async def list_swarms():
    swarms = await db.list_swarms()
    return [SwarmOut(**s) for s in swarms]


# ---------------------------------------------------------------------------
# GET /api/swarm/{swarm_id} — get a single swarm
# ---------------------------------------------------------------------------

@router.get("/{swarm_id}", response_model=SwarmOut)
async def get_swarm(swarm_id: str):
    swarm = await db.get_swarm(swarm_id)
    if not swarm:
        raise HTTPException(status_code=404, detail="Swarm not found")
    return SwarmOut(**swarm)


# ---------------------------------------------------------------------------
# GET /api/swarm/{swarm_id}/agents — list agents in a swarm
# ---------------------------------------------------------------------------

@router.get("/{swarm_id}/agents", response_model=list[SwarmAgentOut])
async def list_agents(swarm_id: str):
    agents = await db.list_swarm_agents(swarm_id)
    return [SwarmAgentOut(**a) for a in agents]


# ---------------------------------------------------------------------------
# GET /api/swarm/{swarm_id}/agents/{agent_id} — get a single agent
# ---------------------------------------------------------------------------

@router.get("/{swarm_id}/agents/{agent_id}", response_model=SwarmAgentOut)
async def get_agent(swarm_id: str, agent_id: str):
    agent = await db.get_swarm_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return SwarmAgentOut(**agent)


# ---------------------------------------------------------------------------
# GET /api/swarm/{swarm_id}/agents/{agent_id}/live — get live state from memory
# ---------------------------------------------------------------------------

@router.get("/{swarm_id}/agents/{agent_id}/live")
async def get_agent_live(swarm_id: str, agent_id: str):
    """Get live in-memory state for a running agent (real-time, no DB)."""
    agent = get_active_agent(swarm_id, agent_id)
    if not agent:
        # Fall back to DB
        db_agent = await db.get_swarm_agent(agent_id)
        if not db_agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return db_agent
    return agent.to_dict()


# ---------------------------------------------------------------------------
# GET /api/swarm/auth/status — check if a browser auth context exists
# ---------------------------------------------------------------------------

@router.get("/auth/status")
async def auth_status():
    context_id = await db.kv_get("browserbase_context_id")
    return {
        "authenticated": bool(context_id),
        "context_id": context_id,
        "setup_in_progress": _auth_session is not None,
    }


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/setup — create a context + session for manual login
# ---------------------------------------------------------------------------

@router.post("/auth/setup")
async def auth_setup():
    global _auth_session
    from browserbase import Browserbase

    if not BROWSERBASE_API_KEY or not BROWSERBASE_PROJECT_ID:
        raise HTTPException(status_code=400, detail="Browserbase credentials not configured")

    # Always create a new context for this setup so we don't reuse an expired one.
    def _create():
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)
        context = bb.contexts.create(project_id=BROWSERBASE_PROJECT_ID)
        context_id = context.id
        print(f"[Auth] Created new context: {context_id}")

        # Start a session with context + persist + proxies + stealth
        # Per https://docs.browserbase.com/guides/authentication
        session = bb.sessions.create(
            project_id=BROWSERBASE_PROJECT_ID,
            browser_settings={
                "context": {
                    "id": context_id,
                    "persist": True,
                },
                "solveCaptchas": True,
                "fingerprint": {
                    "browsers": ["chrome"],
                    "devices": ["desktop"],
                    "operatingSystems": ["macos"],
                },
            },
            proxies=[{
                "type": "browserbase",
                "geolocation": {
                    "country": "US",
                },
            }],
        )

        # Get live view URL
        debug_info = bb.sessions.debug(session.id)
        return {
            "session_id": session.id,
            "context_id": context_id,
            "live_view_url": debug_info.debugger_fullscreen_url,
        }

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _create)
    _auth_session = result

    return {
        "live_view_url": result["live_view_url"],
        "session_id": result["session_id"],
        "context_id": result["context_id"],
        "message": "Log into Google in the browser window, then call /api/swarm/auth/save",
    }


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/save — end the auth session and persist cookies
# ---------------------------------------------------------------------------

@router.post("/auth/save")
async def auth_save():
    global _auth_session
    if not _auth_session:
        raise HTTPException(status_code=400, detail="No auth setup in progress. Call /auth/setup first.")

    context_id = _auth_session["context_id"]

    # Save the context ID to the database
    await db.kv_set("browserbase_context_id", context_id)

    _auth_session = None

    return {
        "status": "saved",
        "context_id": context_id,
        "message": "Auth context saved. All future agents will use this login.",
    }


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/login — auto-login with credentials
# ---------------------------------------------------------------------------

@router.post("/auth/login")
async def auth_login(body: dict):
    from stagehand import AsyncStagehand
    from browserbase import Browserbase

    email = body.get("email", "").strip()
    password = body.get("password", "").strip()
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    if not BROWSERBASE_API_KEY or not BROWSERBASE_PROJECT_ID:
        raise HTTPException(status_code=400, detail="Browserbase not configured")

    # Always create a new context for this login so we don't reuse an expired one.
    # (Stored context is only used when starting agent sessions, not here.)
    def _create_context() -> str:
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)
        context = bb.contexts.create(project_id=BROWSERBASE_PROJECT_ID)
        return context.id

    loop = asyncio.get_event_loop()
    context_id = await loop.run_in_executor(None, _create_context)

    # Use Stagehand to log into Gmail
    from backend.config import ANTHROPIC_API_KEY, STAGEHAND_MODEL

    client = AsyncStagehand(
        browserbase_api_key=BROWSERBASE_API_KEY,
        browserbase_project_id=BROWSERBASE_PROJECT_ID,
        model_api_key=ANTHROPIC_API_KEY,
    )

    session = await client.sessions.start(
        model_name=STAGEHAND_MODEL,
        browserbase_session_create_params={
            "browserSettings": {
                "context": {"id": context_id, "persist": True},
                "solveCaptchas": True,
                "fingerprint": {
                    "browsers": ["chrome"],
                    "devices": ["desktop"],
                    "operatingSystems": ["macos"],
                },
            },
            "proxies": [{"type": "browserbase", "geolocation": {"country": "US"}}],
        },
    )

    try:
        # Navigate to Gmail sign-in
        await session.navigate(url="https://accounts.google.com/signin/v2/identifier?service=mail")

        # Type email
        await session.act(input=f"Type '{email}' into the email input field")
        await session.act(input="Click the Next button")

        # Wait for password page
        import asyncio as aio
        await aio.sleep(2)

        # Type password
        await session.act(input=f"Type '{password}' into the password input field")
        await session.act(input="Click the Next button")

        # Wait for login to complete
        await aio.sleep(5)

        # Save context
        await db.kv_set("browserbase_context_id", context_id)

        # Give Browserbase a moment to persist context (cookies/session) before reusing.
        await asyncio.sleep(3)

        return {
            "status": "success",
            "context_id": context_id,
            "message": "Logged in and session saved.",
        }

    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "context_id": context_id,
            "message": "Login may have partially completed. Try the manual login flow if this fails.",
        }
    finally:
        try:
            await session.end()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/clear — remove saved context (e.g. after session expired)
# ---------------------------------------------------------------------------

@router.post("/auth/clear")
async def auth_clear():
    await db.kv_delete("browserbase_context_id")
    return {
        "status": "cleared",
        "message": "Saved login context removed. You can log in again to create a new session.",
    }


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/cancel — close the auth session without saving
# ---------------------------------------------------------------------------

@router.post("/auth/cancel")
async def auth_cancel():
    global _auth_session
    if not _auth_session:
        return {"status": "no_session"}
    _auth_session = None
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# POST /api/swarm/{swarm_id}/cancel — cancel a running swarm
# ---------------------------------------------------------------------------

@router.post("/{swarm_id}/cancel")
async def cancel_swarm_endpoint(swarm_id: str):
    swarm = await db.get_swarm(swarm_id)
    if not swarm:
        raise HTTPException(status_code=404, detail="Swarm not found")
    if swarm["status"] not in ("running", "queued"):
        raise HTTPException(status_code=400, detail="Swarm is not active")

    cancel_swarm(swarm_id)

    # Mark queued/running agents as skipped
    agents = await db.list_swarm_agents(swarm_id)
    cancelled_count = 0
    now = datetime.now(timezone.utc).isoformat()
    for a in agents:
        if a["status"] in ("queued", "running"):
            await db.update_swarm_agent(a["id"], status="skipped", finished_at=now)
            await db.increment_swarm_counter(swarm_id, "failed_agents")
            cancelled_count += 1

    await db.update_swarm(swarm_id, status="cancelled", finished_at=now)
    return {"status": "cancelled", "cancelled_agents": cancelled_count}
