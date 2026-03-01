"""Run management routes — create, list, get, answer, approve."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend import database as db
from backend.models import RunCreate, RunOut, JobOut, StepOut, QuestionOut, AnswerIn, ApproveIn, TaskIn
from backend.agent.engine import start_run, notify_answer, notify_approval, cancel_run
from backend.agent.screencast import set_visible_jobs

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/runs — create a new run
# ---------------------------------------------------------------------------

@router.post("", response_model=RunOut)
async def create_run(body: RunCreate):
    run = await db.create_run()
    # Convert TaskIn models to dicts for the engine
    generic_tasks = [t.model_dump() for t in body.tasks] if body.tasks else None
    # Start the agent engine in the background
    asyncio.create_task(start_run(
        run["id"],
        thread_ids=body.thread_ids or None,
        thread_subjects=body.thread_subjects or None,
        generic_tasks=generic_tasks,
    ))
    return RunOut(**run)


# ---------------------------------------------------------------------------
# GET /api/runs — list all runs
# ---------------------------------------------------------------------------

@router.get("", response_model=list[RunOut])
async def list_runs():
    runs = await db.list_runs()
    return [RunOut(**r) for r in runs]


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/cancel — cancel a running swarm
# ---------------------------------------------------------------------------

@router.post("/{run_id}/cancel")
async def cancel_run_endpoint(run_id: str):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] not in ("running", "queued"):
        raise HTTPException(status_code=400, detail="Run is not active")

    cancel_run(run_id)

    # Mark any queued/running jobs as skipped immediately
    jobs = await db.list_jobs(run_id)
    cancelled_count = 0
    for j in jobs:
        if j["status"] in ("queued", "running", "pending_approval"):
            await db.update_job(j["id"], status="skipped", current_step="done",
                                finished_at=datetime.now(timezone.utc).isoformat())
            await db.increment_run_counter(run_id, "skipped_jobs")
            # Release any waiting approval/answer events
            notify_approval(j["id"], "discard")
            notify_answer(j["id"], "")
            cancelled_count += 1

    await db.update_run(run_id, status="cancelled", finished_at=datetime.now(timezone.utc).isoformat())
    return {"status": "cancelled", "cancelled_jobs": cancelled_count}


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/visible — update which jobs should stream frames
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel


class VisibleJobsIn(_BaseModel):
    job_ids: list[str]


@router.post("/{run_id}/visible")
async def set_visible(run_id: str, body: VisibleJobsIn):
    await set_visible_jobs(run_id, body.job_ids)
    return {"status": "ok", "visible_count": len(body.job_ids)}


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id} — get a single run
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
            draft_text=j.get("draft_text") or None,
            summary=j.get("summary"),
            tokens_used=j.get("tokens_used", 0),
            duration_ms=duration,
            pipeline_type=j.get("pipeline_type", "gmail"),
            task_instruction=j.get("task_instruction", ""),
            live_view_url=j.get("live_view_url", ""),
        ))
    return result


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/jobs/{job_id}/steps
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


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/questions — list questions for a run
# ---------------------------------------------------------------------------

@router.get("/{run_id}/questions", response_model=list[QuestionOut])
async def list_questions(run_id: str):
    questions = await db.list_questions(run_id)
    return [QuestionOut(**q) for q in questions]


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/jobs/{job_id}/answer — answer a question
# ---------------------------------------------------------------------------

@router.post("/{run_id}/jobs/{job_id}/answer")
async def answer_question(run_id: str, job_id: str, body: AnswerIn):
    # Find pending question for this job
    q = await db.get_pending_question(job_id)
    if not q:
        raise HTTPException(status_code=404, detail="No pending question for this job")

    await db.answer_question(q["id"], body.answer)
    notify_answer(job_id, body.answer)
    return {"status": "answered", "question_id": q["id"]}


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/jobs/{job_id}/approve — approve or discard a draft
# ---------------------------------------------------------------------------

@router.post("/{run_id}/jobs/{job_id}/approve")
async def approve_draft(run_id: str, job_id: str, body: ApproveIn):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    draft_text = body.draft_text or job.get("draft_text", "")
    notify_approval(job_id, body.action, draft_text)

    # Track edit diffs for profile learning
    if body.action == "approve" and body.draft_text:
        original = job.get("draft_text", "")
        if original and body.draft_text != original:
            try:
                await db.append_edit_diff("default", {
                    "original": original[:1000],
                    "edited": body.draft_text[:1000],
                    "subject": job.get("subject", ""),
                    "timestamp": db._now(),
                })
            except Exception:
                pass  # Non-fatal

    # Failsafe: if the engine is no longer listening, update DB directly
    if body.action == "discard":
        await db.update_job(job_id, status="skipped", current_step="done")
        await db.increment_run_counter(run_id, "skipped_jobs")
    elif body.action == "approve" and job.get("status") == "pending_approval":
        # Mark as running — engine will update to completed when done
        await db.update_job(job_id, status="running", current_step="save_draft")

    return {"status": "ok", "action": body.action}
