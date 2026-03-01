"""Browser agent backed by browser-use + Browserbase.

Drop-in alternative to browser_agent.py (Stagehand).
Uses the browser-use library with Browserbase for cloud browser sessions.
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any

from backend.config import (
    BROWSERBASE_API_KEY,
    BROWSERBASE_PROJECT_ID,
    BROWSER_USE_API_KEY,
    BROWSER_USE_MODEL,
    SWARM_AGENT_TIMEOUT,
    SWARM_MAX_STEPS,
    ANTHROPIC_API_KEY,
)
from backend.agent.browser_agent import AgentTask, AgentResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrowserUseBrowserAgent:
    """Browser agent using browser-use + Browserbase.

    Same lifecycle as BrowserAgent (Stagehand):
        1. start() — creates Browserbase session + browser-use connection
        2. run_task() — executes the task autonomously
        3. stop() — cleans up
    """

    def __init__(self, agent_id: str, swarm_id: str, task: AgentTask) -> None:
        self.agent_id = agent_id
        self.swarm_id = swarm_id
        self.task = task

        # Runtime state
        self._bb_session: Any = None
        self._browser_session: Any = None
        self._playwright: Any = None
        self._using_saved_context: bool = False
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
        """Create a Browserbase session and connect browser-use to it."""
        from browserbase import Browserbase
        from backend import database as db_mod

        # Check for saved auth context
        context_id = await db_mod.kv_get("browserbase_context_id")

        def _create_session(ctx_id: str | None):
            bb = Browserbase(api_key=BROWSERBASE_API_KEY)

            browser_settings: dict[str, Any] = {
                "solveCaptchas": True,
                "fingerprint": {
                    "browsers": ["chrome"],
                    "devices": ["desktop"],
                    "operatingSystems": ["macos"],
                },
            }
            if ctx_id:
                browser_settings["context"] = {"id": ctx_id, "persist": True}

            session = bb.sessions.create(
                project_id=BROWSERBASE_PROJECT_ID,
                browser_settings=browser_settings,
                proxies=[{"type": "browserbase", "geolocation": {"country": "US"}}],
            )

            # Get live view URL
            debug_info = bb.sessions.debug(session.id)

            return {
                "session": session,
                "live_view_url": debug_info.debugger_fullscreen_url,
            }

        self._using_saved_context = bool(context_id)
        if context_id:
            print(f"[Agent {self.agent_id}] [browser-use] Using Browserbase context: {context_id}")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _create_session, context_id)

        self._bb_session = result["session"]
        self.session_id = self._bb_session.id
        self.live_view_url = result["live_view_url"]
        self.status = "running"
        self.started_at = _now()

        print(f"[Agent {self.agent_id}] [browser-use] Session started: {self.session_id}")

    async def stop(self) -> None:
        """Clean up browser-use session and Browserbase."""
        try:
            if self._browser_session and self._browser_session.initialized:
                await self._browser_session.stop()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser_session = None
        self._playwright = None
        self._bb_session = None

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def run_task(
        self,
        on_action: Any | None = None,
    ) -> AgentResult:
        """Execute the task using browser-use agent on Browserbase."""
        from browser_use import Agent
        from browser_use.browser.session import BrowserSession
        from browser_use.browser import BrowserProfile

        start_time = datetime.now(timezone.utc)

        try:
            if not self._bb_session:
                raise RuntimeError("Agent not started — call start() first")

            # Connect browser-use to the Browserbase session via CDP
            self.current_action = "Connecting to cloud browser"
            if on_action:
                await on_action(self.agent_id, self.current_action)

            browser_profile = BrowserProfile(
                keep_alive=False,
                wait_between_actions=1.5,
                default_timeout=30000,
                default_navigation_timeout=30000,
            )

            self._browser_session = BrowserSession(
                cdp_url=self._bb_session.connect_url,
                browser_profile=browser_profile,
            )
            await self._browser_session.start()

            # When we have a saved login context, open Gmail inbox first so the browser
            # is already logged in when the agent runs — no sign-in page, no credentials needed.
            if self._using_saved_context:
                self.current_action = "Opening Gmail inbox (saved session)"
                if on_action:
                    await on_action(self.agent_id, self.current_action)
                await self._browser_session.navigate_to("https://mail.google.com/")

            # Navigate if URL provided (and we didn't just open inbox)
            elif self.task.url:
                self.current_action = f"Navigating to {self.task.url}"
                if on_action:
                    await on_action(self.agent_id, self.current_action)

            # Build the LLM
            llm = self._build_llm()

            # Build the task instruction
            instruction = self.task.instruction
            if self.task.url and not self._using_saved_context:
                instruction = f"Go to {self.task.url} and {instruction}"
            elif self._using_saved_context:
                # Browser is already on Gmail inbox; task is just the user's goal.
                instruction = instruction.strip()

            # Create and run the browser-use agent
            self.current_action = "Executing task autonomously"
            if on_action:
                await on_action(self.agent_id, self.current_action)

            agent = Agent(
                task=instruction,
                llm=llm,
                browser_session=self._browser_session,
                enable_memory=False,
                max_failures=3,
                retry_delay=3,
                max_actions_per_step=1,
            )

            history = await agent.run(max_steps=self.task.max_steps)

            # Extract result
            final_result = history.final_result() if history else ""
            self.actions_taken = len(history.action_names()) if history else 0
            is_done = history.is_done() if history else False

            elapsed = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

            print(f"[Agent {self.agent_id}] [browser-use] Completed: done={is_done}, actions={self.actions_taken}, result={final_result[:200] if final_result else 'None'}")

            self.result = AgentResult(
                success=is_done,
                message=final_result or "",
                actions_taken=self.actions_taken,
                error=None if is_done else (final_result or "Task not completed"),
                duration_ms=elapsed,
            )
            self.status = "completed" if is_done else "failed"
            self.error_msg = None if is_done else (final_result or "Task not completed")
            self.finished_at = _now()
            self.current_action = "Done"

            # Clean up agent
            del agent

            return self.result

        except Exception as exc:
            elapsed = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[Agent {self.agent_id}] [browser-use] FAILED: {err_msg}")

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
    # LLM setup
    # ------------------------------------------------------------------

    def _build_llm(self) -> Any:
        """Build the LLM for browser-use based on config."""
        from browser_use.llm.anthropic.chat import ChatAnthropic
        model = BROWSER_USE_MODEL or "claude-opus-4-6"
        print(f"[Agent {self.agent_id}] [browser-use] Using Anthropic model: {model}")
        return ChatAnthropic(
            model=model,
            api_key=ANTHROPIC_API_KEY,
            temperature=0,
        )

    # ------------------------------------------------------------------
    # Serialization (same interface as BrowserAgent)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
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
