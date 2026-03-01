"""Agent engine — orchestrates the step machine for each email thread.

Pipeline:
1. Extract thread content via Gmail API (or DOM fallback)
2. Classify intent via LLM
3. Check if user input is needed → pause if so
4. Generate draft via streaming LLM (tokens sent to frontend live)
5. Wait for user approval
6. On approval: compose in Gmail via API (or Computer Use fallback)
"""

from __future__ import annotations

import asyncio
import random
import traceback
from datetime import datetime, timezone

from playwright.async_api import Page

from backend import database as db
from backend.agent.browser_harness import harness
from backend.agent import text_extractor
from backend.agent import computer_use
from backend.agent import llm
from backend.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    LLM_CONCURRENCY,
    MAX_RETRIES,
    STEP_TIMEOUT_SECONDS,
)
from backend.models import STEP_NAMES, IntentType
from backend.routes.events import publish_event, publish_global

# ---------------------------------------------------------------------------
# Concurrency semaphores
# ---------------------------------------------------------------------------

_browser_sem = asyncio.Semaphore(8)
_llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)
_api_sem = asyncio.Semaphore(25)  # Gmail API concurrent requests

_USE_API = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# ---------------------------------------------------------------------------
# Run cancellation tracking
# ---------------------------------------------------------------------------

_cancelled_runs: set[str] = set()


def cancel_run(run_id: str) -> None:
    """Mark a run for cancellation. Workers will check this flag."""
    _cancelled_runs.add(run_id)


def _is_cancelled(run_id: str) -> bool:
    return run_id in _cancelled_runs


# ---------------------------------------------------------------------------
# Pending approval / answer events — job_id -> asyncio.Event
# ---------------------------------------------------------------------------

_approval_events: dict[str, asyncio.Event] = {}
_approval_data: dict[str, dict] = {}

_answer_events: dict[str, asyncio.Event] = {}
_answer_data: dict[str, str] = {}


def notify_answer(job_id: str, answer: str) -> None:
    _answer_data[job_id] = answer
    evt = _answer_events.get(job_id)
    if evt:
        evt.set()


def notify_approval(job_id: str, action: str, draft_text: str = "") -> None:
    _approval_data[job_id] = {"action": action, "draft_text": draft_text}
    evt = _approval_events.get(job_id)
    if evt:
        evt.set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(run_id: str, event: str, data: dict) -> None:
    await publish_event(run_id, event, data)
    await publish_global(event, {**data, "run_id": run_id})


# ---------------------------------------------------------------------------
# Step execution wrapper
# ---------------------------------------------------------------------------


async def _run_step(
    run_id: str, job_id: str, step_name: str, step_fn, *args,
    semaphore: asyncio.Semaphore | None = None,
    timeout: int | None = None,
):
    step = await db.create_step(job_id, step_name)
    step_id = step["id"]

    await db.update_step(step_id, status="running", started_at=_now())
    await db.update_job(job_id, current_step=step_name)
    await _emit(run_id, "step.started", {"job_id": job_id, "step": step_name, "step_id": step_id})

    step_timeout = timeout or STEP_TIMEOUT_SECONDS
    start = datetime.now(timezone.utc)
    try:
        if semaphore:
            async with semaphore:
                result = await asyncio.wait_for(step_fn(*args), timeout=step_timeout)
        else:
            result = await asyncio.wait_for(step_fn(*args), timeout=step_timeout)

        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        await db.update_step(step_id, status="completed", finished_at=_now(), duration_ms=elapsed)
        await _emit(run_id, "step.completed", {"job_id": job_id, "step": step_name, "step_id": step_id, "duration_ms": elapsed})
        return result

    except Exception as exc:
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        err_msg = f"{type(exc).__name__}: {exc}"
        await db.update_step(step_id, status="failed", finished_at=_now(), duration_ms=elapsed, error_msg=err_msg)
        await _emit(run_id, "step.failed", {"job_id": job_id, "step": step_name, "step_id": step_id, "error": err_msg, "duration_ms": elapsed})
        raise


# ---------------------------------------------------------------------------
# Screenshot callback for Computer Use (approval phase only)
# ---------------------------------------------------------------------------


def _make_screenshot_cb(run_id: str, job_id: str, step_name: str):
    _latest_path = {"path": ""}

    async def _cb(b64_data: str) -> None:
        path = harness.save_screenshot_b64(b64_data, job_id, step_name)
        _latest_path["path"] = path
        await _emit(run_id, "job.screenshot", {"job_id": job_id, "step": step_name, "url": path})

    _cb.latest_path = _latest_path  # type: ignore
    return _cb


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------


async def _step_fetch_thread_api(thread_id: str) -> dict:
    """Fetch via Gmail API (no browser needed)."""
    from backend.services.google_api import fetch_thread_content_api
    return await fetch_thread_content_api(thread_id)


async def _step_fetch_thread_browser(page: Page, thread_id: str) -> dict:
    """Fetch via Playwright DOM extraction."""
    return await text_extractor.extract_thread_content(page, thread_id)


async def _step_compose_via_api(draft_text: str, thread_id: str) -> bool:
    """Create draft via Gmail API."""
    from backend.services.google_api import create_draft_api
    result = await create_draft_api(thread_id, draft_text)
    return bool(result and result.get("draft_id"))


async def _step_check_needs_info(subject: str, sender: str, messages: list[dict]) -> tuple[bool, str]:
    return await llm.check_needs_info(subject, sender, messages)


async def _step_generate_draft_stream(
    run_id: str, job_id: str,
    subject: str, sender: str, messages: list[dict],
    extra_context: str = "",
) -> tuple[str, str, float, int]:
    import json

    async def on_token(chunk: str) -> None:
        await _emit(run_id, "job.draft_token", {"job_id": job_id, "token": chunk})

    user_profile = None
    try:
        profile_data = await db.get_user_profile("default")
        if profile_data:
            style_json = profile_data.get("style_profile", "{}")
            user_profile = json.loads(style_json) if style_json else None
    except Exception:
        pass

    return await llm.generate_draft_stream(
        subject, sender, messages,
        on_token=on_token,
        extra_context=extra_context,
        user_profile=user_profile,
    )


# ---------------------------------------------------------------------------
# Job handler — full pipeline for one email thread
# ---------------------------------------------------------------------------


async def _process_job(job_data: dict) -> None:
    run_id = job_data["run_id"]
    job_id = job_data["job_id"]
    thread_id = job_data["thread_id"]
    subject = job_data.get("subject", "")

    # Check cancellation before starting
    if _is_cancelled(run_id):
        await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
        await db.increment_run_counter(run_id, "skipped_jobs")
        return

    await db.update_job(job_id, status="running", started_at=_now(), attempt=job_data.get("attempt", 1))
    await _emit(run_id, "job.started", {"job_id": job_id, "thread_id": thread_id})

    page: Page | None = None
    try:
        # ── Step 1: Fetch thread ──
        if _USE_API:
            # Gmail API — no browser needed, uses API semaphore
            thread_data = await _run_step(
                run_id, job_id, "fetch_thread",
                _step_fetch_thread_api, thread_id,
                semaphore=_api_sem, timeout=15,
            )
        else:
            async with _browser_sem:
                page = await harness.acquire_page()
                thread_data = await _run_step(
                    run_id, job_id, "fetch_thread",
                    _step_fetch_thread_browser, page, thread_id,
                    timeout=30,
                )
                await harness.release_page(page)
                page = None

        if _is_cancelled(run_id):
            await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
            await db.increment_run_counter(run_id, "skipped_jobs")
            return

        subject = thread_data.get("subject", subject) or "(no subject)"
        sender = thread_data.get("sender", "")
        messages = thread_data.get("messages", [])
        await db.update_job(job_id, subject=subject)

        await _emit(run_id, "job.context", {
            "job_id": job_id, "subject": subject, "sender": sender,
            "message_count": thread_data.get("message_count", 0),
        })

        # ── Step 2: Classify intent via LLM ──
        intent_result = await _run_step(
            run_id, job_id, "classify_intent",
            llm.classify_intent, subject, sender, messages,
            semaphore=_llm_sem,
        )
        intent, classify_tokens = intent_result
        await db.update_job(job_id, intent=intent.value, tokens_used=classify_tokens)
        await _emit(run_id, "job.classified", {"job_id": job_id, "intent": intent.value, "tokens": classify_tokens})

        if _is_cancelled(run_id):
            await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
            await db.increment_run_counter(run_id, "skipped_jobs")
            return

        # ── Branch on intent ──
        if intent in (IntentType.ignore, IntentType.escalate):
            await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
            await db.increment_run_counter(run_id, "skipped_jobs")
            await _emit(run_id, "job.skipped", {"job_id": job_id, "reason": intent.value})
            return

        # ── Step 2.5: Check if user input needed ──
        extra_context = ""
        try:
            async with _llm_sem:
                needs_info, question_text = await _step_check_needs_info(subject, sender, messages)
        except Exception:
            needs_info = False
            question_text = ""

        if needs_info and question_text:
            q = await db.create_question(job_id, run_id, question_text, context="email")
            await db.update_job(job_id, current_step="waiting_for_input")
            await _emit(run_id, "job.question", {
                "job_id": job_id, "question_id": q["id"],
                "question": question_text, "subject": subject,
            })

            evt = asyncio.Event()
            _answer_events[job_id] = evt
            try:
                await asyncio.wait_for(evt.wait(), timeout=600)
                extra_context = _answer_data.get(job_id, "")
            except asyncio.TimeoutError:
                extra_context = ""
            finally:
                _answer_events.pop(job_id, None)
                _answer_data.pop(job_id, None)

        if _is_cancelled(run_id):
            await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
            await db.increment_run_counter(run_id, "skipped_jobs")
            return

        # ── Step 3: Generate draft via streaming LLM ──
        await db.update_job(job_id, current_step="generate_draft")
        await _emit(run_id, "step.started", {"job_id": job_id, "step": "generate_draft", "step_id": ""})

        draft_start = datetime.now(timezone.utc)
        draft_text, summary, confidence, draft_tokens = await _step_generate_draft_stream(
            run_id, job_id, subject, sender, messages,
            extra_context=extra_context,
        )
        draft_elapsed = int((datetime.now(timezone.utc) - draft_start).total_seconds() * 1000)

        total_tokens = classify_tokens + draft_tokens
        await db.update_job(
            job_id, summary=summary, confidence=confidence,
            tokens_used=total_tokens, draft_text=draft_text,
            current_step="pending_approval",
        )

        await _emit(run_id, "step.completed", {"job_id": job_id, "step": "generate_draft", "step_id": "", "duration_ms": draft_elapsed})
        await _emit(run_id, "job.draft_complete", {
            "job_id": job_id, "draft_text": draft_text,
            "summary": summary, "confidence": confidence, "tokens_used": total_tokens,
        })

        # ── Step 4: Wait for user approval ──
        await db.update_job(job_id, status="pending_approval")
        await _emit(run_id, "job.pending_approval", {
            "job_id": job_id, "draft_text": draft_text, "subject": subject,
        })

        evt = asyncio.Event()
        _approval_events[job_id] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=3600)
            approval = _approval_data.get(job_id, {})
        except asyncio.TimeoutError:
            approval = {"action": "discard"}
        finally:
            _approval_events.pop(job_id, None)
            _approval_data.pop(job_id, None)

        action = approval.get("action", "discard")
        final_draft = approval.get("draft_text", draft_text)

        if action == "discard" or _is_cancelled(run_id):
            await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
            await db.increment_run_counter(run_id, "skipped_jobs")
            await _emit(run_id, "job.skipped", {"job_id": job_id, "reason": "discarded"})
            return

        # ── Step 5: Compose in Gmail ──
        if _USE_API:
            await _run_step(
                run_id, job_id, "save_draft",
                _step_compose_via_api, final_draft, thread_id,
                semaphore=_api_sem, timeout=15,
            )
            await db.update_job(job_id, draft_id="api-draft")
        else:
            async with _browser_sem:
                page = await harness.acquire_page()
                try:
                    await _run_step(
                        run_id, job_id, "save_draft",
                        lambda: computer_use.compose_draft_reply(page, final_draft,
                            on_screenshot=_make_screenshot_cb(run_id, job_id, "save_draft")),
                        timeout=60,
                    )
                    await db.update_job(job_id, draft_id="browser-draft")

                    try:
                        await _run_step(
                            run_id, job_id, "apply_label",
                            lambda: computer_use.apply_label(page, "AI-Drafted",
                                on_screenshot=_make_screenshot_cb(run_id, job_id, "apply_label")),
                            timeout=30,
                        )
                    except Exception as label_err:
                        print(f"[Engine] Label step failed (non-fatal): {label_err}")
                finally:
                    await harness.release_page(page)
                    page = None

        # ── Done ──
        await db.update_job(job_id, status="completed", current_step="done", finished_at=_now())
        await db.increment_run_counter(run_id, "completed_jobs")
        await _emit(run_id, "job.completed", {
            "job_id": job_id, "draft_id": "api-draft" if _USE_API else "browser-draft",
            "confidence": confidence, "summary": summary, "tokens_used": total_tokens,
        })

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        attempt = job_data.get("attempt", 1)

        if page:
            try:
                err_ss = await harness.save_screenshot(page, job_id, "error")
                await _emit(run_id, "job.screenshot", {"job_id": job_id, "step": "error", "url": err_ss})
            except Exception:
                pass

        if attempt < MAX_RETRIES and not _is_cancelled(run_id):
            backoff = min(10, (2 ** attempt) + random.uniform(0, 0.5))
            await db.update_job(job_id, status="queued", error_msg=err_msg)
            await _emit(run_id, "job.retrying", {
                "job_id": job_id, "attempt": attempt + 1,
                "backoff_ms": int(backoff * 1000), "error": err_msg,
            })
            await asyncio.sleep(backoff)
            job_data["attempt"] = attempt + 1
            await _process_job(job_data)
        else:
            await db.update_job(job_id, status="failed", error_msg=err_msg, finished_at=_now())
            await db.increment_run_counter(run_id, "failed_jobs")
            await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_msg, "traceback": traceback.format_exc()[-500:]})
    finally:
        if page:
            await harness.release_page(page)


# ---------------------------------------------------------------------------
# Run orchestrator
# ---------------------------------------------------------------------------


async def start_run(run_id: str, thread_ids: list[str] | None = None, thread_subjects: dict[str, str] | None = None) -> None:
    try:
        # Only start browser if we actually need it (no API)
        if not _USE_API and not harness._started:
            await harness.start(headless=True)

        await db.update_run(run_id, status="running")
        await _emit(run_id, "run.started", {"run_id": run_id})

        if not thread_ids:
            await db.update_run(run_id, status="completed", finished_at=_now())
            await _emit(run_id, "run.completed", {"run_id": run_id, "total": 0, "completed": 0, "failed": 0})
            return

        subjects = thread_subjects or {}
        await db.update_run(run_id, total_jobs=len(thread_ids))
        await _emit(run_id, "run.threads_loaded", {"run_id": run_id, "count": len(thread_ids)})

        # Create all jobs
        job_items = []
        for tid in thread_ids:
            subj = subjects.get(tid, "")
            job = await db.create_job(run_id, tid, subj[:100])
            if job["status"] == "skipped":
                continue
            job_items.append({
                "run_id": run_id,
                "job_id": job["id"],
                "thread_id": tid,
                "subject": subj,
                "attempt": 1,
            })

        await _emit(run_id, "run.jobs_queued", {"run_id": run_id, "count": len(job_items)})

        if _USE_API:
            # Gmail API mode — launch ALL jobs as concurrent tasks (no browser bottleneck)
            # The _api_sem and _llm_sem control actual concurrency
            tasks = [asyncio.create_task(_process_job(item)) for item in job_items]
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            # Playwright mode — use queue with bounded workers
            from backend.agent.queue import JobQueue
            queue = JobQueue()
            queue.set_handler(_process_job)
            for item in job_items:
                await queue.put(item)
            pool_workers = min(len(job_items), harness._pool_size)
            await queue.start(num_workers=pool_workers)
            await queue.wait_done()

        # Finalize
        _cancelled_runs.discard(run_id)
        run = await db.get_run(run_id)
        final_status = "completed" if run_id not in _cancelled_runs else "cancelled"
        await db.update_run(run_id, status=final_status, finished_at=_now())
        await _emit(run_id, "run.completed", {
            "run_id": run_id,
            "total": run["total_jobs"] if run else 0,
            "completed": run["completed_jobs"] if run else 0,
            "failed": run["failed_jobs"] if run else 0,
            "skipped": run["skipped_jobs"] if run else 0,
        })

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"[Engine] Run {run_id} FAILED: {err}")
        print(traceback.format_exc())
        _cancelled_runs.discard(run_id)
        await db.update_run(run_id, status="failed", finished_at=_now())
        await _emit(run_id, "run.failed", {"run_id": run_id, "error": err})
