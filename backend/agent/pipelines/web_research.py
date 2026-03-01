"""Web Research pipeline — search the web and compile information.

Uses DuckDuckGo for real web search, fetches page content, and
uses the LLM to synthesize findings. Results stream live to the
hex cell via draft_token events.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend import database as db
from backend.routes.events import publish_event, publish_global


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(run_id: str, event: str, data: dict) -> None:
    await publish_event(run_id, event, data)
    await publish_global(event, {**data, "run_id": run_id})


_SYNTHESIZE_PROMPT = """\
You are a research assistant. Using the web search results below, write a comprehensive \
report on the topic. Be detailed, cite sources, and organize the information clearly.

{web_research}

Original request: {instruction}

Write a thorough, well-organized research report based on the search results above."""


class WebResearchPipeline(Pipeline):
    pipeline_type = "research"
    display_name = "Web Research"
    description = (
        "Search the web for information and compile research. Uses real web search "
        "(DuckDuckGo) to find current information, fetches page content, and "
        "synthesizes findings into a comprehensive summary. Use for fact-finding, "
        "looking up people, companies, topics, current events, etc."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        import re
        desc_lower = task_description.lower()
        # Exclude if it mentions a specific GSuite product
        gsuite_terms = [r"\bgoogle docs?\b", r"\bgoogle sheets?\b", r"\bgoogle slides?\b",
                        r"\bgoogle forms?\b", r"\bgoogle drive\b", r"\bgoogle calendar\b",
                        r"\bgmail\b", r"\bgdocs?\b", r"\bspreadsheet\b", r"\bpresentation\b"]
        if any(re.search(g, desc_lower) for g in gsuite_terms):
            return False
        keywords = [
            r"\bsearch (for|the web|online)\b", r"\blook up\b",
            r"\bfind out about\b", r"\bresearch\b",
            r"\bwho is\b", r"\bwhat is\b", r"\btell me about\b",
        ]
        return any(re.search(kw, desc_lower) for kw in keywords)

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        """Search the web and compile research.

        Streams search results and LLM summary to the hex cell via
        draft_token events so the user sees live progress.
        """
        from backend.agent.llm import _get_async_client
        from backend.agent.web_research import research_topic

        instruction = params.get("instruction", params.get("description", ""))
        if not instruction:
            raise ValueError("Research pipeline requires an 'instruction' parameter")

        await db.update_job(job_id, status="running", started_at=_now(), current_step="web_search")
        await _emit(run_id, "job.started", {"job_id": job_id, "pipeline_type": "research"})
        await _emit(run_id, "job.agent_started", {"job_id": job_id, "pipeline_type": "research"})

        try:
            # Step 1: Web search + fetch
            print(f"[WebResearch] Job {job_id}: Searching: {instruction[:80]}")
            await _emit(run_id, "job.agent_action", {
                "job_id": job_id, "action": "web_search", "status": "running",
                "step": f"Searching: {instruction[:50]}",
            })

            research = await research_topic(
                query=instruction,
                task_context=instruction,
                max_search_results=8,
                max_pages_to_fetch=4,
            )

            n_results = len(research["search_results"])
            n_pages = len(research["page_contents"])
            print(f"[WebResearch] Job {job_id}: Found {n_results} results, fetched {n_pages} pages")

            # Stream search results to hex cell
            for r in research["search_results"]:
                await _emit(run_id, "job.draft_token", {
                    "job_id": job_id,
                    "token": f"🔍 {r['title']}\n   {r['url'][:60]}\n   {r['snippet'][:120]}\n\n",
                })
                await asyncio.sleep(0.15)

            await _emit(run_id, "job.agent_action", {
                "job_id": job_id, "action": "summarize", "status": "running",
                "step": f"Synthesizing {n_results} results...",
            })
            await db.update_job(job_id, current_step="synthesizing")

            # Step 2: LLM synthesis with streaming
            await _emit(run_id, "job.draft_token", {
                "job_id": job_id,
                "token": "\n━━━ Research Report ━━━\n\n",
            })

            client = _get_async_client()
            prompt = _SYNTHESIZE_PROMPT.format(
                web_research=research["combined_text"][:10000],
                instruction=instruction,
            )

            full_text = ""
            async with client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    full_text += text
                    await _emit(run_id, "job.draft_token", {
                        "job_id": job_id,
                        "token": text,
                    })

            summary = full_text.strip()
            print(f"[WebResearch] Job {job_id}: Report complete ({len(summary)} chars)")

            await db.update_job(
                job_id, status="completed", current_step="done",
                summary=summary[:200], finished_at=_now(),
                draft_text=summary[:500],
            )
            await db.increment_run_counter(run_id, "completed_jobs")
            await _emit(run_id, "job.completed", {
                "job_id": job_id,
                "summary": summary[:200],
            })

        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[WebResearch] Job {job_id} FAILED: {err_msg}")
            print(traceback.format_exc()[-500:])
            await db.update_job(
                job_id, status="failed", current_step="done",
                error_msg=err_msg, finished_at=_now(),
            )
            await db.increment_run_counter(run_id, "failed_jobs")
            await _emit(run_id, "job.failed", {"job_id": job_id, "error": err_msg})
