"""Orchestrator pipeline — decomposes compound tasks into multi-pipeline sub-tasks.

Handles tasks that span multiple services (e.g., "search hotels online and
write a doc about them") by breaking them into ordered sub-tasks, each
dispatched to the appropriate pipeline. Results from earlier sub-tasks
are passed as context to subsequent ones.

Each sub-task creates a visible child job in the swarm so the user
sees all workers in the hex grid.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend import database as db
from backend.config import ANTHROPIC_API_KEY


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(run_id: str, event: str, data: dict) -> None:
    from backend.routes.events import publish_event, publish_global
    await publish_event(run_id, event, data)
    await publish_global(event, {**data, "run_id": run_id})


# ---------------------------------------------------------------------------
# LLM decomposition prompt
# ---------------------------------------------------------------------------

_DECOMPOSE_SYSTEM = """\
You are an AI task decomposer for Hive, an agent orchestration platform.
Given a complex task, break it into ordered sub-tasks that each run on a specific pipeline.

Available pipelines:
{pipeline_list}

Rules:
- Break the task into the minimum number of sub-tasks needed.
- Each sub-task must specify which pipeline handles it.
- Sub-tasks execute sequentially. Later sub-tasks can use results from earlier ones.
- Set pass_results=true if a sub-task's output should be forwarded as context to the next sub-task.
- For web research tasks, use "generic" pipeline.
- For GSuite creation/editing, use the specific pipeline (docs, sheets, slides, etc.).
- For Gmail, use "gmail".
- Keep instructions specific and actionable for each sub-task.

Respond with ONLY valid JSON:
{{
  "sub_tasks": [
    {{
      "pipeline_type": "generic",
      "instruction": "Search the web for top-rated hotels in San Francisco and compile a list with names, ratings, and prices",
      "pass_results": true
    }},
    {{
      "pipeline_type": "docs",
      "instruction": "Create a Google Doc titled 'SF Hotel Guide' with the hotel information organized by rating",
      "pass_results": false
    }}
  ],
  "summary": "Research SF hotels and create a guide document"
}}"""

_DECOMPOSE_USER = """\
Task: {task_description}
{extra_context}
Break this into ordered sub-tasks."""


async def _decompose_task(task_description: str, extra_context: str = "") -> dict:
    """Use LLM to decompose a compound task into sub-tasks."""
    from backend.agent.llm import _retry_on_rate_limit, _get_async_client
    from backend.agent.pipelines import list_pipeline_types

    client = _get_async_client()

    pipeline_list = "\n".join(
        f"- {p['type']}: {p['description']}"
        for p in list_pipeline_types()
        if p["type"] != "orchestrator"  # Don't let orchestrator recurse
    )

    system = _DECOMPOSE_SYSTEM.format(pipeline_list=pipeline_list)
    extra = f"\nAdditional context: {extra_context}" if extra_context else ""
    user_msg = _DECOMPOSE_USER.format(
        task_description=task_description,
        extra_context=extra,
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
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
# Pipeline class
# ---------------------------------------------------------------------------


class OrchestratorPipeline(Pipeline):
    pipeline_type = "orchestrator"
    display_name = "Orchestrator"
    description = (
        "Handles compound tasks that span multiple services. For example: "
        "'research hotels online and write a doc about them' or "
        "'check my calendar and draft emails to reschedule conflicts'. "
        "Breaks complex tasks into ordered sub-tasks across different pipelines."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        # The orchestrator is never auto-detected by heuristics —
        # only the LLM router sends tasks here.
        return False

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        """Decompose a compound task and execute sub-tasks sequentially.

        1. LLM decomposes the task into ordered sub-tasks
        2. For each sub-task, create a child job with the appropriate pipeline
        3. Dispatch each child job through the pipeline registry
        4. Pass results from earlier sub-tasks as context to later ones
        5. Parent job tracks overall progress
        """
        task_description = params.get("instruction", params.get("description", ""))
        extra_context = params.get("extra_context", "")

        await db.update_job(job_id, status="running", started_at=_now(), current_step="decomposing")
        await _emit(run_id, "job.started", {"job_id": job_id, "pipeline_type": "orchestrator"})
        await _emit(run_id, "job.agent_started", {"job_id": job_id, "pipeline_type": "orchestrator"})

        try:
            # Step 1: Decompose the task
            print(f"[Orchestrator] Job {job_id}: Decomposing task...")
            plan = await _decompose_task(task_description, extra_context)

            sub_tasks = plan.get("sub_tasks", [])
            summary = plan.get("summary", f"Orchestrated: {task_description[:80]}")

            if not sub_tasks:
                await db.update_job(
                    job_id, status="completed", current_step="done",
                    summary="No sub-tasks identified", finished_at=_now(),
                )
                await db.increment_run_counter(run_id, "completed_jobs")
                await _emit(run_id, "job.completed", {"job_id": job_id, "summary": "No sub-tasks needed"})
                return

            print(f"[Orchestrator] Job {job_id}: Decomposed into {len(sub_tasks)} sub-tasks")
            await db.update_job(job_id, current_step=f"0/{len(sub_tasks)} sub-tasks")
            await _emit(run_id, "job.agent_action", {
                "job_id": job_id, "action": "decompose", "status": "completed",
                "step": f"Planned {len(sub_tasks)} sub-tasks",
            })

            # Step 2: Execute sub-tasks sequentially
            from backend.agent.pipelines import get_pipeline
            from backend.agent.engine import _process_pipeline_job, _is_cancelled

            accumulated_context = ""
            child_results: list[dict] = []

            for idx, sub_task in enumerate(sub_tasks):
                if _is_cancelled(run_id):
                    break

                st_pipeline = sub_task.get("pipeline_type", "generic")
                st_instruction = sub_task.get("instruction", "")
                st_pass_results = sub_task.get("pass_results", False)

                # Append accumulated context from previous sub-tasks
                full_instruction = st_instruction
                if accumulated_context:
                    full_instruction += f"\n\nContext from previous steps:\n{accumulated_context}"

                # Create a child job
                child_job = await db.create_job(run_id, "", f"[{idx + 1}/{len(sub_tasks)}] {st_instruction[:80]}")
                child_job_id = child_job["id"]

                await db.update_job(
                    child_job_id,
                    pipeline_type=st_pipeline,
                    task_instruction=full_instruction,
                    subject=f"[{idx + 1}/{len(sub_tasks)}] {st_instruction[:80]}",
                )

                # Update parent job progress
                await db.update_job(job_id, current_step=f"{idx + 1}/{len(sub_tasks)} sub-tasks")
                await _emit(run_id, "job.agent_action", {
                    "job_id": job_id, "action": f"sub_task_{idx + 1}",
                    "status": "running",
                    "step": f"Running sub-task {idx + 1}/{len(sub_tasks)}: {st_pipeline}",
                })

                print(f"[Orchestrator] Job {job_id}: Running sub-task {idx + 1}/{len(sub_tasks)} ({st_pipeline})")

                # Dispatch child job through the pipeline
                try:
                    pipeline = get_pipeline(st_pipeline)
                    child_params = {
                        "instruction": full_instruction,
                        "description": full_instruction,
                        "url": params.get("url", ""),
                    }
                    await pipeline.execute(run_id, child_job_id, child_params)

                    # Retrieve child job result
                    child_data = await db.get_job(child_job_id)
                    child_status = child_data.get("status", "unknown") if child_data else "unknown"
                    child_summary = child_data.get("summary", "") if child_data else ""

                    child_results.append({
                        "sub_task": idx + 1,
                        "pipeline": st_pipeline,
                        "status": child_status,
                        "summary": child_summary,
                    })

                    # Accumulate results as context for next sub-task
                    if st_pass_results and child_summary:
                        accumulated_context += f"\nSub-task {idx + 1} ({st_pipeline}): {child_summary}\n"

                    await _emit(run_id, "job.agent_action", {
                        "job_id": job_id, "action": f"sub_task_{idx + 1}",
                        "status": "completed" if child_status == "completed" else "error",
                        "step": f"Sub-task {idx + 1} {child_status}",
                    })

                except Exception as exc:
                    err_msg = f"{type(exc).__name__}: {exc}"
                    print(f"[Orchestrator] Sub-task {idx + 1} failed: {err_msg}")

                    child_results.append({
                        "sub_task": idx + 1,
                        "pipeline": st_pipeline,
                        "status": "failed",
                        "error": err_msg,
                    })

                    await _emit(run_id, "job.agent_action", {
                        "job_id": job_id, "action": f"sub_task_{idx + 1}",
                        "status": "error", "error": err_msg,
                    })

                    # Don't abort — try remaining sub-tasks if they're independent
                    if accumulated_context:
                        accumulated_context += f"\nSub-task {idx + 1} ({st_pipeline}): FAILED - {err_msg}\n"

            # Step 3: Finalize parent job
            failed_count = sum(1 for r in child_results if r.get("status") == "failed")
            total_count = len(child_results)

            if failed_count == total_count:
                await db.update_job(
                    job_id, status="failed", current_step="done",
                    error_msg=f"All {total_count} sub-tasks failed",
                    finished_at=_now(),
                )
                await db.increment_run_counter(run_id, "failed_jobs")
                await _emit(run_id, "job.failed", {
                    "job_id": job_id, "error": f"All {total_count} sub-tasks failed",
                })
            else:
                await db.update_job(
                    job_id, status="completed", current_step="done",
                    summary=f"{summary} ({total_count - failed_count}/{total_count} succeeded)",
                    finished_at=_now(),
                )
                await db.increment_run_counter(run_id, "completed_jobs")
                await _emit(run_id, "job.completed", {
                    "job_id": job_id,
                    "summary": f"{summary} ({total_count - failed_count}/{total_count} succeeded)",
                })

            print(f"[Orchestrator] Job {job_id}: Done — {total_count - failed_count}/{total_count} sub-tasks succeeded")

        except json.JSONDecodeError as exc:
            err_msg = f"LLM returned invalid JSON during decomposition: {exc}"
            print(f"[Orchestrator] Job {job_id} FAILED: {err_msg}")
            await db.update_job(
                job_id, status="failed", current_step="done",
                error_msg=err_msg, finished_at=_now(),
            )
            await db.increment_run_counter(run_id, "failed_jobs")
            await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_msg})

        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[Orchestrator] Job {job_id} FAILED: {err_msg}")
            print(traceback.format_exc()[-500:])
            await db.update_job(
                job_id, status="failed", current_step="done",
                error_msg=err_msg, finished_at=_now(),
            )
            await db.increment_run_counter(run_id, "failed_jobs")
            await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_msg})
