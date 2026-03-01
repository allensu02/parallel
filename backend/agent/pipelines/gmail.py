"""Gmail pipeline -- deterministic Playwright-based email reply drafting.

This wraps the existing engine.py email workflow:
  fetch thread -> classify intent -> generate draft -> visual compose -> approve -> save
"""

from __future__ import annotations

from typing import Any

from backend.agent.pipelines.base import Pipeline


class GmailPipeline(Pipeline):
    pipeline_type = "gmail"
    display_name = "Gmail"
    description = (
        "Reply to Gmail email threads. Fetches thread content, classifies intent, "
        "generates a draft reply, visually composes it in Chrome, and waits for approval."
    )
    uses_local_browser = True

    def can_handle(self, task_description: str) -> bool:
        import re
        keywords = [
            r"\bgmail\b", r"\bemail\b", r"\binbox\b", r"\bsend.{0,10}mail\b",
            r"\breply to\b", r"\bdraft.{0,10}(email|mail)\b",
        ]
        desc_lower = task_description.lower()
        return any(re.search(kw, desc_lower) for kw in keywords)

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        """Execute the Gmail pipeline for a single job.

        This delegates to the existing _process_gmail_job logic in engine.py.
        The params dict should contain:
          - thread_id: str -- the Gmail thread ID
          - subject: str -- the thread subject (optional)
        """
        # Import here to avoid circular imports
        from backend.agent.engine import _process_gmail_job

        thread_id = params.get("thread_id", "")
        subject = params.get("subject", "")

        if not thread_id:
            raise ValueError("Gmail pipeline requires a thread_id parameter")

        await _process_gmail_job(
            run_id=run_id,
            job_id=job_id,
            thread_id=thread_id,
            subject=subject,
        )
