"""Single autonomous browser agent backed by Stagehand + Browserbase.

Each BrowserAgent gets its own cloud browser session on Browserbase,
controlled via Stagehand's AI-powered automation. Agents can:

- execute() -- autonomous multi-step task completion
- act() -- single targeted action (click, type, etc.)
- extract() -- structured data extraction from page
- observe() -- find interactive elements matching a description
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import re

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


# Keywords in error messages that suggest a login wall caused the failure
_LOGIN_ERROR_HINTS = re.compile(
    r"(login|sign.?in|auth|password|credential|forbidden|unauthorized|access denied|session expired)",
    re.IGNORECASE,
)


@dataclass
class AgentTask:
    """Describes what a browser agent should do."""
    instruction: str
    url: str = ""
    max_steps: int = SWARM_MAX_STEPS
    timeout: float = SWARM_AGENT_TIMEOUT
    extract_schema: dict | None = None


@dataclass
class Artifact:
    """A single piece of information the agent found and wants to surface."""
    type: str  # "link", "text", "screenshot"
    label: str
    content: str = ""  # text content or URL

    def to_dict(self) -> dict:
        return {"type": self.type, "label": self.label, "content": self.content}


@dataclass
class AgentResult:
    """Result returned by a browser agent after completing (or failing) a task."""
    success: bool = False
    message: str = ""
    actions_taken: int = 0
    extracted_data: dict | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class BrowserAgent:
    """A single autonomous browser agent.

    Lifecycle:
        1. start() -- creates Stagehand session (which creates Browserbase session)
        2. run_task() -- navigates + executes the task autonomously
        3. stop() -- cleans up the session

    The agent exposes live_view_url after start() for real-time monitoring.
    """

    def __init__(self, agent_id: str, swarm_id: str, task: AgentTask) -> None:
        self.agent_id = agent_id
        self.swarm_id = swarm_id
        self.task = task

        # Runtime state
        self._client: Any = None
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

    async def start(self) -> None:
        """Create a Stagehand client + session (spawns a Browserbase browser)."""
        from stagehand import AsyncStagehand
        from backend import database as db_mod

        self._client = AsyncStagehand(
            browserbase_api_key=BROWSERBASE_API_KEY,
            browserbase_project_id=BROWSERBASE_PROJECT_ID,
            model_api_key=ANTHROPIC_API_KEY,
        )

        context_id = await db_mod.kv_get("browserbase_context_id")

        browser_settings: dict[str, Any] = {
            "solveCaptchas": True,
            "viewport": {
                "width": 1280,
                "height": 720,
            },
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

            self.live_view_url = await asyncio.get_event_loop().run_in_executor(
                None, _get_debug_url
            )
        except Exception:
            self.live_view_url = f"https://www.browserbase.com/sessions/{self.session_id}"

    async def stop(self) -> None:
        """End the Stagehand session and clean up."""
        if self._session:
            try:
                await asyncio.wait_for(self._session.end(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
        self._session = None
        self._client = None

    # ------------------------------------------------------------------
    # Login detection
    # ------------------------------------------------------------------

    async def _detect_login_page(self) -> bool:
        """Detect if the browser is currently on a login / auth page.

        Uses Stagehand observe() to look for login form elements on the
        page.  This works with remote Browserbase sessions (where there
        is no local Playwright page object to read the URL from).
        """
        if not self._session:
            return False

        try:
            response = await self._session.observe(
                instruction=(
                    "Find any login or authentication form elements on the page: "
                    "username or email input fields, password input fields, "
                    "or sign-in / login / log-in buttons.  "
                    "Only return elements that are clearly part of a login or SSO form."
                ),
            )
            elements = response.data.result
            if isinstance(elements, list) and len(elements) > 0:
                desc = ", ".join(
                    getattr(e, "description", str(e))[:60]
                    for e in elements[:3]
                )
                print(
                    f"[Agent {self.agent_id}] Login page detected via observe(): "
                    f"{len(elements)} element(s) — {desc}"
                )
                return True
        except Exception as exc:
            print(f"[Agent {self.agent_id}] observe() login check failed: {exc}")

        return False

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _PROBE_STEPS = 3  # Short first execute() to detect login redirects fast

    async def _execute_task(self, max_steps: int | None = None) -> Any:
        """Run self._session.execute() with the configured instruction/model."""
        steps = max_steps if max_steps is not None else self.task.max_steps
        # Append a URL-reporting suffix so the agent's completion message
        # includes the final URL, which we can extract as an artifact/link.
        enriched_instruction = (
            self.task.instruction.rstrip()
            + "\n\nIMPORTANT: When you complete the task, include the full URL "
            "of the final page you navigated to in your completion message."
        )
        return await self._session.execute(
            execute_options={
                "instruction": enriched_instruction,
                "max_steps": steps,
            },
            agent_config={
                "model": {
                    "model_name": STAGEHAND_MODEL,
                    "api_key": ANTHROPIC_API_KEY,
                },
            },
            timeout=self.task.timeout,
        )

    async def _handle_login(
        self,
        on_login_needed: Any,
        on_action: Any | None,
        reason: str,
    ) -> None:
        """Pause the agent, hand off to user for login, wait for completion.

        After the user finishes logging in, we re-navigate to re-establish
        Stagehand's active page reference (SSO redirects during manual login
        can cause Stagehand to lose track of the page).
        """
        print(
            f"[Agent {self.agent_id}] Login wall detected via {reason}, "
            f"requesting user login"
        )
        self.current_action = "Waiting for user login"
        if on_action:
            await on_action(self.agent_id, self.current_action)
        await on_login_needed(self.agent_id, self.live_view_url)
        print(f"[Agent {self.agent_id}] User completed login, re-establishing page")

        # After manual login the SSO redirect chain (e.g. Okta → MIT → Canvas)
        # can leave Stagehand's internal page reference stale.  Re-navigate to
        # force Stagehand to reconnect to the active page.
        try:
            target = self.task.url or "about:blank"
            await self._session.navigate(url=target)
            print(f"[Agent {self.agent_id}] Re-navigated to {target}, page restored")
        except Exception as exc:
            print(f"[Agent {self.agent_id}] Re-navigate warning: {exc}")

    def _parse_execute_result(self, response: Any) -> tuple[bool, str, int]:
        """Extract (success, message, action_count) from an execute response."""
        rd = response.data.result
        success = getattr(rd, "success", False)
        message = getattr(rd, "message", "")
        actions = getattr(rd, "actions", [])
        return success, message, len(actions) if actions else 0

    def _extract_artifacts_from_message(self, execute_message: str, final_url: str | None = None) -> list[Artifact]:
        """Build artifacts from the execute() result message (instant, no API call).

        Parses the summary text, extracts any URLs mentioned, and includes
        the final browser URL if available.
        """
        artifacts: list[Artifact] = []
        if not execute_message:
            return artifacts

        artifacts.append(Artifact(
            type="text",
            label="Summary",
            content=execute_message[:500],
        ))

        # Pull any URLs out of the message text
        urls = re.findall(r'https?://[^\s,)\"\']+', execute_message)
        seen_urls = set()
        for url in urls[:5]:
            clean = url.rstrip(".")
            if clean not in seen_urls:
                seen_urls.add(clean)
                artifacts.append(Artifact(
                    type="link",
                    label="Link found",
                    content=clean,
                ))

        # Include the final browser URL if it's meaningful and not already captured
        if final_url and final_url not in ("about:blank", "") and final_url not in seen_urls:
            artifacts.append(Artifact(
                type="link",
                label="Final page",
                content=final_url,
            ))

        # If we still have no links, include the original task URL as a reference
        if not seen_urls and not final_url and self.task.url:
            artifacts.append(Artifact(
                type="link",
                label="Task URL",
                content=self.task.url,
            ))

        return artifacts

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run_task(
        self,
        on_action: Any | None = None,
        on_login_needed: Any | None = None,
    ) -> AgentResult:
        """Execute the assigned task autonomously.

        Login detection strategy (works with remote Browserbase sessions):

        1. **Pre-navigate check**: After navigating to an explicit URL, use
           observe() to look for login form elements before execute() starts.
        2. **Probe phase**: Run a short execute() (3 steps). If it doesn't
           complete, check with observe() for login elements. This catches
           internal redirects (e.g. Canvas → Okta) within seconds instead of
           burning all max_steps on the login form.
        3. **Post-failure check**: If full execute() still fails, one more
           observe() + error-message check as a safety net.
        """
        start_time = datetime.now(timezone.utc)
        login_handled = False

        try:
            if not self._session:
                raise RuntimeError("Agent not started -- call start() first")

            # ── Navigate to starting URL (if provided) ──
            if self.task.url:
                self.current_action = f"Navigating to {self.task.url}"
                if on_action:
                    await on_action(self.agent_id, self.current_action)
                await self._session.navigate(url=self.task.url)

                # Pre-execute login check (session is idle, observe is safe)
                if on_login_needed and await self._detect_login_page():
                    await self._handle_login(on_login_needed, on_action, "pre-navigate observe()")
                    login_handled = True

            # ── Probe phase: short execute to detect login redirects ──
            self.current_action = "Executing task autonomously"
            if on_action:
                await on_action(self.agent_id, self.current_action)

            if on_login_needed and not login_handled:
                probe_steps = min(self._PROBE_STEPS, self.task.max_steps)
                print(f"[Agent {self.agent_id}] Running {probe_steps}-step probe for login detection")
                probe_resp = await self._execute_task(max_steps=probe_steps)
                probe_ok, probe_msg, probe_actions = self._parse_execute_result(probe_resp)
                self.actions_taken = probe_actions

                print(
                    f"[Agent {self.agent_id}] Probe returned: "
                    f"success={probe_ok}, message={probe_msg}, actions={probe_actions}"
                )

                if probe_ok:
                    # Task completed in the probe — we're done
                    success, message = probe_ok, probe_msg
                else:
                    # Probe didn't finish — check if we're on a login page
                    is_login = await self._detect_login_page()
                    error_hints = bool(probe_msg and _LOGIN_ERROR_HINTS.search(probe_msg))

                    if is_login or error_hints:
                        reason = "observe()" if is_login else "error hints"
                        await self._handle_login(on_login_needed, on_action, reason)
                        login_handled = True

                    # Continue with full execution (post-login or just needs more steps)
                    self.current_action = "Executing task autonomously"
                    if on_action:
                        await on_action(self.agent_id, self.current_action)
                    execute_response = await self._execute_task()
                    success, message, extra_actions = self._parse_execute_result(execute_response)
                    self.actions_taken += extra_actions

                    print(
                        f"[Agent {self.agent_id}] Full execute returned: "
                        f"success={success}, message={message}, actions={extra_actions}"
                    )
            else:
                # No login callback, or login was already handled pre-navigate
                execute_response = await self._execute_task()
                success, message, action_count = self._parse_execute_result(execute_response)
                self.actions_taken += action_count
                print(
                    f"[Agent {self.agent_id}] execute() returned: "
                    f"success={success}, message={message}, actions={action_count}"
                )

            # ── Post-failure safety net ──
            if not success and not login_handled and on_login_needed:
                is_login = await self._detect_login_page()
                error_hints = bool(message and _LOGIN_ERROR_HINTS.search(message))
                if is_login or error_hints:
                    reason = "observe()" if is_login else "error hints"
                    await self._handle_login(on_login_needed, on_action, f"post-failure {reason}")
                    login_handled = True

                    self.current_action = "Retrying task after login"
                    if on_action:
                        await on_action(self.agent_id, self.current_action)
                    execute_response = await self._execute_task()
                    success, message, extra_actions = self._parse_execute_result(execute_response)
                    self.actions_taken += extra_actions
                    print(
                        f"[Agent {self.agent_id}] Post-login retry: "
                        f"success={success}, message={message}"
                    )

            # ── Extract structured data (if requested and task succeeded) ──
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

            # ── Try to capture the final browser URL ──
            final_url: str | None = None
            if success:
                try:
                    import asyncio as _aio
                    url_result = await _aio.wait_for(
                        self._session.extract(
                            instruction="What is the current page URL shown in the browser address bar?",
                            schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                        ),
                        timeout=8,
                    )
                    if hasattr(url_result, "data") and hasattr(url_result.data, "result"):
                        raw = url_result.data.result
                        if isinstance(raw, dict):
                            final_url = raw.get("url")
                        elif hasattr(raw, "url"):
                            final_url = raw.url
                except Exception:
                    pass  # Non-critical — we'll still have the message-based artifacts

            # ── Build artifacts from the execute message (instant) ──
            artifacts = self._extract_artifacts_from_message(message, final_url) if success else []

            elapsed = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )

            self.result = AgentResult(
                success=success,
                message=message,
                actions_taken=self.actions_taken,
                extracted_data=extracted_data,
                artifacts=artifacts,
                error=message if not success else None,
                duration_ms=elapsed,
            )
            self.status = "completed" if success else "failed"
            self.error_msg = message if not success else None
            self.finished_at = _now()
            self.current_action = "Done"

            return self.result

        except Exception as exc:
            elapsed = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
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

    async def navigate(self, url: str) -> None:
        if not self._session:
            raise RuntimeError("Agent not started")
        await self._session.navigate(url=url)

    async def act(self, instruction: str) -> dict:
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
                "artifacts": [a.to_dict() for a in self.result.artifacts],
                "error": self.result.error,
                "duration_ms": self.result.duration_ms,
            } if self.result else None,
        }
