"""Gmail OAuth2 routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from backend import config
from backend.database import save_oauth_token, get_oauth_token
from backend.models import OAuthStartOut, OAuthStatusOut

router = APIRouter()

# ---------------------------------------------------------------------------
# Build an OAuth flow object
# ---------------------------------------------------------------------------

def _make_flow() -> Flow:
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth credentials not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in backend/.env",
        )
    return Flow.from_client_config(
        {
            "web": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [config.GOOGLE_REDIRECT_URI],
            }
        },
        scopes=config.GOOGLE_SCOPES,
        redirect_uri=config.GOOGLE_REDIRECT_URI,
    )


# ---------------------------------------------------------------------------
# GET /api/auth/status — check if we have valid tokens
# ---------------------------------------------------------------------------

@router.get("/status", response_model=OAuthStatusOut)
async def auth_status():
    token = await get_oauth_token()
    if token and token.get("access_token"):
        return OAuthStatusOut(authenticated=True, email=token.get("email"))
    return OAuthStatusOut(authenticated=False, email=None)


# ---------------------------------------------------------------------------
# GET /api/auth/login — start OAuth flow, return auth URL
# ---------------------------------------------------------------------------

@router.get("/login", response_model=OAuthStartOut)
async def auth_login():
    flow = _make_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return OAuthStartOut(auth_url=auth_url)


# ---------------------------------------------------------------------------
# GET /api/auth/callback?code=... — exchange code for tokens
# ---------------------------------------------------------------------------

@router.get("/callback")
async def auth_callback(code: str):
    flow = _make_flow()
    flow.fetch_token(code=code)
    creds: Credentials = flow.credentials

    # Fetch the user's email address
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    email = profile.get("emailAddress", "")

    # Persist tokens
    await save_oauth_token(
        {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token or "",
            "token_uri": creds.token_uri or "",
            "client_id": creds.client_id or "",
            "client_secret": creds.client_secret or "",
            "expiry": creds.expiry.isoformat() if creds.expiry else "",
            "email": email,
        }
    )

    # Redirect back to frontend
    return RedirectResponse(url=f"{config.FRONTEND_URL}?auth=success")


# ---------------------------------------------------------------------------
# GET /api/auth/logout — clear stored tokens
# ---------------------------------------------------------------------------

@router.post("/logout")
async def auth_logout():
    from backend.database import get_db
    db = await get_db()
    await db.execute("DELETE FROM oauth_tokens")
    await db.commit()
    return {"status": "logged_out"}
