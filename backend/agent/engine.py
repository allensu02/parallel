"""Agent engine — orchestrates the step machine for each email thread."""

from __future__ import annotations

import asyncio
import random
import traceback
from datetime import datetime, timezone

from backend import database as db
from backend.agent import gmail, llm
from backend.agent.queue import JobQueue
from backend.config import (
    GMAIL_CONCURRENCY,
    LLM_CONCURRENCY,
    MAX_RETRIES,
    STEP_TIMEOUT_SECONDS,
)
from backend.models import STEP_NAMES, IntentType
from backend.routes.events import publish_event, publish_global

# ---------------------------------------------------------------------------
# Concurrency semaphores
# ---------------------------------------------------------------------------

_gmail_sem = asyncio.Semaphore(GMAIL_CONCURRENCY)
_llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(run_id: str, event: str, data: dict) -> None:
    """Publish event to both run-specific and global streams."""
    await publish_event(run_id, event, data)
    await publish_global(event, {**data, "run_id": run_id})


# ---------------------------------------------------------------------------
# Step execution with timeout and retry
# ---------------------------------------------------------------------------

async def _run_step(
    run_id: str,
    job_id: str,
    step_name: str,
    step_fn,
    *args,
    semaphore: asyncio.Semaphore | None = None,
) -> dict | None:
    """Execute a single step, emitting events and handling errors."""
    step = await db.create_step(job_id, step_name)
    step_id = step["id"]

    await db.update_step(step_id, status="running", started_at=_now())
    await db.update_job(job_id, current_step=step_name)
    await _emit(run_id, "step.started", {
        "job_id": job_id, "step": step_name, "step_id": step_id,
    })

    start = datetime.now(timezone.utc)
    try:
        if semaphore:
            async with semaphore:
                result = await asyncio.wait_for(
                    step_fn(*args), timeout=STEP_TIMEOUT_SECONDS
                )
        else:
            result = await asyncio.wait_for(
                step_fn(*args), timeout=STEP_TIMEOUT_SECONDS
            )

        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        await db.update_step(
            step_id,
            status="completed",
            finished_at=_now(),
            duration_ms=elapsed,
        )
        await _emit(run_id, "step.completed", {
            "job_id": job_id,
            "step": step_name,
            "step_id": step_id,
            "duration_ms": elapsed,
        })
        return result

    except Exception as exc:
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        err_msg = f"{type(exc).__name__}: {exc}"
        await db.update_step(
            step_id,
            status="failed",
            finished_at=_now(),
            duration_ms=elapsed,
            error_msg=err_msg,
        )
        await _emit(run_id, "step.failed", {
            "job_id": job_id,
            "step": step_name,
            "step_id": step_id,
            "error": err_msg,
            "duration_ms": elapsed,
        })
        raise


# ---------------------------------------------------------------------------
# Job handler — the full step machine for a single thread
# ---------------------------------------------------------------------------

async def _process_job(job_data: dict) -> None:
    """Process a single email thread through the full agent pipeline."""
    run_id = job_data["run_id"]
    job_id = job_data["job_id"]
    thread_id = job_data["thread_id"]

    await db.update_job(job_id, status="running", started_at=_now(), attempt=job_data.get("attempt", 1))
    await _emit(run_id, "job.started", {
        "job_id": job_id, "thread_id": thread_id,
    })

    try:
        # Step 1: Fetch thread
        thread_data = await _run_step(
            run_id, job_id, "fetch_thread",
            gmail.fetch_thread, thread_id,
            semaphore=_gmail_sem,
        )

        subject = thread_data["subject"]
        sender = thread_data["sender"]
        messages = thread_data["messages"]
        await db.update_job(job_id, subject=subject)

        await _emit(run_id, "job.context", {
            "job_id": job_id,
            "subject": subject,
            "sender": sender,
            "message_count": thread_data["message_count"],
        })

        # Step 2: Classify intent
        intent_result = await _run_step(
            run_id, job_id, "classify_intent",
            llm.classify_intent, subject, sender, messages,
            semaphore=_llm_sem,
        )
        intent, classify_tokens = intent_result
        await db.update_job(job_id, intent=intent.value, tokens_used=classify_tokens)

        await _emit(run_id, "job.classified", {
            "job_id": job_id, "intent": intent.value, "tokens": classify_tokens,
        })

        # Branch based on intent
        if intent == IntentType.ignore:
            # Step: label as skipped
            await _run_step(
                run_id, job_id, "apply_label",
                gmail.apply_label, thread_id, "AI-Skipped",
                semaphore=_gmail_sem,
            )
            await db.update_job(
                job_id, status="skipped", current_step="done", finished_at=_now()
            )
            await db.increment_run_counter(run_id, "skipped_jobs")
            await _emit(run_id, "job.skipped", {"job_id": job_id, "reason": "ignore"})
            return

        if intent == IntentType.escalate:
            await _run_step(
                run_id, job_id, "apply_label",
                gmail.apply_label, thread_id, "AI-Needs-Review",
                semaphore=_gmail_sem,
            )
            await db.update_job(
                job_id, status="skipped", current_step="done", finished_at=_now()
            )
            await db.increment_run_counter(run_id, "skipped_jobs")
            await _emit(run_id, "job.skipped", {"job_id": job_id, "reason": "escalate"})
            return

        # Step 3: Generate draft
        draft_result = await _run_step(
            run_id, job_id, "generate_draft",
            llm.generate_draft, subject, sender, messages,
            semaphore=_llm_sem,
        )
        draft_text, summary, confidence, draft_tokens = draft_result
        total_tokens = classify_tokens + draft_tokens
        await db.update_job(
            job_id,
            summary=summary,
            confidence=confidence,
            tokens_used=total_tokens,
        )

        # Step 4: Save draft to Gmail
        reply_to = sender
        draft_html = draft_text.replace("\n", "<br>")
        draft_id = await _run_step(
            run_id, job_id, "save_draft",
            gmail.create_draft, thread_id, reply_to, subject, draft_html,
            semaphore=_gmail_sem,
        )
        await db.update_job(job_id, draft_id=draft_id)

        # Step 5: Apply label
        await _run_step(
            run_id, job_id, "apply_label",
            gmail.apply_label, thread_id, "AI-Drafted",
            semaphore=_gmail_sem,
        )

        # Done!
        await db.update_job(
            job_id, status="completed", current_step="done", finished_at=_now()
        )
        await db.increment_run_counter(run_id, "completed_jobs")
        await _emit(run_id, "job.completed", {
            "job_id": job_id,
            "draft_id": draft_id,
            "confidence": confidence,
            "summary": summary,
            "tokens_used": total_tokens,
        })

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        attempt = job_data.get("attempt", 1)

        if attempt < MAX_RETRIES:
            # Retry with backoff
            backoff = min(30, (2 ** attempt) + random.uniform(0, 1))
            await db.update_job(job_id, status="queued", error_msg=err_msg)
            await _emit(run_id, "job.retrying", {
                "job_id": job_id,
                "attempt": attempt + 1,
                "backoff_ms": int(backoff * 1000),
                "error": err_msg,
            })
            await asyncio.sleep(backoff)
            job_data["attempt"] = attempt + 1
            await _process_job(job_data)
        else:
            await db.update_job(
                job_id, status="failed", error_msg=err_msg, finished_at=_now()
            )
            await db.increment_run_counter(run_id, "failed_jobs")
            await _emit(run_id, "job.failed", {
                "job_id": job_id,
                "error": err_msg,
                "traceback": traceback.format_exc()[-500:],
            })


# ---------------------------------------------------------------------------
# Run orchestrator
# ---------------------------------------------------------------------------

async def start_run(run_id: str, max_threads: int = 100) -> None:
    """Kick off a full run: fetch threads, enqueue jobs, process them all."""
    try:
        await db.update_run(run_id, status="running")
        await _emit(run_id, "run.started", {"run_id": run_id})

        # Fetch thread list from Gmail
        threads = await gmail.fetch_thread_list(max_results=max_threads)
        if not threads:
            await db.update_run(run_id, status="completed", finished_at=_now())
            await _emit(run_id, "run.completed", {
                "run_id": run_id, "total": 0, "completed": 0, "failed": 0,
            })
            return

        await db.update_run(run_id, total_jobs=len(threads))
        await _emit(run_id, "run.threads_loaded", {
            "run_id": run_id, "count": len(threads),
        })

        # Create jobs
        queue = JobQueue()
        queue.set_handler(_process_job)

        for t in threads:
            job = await db.create_job(run_id, t["id"], t.get("snippet", "")[:100])
            if job["status"] == "skipped":
                continue  # Idempotency — already processed
            await queue.put({
                "run_id": run_id,
                "job_id": job["id"],
                "thread_id": t["id"],
                "attempt": 1,
            })

        await _emit(run_id, "run.jobs_queued", {
            "run_id": run_id, "count": len(threads),
        })

        # Process all jobs
        await queue.start(num_workers=min(len(threads), 50))
        await queue.wait_done()

        # Finalize run
        run = await db.get_run(run_id)
        await db.update_run(run_id, status="completed", finished_at=_now())
        await _emit(run_id, "run.completed", {
            "run_id": run_id,
            "total": run["total_jobs"] if run else 0,
            "completed": run["completed_jobs"] if run else 0,
            "failed": run["failed_jobs"] if run else 0,
            "skipped": run["skipped_jobs"] if run else 0,
        })

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        await db.update_run(run_id, status="failed", finished_at=_now())
        await _emit(run_id, "run.failed", {"run_id": run_id, "error": err})
