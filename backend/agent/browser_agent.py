"""Single autonomous browser agent backed by Stagehand + Browserbase.

Each BrowserAgent gets its own cloud browser session on Browserbase,
controlled via Stagehand's AI-powered automation. Agents can:

- execute() — autonomous multi-step task completion
- act() — single targeted action (click, type, etc.)
- extract() — structured data extraction from page
- observe() — find interactive elements matching a description
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from stagehand import AsyncStagehand

from backend.config import (
    BROWSERBASE_API_KEY,
    BROWSERBASE_PROJECT_ID,
    STAGEHAND_MODEL,
    SWARM_AGENT_TIMEOUT,
    SWARM_MAX_STEPS,
    ANTHROPIC_API_KEY,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentTask:
    """Describes what a browser agent should do."""
    instruction: str
    url: str = ""  # optional — agent navigates on its own if blank
    max_steps: int = SWARM_MAX_STEPS
    timeout: float = SWARM_AGENT_TIMEOUT
    extract_schema: dict | None = None  # optional JSON schema for extraction


@dataclass
class AgentResult:
    """Result returned by a browser agent after completing (or failing) a task."""
    success: bool = False
    message: str = ""
    actions_taken: int = 0
    extracted_data: dict | None = None
    error: str | None = None
    duration_ms: int = 0


class BrowserAgent:
    """A single autonomous browser agent.

    Lifecycle:
        1. start() — creates Stagehand session (which creates Browserbase session)
        2. run_task() — navigates + executes the task autonomously
        3. stop() — cleans up the session

    The agent exposes live_view_url after start() for real-time monitoring.
    """

    def __init__(self, agent_id: str, swarm_id: str, task: AgentTask) -> None:
        self.agent_id = agent_id
        self.swarm_id = swarm_id
        self.task = task

        # Runtime state
        self._client: AsyncStagehand | None = None
        self._session: Any = None
        self.session_id: str | None = None
        self.live_view_url: str | None = None
        self.status: str = "queued"
        self.actions_taken: int = 0
        self.current_action: str = ""
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error_msg: str | None = None
        self.result: AgentResult | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create a Stagehand client + session (spawns a Browserbase browser)."""
        from backend import database as db_mod

        self._client = AsyncStagehand(
            browserbase_api_key=BROWSERBASE_API_KEY,
            browserbase_project_id=BROWSERBASE_PROJECT_ID,
            model_api_key=ANTHROPIC_API_KEY,
        )

        # Check if we have a saved auth context (for Google login, etc.)
        context_id = await db_mod.kv_get("browserbase_context_id")

        # Shared browser settings: stealth fingerprint + captcha solving + proxy
        # Per https://docs.browserbase.com/guides/authentication
        browser_settings: dict[str, Any] = {
            "solveCaptchas": True,
            "fingerprint": {
                "browsers": ["chrome"],
                "devices": ["desktop"],
                "operatingSystems": ["macos"],
            },
        }
        bb_params: dict[str, Any] = {
            "browserSettings": browser_settings,
            "proxies": [{
                "type": "browserbase",
                "geolocation": {"country": "US"},
            }],
        }

        if context_id:
            print(f"[Agent {self.agent_id}] Using Browserbase context: {context_id}")
            browser_settings["context"] = {
                "id": context_id,
                "persist": True,
            }

        start_kwargs: dict[str, Any] = {
            "model_name": STAGEHAND_MODEL,
            "browserbase_session_create_params": bb_params,
        }

        self._session = await self._client.sessions.start(**start_kwargs)
        self.session_id = self._session.id
        self.status = "running"
        self.started_at = _now()

        # Get live view URL from Browserbase (sync call, run in thread)
        await self._fetch_live_view_url()

    async def _fetch_live_view_url(self) -> None:
        """Retrieve the Browserbase live view URL for this session."""
        if not self.session_id:
            return
        try:
            from browserbase import Browserbase

            def _get_debug_url() -> str:
                bb = Browserbase(api_key=BROWSERBASE_API_KEY)
                debug_info = bb.sessions.debug(self.session_id)
                return debug_info.debugger_fullscreen_url

            # Run sync Browserbase SDK call in a thread to avoid blocking
            self.live_view_url = await asyncio.get_event_loop().run_in_executor(
                None, _get_debug_url
            )
        except Exception:
            # Fallback: construct URL manually
            self.live_view_url = f"https://www.browserbase.com/sessions/{self.session_id}"

    async def stop(self) -> None:
        """End the Stagehand session and clean up."""
        if self._session:
            try:
                await self._session.end()
            except Exception:
                pass
        self._session = None
        self._client = None

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def run_task(
        self,
        on_action: Any | None = None,
    ) -> AgentResult:
        """Execute the assigned task autonomously.

        Args:
            on_action: Optional async callback(agent_id, action_description) for SSE.

        Returns:
            AgentResult with success status, message, and extracted data.
        """
        start_time = datetime.now(timezone.utc)

        try:
            if not self._session:
                raise RuntimeError("Agent not started — call start() first")

            # Step 1: Navigate to the target URL (if provided)
            if self.task.url:
                self.current_action = f"Navigating to {self.task.url}"
                if on_action:
                    await on_action(self.agent_id, self.current_action)
                await self._session.navigate(url=self.task.url)

            # Step 2: Execute the autonomous agent
            self.current_action = "Executing task autonomously"
            if on_action:
                await on_action(self.agent_id, self.current_action)

            execute_response = await self._session.execute(
                execute_options={
                    "instruction": self.task.instruction,
                    "max_steps": self.task.max_steps,
                },
                agent_config={
                    "model": {
                        "model_name": STAGEHAND_MODEL,
                        "api_key": ANTHROPIC_API_KEY,
                    },
                },
                timeout=self.task.timeout,
            )

            result_data = execute_response.data.result
            success = getattr(result_data, "success", False)
            message = getattr(result_data, "message", "")
            completed = getattr(result_data, "completed", None)
            actions = getattr(result_data, "actions", [])
            self.actions_taken = len(actions) if actions else 0

            # Log the full result for debugging
            print(f"[Agent {self.agent_id}] execute() returned: success={success}, completed={completed}, message={message}, actions={self.actions_taken}")
            if not success:
                print(f"[Agent {self.agent_id}] FAILED — message: {message}")
                # Log last few actions for context
                if actions:
                    for a in actions[-3:]:
                        reasoning = getattr(a, "reasoning", None) or (a.get("reasoning") if isinstance(a, dict) else None)
                        action_type = getattr(a, "type", None) or (a.get("type") if isinstance(a, dict) else None)
                        print(f"  action: {action_type}, reasoning: {reasoning}")

            # Step 3: Optional data extraction
            extracted_data = None
            if self.task.extract_schema and success:
                self.current_action = "Extracting structured data"
                if on_action:
                    await on_action(self.agent_id, self.current_action)

                extract_response = await self._session.extract(
                    instruction=self.task.instruction,
                    schema=self.task.extract_schema,
                )
                extracted_data = extract_response.data.result
                if hasattr(extracted_data, "model_dump"):
                    extracted_data = extracted_data.model_dump()
                elif hasattr(extracted_data, "__dict__"):
                    extracted_data = extracted_data.__dict__

            elapsed = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

            self.result = AgentResult(
                success=success,
                message=message,
                actions_taken=self.actions_taken,
                extracted_data=extracted_data,
                error=message if not success else None,
                duration_ms=elapsed,
            )
            self.status = "completed" if success else "failed"
            self.error_msg = message if not success else None
            self.finished_at = _now()
            self.current_action = "Done"

            return self.result

        except Exception as exc:
            elapsed = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            err_msg = f"{type(exc).__name__}: {exc}"
            self.error_msg = err_msg
            self.status = "failed"
            self.finished_at = _now()
            self.current_action = "Failed"

            self.result = AgentResult(
                success=False,
                message="",
                actions_taken=self.actions_taken,
                error=err_msg,
                duration_ms=elapsed,
            )
            return self.result

    # ------------------------------------------------------------------
    # Lower-level primitives (for custom pipelines)
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> None:
        """Navigate to a URL."""
        if not self._session:
            raise RuntimeError("Agent not started")
        await self._session.navigate(url=url)

    async def act(self, instruction: str) -> dict:
        """Perform a single action on the page."""
        if not self._session:
            raise RuntimeError("Agent not started")
        response = await self._session.act(input=instruction)
        self.actions_taken += 1
        result = response.data.result
        return {
            "success": getattr(result, "success", True),
            "message": getattr(result, "message", ""),
        }

    async def extract(self, instruction: str, schema: dict | None = None) -> dict:
        """Extract data from the current page."""
        if not self._session:
            raise RuntimeError("Agent not started")
        kwargs: dict[str, Any] = {"instruction": instruction}
        if schema:
            kwargs["schema"] = schema
        response = await self._session.extract(**kwargs)
        result = response.data.result
        if hasattr(result, "model_dump"):
            return result.model_dump()
        elif hasattr(result, "__dict__"):
            return result.__dict__
        return {"extraction": str(result)}

    async def observe(self, instruction: str) -> list[dict]:
        """Find interactive elements matching a description."""
        if not self._session:
            raise RuntimeError("Agent not started")
        response = await self._session.observe(instruction=instruction)
        results = response.data.result
        if isinstance(results, list):
            return [
                r.to_dict(exclude_none=True) if hasattr(r, "to_dict") else r
                for r in results
            ]
        return []

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize agent state for API responses."""
        return {
            "id": self.agent_id,
            "swarm_id": self.swarm_id,
            "task_url": self.task.url or "",
            "task_instruction": self.task.instruction,
            "status": self.status,
            "session_id": self.session_id,
            "live_view_url": self.live_view_url,
            "current_action": self.current_action,
            "actions_taken": self.actions_taken,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_msg": self.error_msg,
            "result": {
                "success": self.result.success,
                "message": self.result.message,
                "actions_taken": self.result.actions_taken,
                "extracted_data": self.result.extracted_data,
                "error": self.result.error,
                "duration_ms": self.result.duration_ms,
            } if self.result else None,
        }
