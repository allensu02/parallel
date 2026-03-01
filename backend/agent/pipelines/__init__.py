"""Pipeline registry -- maps pipeline_type strings to Pipeline instances."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent.pipelines.base import Pipeline

# Lazy registry -- populated on first access to avoid circular imports
_registry: dict[str, "Pipeline"] | None = None


def _build_registry() -> dict[str, "Pipeline"]:
    from backend.agent.pipelines.gmail import GmailPipeline
    from backend.agent.pipelines.generic import GenericPipeline
    from backend.agent.pipelines.slides import SlidesPipeline
    from backend.agent.pipelines.sheets import SheetsPipeline
    from backend.agent.pipelines.docs import DocsPipeline
    from backend.agent.pipelines.forms import FormsPipeline
    from backend.agent.pipelines.drive import DrivePipeline
    from backend.agent.pipelines.slack import SlackPipeline
    from backend.agent.pipelines.calendar import CalendarPipeline
    from backend.agent.pipelines.orchestrator import OrchestratorPipeline
    from backend.agent.pipelines.web_research import WebResearchPipeline

    pipelines = [
        GmailPipeline(),
        GenericPipeline(),
        # GSuite pipelines first — so "google docs" etc. match before research
        SlidesPipeline(),
        SheetsPipeline(),
        DocsPipeline(),
        FormsPipeline(),
        DrivePipeline(),
        CalendarPipeline(),
        SlackPipeline(),
        # Research after GSuite so it doesn't steal "google docs" queries
        WebResearchPipeline(),
        OrchestratorPipeline(),
    ]
    return {p.pipeline_type: p for p in pipelines}


def get_registry() -> dict[str, "Pipeline"]:
    """Return the pipeline registry, building it on first call."""
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def get_pipeline(pipeline_type: str) -> "Pipeline":
    """Get a pipeline by type. Falls back to 'generic' if unknown."""
    reg = get_registry()
    return reg.get(pipeline_type, reg["generic"])


def list_pipeline_types() -> list[dict[str, str]]:
    """Return metadata for all registered pipelines (for the router prompt)."""
    reg = get_registry()
    return [
        {
            "type": p.pipeline_type,
            "name": p.display_name,
            "description": p.description,
        }
        for p in reg.values()
    ]
