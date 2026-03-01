"""Application configuration — loaded from environment / .env file."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the backend directory
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path)

# ---------------------------------------------------------------------------
# Google OAuth2
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI: str = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/callback"
)
GOOGLE_SCOPES: list[str] = [
    # Gmail
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    # Calendar (read/write)
    "https://www.googleapis.com/auth/calendar",
    # Google Docs
    "https://www.googleapis.com/auth/documents",
    # Google Sheets
    "https://www.googleapis.com/auth/spreadsheets",
    # Google Slides
    "https://www.googleapis.com/auth/presentations",
    # Google Forms
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    # Google Drive
    "https://www.googleapis.com/auth/drive",
    # Identity
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_PATH: str = os.getenv(
    "DATABASE_PATH",
    str(Path(__file__).resolve().parent / "parallel.db"),
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
SECRET_KEY: str = os.getenv("SECRET_KEY", "parallel-dev-secret-key")
FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", "8000"))

# ---------------------------------------------------------------------------
# Browser harness
# ---------------------------------------------------------------------------
BROWSER_POOL_SIZE: int = int(os.getenv("BROWSER_POOL_SIZE", "7"))

# ---------------------------------------------------------------------------
# Concurrency limits
# ---------------------------------------------------------------------------
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "200"))
GMAIL_CONCURRENCY: int = int(os.getenv("GMAIL_CONCURRENCY", "25"))
LLM_CONCURRENCY: int = int(os.getenv("LLM_CONCURRENCY", "5"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
STEP_TIMEOUT_SECONDS: int = int(os.getenv("STEP_TIMEOUT_SECONDS", "180"))

# ---------------------------------------------------------------------------
# Screencast / live browser streaming
# ---------------------------------------------------------------------------
SCREENCAST_QUALITY: int = int(os.getenv("SCREENCAST_QUALITY", "50"))
SCREENCAST_MAX_WIDTH: int = int(os.getenv("SCREENCAST_MAX_WIDTH", "800"))
SCREENCAST_MAX_HEIGHT: int = int(os.getenv("SCREENCAST_MAX_HEIGHT", "600"))
MAX_VISIBLE_STREAMS: int = int(os.getenv("MAX_VISIBLE_STREAMS", "7"))

# ---------------------------------------------------------------------------
# Browserbase + Stagehand (generic browser agent / swarm fallback)
# ---------------------------------------------------------------------------
BROWSERBASE_API_KEY: str = os.getenv("BROWSERBASE_API_KEY", "")
BROWSERBASE_PROJECT_ID: str = os.getenv("BROWSERBASE_PROJECT_ID", "")
STAGEHAND_MODEL: str = os.getenv("STAGEHAND_MODEL", "anthropic/claude-sonnet-4-5")
STAGEHAND_API_URL: str = os.getenv("STAGEHAND_API_URL", "https://api.stagehand.browserbase.com")
SWARM_MAX_AGENTS: int = int(os.getenv("SWARM_MAX_AGENTS", "20"))
SWARM_AGENT_TIMEOUT: int = int(os.getenv("SWARM_AGENT_TIMEOUT", "300"))
SWARM_MAX_STEPS: int = int(os.getenv("SWARM_MAX_STEPS", "30"))
