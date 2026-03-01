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
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
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
# Concurrency limits
# ---------------------------------------------------------------------------
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "200"))
GMAIL_CONCURRENCY: int = int(os.getenv("GMAIL_CONCURRENCY", "25"))
LLM_CONCURRENCY: int = int(os.getenv("LLM_CONCURRENCY", "10"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
STEP_TIMEOUT_SECONDS: int = int(os.getenv("STEP_TIMEOUT_SECONDS", "60"))
