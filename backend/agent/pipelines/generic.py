"""Generic pipeline -- Stagehand + Browserbase fallback for unknown tasks.

Used when no specialized pipeline matches the task. Launches an autonomous
browser agent on Browserbase that uses AI vision to interact with any webpage.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend import database as db
from backend.routes.events import publish_event, publish_global
from backend.config import SWARM_MAX_AGENTS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(run_id: str, event: str, data: dict) -> None:
    await publish_event(run_id, event, data)
    await publish_global(event, {**data, "run_id": run_id})


class GenericPipeline(Pipeline):
    pipeline_type = "generic"
    display_name = "Generic Browser"
    description = (
        "Autonomous browser agent for any web task. Uses Stagehand + Browserbase "
        "to navigate pages, click buttons, fill forms, and extract data. "
        "Fallback for tasks that don't match a specialized pipeline."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        # Generic pipeline is the fallback -- always returns False for
        # can_handle so specialized pipelines get priority.
        return False

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        """Run a generic browser task via Stagehand on Browserbase.

        params:
          - instruction: str -- what the agent should do
          - url: str -- optional starting URL
          - max_steps: int -- max autonomous steps (default 20)
          - timeout: float -- task timeout in seconds (default 300)
        """
        from backend.agent.browser_agent import BrowserAgent, AgentTask

        instruction = params.get("instruction", params.get("description", ""))
        url = params.get("url", "")
        max_steps = params.get("max_steps", 20)
        timeout = params.get("timeout", 300)

        if not instruction:
            raise ValueError("Generic pipeline requires an 'instruction' parameter")

        task = AgentTask(
            instruction=instruction,
            url=url,
            max_steps=max_steps,
            timeout=timeout,
        )

        agent = BrowserAgent(
            agent_id=job_id,
            swarm_id=run_id,
            task=task,
        )

        await db.update_job(job_id, status="running", current_step="browser_agent", started_at=_now())
        await _emit(run_id, "job.started", {"job_id": job_id, "pipeline_type": "generic"})

        try:
            await agent.start()

            # Update job with live view URL so frontend can display it
            await db.update_job(
                job_id,
                live_view_url=agent.live_view_url or "",
            )
            await _emit(run_id, "job.agent_started", {
                "job_id": job_id,
                "session_id": agent.session_id,
                "live_view_url": agent.live_view_url,
            })

            async def on_action(agent_id: str, action_desc: str) -> None:
                await db.update_job(job_id, current_step=action_desc[:100])
                await _emit(run_id, "job.agent_action", {
                    "job_id": job_id,
                    "action": action_desc,
                })

            async def on_login_needed(agent_id: str, live_view_url: str) -> None:
                """Pause the job and ask the user to log in via the live browser view."""
                from backend.agent.engine import _answer_events, _answer_data

                q = await db.create_question(
                    job_id, run_id,
                    "I hit a login page and need you to log in. "
                    "Open the live browser view, complete the login, then click Done.",
                    context="login_required",
                )
                await db.update_job(job_id, current_step="waiting_for_login")
                await _emit(run_id, "job.question", {
                    "job_id": job_id,
                    "question_id": q["id"],
                    "question": q["question"],
                    "subject": "Login Required",
                    "live_view_url": live_view_url or "",
                    "type": "login_required",
                })

                evt = asyncio.Event()
                _answer_events[job_id] = evt
                try:
                    await asyncio.wait_for(evt.wait(), timeout=600)
                except asyncio.TimeoutError:
                    print(f"[GenericPipeline] Job {job_id}: Login handoff timed out after 600s")
                finally:
                    _answer_events.pop(job_id, None)
                    _answer_data.pop(job_id, None)

            result = await agent.run_task(on_action=on_action, on_login_needed=on_login_needed)

            # Serialize artifacts for storage and SSE
            artifacts_list = [a.to_dict() for a in result.artifacts] if result.artifacts else []
            artifacts_json = json.dumps(artifacts_list)

            result_json = json.dumps({
                "success": result.success,
                "message": result.message,
                "actions_taken": result.actions_taken,
                "error": result.error,
                "duration_ms": result.duration_ms,
            })

            if result.success:
                await db.update_job(
                    job_id,
                    status="completed",
                    current_step="done",
                    summary=result.message[:200] if result.message else "Task completed",
                    artifacts=artifacts_json,
                    finished_at=_now(),
                )
                await db.increment_run_counter(run_id, "completed_jobs")
                await _emit(run_id, "job.completed", {
                    "job_id": job_id,
                    "result": result_json,
                    "actions_taken": result.actions_taken,
                })

                # Emit artifacts as a separate event for the frontend
                if artifacts_list:
                    await _emit(run_id, "job.artifacts", {
                        "job_id": job_id,
                        "artifacts": artifacts_list,
                        "task_instruction": instruction[:200],
                    })
            else:
                await db.update_job(
                    job_id,
                    status="failed",
                    current_step="done",
                    error_msg=result.error or "Task failed",
                    finished_at=_now(),
                )
                await db.increment_run_counter(run_id, "failed_jobs")
                await _emit(run_id, "job.failed", {
                    "job_id": job_id,
                    "error": result.error,
                })

        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[GenericPipeline] Job {job_id} FAILED: {err_msg}")
            print(traceback.format_exc()[-500:])

            await db.update_job(
                job_id,
                status="failed",
                current_step="done",
                error_msg=err_msg,
                finished_at=_now(),
            )
            await db.increment_run_counter(run_id, "failed_jobs")
            await _emit(run_id, "job.failed", {
                "job_id": job_id,
                "error": err_msg,
            })
        finally:
            await agent.stop()
