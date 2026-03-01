"""Task router -- classifies a free-text task description into a pipeline type.

Uses a lightweight LLM call with structured output to determine which
pipeline should handle a given task. Falls back to heuristic matching
first (keyword-based) to avoid unnecessary LLM calls for obvious matches.
"""

from __future__ import annotations

import json
from typing import Any

from backend.config import ANTHROPIC_API_KEY


# ---------------------------------------------------------------------------
# Heuristic pre-filter
# ---------------------------------------------------------------------------

def _heuristic_route(description: str) -> str | None:
    """Try to match a pipeline via simple keyword heuristics.
    Returns pipeline_type or None if uncertain."""
    from backend.agent.pipelines import get_registry

    for pipeline in get_registry().values():
        if pipeline.pipeline_type == "generic":
            continue  # Skip generic -- it's the fallback
        if pipeline.can_handle(description):
            return pipeline.pipeline_type
    return None


# ---------------------------------------------------------------------------
# LLM-based router
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM_PROMPT = """\
You are a task router for Hive, an AI agent orchestration platform.
Given a task description, classify it into the most appropriate pipeline.

Available pipelines:
{pipeline_list}

Rules:
- If the task clearly involves one of the specialized pipelines, route to it.
- If the task is ambiguous or doesn't match any specialized pipeline, route to "generic".
- Gmail tasks include: replying to emails, drafting emails, managing inbox, responding to messages.
- For Gmail tasks, include "max_threads" in params (default 20, or use the number the user specified).
- Calendar tasks include: scheduling, creating meetings, checking availability, finding free time.
- Research tasks include: "search for", "look up", "find out about", "who is", "what is", "research". \
Route these to "research" — it does real web search via DuckDuckGo.
- For tasks that mention a specific URL, still classify by the platform the URL belongs to.
- If the task involves MULTIPLE distinct steps across DIFFERENT services (e.g., "research X online \
and write a doc about it", "check my calendar and send emails", "search for Y and make a spreadsheet"), \
route to "orchestrator". The orchestrator will decompose it into sub-tasks.
- Only use orchestrator when the task genuinely needs multiple pipelines. If it's just one service, \
route directly to that service.

Respond with ONLY a JSON object:
{{"pipeline_type": "<type>", "params": {{"instruction": "<refined instruction>", "url": "<url if any>", "max_threads": <number if gmail>}}}}
"""


async def route_task(description: str, url: str = "") -> dict[str, Any]:
    """Classify a task and return routing info.

    Returns:
        dict with keys:
          - pipeline_type: str
          - params: dict (pipeline-specific parameters)
    """
    # Step 1: Try heuristic match
    heuristic_match = _heuristic_route(description)
    if heuristic_match:
        return {
            "pipeline_type": heuristic_match,
            "params": {
                "instruction": description,
                "description": description,
                "url": url,
            },
        }

    # Step 2: LLM-based classification
    try:
        import anthropic
        from backend.agent.pipelines import list_pipeline_types

        pipeline_list = "\n".join(
            f"- {p['type']}: {p['description']}"
            for p in list_pipeline_types()
        )

        system_prompt = _ROUTER_SYSTEM_PROMPT.format(pipeline_list=pipeline_list)
        user_msg = f"Task: {description}"
        if url:
            user_msg += f"\nURL: {url}"

        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )

        text = response.content[0].text.strip()
        # Extract JSON from response (handle potential markdown wrapping)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        pipeline_type = result.get("pipeline_type", "generic")
        params = result.get("params", {})

        # Ensure essential fields
        params.setdefault("instruction", description)
        params.setdefault("description", description)
        params.setdefault("url", url)

        return {
            "pipeline_type": pipeline_type,
            "params": params,
        }

    except Exception as exc:
        print(f"[Router] LLM classification failed ({exc}), falling back to generic")
        return {
            "pipeline_type": "generic",
            "params": {
                "instruction": description,
                "description": description,
                "url": url,
            },
        }
