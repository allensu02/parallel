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
    pending_approval = "pending_approval"
    waiting_for_input = "waiting_for_input"
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
    thread_ids: list[str] = Field(default_factory=list)
    thread_subjects: dict[str, str] = Field(default_factory=dict)
    # Legacy fallback
    max_threads: int | None = None


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
    status: str = ""
    current_step: str = ""
    attempt: int = 0
    error_msg: str | None = None
    intent: str | None = None
    confidence: float | None = None
    draft_id: str | None = None
    draft_text: str | None = None
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
    event: str
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# Question / Answer / Approve models
# ---------------------------------------------------------------------------

class QuestionOut(BaseModel):
    id: str
    job_id: str
    run_id: str
    question: str
    context: str = ""
    answer: str | None = None
    status: str = "pending"
    created_at: str = ""
    answered_at: str | None = None


class AnswerIn(BaseModel):
    answer: str


class ApproveIn(BaseModel):
    action: str = "approve"  # "approve" or "discard"
    draft_text: str = ""  # optional edited draft


# ---------------------------------------------------------------------------
# Browser Swarm models
# ---------------------------------------------------------------------------

class SwarmTaskIn(BaseModel):
    """A single task for a browser agent."""
    instruction: str
    url: str = ""  # optional — agent will navigate on its own if blank
    max_steps: int = 50
    timeout: float = 300
    extract_schema: dict[str, Any] | None = None


class SwarmCreate(BaseModel):
    """Request body for launching a browser swarm."""
    tasks: list[SwarmTaskIn]


class SwarmOut(BaseModel):
    id: str
    status: str
    created_at: str
    finished_at: str | None = None
    total_agents: int = 0
    completed_agents: int = 0
    failed_agents: int = 0


class SwarmAgentOut(BaseModel):
    id: str
    swarm_id: str
    task_url: str = ""
    task_instruction: str = ""
    status: str = "queued"
    current_action: str = ""
    session_id: str = ""
    live_view_url: str = ""
    result: str | None = None
    error_msg: str | None = None
    actions_taken: int = 0
    started_at: str | None = None
    finished_at: str | None = None
