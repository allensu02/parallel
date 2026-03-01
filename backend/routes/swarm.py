"""Browser swarm routes -- Browserbase auth management and swarm CRUD."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend import database as db
from backend.config import BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory auth session tracking (for the manual login flow)
# ---------------------------------------------------------------------------
_auth_session: dict | None = None


# ---------------------------------------------------------------------------
# GET /api/swarm/auth/status
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
# POST /api/swarm/auth/setup -- create context + session for manual login
# ---------------------------------------------------------------------------

@router.post("/auth/setup")
async def auth_setup():
    global _auth_session
    from browserbase import Browserbase

    if not BROWSERBASE_API_KEY or not BROWSERBASE_PROJECT_ID:
        raise HTTPException(status_code=400, detail="Browserbase credentials not configured")

    existing_context_id = await db.kv_get("browserbase_context_id")

    def _create(reuse_context_id: str | None):
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)

        if reuse_context_id:
            context_id = reuse_context_id
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
            proxies=[{
                "type": "browserbase",
                "geolocation": {"country": "US"},
            }],
        )

        debug_info = bb.sessions.debug(session.id)
        return {
            "session_id": session.id,
            "context_id": context_id,
            "live_view_url": debug_info.debugger_fullscreen_url,
        }

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _create, existing_context_id)
    _auth_session = result

    return {
        "live_view_url": result["live_view_url"],
        "session_id": result["session_id"],
        "context_id": result["context_id"],
        "message": "Log into Google in the browser window, then call /api/swarm/auth/save",
    }


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/save
# ---------------------------------------------------------------------------

@router.post("/auth/save")
async def auth_save():
    global _auth_session
    if not _auth_session:
        raise HTTPException(status_code=400, detail="No auth setup in progress.")

    context_id = _auth_session["context_id"]
    await db.kv_set("browserbase_context_id", context_id)
    _auth_session = None

    return {
        "status": "saved",
        "context_id": context_id,
        "message": "Auth context saved. All future agents will use this login.",
    }


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/login -- auto-login with credentials
# ---------------------------------------------------------------------------

@router.post("/auth/login")
async def auth_login(body: dict):
    from stagehand import AsyncStagehand
    from browserbase import Browserbase
    from backend.config import ANTHROPIC_API_KEY, STAGEHAND_MODEL

    email = body.get("email", "").strip()
    password = body.get("password", "").strip()
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    if not BROWSERBASE_API_KEY or not BROWSERBASE_PROJECT_ID:
        raise HTTPException(status_code=400, detail="Browserbase not configured")

    existing_context_id = await db.kv_get("browserbase_context_id")

    def _create_context(reuse_id: str | None) -> str:
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)
        if reuse_id:
            return reuse_id
        context = bb.contexts.create(project_id=BROWSERBASE_PROJECT_ID)
        return context.id

    loop = asyncio.get_event_loop()
    context_id = await loop.run_in_executor(None, _create_context, existing_context_id)

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
        await session.navigate(url="https://accounts.google.com/signin/v2/identifier?service=mail")
        await session.act(input=f"Type '{email}' into the email input field")
        await session.act(input="Click the Next button")
        await asyncio.sleep(2)
        await session.act(input=f"Type '{password}' into the password input field")
        await session.act(input="Click the Next button")
        await asyncio.sleep(5)

        await db.kv_set("browserbase_context_id", context_id)

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
            "message": "Login may have partially completed. Try the manual login flow.",
        }
    finally:
        try:
            await session.end()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# POST /api/swarm/auth/cancel
# ---------------------------------------------------------------------------

@router.post("/auth/cancel")
async def auth_cancel():
    global _auth_session
    if not _auth_session:
        return {"status": "no_session"}
    _auth_session = None
    return {"status": "cancelled"}
