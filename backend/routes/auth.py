"""Authentication routes — Google OAuth2 flow.

Supports both:
  1. Real OAuth2 (when GOOGLE_CLIENT_ID is set)
  2. Fallback to Playwright browser-based login (when no OAuth config)
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from backend import database as db
from backend.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GOOGLE_SCOPES,
    FRONTEND_URL,
)
from backend.models import OAuthStatusOut

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers — build Google OAuth2 flow
# ---------------------------------------------------------------------------

_USE_REAL_OAUTH = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _build_flow():
    """Build a google_auth_oauthlib Flow from config."""
    from google_auth_oauthlib.flow import Flow

    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    return flow


async def _get_credentials():
    """Rebuild google.oauth2.credentials.Credentials from stored tokens."""
    token_data = await db.get_oauth_token()
    if not token_data or not token_data.get("access_token"):
        return None

    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", ""),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id", GOOGLE_CLIENT_ID),
        client_secret=token_data.get("client_secret", GOOGLE_CLIENT_SECRET),
    )
    # Refresh if expired
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        try:
            creds.refresh(Request())
            await db.save_oauth_token({
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "expiry": creds.expiry.isoformat() if creds.expiry else "",
                "email": token_data.get("email", ""),
            })
        except Exception as e:
            print(f"[Auth] Token refresh failed: {e}")
            return None
    return creds


# ---------------------------------------------------------------------------
# GET /api/auth/status — check authentication
# ---------------------------------------------------------------------------

@router.get("/status", response_model=OAuthStatusOut)
async def auth_status():
    if _USE_REAL_OAUTH:
        token_data = await db.get_oauth_token()
        if token_data and token_data.get("access_token"):
            return OAuthStatusOut(
                authenticated=True,
                email=token_data.get("email") or None,
            )
        return OAuthStatusOut(authenticated=False, email=None)
    else:
        # Fallback: Playwright-based auth
        from backend.agent.browser_harness import harness
        return OAuthStatusOut(
            authenticated=harness.authenticated,
            email=harness.email or None,
        )


# ---------------------------------------------------------------------------
# GET /api/auth/login — start OAuth flow (or Playwright fallback)
# ---------------------------------------------------------------------------

@router.get("/login")
async def auth_login_get():
    """Returns the Google OAuth URL for the frontend to redirect to."""
    if not _USE_REAL_OAUTH:
        raise HTTPException(
            status_code=400,
            detail="OAuth not configured. Use POST /api/auth/login for browser login.",
        )
    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return {"auth_url": auth_url}


@router.post("/login")
async def auth_login_post():
    """Fallback: Playwright-based browser login, or return OAuth URL."""
    if _USE_REAL_OAUTH:
        flow = _build_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return {"status": "redirect", "auth_url": auth_url}
    else:
        # No visible browser login — direct to OAuth
        return {
            "status": "error",
            "message": "Please use Google OAuth to sign in.",
        }


# ---------------------------------------------------------------------------
# GET /api/auth/callback — handle Google OAuth redirect
# ---------------------------------------------------------------------------

@router.get("/callback")
async def auth_callback(code: str = "", error: str = ""):
    """Handle the Google OAuth2 callback."""
    if error:
        return RedirectResponse(f"{FRONTEND_URL}?auth_error={error}")

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received")

    try:
        import os
        # Google returns the union of old + new scopes when include_granted_scopes
        # is used, which causes a scope mismatch error. Disable the check.
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        flow = _build_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Get user info
        from googleapiclient.discovery import build
        oauth2_service = build("oauth2", "v2", credentials=creds)
        user_info = oauth2_service.userinfo().get().execute()
        email = user_info.get("email", "")

        # Store tokens in DB
        await db.save_oauth_token({
            "access_token": creds.token,
            "refresh_token": creds.refresh_token or "",
            "token_uri": creds.token_uri or "https://oauth2.googleapis.com/token",
            "client_id": creds.client_id or GOOGLE_CLIENT_ID,
            "client_secret": creds.client_secret or GOOGLE_CLIENT_SECRET,
            "expiry": creds.expiry.isoformat() if creds.expiry else "",
            "email": email,
        })

        print(f"[Auth] OAuth login successful: {email}")
        return RedirectResponse(f"{FRONTEND_URL}?auth_success=true")

    except Exception as e:
        print(f"[Auth] OAuth callback error: {e}")
        return RedirectResponse(f"{FRONTEND_URL}?auth_error=callback_failed")


# ---------------------------------------------------------------------------
# POST /api/auth/logout — clear stored tokens
# ---------------------------------------------------------------------------

@router.post("/logout")
async def auth_logout():
    if _USE_REAL_OAUTH:
        # Clear OAuth tokens from DB
        d = await db.get_db()
        await d.execute("DELETE FROM oauth_tokens WHERE id = 'default'")
        await d.commit()
    else:
        from backend.agent.browser_harness import harness, STORAGE_STATE_PATH
        if STORAGE_STATE_PATH.exists():
            STORAGE_STATE_PATH.unlink()
    return {"status": "logged_out"}
