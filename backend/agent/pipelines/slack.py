"""Slack pipeline -- send messages and manage channels.

STUB: Currently delegates to the generic Stagehand pipeline.
When fleshed out, this will use deterministic Playwright interactions
to navigate Slack's web UI (send messages, create channels, etc.)
while the LLM handles message content generation.
"""

from __future__ import annotations

from typing import Any

from backend.agent.pipelines.base import Pipeline


class SlackPipeline(Pipeline):
    pipeline_type = "slack"
    display_name = "Slack"
    description = (
        "Send messages and manage Slack channels. Can send messages, "
        "reply to threads, create channels, and search conversations."
    )
    uses_local_browser = True

    def can_handle(self, task_description: str) -> bool:
        import re
        keywords = [
            r"\bslack\b", r"\bslack message\b", r"\bdirect message\b",
        ]
        desc_lower = task_description.lower()
        return any(re.search(kw, desc_lower) for kw in keywords)

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        from backend.agent.pipelines.generic import GenericPipeline

        instruction = params.get("instruction", params.get("description", ""))
        if not params.get("url"):
            params["url"] = "https://app.slack.com"
        params["instruction"] = (
            f"You are working in Slack. {instruction} "
            f"Navigate the Slack web interface to complete this task."
        )

        generic = GenericPipeline()
        await generic.execute(run_id, job_id, params)
