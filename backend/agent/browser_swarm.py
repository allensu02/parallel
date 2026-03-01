"""Browser swarm orchestrator — launches and manages parallel browser agents.

This is the browser equivalent of engine.py. It manages N autonomous
browser agents running concurrently on Browserbase, each controlled
via Stagehand.

Flow:
1. User submits a list of tasks (url + instruction pairs)
2. SwarmManager creates a BrowserAgent per task
3. All agents launch in parallel (bounded by semaphore)
4. Each agent gets its own cloud browser on Browserbase
5. Frontend can embed live view iframes for each agent
6. Agents report progress via SSE events
7. Results collected when all agents complete
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone

from backend import database as db
from backend.agent.browser_agent import BrowserAgent, AgentTask, AgentResult
from backend.config import SWARM_MAX_AGENTS
from backend.routes.events import publish_event, publish_global


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(swarm_id: str, event: str, data: dict) -> None:
    await publish_event(swarm_id, event, data)
    await publish_global(event, {**data, "swarm_id": swarm_id})


# ---------------------------------------------------------------------------
# Swarm cancellation tracking
# ---------------------------------------------------------------------------

_cancelled_swarms: set[str] = set()


def cancel_swarm(swarm_id: str) -> None:
    _cancelled_swarms.add(swarm_id)


def _is_cancelled(swarm_id: str) -> bool:
    return swarm_id in _cancelled_swarms


# ---------------------------------------------------------------------------
# Active agent tracking (for live queries)
# ---------------------------------------------------------------------------

_active_agents: dict[str, dict[str, BrowserAgent]] = {}  # swarm_id -> {agent_id -> agent}


def get_active_agents(swarm_id: str) -> list[BrowserAgent]:
    return list(_active_agents.get(swarm_id, {}).values())


def get_active_agent(swarm_id: str, agent_id: str) -> BrowserAgent | None:
    return _active_agents.get(swarm_id, {}).get(agent_id)


# ---------------------------------------------------------------------------
# Single agent runner
# ---------------------------------------------------------------------------

async def _run_single_agent(
    swarm_id: str,
    agent: BrowserAgent,
    semaphore: asyncio.Semaphore,
) -> AgentResult:
    """Run one agent with lifecycle management and SSE events."""

    if _is_cancelled(swarm_id):
        await db.update_swarm_agent(agent.agent_id, status="skipped", finished_at=_now())
        await db.increment_swarm_counter(swarm_id, "failed_agents")
        return AgentResult(success=False, error="Swarm cancelled")

    async with semaphore:
        try:
            # Start the agent (creates Browserbase session)
            await agent.start()

            await db.update_swarm_agent(
                agent.agent_id,
                status="running",
                session_id=agent.session_id or "",
                live_view_url=agent.live_view_url or "",
                started_at=_now(),
            )
            await _emit(swarm_id, "agent.started", {
                "agent_id": agent.agent_id,
                "session_id": agent.session_id,
                "live_view_url": agent.live_view_url,
                "task_url": agent.task.url,
                "task_instruction": agent.task.instruction,
            })

            if _is_cancelled(swarm_id):
                await agent.stop()
                await db.update_swarm_agent(agent.agent_id, status="skipped", finished_at=_now())
                await db.increment_swarm_counter(swarm_id, "failed_agents")
                return AgentResult(success=False, error="Swarm cancelled")

            # Run the task with action callback for SSE
            async def on_action(agent_id: str, action_desc: str) -> None:
                agent.current_action = action_desc
                await db.update_swarm_agent(agent_id, current_action=action_desc)
                await _emit(swarm_id, "agent.action", {
                    "agent_id": agent_id,
                    "action": action_desc,
                })

            result = await agent.run_task(on_action=on_action)

            # Update DB with result
            import json
            result_json = json.dumps({
                "success": result.success,
                "message": result.message,
                "actions_taken": result.actions_taken,
                "extracted_data": result.extracted_data,
                "error": result.error,
                "duration_ms": result.duration_ms,
            })

            if result.success:
                await db.update_swarm_agent(
                    agent.agent_id,
                    status="completed",
                    actions_taken=result.actions_taken,
                    result=result_json,
                    finished_at=_now(),
                )
                await db.increment_swarm_counter(swarm_id, "completed_agents")
                await _emit(swarm_id, "agent.completed", {
                    "agent_id": agent.agent_id,
                    "result": result_json,
                    "actions_taken": result.actions_taken,
                    "duration_ms": result.duration_ms,
                })
            else:
                await db.update_swarm_agent(
                    agent.agent_id,
                    status="failed",
                    error_msg=result.error or "Task failed",
                    actions_taken=result.actions_taken,
                    result=result_json,
                    finished_at=_now(),
                )
                await db.increment_swarm_counter(swarm_id, "failed_agents")
                await _emit(swarm_id, "agent.failed", {
                    "agent_id": agent.agent_id,
                    "error": result.error,
                    "actions_taken": result.actions_taken,
                })

            return result

        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[Swarm] Agent {agent.agent_id} FAILED: {err_msg}")
            print(traceback.format_exc())

            await db.update_swarm_agent(
                agent.agent_id,
                status="failed",
                error_msg=err_msg,
                finished_at=_now(),
            )
            await db.increment_swarm_counter(swarm_id, "failed_agents")
            await _emit(swarm_id, "agent.failed", {
                "agent_id": agent.agent_id,
                "error": err_msg,
                "traceback": traceback.format_exc()[-500:],
            })

            return AgentResult(success=False, error=err_msg)

        finally:
            await agent.stop()


# ---------------------------------------------------------------------------
# Swarm orchestrator
# ---------------------------------------------------------------------------

async def start_swarm(swarm_id: str, tasks: list[dict]) -> None:
    """Launch a swarm of browser agents.

    Args:
        swarm_id: ID of the swarm run (already created in DB).
        tasks: List of dicts with keys: instruction, url? max_steps?, timeout?, extract_schema?
    """
    try:
        await db.update_swarm(swarm_id, status="running")
        await _emit(swarm_id, "swarm.started", {"swarm_id": swarm_id, "total_agents": len(tasks)})

        if not tasks:
            await db.update_swarm(swarm_id, status="completed", finished_at=_now())
            await _emit(swarm_id, "swarm.completed", {
                "swarm_id": swarm_id, "total": 0, "completed": 0, "failed": 0,
            })
            return

        # Cap the number of agents
        effective_tasks = tasks[:SWARM_MAX_AGENTS]
        await db.update_swarm(swarm_id, total_agents=len(effective_tasks))

        # Create agents and DB records
        agents: list[BrowserAgent] = []
        for task_dict in effective_tasks:
            task = AgentTask(
                instruction=task_dict["instruction"],
                url=task_dict.get("url", ""),
                max_steps=task_dict.get("max_steps", 20),
                timeout=task_dict.get("timeout", 300),
                extract_schema=task_dict.get("extract_schema"),
            )

            agent_record = await db.create_swarm_agent(
                swarm_id=swarm_id,
                task_url=task.url or "",
                task_instruction=task.instruction,
            )
            agent = BrowserAgent(
                agent_id=agent_record["id"],
                swarm_id=swarm_id,
                task=task,
            )
            agents.append(agent)

        # Track active agents for live queries
        _active_agents[swarm_id] = {a.agent_id: a for a in agents}

        await _emit(swarm_id, "swarm.agents_created", {
            "swarm_id": swarm_id,
            "count": len(agents),
            "agent_ids": [a.agent_id for a in agents],
        })

        # Launch all agents concurrently with semaphore
        sem = asyncio.Semaphore(min(len(agents), SWARM_MAX_AGENTS))
        results = await asyncio.gather(
            *[_run_single_agent(swarm_id, agent, sem) for agent in agents],
            return_exceptions=True,
        )

        # Handle any unhandled exceptions from gather
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                err = f"{type(r).__name__}: {r}"
                print(f"[Swarm] Unhandled exception in agent {agents[i].agent_id}: {err}")

        # Finalize
        _cancelled_swarms.discard(swarm_id)
        _active_agents.pop(swarm_id, None)

        swarm = await db.get_swarm(swarm_id)
        final_status = "completed" if swarm_id not in _cancelled_swarms else "cancelled"
        await db.update_swarm(swarm_id, status=final_status, finished_at=_now())
        await _emit(swarm_id, "swarm.completed", {
            "swarm_id": swarm_id,
            "total": swarm["total_agents"] if swarm else 0,
            "completed": swarm["completed_agents"] if swarm else 0,
            "failed": swarm["failed_agents"] if swarm else 0,
        })

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"[Swarm] Swarm {swarm_id} FAILED: {err}")
        print(traceback.format_exc())
        _cancelled_swarms.discard(swarm_id)
        _active_agents.pop(swarm_id, None)
        await db.update_swarm(swarm_id, status="failed", finished_at=_now())
        await _emit(swarm_id, "swarm.failed", {"swarm_id": swarm_id, "error": err})
