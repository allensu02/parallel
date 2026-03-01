"""Pydantic models for API request/response and internal data transfer."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class StepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class IntentType(str, enum.Enum):
    reply = "reply"
    ignore = "ignore"
    escalate = "escalate"


# ---------------------------------------------------------------------------
# Step names (deterministic pipeline)
# ---------------------------------------------------------------------------

STEP_NAMES: list[str] = [
    "fetch_thread",
    "classify_intent",
    "generate_draft",
    "save_draft",
    "apply_label",
]


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class RunCreate(BaseModel):
    max_threads: int = Field(default=100, ge=1, le=500)


class RunOut(BaseModel):
    id: str
    status: RunStatus
    created_at: str
    finished_at: str | None = None
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    skipped_jobs: int = 0


class JobOut(BaseModel):
    id: str
    run_id: str
    thread_id: str
    subject: str = ""
    status: JobStatus
    current_step: str = ""
    attempt: int = 0
    error_msg: str | None = None
    intent: str | None = None
    confidence: float | None = None
    draft_id: str | None = None
    summary: str | None = None
    tokens_used: int = 0
    duration_ms: int = 0


class StepOut(BaseModel):
    id: str
    job_id: str
    name: str
    status: StepStatus
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int = 0
    error_msg: str | None = None


class OAuthStartOut(BaseModel):
    auth_url: str


class OAuthStatusOut(BaseModel):
    authenticated: bool
    email: str | None = None


# ---------------------------------------------------------------------------
# SSE event model
# ---------------------------------------------------------------------------

class SSEEvent(BaseModel):
    event: str  # e.g. "job.started", "step.completed", "run.completed"
    data: dict[str, Any]
