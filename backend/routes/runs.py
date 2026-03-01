"""Run management routes — create, list, get, start."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from backend import database as db
from backend.models import RunCreate, RunOut, JobOut, StepOut
from backend.agent.engine import start_run

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/runs — create a new run
# ---------------------------------------------------------------------------

@router.post("", response_model=RunOut)
async def create_run(body: RunCreate):
    run = await db.create_run()
    # Start the agent engine in the background
    asyncio.create_task(start_run(run["id"], body.max_threads))
    return RunOut(**run)


# ---------------------------------------------------------------------------
# GET /api/runs — list all runs
# ---------------------------------------------------------------------------

@router.get("", response_model=list[RunOut])
async def list_runs():
    runs = await db.list_runs()
    return [RunOut(**r) for r in runs]


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id} — get a single run with summary
# ---------------------------------------------------------------------------

@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunOut(**run)


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/jobs — list jobs in a run
# ---------------------------------------------------------------------------

@router.get("/{run_id}/jobs", response_model=list[JobOut])
async def list_jobs(run_id: str):
    jobs = await db.list_jobs(run_id)
    result = []
    for j in jobs:
        started = j.get("started_at") or ""
        finished = j.get("finished_at") or ""
        duration = 0
        if started and finished:
            from datetime import datetime
            try:
                t0 = datetime.fromisoformat(started)
                t1 = datetime.fromisoformat(finished)
                duration = int((t1 - t0).total_seconds() * 1000)
            except Exception:
                pass
        result.append(JobOut(
            id=j["id"],
            run_id=j["run_id"],
            thread_id=j["thread_id"],
            subject=j.get("subject", ""),
            status=j["status"],
            current_step=j.get("current_step", ""),
            attempt=j.get("attempt", 0),
            error_msg=j.get("error_msg"),
            intent=j.get("intent"),
            confidence=j.get("confidence"),
            draft_id=j.get("draft_id"),
            summary=j.get("summary"),
            tokens_used=j.get("tokens_used", 0),
            duration_ms=duration,
        ))
    return result


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/jobs/{job_id}/steps — list steps for a job
# ---------------------------------------------------------------------------

@router.get("/{run_id}/jobs/{job_id}/steps", response_model=list[StepOut])
async def list_steps(run_id: str, job_id: str):
    steps = await db.list_steps(job_id)
    return [
        StepOut(
            id=s["id"],
            job_id=s["job_id"],
            name=s["name"],
            status=s["status"],
            started_at=s.get("started_at"),
            finished_at=s.get("finished_at"),
            duration_ms=s.get("duration_ms", 0),
            error_msg=s.get("error_msg"),
        )
        for s in steps
    ]
