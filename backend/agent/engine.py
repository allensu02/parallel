"""Agent engine — orchestrates the step machine for each email thread.

Pipeline:
1. Acquire dedicated browser page + start screencast
2. Fetch thread content via Gmail API (fast) while browser shows the email
3. Classify intent via LLM
4. Check if user input is needed → pause if so
5. Generate draft via streaming LLM (tokens sent to frontend live)
6. Visual compose: navigate to thread, click reply, type draft in real Chrome
7. Wait for user approval
8. On approval: save draft via Gmail API
"""

from __future__ import annotations

import asyncio
import random
import traceback
from datetime import datetime, timezone

from playwright.async_api import Page

from backend import database as db
from backend.agent.browser_harness import harness
from backend.agent import llm
from backend.agent import screencast as sc
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

from backend.config import BROWSER_POOL_SIZE
_browser_sem = asyncio.Semaphore(BROWSER_POOL_SIZE)
_llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)
_api_sem = asyncio.Semaphore(25)  # Gmail API concurrent requests

_USE_API = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# ---------------------------------------------------------------------------
# Run cancellation tracking
# ---------------------------------------------------------------------------

_cancelled_runs: set[str] = set()


def cancel_run(run_id: str) -> None:
    _cancelled_runs.add(run_id)


def _is_cancelled(run_id: str) -> bool:
    return run_id in _cancelled_runs


# ---------------------------------------------------------------------------
# Pending approval / answer events
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
# Step functions
# ---------------------------------------------------------------------------


async def _step_fetch_thread_api(thread_id: str) -> dict:
    from backend.services.google_api import fetch_thread_content_api
    return await fetch_thread_content_api(thread_id)


async def _step_compose_via_api(draft_text: str, thread_id: str) -> bool:
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
# Visual compose — navigate to Gmail thread and type draft in real Chrome
# ---------------------------------------------------------------------------

GMAIL_THREAD_URL = "https://mail.google.com/mail/u/0/#inbox/{thread_id}"


_account_chooser_warned = False


async def _handle_account_chooser(page: Page) -> None:
    """If the page landed on Google 'Choose an account', auto-click first account."""
    global _account_chooser_warned
    url = page.url
    if "accounts.google.com" not in url or "signin/rejected" in url:
        return

    has_accounts = await page.evaluate("""
        () => document.querySelectorAll('[data-identifier], [data-authuser]').length > 0
    """)

    if not has_accounts:
        if not _account_chooser_warned:
            print("[Engine] Page on Google sign-in (no accounts to choose). Browser needs manual sign-in.")
            _account_chooser_warned = True
        return

    print("[Engine] Page hit account chooser, auto-clicking first account...")
    for selector in ['div[data-identifier]', 'div[data-authuser]', 'li[data-identifier]']:
        try:
            el = await page.wait_for_selector(selector, timeout=2000)
            if el:
                await el.click()
                break
        except Exception:
            continue
    try:
        await page.wait_for_url("**/mail.google.com/**", timeout=15000)
        await page.wait_for_timeout(2000)
    except Exception:
        pass


async def _wait_for_gmail_ready(page: Page, timeout_ms: int = 15000) -> bool:
    """Wait for Gmail's main UI to be interactive. Returns True if ready."""
    try:
        # Wait for Gmail's main navigation OR message list to appear
        await page.wait_for_selector(
            'div[role="navigation"], div[role="main"], div.nH, div.AO',
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def _wait_for_thread_view(page: Page, timeout_ms: int = 10000) -> bool:
    """Wait for a Gmail thread to actually render (message body visible)."""
    try:
        # Thread-view selectors: message body, thread subject, message containers
        await page.wait_for_selector(
            'div.adn, h2.hP, div[role="listitem"], div.gs, table.cf.gJ',
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def _ensure_gmail_thread_loaded(page: Page, thread_id: str, job_id: str) -> bool:
    """Navigate to a Gmail thread and ensure it actually renders.
    Returns True if the thread view is visible."""
    thread_url = GMAIL_THREAD_URL.format(thread_id=thread_id)

    # Check if we're already on this thread
    current_url = page.url
    if f"/{thread_id}" in current_url and "mail.google.com" in current_url:
        # Already on the right thread — just check if content loaded
        if await _wait_for_thread_view(page, timeout_ms=3000):
            return True

    # Navigate to thread
    print(f"[Engine] Job {job_id}: Navigating to Gmail thread {thread_id}")
    try:
        await page.goto(thread_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"[Engine] Job {job_id}: Navigation error (non-fatal): {e}")

    # Handle account chooser if redirected
    await _handle_account_chooser(page)

    # Wait for Gmail to be ready
    gmail_ready = await _wait_for_gmail_ready(page, timeout_ms=10000)
    if not gmail_ready:
        print(f"[Engine] Job {job_id}: Gmail didn't load in time (url: {page.url[:80]})")
        return False

    # If we landed on inbox instead of thread, try hash navigation
    current_url = page.url
    if "mail.google.com" in current_url and f"/{thread_id}" not in current_url:
        print(f"[Engine] Job {job_id}: Landed on inbox, trying hash navigation...")
        try:
            await page.evaluate(f"() => window.location.hash = '#inbox/{thread_id}'")
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    # Wait for thread content to render
    thread_loaded = await _wait_for_thread_view(page, timeout_ms=8000)
    if not thread_loaded:
        print(f"[Engine] Job {job_id}: Thread view didn't render (url: {page.url[:80]})")
        # One more attempt: full page reload
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
            thread_loaded = await _wait_for_thread_view(page, timeout_ms=5000)
        except Exception:
            pass

    if thread_loaded:
        print(f"[Engine] Job {job_id}: Thread loaded successfully")
    else:
        print(f"[Engine] Job {job_id}: Thread may not have loaded (continuing anyway)")

    return thread_loaded


async def _visual_compose_in_browser(page: Page, thread_id: str, draft_text: str, run_id: str, job_id: str) -> None:
    """Navigate to a Gmail thread, click Reply, and type the draft text visibly."""

    # Ensure thread is loaded (may already be loaded from step 1)
    await _emit(run_id, "job.visual_step", {"job_id": job_id, "step": "navigating"})
    thread_ok = await _ensure_gmail_thread_loaded(page, thread_id, job_id)

    if not thread_ok:
        print(f"[Engine] Job {job_id}: Skipping visual compose — thread didn't load")
        await _emit(run_id, "job.visual_step", {"job_id": job_id, "step": "skipped_no_thread"})
        return

    # Click Reply button
    await _emit(run_id, "job.visual_step", {"job_id": job_id, "step": "clicking_reply"})
    reply_clicked = False

    # First try keyboard shortcut 'r' (most reliable in Gmail)
    try:
        # Click on the page body first to ensure focus is on Gmail
        await page.click('body', timeout=2000)
        await page.wait_for_timeout(300)
        await page.keyboard.press("r")
        await page.wait_for_timeout(1500)

        # Check if compose box appeared
        compose_check = page.locator(
            'div[aria-label="Message Body"][contenteditable="true"], '
            'div[role="textbox"][aria-label="Message Body"], '
            'div[contenteditable="true"][g_editable="true"]'
        ).first
        try:
            await compose_check.wait_for(state="visible", timeout=3000)
            reply_clicked = True
            print(f"[Engine] Job {job_id}: Reply opened via keyboard shortcut")
        except Exception:
            pass
    except Exception:
        pass

    # Fallback: click Reply button
    if not reply_clicked:
        for sel in [
            '[data-tooltip="Reply"]',
            'div[role="button"][data-tooltip*="Reply"]',
            'span[role="link"]:has-text("Reply")',
            '.ams.bkH',
            'div[aria-label="Reply"]',
        ]:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2000)
                await btn.click()
                reply_clicked = True
                print(f"[Engine] Job {job_id}: Reply clicked via selector: {sel}")
                break
            except Exception:
                continue

    if not reply_clicked:
        print(f"[Engine] Job {job_id}: Could not find Reply button — trying 'r' shortcut again")
        try:
            await page.keyboard.press("r")
        except Exception:
            pass

    await page.wait_for_timeout(1000)

    # Type draft in compose box
    await _emit(run_id, "job.visual_step", {"job_id": job_id, "step": "typing_draft"})
    compose_box = None
    for sel in [
        'div[aria-label="Message Body"][contenteditable="true"]',
        'div[role="textbox"][aria-label="Message Body"]',
        'div.Am.Al.editable',
        'div[contenteditable="true"][g_editable="true"]',
        'div[contenteditable="true"]',
    ]:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=3000)
            compose_box = el
            print(f"[Engine] Job {job_id}: Found compose box via: {sel}")
            break
        except Exception:
            continue

    if compose_box:
        await compose_box.click()
        await page.wait_for_timeout(300)
        words = draft_text.split(" ")
        for i, word in enumerate(words):
            if _is_cancelled(run_id):
                break
            text = word if i == len(words) - 1 else word + " "
            await page.keyboard.type(text, delay=15)
            if i % 10 == 9:
                await page.wait_for_timeout(60)
        print(f"[Engine] Job {job_id}: Draft typed ({len(words)} words)")
    else:
        print(f"[Engine] Job {job_id}: Could not find compose box — draft NOT typed visually")

    await _emit(run_id, "job.visual_step", {"job_id": job_id, "step": "draft_typed"})
    await page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# Job handler — full pipeline for one email thread
# ---------------------------------------------------------------------------


async def _process_job(job_data: dict) -> None:
    run_id = job_data["run_id"]
    job_id = job_data["job_id"]
    thread_id = job_data["thread_id"]
    subject = job_data.get("subject", "")

    if _is_cancelled(run_id):
        await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
        await db.increment_run_counter(run_id, "skipped_jobs")
        return

    await db.update_job(job_id, status="running", started_at=_now(), attempt=job_data.get("attempt", 1))
    await _emit(run_id, "job.started", {"job_id": job_id, "thread_id": thread_id})
    print(f"[Engine] Job {job_id}: Started (thread={thread_id[:12]}..., subject={subject[:40]})")

    # Semaphore limits concurrent browser pages — only BROWSER_POOL_SIZE jobs at once
    page: Page | None = None
    _sem_held = True
    await _browser_sem.acquire()
    try:
        page = await harness.acquire_page()

        # Start CDP screencast on this page
        await sc.start_screencast(page, job_id, run_id)

        # ── Step 1: Fetch thread via API + navigate browser (parallel) ──
        await db.update_job(job_id, current_step="fetch_thread")
        await _emit(run_id, "step.started", {"job_id": job_id, "step": "fetch_thread", "step_id": ""})

        fetch_start = datetime.now(timezone.utc)

        async def _navigate_to_thread() -> None:
            await _ensure_gmail_thread_loaded(page, thread_id, job_id)

        nav_task = asyncio.create_task(_navigate_to_thread())
        api_task = asyncio.create_task(_step_fetch_thread_api(thread_id))
        await nav_task
        thread_data = await api_task

        fetch_elapsed = int((datetime.now(timezone.utc) - fetch_start).total_seconds() * 1000)
        await _emit(run_id, "step.completed", {"job_id": job_id, "step": "fetch_thread", "step_id": "", "duration_ms": fetch_elapsed})

        if _is_cancelled(run_id):
            return

        subject = thread_data.get("subject", subject) or "(no subject)"
        sender = thread_data.get("sender", "")
        messages = thread_data.get("messages", [])
        await db.update_job(job_id, subject=subject)

        await _emit(run_id, "job.context", {
            "job_id": job_id, "subject": subject, "sender": sender,
            "message_count": thread_data.get("message_count", 0),
        })

        # ── Step 2: Classify intent + check needs info (single LLM call) ──
        classify_result = await _run_step(
            run_id, job_id, "classify_intent",
            llm.classify_and_check, subject, sender, messages,
            semaphore=_llm_sem,
        )
        intent, needs_info, question_text, classify_tokens = classify_result
        await db.update_job(job_id, intent=intent.value, tokens_used=classify_tokens)
        await _emit(run_id, "job.classified", {"job_id": job_id, "intent": intent.value, "tokens": classify_tokens})
        print(f"[Engine] Job {job_id}: Classified as {intent.value} (tokens: {classify_tokens})")

        if _is_cancelled(run_id):
            return

        if intent in (IntentType.ignore, IntentType.escalate):
            await db.update_job(job_id, status="skipped", current_step="done", finished_at=_now())
            await db.increment_run_counter(run_id, "skipped_jobs")
            await _emit(run_id, "job.skipped", {"job_id": job_id, "reason": intent.value})
            return

        # ── Step 2.5: Prompt user if info needed ──
        extra_context = ""

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
        print(f"[Engine] Job {job_id}: Draft generated ({len(draft_text)} chars, {draft_elapsed}ms, conf={confidence})")

        # ── Step 4: Visual compose — type draft in real Chrome browser ──
        await db.update_job(job_id, current_step="visual_compose")
        await _emit(run_id, "step.started", {"job_id": job_id, "step": "visual_compose", "step_id": ""})
        vc_start = datetime.now(timezone.utc)
        try:
            await _visual_compose_in_browser(page, thread_id, draft_text, run_id, job_id)
        except Exception as vc_err:
            print(f"[Engine] Visual compose error (non-fatal): {vc_err}")
        vc_elapsed = int((datetime.now(timezone.utc) - vc_start).total_seconds() * 1000)
        await _emit(run_id, "step.completed", {"job_id": job_id, "step": "visual_compose", "step_id": "", "duration_ms": vc_elapsed})

        # ── Step 5: Wait for user approval ──
        await db.update_job(job_id, status="pending_approval", current_step="pending_approval")
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

        # ── Step 5: Save draft via Gmail API ──
        await _run_step(
            run_id, job_id, "save_draft",
            _step_compose_via_api, final_draft, thread_id,
            semaphore=_api_sem, timeout=15,
        )
        await db.update_job(job_id, draft_id="api-draft")

        # ── Done ──
        await db.update_job(job_id, status="completed", current_step="done", finished_at=_now())
        await db.increment_run_counter(run_id, "completed_jobs")
        await _emit(run_id, "job.completed", {
            "job_id": job_id, "draft_id": "api-draft",
            "confidence": confidence, "summary": summary, "tokens_used": total_tokens,
        })

        # Keep page alive briefly so user can see the completed state in browser
        if page:
            try:
                await page.wait_for_timeout(2000)
            except Exception:
                pass

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        attempt = job_data.get("attempt", 1)
        print(f"[Engine] Job {job_id} ERROR (attempt {attempt}/{MAX_RETRIES}): {err_msg}")
        print(traceback.format_exc()[-800:])

        if attempt < MAX_RETRIES and not _is_cancelled(run_id):
            backoff = min(10, (2 ** attempt) + random.uniform(0, 0.5))
            print(f"[Engine] Job {job_id}: Retrying in {backoff:.1f}s (attempt {attempt + 1})")
            await db.update_job(job_id, status="queued", error_msg=err_msg)
            await _emit(run_id, "job.retrying", {
                "job_id": job_id, "attempt": attempt + 1,
                "backoff_ms": int(backoff * 1000), "error": err_msg,
            })
            await sc.stop_screencast(job_id)
            if page:
                await harness.release_page(page)
                page = None
            _browser_sem.release()
            _sem_held = False
            await asyncio.sleep(backoff)
            job_data["attempt"] = attempt + 1
            await _process_job(job_data)
            return
        else:
            print(f"[Engine] Job {job_id}: FAILED permanently after {attempt} attempts")
            await db.update_job(job_id, status="failed", error_msg=err_msg, finished_at=_now())
            await db.increment_run_counter(run_id, "failed_jobs")
            await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_msg, "traceback": traceback.format_exc()[-500:]})
    finally:
        await sc.stop_screencast(job_id)
        if page:
            await harness.release_page(page)
        if _sem_held:
            _browser_sem.release()


# ---------------------------------------------------------------------------
# Run orchestrator
# ---------------------------------------------------------------------------


async def start_run(run_id: str, thread_ids: list[str] | None = None, thread_subjects: dict[str, str] | None = None) -> None:
    try:
        # Ensure headless browser is running
        if not harness._started:
            await harness.start()

        # Quick auth check
        if not harness.authenticated:
            await harness.ensure_gmail_auth()

        await db.update_run(run_id, status="running")
        await _emit(run_id, "run.started", {"run_id": run_id})
        print(f"[Engine] Run {run_id}: Started (auth={harness.authenticated})")

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
        print(f"[Engine] Run {run_id}: Launching {len(job_items)} jobs (browser pool: {BROWSER_POOL_SIZE}, LLM concurrency: {LLM_CONCURRENCY})")

        # Launch all jobs as concurrent tasks
        tasks = [asyncio.create_task(_process_job(item)) for item in job_items]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Finalize
        await sc.cleanup_run(run_id)
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
        await sc.cleanup_run(run_id)
        _cancelled_runs.discard(run_id)
        await db.update_run(run_id, status="failed", finished_at=_now())
        await _emit(run_id, "run.failed", {"run_id": run_id, "error": err})
