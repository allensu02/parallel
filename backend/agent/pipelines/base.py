"""Abstract base class for all task pipelines.

Each pipeline handles a specific type of task (Gmail, Google Slides, etc.)
by providing:
  - A pipeline_type identifier used for routing
  - An execute() method that runs the full task lifecycle
  - Optional browser_interact() for deterministic Playwright interactions
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Pipeline(ABC):
    """Base class for all task execution pipelines."""

    pipeline_type: str = ""
    display_name: str = ""
    description: str = ""

    # Whether this pipeline uses local Playwright (True) or Browserbase (False)
    uses_local_browser: bool = True

    @abstractmethod
    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        """Run the full task lifecycle for a single job.

        This is the main entry point called by the engine dispatcher.
        The pipeline owns the entire lifecycle: browser interaction,
        LLM calls, SSE events, DB updates, and approval flows.

        Args:
            run_id: The parent run identifier.
            job_id: The job identifier.
            params: Pipeline-specific parameters extracted by the router.
        """
        raise NotImplementedError

    def can_handle(self, task_description: str) -> bool:
        """Quick heuristic check (no LLM) for whether this pipeline
        might handle a given task. Used as a pre-filter before the
        LLM-based router for obvious matches."""
        return False
