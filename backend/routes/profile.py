"""Profile routes — user style profile and calendar availability."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import database as db

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PreferencesUpdate(BaseModel):
    tone: str = ""
    formality: int = 3
    greeting_style: str = ""
    signoff_style: str = ""
    avg_length: str = "medium"
    uses_emoji: bool = False


# ---------------------------------------------------------------------------
# GET /api/profile — get current profile
# ---------------------------------------------------------------------------

@router.get("")
async def get_profile():
    profile = await db.get_user_profile("default")
    if not profile:
        return {
            "id": "default",
            "email": "",
            "style_profile": {},
            "preferences": {},
            "has_profile": False,
        }

    return {
        "id": profile["id"],
        "email": profile.get("email", ""),
        "style_profile": json.loads(profile.get("style_profile", "{}")),
        "preferences": json.loads(profile.get("preferences", "{}")),
        "has_profile": True,
        "updated_at": profile.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# POST /api/profile/analyze — trigger sent email analysis
# ---------------------------------------------------------------------------

@router.post("/analyze")
async def analyze_profile():
    """Analyze sent emails to build a style profile."""
    try:
        from backend.services.profiler import analyze_sent_emails

        profile = await analyze_sent_emails(count=20)

        # Get email from OAuth tokens
        token_data = await db.get_oauth_token()
        email = token_data.get("email", "") if token_data else ""

        await db.save_user_profile(
            profile_id="default",
            email=email,
            style_profile=json.dumps(profile),
        )

        return {
            "status": "analyzed",
            "style_profile": profile,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")


# ---------------------------------------------------------------------------
# PUT /api/profile/preferences — update manual preferences
# ---------------------------------------------------------------------------

@router.put("/preferences")
async def update_preferences(body: PreferencesUpdate):
    prefs = body.model_dump()

    existing = await db.get_user_profile("default")
    if not existing:
        await db.save_user_profile(
            profile_id="default",
            preferences=json.dumps(prefs),
        )
    else:
        await db.update_user_profile("default", preferences=json.dumps(prefs))

    return {"status": "updated", "preferences": prefs}


# ---------------------------------------------------------------------------
# GET /api/profile/calendar/availability — check calendar
# ---------------------------------------------------------------------------

@router.get("/calendar/availability")
async def get_availability(start: str = "", end: str = ""):
    try:
        from backend.services.google_api import get_calendar_events
        events = await get_calendar_events(
            time_min=start or None,
            time_max=end or None,
        )
        return {"events": events}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Calendar error: {e}")


# ---------------------------------------------------------------------------
# POST /api/profile/update-from-diffs — update profile from edit history
# ---------------------------------------------------------------------------

@router.post("/update-from-diffs")
async def update_from_diffs():
    try:
        from backend.services.profiler import update_profile_from_diffs
        updated = await update_profile_from_diffs("default")
        return {"status": "updated", "style_profile": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")
