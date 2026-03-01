"""Shared GSuite executor — LLM-planned, API-executed pipeline runner.

All GSuite pipelines (Docs, Sheets, Slides, Forms, Drive, Calendar) use
this module. Each pipeline defines a set of available operations (thin
wrappers around Google API calls), and this executor:

1. Sends the task description + available ops to the LLM for planning
2. If the LLM needs clarification, asks the user via the question flow
3. If the user's answer implies research is needed, spawns a browser
   research worker to gather information first
4. Executes each planned action by calling the corresponding operation
5. Opens a parallel browser page to show live changes via screencast
6. Recovers from errors by re-planning with LLM feedback

Reuses:
  - _retry_on_rate_limit from llm.py
  - _emit, _answer_events, _answer_data from engine.py
  - db.create_question, db.update_job from database.py
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

import anthropic

from backend import database as db
from backend.config import ANTHROPIC_API_KEY

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# An operation is a dict: {"name": str, "description": str, "parameters": str, "fn": Callable}
OpDef = dict[str, Any]

# Operations that insert text and should receive _context for animation
_ANIMATED_OPS = {"write_content", "append_content", "write_range", "clear_and_rewrite"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(run_id: str, event: str, data: dict) -> None:
    from backend.routes.events import publish_event, publish_global
    await publish_event(run_id, event, data)
    await publish_global(event, {**data, "run_id": run_id})


# ---------------------------------------------------------------------------
# LLM Action Planner
# ---------------------------------------------------------------------------

_PLAN_SYSTEM_PROMPT = """\
You are an AI agent that plans actions for Google Workspace tasks.
You have access to a set of API operations for {service_name}.

Available operations:
{operations_list}

Given a task description, produce a JSON plan. You MUST respond with ONLY valid JSON, no markdown fences.

Response format:
{{
  "needs_question": true/false,
  "question": "question to ask user if needs_question is true, else empty string",
  "reasoning": "brief explanation of your plan",
  "actions": [
    {{"op": "operation_name", "params": {{...}} }}
  ],
  "summary": "one-line summary of what will be done"
}}

Rules:
- If the task is ambiguous or you need user preferences (style, tone, specific content, etc.), \
set needs_question=true and ask a specific question. Be AGGRESSIVE about asking — it's better \
to ask than to guess wrong.
- If you have enough information, plan concrete actions using the available operations.
- For content creation tasks, generate the full content directly in the action params.
- CRITICAL: If research results or additional context is provided, you MUST incorporate \
specific facts, names, details, and information from that context into your content. \
Do NOT write generic content when specific information is available. Reference real \
details from the research.
- Use the operations exactly as defined — the "op" field must match an operation name.
- Parameters must match what each operation expects.
- You can chain multiple operations (e.g., create a document then write content to it).
- For operations that return IDs (create_document, create_spreadsheet, etc.), use the \
placeholder "$RESULT_N" where N is the 0-indexed action number to reference a previous result.
- Be thorough — if a task asks for formatting, include formatting actions.
- For EDITING/REWRITING an existing document: first use get_document to read current content, \
then use clear_and_rewrite (if available) to replace the entire document with improved content. \
Do NOT just append — that creates duplicates. Generate the COMPLETE new version of the content.
"""

_PLAN_USER_PROMPT = """\
Task: {task_description}
{extra_context}
Plan the actions needed to complete this task."""

_REFINE_PROMPT = """\
The user answered your question. Now plan the concrete actions.

Original task: {task_description}
Your question: {question}
User's answer: {answer}
{extra_context}

Respond with ONLY valid JSON in the same format as before. \
This time, needs_question should be false and you should provide concrete actions.
Include ALL content in your action params — generate the actual text, data, etc."""

_RECOVERY_PROMPT = """\
An action failed during execution. Here is the full context:

Original task: {task_description}
Service: {service_name}

Completed actions so far (with results):
{completed_summary}

Failed action: {failed_op}({failed_params})
Error: {error_msg}

Remaining planned actions:
{remaining_summary}

Re-plan the remaining work to complete the original task. You may:
- Retry the failed action with different parameters
- Skip it and adjust subsequent actions
- Take an alternative approach

Respond with ONLY valid JSON:
{{
  "actions": [ {{"op": "operation_name", "params": {{...}} }} ],
  "summary": "what this revised plan does"
}}"""


def _format_ops_list(available_ops: list[OpDef]) -> str:
    """Format available operations for the LLM prompt."""
    lines = []
    for op in available_ops:
        lines.append(f"- {op['name']}({op['parameters']}): {op['description']}")
    return "\n".join(lines)


async def _llm_plan_actions(
    service_name: str,
    task_description: str,
    available_ops: list[OpDef],
    extra_context: str = "",
) -> dict:
    """Call the LLM to produce an action plan.

    Returns parsed JSON dict with keys: needs_question, question, actions, summary.
    Raises on parse failure after exhausting retries.
    """
    from backend.agent.llm import _retry_on_rate_limit, _get_async_client

    client = _get_async_client()
    ops_list = _format_ops_list(available_ops)

    system = _PLAN_SYSTEM_PROMPT.format(
        service_name=service_name,
        operations_list=ops_list,
    )

    extra = ""
    if extra_context:
        extra = f"\nAdditional context from user: {extra_context}\n"

    user_msg = _PLAN_USER_PROMPT.format(
        task_description=task_description,
        extra_context=extra,
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = resp.content[0].text.strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines).strip()

    return json.loads(text)


async def _llm_refine_plan(
    service_name: str,
    task_description: str,
    question: str,
    answer: str,
    available_ops: list[OpDef],
    extra_context: str = "",
) -> dict:
    """After the user answers a question, get a refined concrete plan."""
    from backend.agent.llm import _retry_on_rate_limit, _get_async_client

    client = _get_async_client()
    ops_list = _format_ops_list(available_ops)

    system = _PLAN_SYSTEM_PROMPT.format(
        service_name=service_name,
        operations_list=ops_list,
    )

    extra = ""
    if extra_context:
        extra = f"\nAdditional context: {extra_context}\n"

    user_msg = _REFINE_PROMPT.format(
        task_description=task_description,
        question=question,
        answer=answer,
        extra_context=extra,
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = resp.content[0].text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines).strip()

    return json.loads(text)


async def _llm_recover_from_error(
    service_name: str,
    task_description: str,
    completed: list[tuple[str, dict, Any]],
    failed_op: str,
    failed_params: dict,
    error_msg: str,
    remaining: list[dict],
    available_ops: list[OpDef],
) -> dict:
    """Ask the LLM to re-plan after an action failure.

    Returns parsed JSON with keys: actions, summary.
    """
    from backend.agent.llm import _retry_on_rate_limit, _get_async_client

    client = _get_async_client()
    ops_list = _format_ops_list(available_ops)

    system = _PLAN_SYSTEM_PROMPT.format(
        service_name=service_name,
        operations_list=ops_list,
    )

    completed_lines = []
    for op_name, params, result in completed:
        result_str = json.dumps(result)[:200] if result else "OK"
        completed_lines.append(f"  {op_name}({json.dumps(params)[:100]}) -> {result_str}")
    completed_summary = "\n".join(completed_lines) if completed_lines else "  (none)"

    remaining_lines = []
    for action in remaining:
        remaining_lines.append(f"  {action.get('op', '?')}({json.dumps(action.get('params', {}))[:100]})")
    remaining_summary = "\n".join(remaining_lines) if remaining_lines else "  (none)"

    user_msg = _RECOVERY_PROMPT.format(
        task_description=task_description,
        service_name=service_name,
        completed_summary=completed_summary,
        failed_op=failed_op,
        failed_params=json.dumps(failed_params)[:200],
        error_msg=error_msg[:300],
        remaining_summary=remaining_summary,
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = resp.content[0].text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines).strip()

    return json.loads(text)


# ---------------------------------------------------------------------------
# Answer classification & research delegation
# ---------------------------------------------------------------------------

_CLASSIFY_ANSWER_PROMPT = """\
A user was asked a clarifying question by an AI assistant. Classify the user's answer.

Question the AI asked: {question}
User's answer: {answer}

Does the user's answer tell the assistant to search/look up/research information externally?
Examples of "research needed" answers:
- "search it up", "google it", "look it up", "find out yourself"
- "just research them", "go check online", "search for that info"

Respond with ONLY valid JSON:
{{"needs_research": true/false, "search_query": "specific query to search if research needed, else empty string"}}"""


async def _classify_answer(question: str, answer: str) -> dict:
    """Classify whether a user's answer implies the agent should do research.

    Returns {"needs_research": bool, "search_query": str}.
    """
    from backend.agent.llm import _retry_on_rate_limit, _get_async_client

    client = _get_async_client()
    prompt = _CLASSIFY_ANSWER_PROMPT.format(question=question, answer=answer)

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"needs_research": False, "search_query": ""}


async def _do_research(
    run_id: str,
    parent_job_id: str,
    task_description: str,
    search_query: str,
) -> str:
    """Real web research: search the web, fetch pages, stream results.

    1. DuckDuckGo search for real results
    2. Fetch top page content
    3. Stream the raw findings to the hex cell via draft_token

    Returns the raw search data (snippets + page text) directly — no
    extra LLM summarization hop, so specific facts are preserved intact
    for the planner to use.
    """
    from backend.agent.web_research import research_topic

    await _emit(run_id, "job.agent_action", {
        "job_id": parent_job_id,
        "action": "web_search",
        "status": "running",
        "step": f"Searching the web: {search_query[:50]}",
    })
    await db.update_job(parent_job_id, current_step="web_search")

    print(f"[GSuiteExecutor] Web search: {search_query[:80]}")

    try:
        # Real web search + page fetching
        research = await research_topic(
            query=search_query,
            task_context=task_description,
            max_search_results=5,
            max_pages_to_fetch=3,
        )

        n_results = len(research["search_results"])
        n_pages = len(research["page_contents"])
        print(f"[GSuiteExecutor] Found {n_results} results, fetched {n_pages} pages")

        # Stream search results to hex cell so user sees live progress
        for r in research["search_results"]:
            await _emit(run_id, "job.draft_token", {
                "job_id": parent_job_id,
                "token": f"🔍 {r['title']}\n   {r['snippet'][:120]}\n\n",
            })
            await asyncio.sleep(0.15)

        if research["page_contents"]:
            await _emit(run_id, "job.draft_token", {
                "job_id": parent_job_id,
                "token": f"\n📄 Read {n_pages} pages for details...\n",
            })

        await _emit(run_id, "job.agent_action", {
            "job_id": parent_job_id,
            "action": "research_completed",
            "status": "completed",
            "step": f"Found {n_results} results, read {n_pages} pages",
        })

        # Return the raw combined text — no LLM summarization hop.
        # This preserves all specific facts for the planner to use directly.
        return research["combined_text"]

    except Exception as exc:
        err = f"Research failed: {exc}"
        print(f"[GSuiteExecutor] {err}")
        await _emit(run_id, "job.agent_action", {
            "job_id": parent_job_id,
            "action": "research_failed",
            "status": "error",
            "error": str(exc)[:200],
        })
        return f"Could not research '{search_query}'. Proceed with best judgment."


# ---------------------------------------------------------------------------
# Live view — parallel browser page for screencast
# ---------------------------------------------------------------------------


async def _open_live_view(run_id: str, job_id: str, url: str):
    """Open a browser page to the given URL and start screencasting.

    Returns (page, True) on success, (None, False) on failure.
    Uses the local Playwright harness which has Google auth cookies.
    """
    from backend.agent.browser_harness import harness
    from backend.agent import screencast as sc
    from backend.agent.engine import _handle_account_chooser

    try:
        if not harness._started:
            await harness.start()
        if not harness.authenticated:
            await harness.ensure_gmail_auth()

        page = await harness.acquire_page()
        await sc.start_screencast(page, job_id, run_id)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as nav_err:
            print(f"[GSuiteExecutor] Live view navigation warning: {nav_err}")

        await _handle_account_chooser(page)
        await page.wait_for_timeout(2000)
        print(f"[GSuiteExecutor] Live view opened for job {job_id}: {url[:80]}")
        return page
    except Exception as exc:
        print(f"[GSuiteExecutor] Live view failed (non-fatal): {exc}")
        return None


async def _navigate_live_view(page, url: str) -> None:
    """Navigate an existing live-view page to a new URL."""
    from backend.agent.engine import _handle_account_chooser
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await _handle_account_chooser(page)
        await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"[GSuiteExecutor] Live view navigate warning: {exc}")


async def _close_live_view(job_id: str, page) -> None:
    """Stop screencast and release the browser page."""
    from backend.agent.browser_harness import harness
    from backend.agent import screencast as sc
    try:
        await sc.stop_screencast(job_id)
    except Exception:
        pass
    if page:
        try:
            await harness.release_page(page)
        except Exception:
            pass


def _extract_url_from_result(result: Any) -> str | None:
    """Extract a URL from an action result if one exists."""
    if not isinstance(result, dict):
        return None
    # Check common URL fields
    for key in ("url", "webViewLink", "htmlLink", "responderUrl"):
        val = result.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    return None


# ---------------------------------------------------------------------------
# Action Executor (with error recovery + live view + context injection)
# ---------------------------------------------------------------------------

_MAX_RECOVERIES = 3


async def _execute_actions(
    run_id: str,
    job_id: str,
    actions: list[dict],
    op_map: dict[str, Callable[..., Awaitable[Any]]],
    pipeline_type: str,
    service_name: str = "",
    task_description: str = "",
    available_ops: list[OpDef] | None = None,
    live_page: Any = None,
) -> list[Any]:
    """Execute a sequence of planned actions, resolving $RESULT_N references.

    Features:
    - Resolves $RESULT_N placeholders from previous results
    - Injects _context dict into animated operations (write_content, etc.)
    - Navigates live_page to URLs returned by actions
    - On failure, asks LLM to re-plan (up to _MAX_RECOVERIES total)

    Returns list of results from each action.
    """
    results: list[Any] = []
    completed_log: list[tuple[str, dict, Any]] = []  # (op_name, params, result)
    recovery_count = 0

    i = 0
    while i < len(actions):
        action = actions[i]
        op_name = action.get("op", "")
        params = action.get("params", {})

        # Resolve $RESULT_N placeholders in params
        resolved_params = _resolve_placeholders(params, results)

        # Inject _context for animated operations
        if op_name in _ANIMATED_OPS:
            resolved_params["_context"] = {"run_id": run_id, "job_id": job_id}

        fn = op_map.get(op_name)
        if not fn:
            err = f"Unknown operation: {op_name}"
            print(f"[GSuiteExecutor] {err}")
            await _emit(run_id, "job.agent_action", {
                "job_id": job_id, "action": op_name, "status": "error", "error": err,
            })
            results.append({"error": err})
            completed_log.append((op_name, resolved_params, {"error": err}))
            i += 1
            continue

        await _emit(run_id, "job.agent_action", {
            "job_id": job_id, "action": op_name, "status": "running",
            "step": f"{i + 1}/{len(actions)}",
        })
        await db.update_job(job_id, current_step=f"executing_{op_name}")

        try:
            result = await fn(**resolved_params)
            results.append(result)
            completed_log.append((op_name, resolved_params, result))
            print(f"[GSuiteExecutor] Job {job_id}: {op_name} -> OK")
            await _emit(run_id, "job.agent_action", {
                "job_id": job_id, "action": op_name, "status": "completed",
                "step": f"{i + 1}/{len(actions)}",
            })

            # Navigate live view to any URL in the result
            if live_page:
                url = _extract_url_from_result(result)
                if url:
                    await _navigate_live_view(live_page, url)

            i += 1

        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[GSuiteExecutor] Job {job_id}: {op_name} FAILED: {err_msg}")
            await _emit(run_id, "job.agent_action", {
                "job_id": job_id, "action": op_name, "status": "error", "error": err_msg,
            })

            # Attempt LLM recovery if we have enough context
            if recovery_count < _MAX_RECOVERIES and available_ops and service_name:
                recovery_count += 1
                print(f"[GSuiteExecutor] Job {job_id}: Attempting recovery ({recovery_count}/{_MAX_RECOVERIES})...")
                await _emit(run_id, "job.agent_action", {
                    "job_id": job_id, "action": "recovery", "status": "running",
                    "step": f"recovery {recovery_count}/{_MAX_RECOVERIES}",
                })

                remaining = actions[i + 1:]
                try:
                    recovery_plan = await _llm_recover_from_error(
                        service_name, task_description,
                        completed_log, op_name, params, err_msg,
                        remaining, available_ops,
                    )
                    new_actions = recovery_plan.get("actions", [])
                    if new_actions:
                        print(f"[GSuiteExecutor] Job {job_id}: Recovery produced {len(new_actions)} new actions")
                        # Replace remaining actions with the recovery plan
                        actions = actions[:i] + new_actions
                        # Don't increment i — we'll execute the first recovery action
                        continue
                except Exception as recovery_exc:
                    print(f"[GSuiteExecutor] Job {job_id}: Recovery failed: {recovery_exc}")

            # Recovery failed or not available — log error and move on
            results.append({"error": err_msg})
            completed_log.append((op_name, resolved_params, {"error": err_msg}))
            i += 1

    return results


def _resolve_placeholders(params: dict, results: list[Any]) -> dict:
    """Replace $RESULT_N placeholders with actual values from previous results.

    Supports two formats:
    - $RESULT_N          — context-aware extraction based on parameter name
    - $RESULT_N.field    — explicit field extraction (e.g. $RESULT_1.endIndex)

    Resolution is context-aware based on the parameter name:
    - If the param name suggests a position (contains "index"), extract endIndex
    - Otherwise, extract the most likely ID field
    """
    _INDEX_FIELDS = ("endIndex", "startIndex", "index")
    _ID_FIELDS = ("id", "spreadsheetId", "presentationId", "documentId", "formId", "fileId")

    resolved = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("$RESULT_"):
            try:
                # Parse: "$RESULT_N" or "$RESULT_N.field"
                after_prefix = value[len("$RESULT_"):]  # e.g. "1" or "1.endIndex"
                explicit_field = None
                if "." in after_prefix:
                    idx_str, explicit_field = after_prefix.split(".", 1)
                else:
                    idx_str = after_prefix
                idx = int(idx_str)
                prev = results[idx]

                if explicit_field and isinstance(prev, dict):
                    # Explicit field requested: $RESULT_N.fieldName
                    resolved[key] = prev.get(explicit_field, prev)
                elif isinstance(prev, dict):
                    # Context-aware extraction based on parameter name
                    if "index" in key.lower():
                        candidates = _INDEX_FIELDS
                    else:
                        candidates = _ID_FIELDS
                    for field in candidates:
                        if field in prev:
                            resolved[key] = prev[field]
                            break
                    else:
                        # Fallback: try the other set
                        fallback = _ID_FIELDS if candidates is _INDEX_FIELDS else _INDEX_FIELDS
                        for field in fallback:
                            if field in prev:
                                resolved[key] = prev[field]
                                break
                        else:
                            resolved[key] = prev
                else:
                    resolved[key] = prev
            except (IndexError, ValueError):
                resolved[key] = value
        elif isinstance(value, dict):
            resolved[key] = _resolve_placeholders(value, results)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_placeholders(item, results) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            resolved[key] = value
    return resolved


# ---------------------------------------------------------------------------
# Result verification & cross-pipeline delegation
# ---------------------------------------------------------------------------

_VERIFY_PROMPT = """\
An AI agent executed some actions. Check whether the ORIGINAL task is FULLY complete.
Be strict — if the task says "all" or implies multiple items, verify ALL were handled.

Original task: {task_description}
Pipeline used: {pipeline_type}
Plan summary: {summary}
Action results (abbreviated): {results_summary}

Available operations in this pipeline: {available_ops_names}

Respond with ONLY valid JSON:
{{
  "fully_complete": true/false,
  "needs_followup": true/false,
  "followup_pipeline": "pipeline type if followup needs a DIFFERENT pipeline (docs/sheets/slides/drive/research/generic), else same as current",
  "followup_instruction": "SPECIFIC instruction for remaining work, referencing concrete items still pending",
  "reasoning": "brief explanation of what was done vs what remains"
}}

Rules:
- If the task says "all", "every", "each" — check that ALL matching items were handled, \
not just one. For example, "delete all poems" means EVERY poem found, not just one.
- If a search returned N items but only M < N were acted on, set needs_followup=true \
and explain which items remain.
- If the task is clearly done (create one doc and it was created), set fully_complete=true.
- For followup_pipeline, only suggest a different pipeline if the current one truly can't \
do the remaining work. Otherwise keep the same pipeline type."""


async def _verify_completion(
    task_description: str,
    summary: str,
    results: list,
    pipeline_type: str,
    available_ops: list[OpDef],
) -> dict:
    """Quick LLM check: did the execution actually fulfill the task?

    Returns {"needs_followup": bool, "followup_pipeline": str, "followup_instruction": str}.
    """
    from backend.agent.llm import _retry_on_rate_limit, _get_async_client

    # Abbreviate results for the prompt — include all of them so the LLM
    # can tell how many items were actually handled vs how many remain.
    results_abbrev = []
    for r in results[:30]:
        if isinstance(r, dict):
            brief = {k: (str(v)[:80] if isinstance(v, str) else v) for k, v in r.items()}
            results_abbrev.append(brief)
        else:
            results_abbrev.append(str(r)[:100])

    ops_names = [op["name"] for op in available_ops]

    prompt = _VERIFY_PROMPT.format(
        task_description=task_description,
        pipeline_type=pipeline_type,
        summary=summary,
        results_summary=json.dumps(results_abbrev, default=str)[:2000],
        available_ops_names=", ".join(ops_names),
    )

    try:
        client = _get_async_client()
        resp = await _retry_on_rate_limit(
            client.messages.create,
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )

        text = resp.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(lines).strip()

        result = json.loads(text)
        if result.get("needs_followup"):
            print(f"[GSuiteExecutor] Verification: needs followup — {result.get('reasoning', '')[:80]}")
        return result

    except Exception as exc:
        print(f"[GSuiteExecutor] Verification check failed (non-fatal): {exc}")
        return {"needs_followup": False}


async def _try_cross_pipeline_delegation(
    run_id: str,
    job_id: str,
    task_description: str,
    current_pipeline: str,
    errors: list[dict],
    available_ops: list[OpDef],
) -> bool:
    """When all actions fail, check if a different pipeline could handle the task.

    Returns True if delegation succeeded, False otherwise.
    """
    # Check if errors suggest capability mismatch (not just API errors)
    error_texts = " ".join(e.get("error", "") for e in errors).lower()
    capability_mismatch = any(phrase in error_texts for phrase in [
        "unknown operation", "not available", "not supported",
    ])

    if not capability_mismatch:
        return False

    # Try routing to the right pipeline
    from backend.agent.router import route_task
    routing = await route_task(task_description)
    new_pipeline = routing.get("pipeline_type", "generic")

    if new_pipeline == current_pipeline or new_pipeline == "generic":
        return False

    print(f"[GSuiteExecutor] Cross-pipeline delegation: {current_pipeline} -> {new_pipeline}")
    await _spawn_followup(run_id, job_id, new_pipeline, task_description)
    return True


async def _spawn_followup(
    run_id: str,
    parent_job_id: str,
    pipeline_type: str,
    instruction: str,
) -> None:
    """Spawn a child job in a different pipeline to handle a followup task."""
    from backend.agent.pipelines import get_pipeline

    child_job = await db.create_job(run_id, "", f"[Followup] {instruction[:70]}")
    child_job_id = child_job["id"]

    await db.update_job(
        child_job_id,
        pipeline_type=pipeline_type,
        task_instruction=instruction,
        subject=f"[Followup] {instruction[:70]}",
    )

    await _emit(run_id, "job.agent_action", {
        "job_id": parent_job_id,
        "action": "followup_spawned",
        "status": "completed",
        "step": f"Spawned {pipeline_type} worker: {instruction[:50]}",
    })

    print(f"[GSuiteExecutor] Followup job {child_job_id} ({pipeline_type}): {instruction[:60]}")

    try:
        pipeline = get_pipeline(pipeline_type)
        await pipeline.execute(run_id, child_job_id, {
            "instruction": instruction,
            "description": instruction,
        })
    except Exception as exc:
        print(f"[GSuiteExecutor] Followup failed: {exc}")
        await db.update_job(
            child_job_id, status="failed", current_step="done",
            error_msg=str(exc)[:200], finished_at=_now(),
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def plan_and_execute(
    run_id: str,
    job_id: str,
    pipeline_type: str,
    service_name: str,
    task_description: str,
    available_ops: list[OpDef],
    op_map: dict[str, Callable[..., Awaitable[Any]]],
    extra_context: str = "",
    enable_live_view: bool = True,
) -> None:
    """Plan and execute a GSuite task using LLM planning + API execution.

    This is the main entry point for all GSuite pipelines. It:
    1. Marks the job as running
    2. Asks the LLM to plan actions
    3. If LLM needs clarification, asks the user
    4. Opens a parallel browser page for live screencast
    5. Executes the planned actions (with error recovery)
    6. Updates job status throughout

    Args:
        run_id: Parent run ID.
        job_id: Job ID.
        pipeline_type: e.g. "docs", "sheets", "slides".
        service_name: Human-readable name, e.g. "Google Docs".
        task_description: The user's task description.
        available_ops: List of OpDef dicts describing available operations.
        op_map: Map of operation name -> async callable.
        extra_context: Optional additional context from user.
        enable_live_view: Whether to open a browser page for screencast.
    """
    await db.update_job(job_id, status="running", started_at=_now(), current_step="planning")
    await _emit(run_id, "job.started", {"job_id": job_id, "pipeline_type": pipeline_type})
    await _emit(run_id, "job.agent_started", {"job_id": job_id, "pipeline_type": pipeline_type})

    live_page = None

    try:
        # Step 1: LLM planning
        print(f"[GSuiteExecutor] Job {job_id} ({pipeline_type}): Planning actions...")
        plan = await _llm_plan_actions(service_name, task_description, available_ops, extra_context)

        # Step 2: Handle question flow if needed
        if plan.get("needs_question") and plan.get("question"):
            question_text = plan["question"]
            print(f"[GSuiteExecutor] Job {job_id}: Asking user: {question_text[:80]}")

            q = await db.create_question(job_id, run_id, question_text, context=pipeline_type)
            await db.update_job(job_id, current_step="waiting_for_input")
            await _emit(run_id, "job.question", {
                "job_id": job_id, "question_id": q["id"],
                "question": question_text, "subject": task_description[:100],
            })

            # Wait for user answer
            from backend.agent.engine import _answer_events, _answer_data
            evt = asyncio.Event()
            _answer_events[job_id] = evt
            try:
                await asyncio.wait_for(evt.wait(), timeout=600)
                answer = _answer_data.get(job_id, "")
            except asyncio.TimeoutError:
                answer = ""
            finally:
                _answer_events.pop(job_id, None)
                _answer_data.pop(job_id, None)

            if answer:
                # Classify the answer — does the user want us to research?
                print(f"[GSuiteExecutor] Job {job_id}: Got answer, classifying...")
                classification = await _classify_answer(question_text, answer)

                if classification.get("needs_research"):
                    search_query = classification.get("search_query", task_description)
                    print(f"[GSuiteExecutor] Job {job_id}: Answer requires research — spawning worker")
                    await db.update_job(job_id, current_step="researching")

                    research_results = await _do_research(
                        run_id, job_id, task_description, search_query,
                    )

                    # Feed raw research directly to the planner — no summarization
                    # hop so all specific facts (names, roles, publications, etc.)
                    # are preserved and available for content generation.
                    enriched_context = (
                        f"{extra_context}\n\n"
                        f"=== WEB RESEARCH RESULTS (use these specific facts!) ===\n"
                        f"{research_results}\n"
                        f"=== END RESEARCH ===\n\n"
                        f"IMPORTANT: You MUST incorporate specific facts, names, roles, "
                        f"achievements, and details from the research above into the "
                        f"content you generate. Do NOT write generic content. Reference "
                        f"real information found in the search results. "
                        f"Do not ask more questions — proceed with what you have."
                    )
                    plan = await _llm_plan_actions(
                        service_name, task_description, available_ops,
                        extra_context=enriched_context,
                    )
                else:
                    print(f"[GSuiteExecutor] Job {job_id}: Refining plan with user's answer...")
                    plan = await _llm_refine_plan(
                        service_name, task_description,
                        question_text, answer,
                        available_ops, extra_context,
                    )
            else:
                print(f"[GSuiteExecutor] Job {job_id}: No answer received, proceeding with original plan")
                plan = await _llm_plan_actions(
                    service_name, task_description, available_ops,
                    extra_context=extra_context + "\n(User did not provide additional context; proceed with best judgment.)",
                )

        actions = plan.get("actions", [])
        summary = plan.get("summary", f"Completed {pipeline_type} task")

        if not actions:
            await db.update_job(
                job_id, status="completed", current_step="done",
                summary="No actions needed", finished_at=_now(),
            )
            await db.increment_run_counter(run_id, "completed_jobs")
            await _emit(run_id, "job.completed", {"job_id": job_id, "summary": "No actions needed"})
            return

        # Step 3: Open live browser view (best-effort, non-blocking)
        if enable_live_view:
            # Determine an initial URL from the pipeline type
            default_urls = {
                "docs": "https://docs.google.com",
                "sheets": "https://sheets.google.com",
                "slides": "https://slides.google.com",
                "forms": "https://forms.google.com",
                "drive": "https://drive.google.com",
                "calendar": "https://calendar.google.com",
            }
            initial_url = default_urls.get(pipeline_type, "https://drive.google.com")
            live_page = await _open_live_view(run_id, job_id, initial_url)

        # Step 4–6: Execute → Verify → Re-plan loop
        # Keeps executing until the ORIGINAL task is fully satisfied,
        # up to MAX_VERIFY_LOOPS iterations.
        MAX_VERIFY_LOOPS = 4
        all_results: list[Any] = []
        completed_summary_parts: list[str] = []

        for loop_i in range(MAX_VERIFY_LOOPS):
            iteration_label = f"[iteration {loop_i + 1}/{MAX_VERIFY_LOOPS}]"

            # Execute current batch of actions
            await db.update_job(job_id, current_step=f"executing {iteration_label}")
            results = await _execute_actions(
                run_id, job_id, actions, op_map, pipeline_type,
                service_name=service_name,
                task_description=task_description,
                available_ops=available_ops,
                live_page=live_page,
            )
            all_results.extend(results)

            errors = [r for r in results if isinstance(r, dict) and "error" in r]
            successes = [r for r in results if not (isinstance(r, dict) and "error" in r)]

            if successes:
                completed_summary_parts.append(
                    f"Iteration {loop_i + 1}: {len(successes)} actions succeeded"
                )

            # If ALL actions in this batch failed, don't loop — abort
            if errors and len(errors) == len(results):
                err_summary = "; ".join(e["error"] for e in errors)
                await db.update_job(
                    job_id, status="failed", current_step="done",
                    error_msg=err_summary[:500], finished_at=_now(),
                )
                await db.increment_run_counter(run_id, "failed_jobs")
                await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_summary[:200]})
                return

            # Verify: is the ORIGINAL task fully complete?
            if loop_i < MAX_VERIFY_LOOPS - 1:  # Skip verify on last iteration
                verification = await _verify_completion(
                    task_description, summary, all_results, pipeline_type, available_ops,
                )

                if verification.get("fully_complete") or not verification.get("needs_followup"):
                    print(f"[GSuiteExecutor] Job {job_id}: Task verified complete {iteration_label}")
                    break

                # Task is NOT fully complete — re-plan with context of what's done
                followup_instruction = verification.get("followup_instruction", "")
                followup_pipeline = verification.get("followup_pipeline", pipeline_type)
                reasoning = verification.get("reasoning", "")

                print(f"[GSuiteExecutor] Job {job_id}: Task incomplete {iteration_label}: {reasoning[:80]}")
                await _emit(run_id, "job.agent_action", {
                    "job_id": job_id, "action": "re_verifying",
                    "status": "running",
                    "step": f"Task incomplete — re-planning ({reasoning[:50]})",
                })

                # If the followup needs a DIFFERENT pipeline, spawn it and stop
                if followup_pipeline != pipeline_type:
                    print(f"[GSuiteExecutor] Job {job_id}: Cross-pipeline followup -> {followup_pipeline}")
                    await _spawn_followup(run_id, job_id, followup_pipeline, followup_instruction)
                    break

                # Same pipeline — re-plan with accumulated context
                done_context = "\n".join(completed_summary_parts)
                results_brief = json.dumps(
                    [r for r in all_results if not (isinstance(r, dict) and "error" in r)],
                    default=str,
                )[:2000]

                replan_context = (
                    f"{extra_context}\n\n"
                    f"=== PROGRESS SO FAR ===\n"
                    f"{done_context}\n"
                    f"Previous results: {results_brief}\n"
                    f"=== REMAINING WORK ===\n"
                    f"{followup_instruction}\n"
                    f"Complete the remaining work to fully satisfy the original task."
                )

                print(f"[GSuiteExecutor] Job {job_id}: Re-planning with new context...")
                plan = await _llm_plan_actions(
                    service_name, task_description, available_ops,
                    extra_context=replan_context,
                )
                actions = plan.get("actions", [])
                summary = plan.get("summary", summary)

                if not actions:
                    print(f"[GSuiteExecutor] Job {job_id}: Re-plan produced no actions, done.")
                    break
            # else: last iteration, fall through to completion

        # Keep live view open so user can see the final state
        if live_page:
            try:
                await live_page.wait_for_timeout(8000)
            except Exception:
                pass

        await db.update_job(
            job_id, status="completed", current_step="done",
            summary=summary[:200], finished_at=_now(),
        )
        await db.increment_run_counter(run_id, "completed_jobs")
        await _emit(run_id, "job.completed", {"job_id": job_id, "summary": summary})

        print(f"[GSuiteExecutor] Job {job_id}: Done — {summary}")

    except json.JSONDecodeError as exc:
        err_msg = f"LLM returned invalid JSON: {exc}"
        print(f"[GSuiteExecutor] Job {job_id} FAILED: {err_msg}")
        await db.update_job(
            job_id, status="failed", current_step="done",
            error_msg=err_msg, finished_at=_now(),
        )
        await db.increment_run_counter(run_id, "failed_jobs")
        await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_msg})

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        print(f"[GSuiteExecutor] Job {job_id} FAILED: {err_msg}")
        print(traceback.format_exc()[-500:])
        await db.update_job(
            job_id, status="failed", current_step="done",
            error_msg=err_msg, finished_at=_now(),
        )
        await db.increment_run_counter(run_id, "failed_jobs")
        await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_msg})

    finally:
        # Always clean up the live view
        if live_page:
            await _close_live_view(job_id, live_page)
