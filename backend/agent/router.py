"""Task router -- classifies a free-text task description into a pipeline type.

Uses an LLM call as the PRIMARY classification mechanism. Keyword heuristics
are only used as a fallback when the LLM call fails (e.g. network error,
rate limit, missing API key).
"""

from __future__ import annotations

import json
from typing import Any

from backend.config import ANTHROPIC_API_KEY


# ---------------------------------------------------------------------------
# Heuristic fallback (only used when LLM is unavailable)
# ---------------------------------------------------------------------------

def _heuristic_route(description: str) -> str | None:
    """Try to match a pipeline via simple keyword heuristics.

    This is ONLY called as a fallback when LLM classification fails.
    Returns pipeline_type or None if uncertain.
    """
    from backend.agent.pipelines import get_registry

    for pipeline in get_registry().values():
        if pipeline.pipeline_type == "generic":
            continue
        if pipeline.can_handle(description):
            return pipeline.pipeline_type
    return None


# ---------------------------------------------------------------------------
# LLM-based router (primary)
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM_PROMPT = """\
You are a task router for Hive, an AI agent orchestration platform.
Given a task description, classify it into the SINGLE most appropriate pipeline.

Available pipelines:
{pipeline_list}

Classification rules (apply in order):

1. **Orchestrator** — Route here ONLY when the task explicitly requires MULTIPLE \
DIFFERENT services in sequence (e.g., "research X and write a doc about it", \
"check my calendar and send emails about it"). If the task can be handled by \
one service, do NOT use orchestrator.

2. **Gmail** — Anything about emails: replying, drafting, managing inbox, \
forwarding, sending. Include "max_threads" in params (default 20, or the \
number the user specified).

3. **Google Docs** — Creating, editing, writing documents. Includes "write a doc", \
"create a document", "edit my doc", "google docs".

4. **Google Sheets** — Creating or editing spreadsheets, formulas, data tables. \
Includes "spreadsheet", "google sheets", "create a sheet".

5. **Google Slides** — Creating or editing presentations, slide decks, pitch decks.

6. **Google Forms** — Creating surveys, questionnaires, feedback forms, RSVPs. \
The word "form" inside other words like "information" or "platform" does NOT \
mean Google Forms.

7. **Google Calendar** — Scheduling meetings, creating events, checking availability, \
finding free time.

8. **Google Drive** — File management, sharing, organizing files and folders.

9. **Slack** — Sending messages, channel management, DMs.

10. **Web Research** — "Search for", "look up", "find out about", "who is", \
"what is", "tell me about". Uses real web search (DuckDuckGo). Route here \
for general knowledge or fact-finding that doesn't involve a specific service.

11. **Generic (browser agent)** — Fallback for anything that doesn't match above. \
Navigates websites autonomously. Use for tasks involving specific URLs or \
web platforms that aren't one of the services above (e.g., Canvas, GitHub, \
LinkedIn, any custom website).

IMPORTANT:
- Read the task CAREFULLY. "Pull up course information from canvas" is a \
generic browser task (Canvas website), NOT Google Forms or Docs.
- "Create a form for..." IS Google Forms.
- "I need information about..." is NOT Google Forms — it's either research \
or generic depending on context.
- When in doubt between research and generic, prefer generic if a specific \
website/URL is mentioned, and research if it's a general knowledge question.

Respond with ONLY a JSON object (no markdown, no explanation):
{{"pipeline_type": "<type>", "params": {{"instruction": "<refined instruction>", "url": "<url if any>", "max_threads": <number if gmail>}}}}
"""


async def route_task(description: str, url: str = "") -> dict[str, Any]:
    """Classify a task and return routing info.

    Primary path: LLM classification (always attempted first).
    Fallback: keyword heuristics (only if LLM call fails).

    Returns:
        dict with keys:
          - pipeline_type: str
          - params: dict (pipeline-specific parameters)
    """
    # Primary: LLM-based classification
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

        # Validate that the pipeline_type is actually registered
        from backend.agent.pipelines import get_registry
        if pipeline_type not in get_registry():
            print(f"[Router] LLM returned unknown pipeline '{pipeline_type}', falling back to generic")
            pipeline_type = "generic"

        # Ensure essential fields
        params.setdefault("instruction", description)
        params.setdefault("description", description)
        params.setdefault("url", url)

        print(f"[Router] LLM classified '{description[:60]}...' -> {pipeline_type}")
        return {
            "pipeline_type": pipeline_type,
            "params": params,
        }

    except Exception as exc:
        print(f"[Router] LLM classification failed ({exc}), trying heuristic fallback")

        # Fallback: keyword heuristics
        heuristic_match = _heuristic_route(description)
        if heuristic_match:
            print(f"[Router] Heuristic fallback matched: {heuristic_match}")
            return {
                "pipeline_type": heuristic_match,
                "params": {
                    "instruction": description,
                    "description": description,
                    "url": url,
                },
            }

        # Ultimate fallback: generic
        print(f"[Router] No heuristic match either, defaulting to generic")
        return {
            "pipeline_type": "generic",
            "params": {
                "instruction": description,
                "description": description,
                "url": url,
            },
        }
