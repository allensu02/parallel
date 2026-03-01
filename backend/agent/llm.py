"""Anthropic Claude connector — intent classification + draft generation.

Uses AsyncAnthropic for truly non-blocking concurrent API calls.
Supports streaming draft generation for real-time typing animation.
Includes automatic retry with exponential backoff for rate limits.
"""

from __future__ import annotations

import asyncio
import random
from typing import Callable, Awaitable

import anthropic

from backend.config import ANTHROPIC_API_KEY
from backend.models import IntentType

# ---------------------------------------------------------------------------
# Async Client
# ---------------------------------------------------------------------------

_async_client: anthropic.AsyncAnthropic | None = None


def _get_async_client() -> anthropic.AsyncAnthropic:
    global _async_client
    if _async_client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set — add it to backend/.env")
        _async_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _async_client


# ---------------------------------------------------------------------------
# Rate-limit aware retry wrapper
# ---------------------------------------------------------------------------

MAX_RETRIES = 5


async def _retry_on_rate_limit(coro_fn, *args, **kwargs):
    """Call an async function, retrying on 429 RateLimitError with backoff.
    
    Uses exponential backoff starting at 15s (since the limit is per-minute).
    """
    for attempt in range(MAX_RETRIES):
        try:
            return await coro_fn(*args, **kwargs)
        except anthropic.RateLimitError as e:
            if attempt == MAX_RETRIES - 1:
                raise
            # Backoff: 15s, 30s, 45s, 60s — stay within the 1-minute window
            wait = 15 * (attempt + 1) + random.uniform(0, 5)
            print(f"[LLM] Rate limited (attempt {attempt + 1}/{MAX_RETRIES}), waiting {wait:.0f}s...")
            await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
You are an email triage assistant. Given an email thread, classify the required action.

Respond with EXACTLY one word: reply, ignore, or escalate.

Rules:
- "reply": The email requires or would benefit from a response (questions, requests, conversations).
- "ignore": The email is a notification, newsletter, automated message, or requires no response.
- "escalate": The email involves sensitive topics (legal, financial, HR, complaints) that need human review.

Email thread:
Subject: {subject}
From: {sender}

{messages}

Classification:"""


_CLASSIFY_AND_CHECK_PROMPT = """\
You are an email triage assistant. Given an email thread, do TWO things:

1. Classify the required action as reply, ignore, or escalate.
2. If the action is "reply", determine whether you should ask the user a question before drafting. You should almost ALWAYS ask a question — the goal is to write the best possible reply, and that requires understanding the user's intent, preferences, and context.

Respond in EXACTLY this format (three lines):
ACTION: reply OR ignore OR escalate
NEEDS_INFO: yes OR no
QUESTION: (if NEEDS_INFO is yes, write the specific question to ask the user; otherwise write "none")

Rules for ACTION:
- "reply": The email requires or would benefit from a response.
- "ignore": Notification, newsletter, automated — no response needed.
- "escalate": Sensitive topics (legal, financial, HR) needing human review.

Rules for NEEDS_INFO — be AGGRESSIVE about asking questions:
- Default to "yes" for almost all replies. A quick question ensures the draft matches what the user actually wants to say.
- "yes" for: meeting requests, invitations, RSVPs, scheduling, decisions, opinions, project updates, feedback requests, introductions, proposals, questions that could be answered multiple ways, anything where tone/stance matters.
- "yes" if there's ANY ambiguity about what the user would want to convey.
- "no" ONLY for truly trivial cases: simple "thanks for letting me know" acknowledgements where the reply is obvious and there's zero ambiguity.
- When in doubt, ask. It's always better to ask than to guess wrong.
- Ask specific, concise questions. Example: "This is a meeting invite for Friday 3pm — can you attend? Any scheduling conflicts?"

Email thread:
Subject: {subject}
From: {sender}

{messages}"""


async def classify_intent(
    subject: str, sender: str, messages: list[dict]
) -> tuple[IntentType, int]:
    """Classify an email thread. Returns (intent, tokens_used)."""
    client = _get_async_client()

    messages_text = ""
    for m in messages:
        messages_text += f"\nFrom: {m.get('from', '')}\nDate: {m.get('date', '')}\n{m.get('body', m.get('snippet', ''))}\n---\n"

    prompt = _CLASSIFY_PROMPT.format(
        subject=subject,
        sender=sender,
        messages=messages_text[:4000],
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip().lower()
    tokens = (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)

    if "reply" in text:
        return IntentType.reply, tokens
    elif "escalate" in text:
        return IntentType.escalate, tokens
    else:
        return IntentType.ignore, tokens


async def classify_and_check(
    subject: str, sender: str, messages: list[dict]
) -> tuple[IntentType, bool, str, int]:
    """Classify intent AND check if user info is needed in ONE LLM call.

    Returns (intent, needs_info, question_text, tokens_used).
    """
    client = _get_async_client()

    messages_text = ""
    for m in messages:
        messages_text += f"\nFrom: {m.get('from', '')}\nDate: {m.get('date', '')}\n{m.get('body', m.get('snippet', ''))}\n---\n"

    prompt = _CLASSIFY_AND_CHECK_PROMPT.format(
        subject=subject,
        sender=sender,
        messages=messages_text[:4000],
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    tokens = (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)

    # Parse the structured response
    intent = IntentType.ignore
    needs_info = False
    question = ""

    for line in text.split("\n"):
        line = line.strip()
        if line.upper().startswith("ACTION:"):
            val = line.split(":", 1)[1].strip().lower()
            if "reply" in val:
                intent = IntentType.reply
            elif "escalate" in val:
                intent = IntentType.escalate
            else:
                intent = IntentType.ignore
        elif line.upper().startswith("NEEDS_INFO:"):
            val = line.split(":", 1)[1].strip().lower()
            needs_info = val in ("yes", "true")
        elif line.upper().startswith("QUESTION:"):
            question = line.split(":", 1)[1].strip()
            if question.lower() == "none":
                question = ""

    return intent, needs_info, question, tokens


# ---------------------------------------------------------------------------
# Draft generation (non-streaming)
# ---------------------------------------------------------------------------

_DRAFT_PROMPT = """\
You are a professional email assistant. Write a concise, helpful reply to the following email thread.

Guidelines:
- Be professional but warm
- Address the key points raised
- Keep it concise (2-4 paragraphs max)
- Do NOT include a subject line
- Do NOT include "Dear" or overly formal greetings — use first name if available
- Sign off naturally

Email thread:
Subject: {subject}
From: {sender}

{messages}

Draft reply:"""


async def generate_draft(
    subject: str, sender: str, messages: list[dict]
) -> tuple[str, str, float, int]:
    """Generate a draft reply. Returns (draft_text, summary, confidence, tokens_used)."""
    client = _get_async_client()

    messages_text = ""
    for m in messages:
        messages_text += f"\nFrom: {m.get('from', '')}\nDate: {m.get('date', '')}\n{m.get('body', m.get('snippet', ''))}\n---\n"

    prompt = _DRAFT_PROMPT.format(
        subject=subject,
        sender=sender,
        messages=messages_text[:6000],
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    draft_text = resp.content[0].text.strip()
    tokens = (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)

    summary = _local_summary(draft_text)
    confidence = min(0.95, max(0.5, len(draft_text) / 500))
    return draft_text, summary, confidence, tokens


# ---------------------------------------------------------------------------
# Streaming draft generation (for live typing animation)
# ---------------------------------------------------------------------------

_DRAFT_STREAM_PROMPT = """\
You are a professional email assistant. Write a concise, helpful reply to the following email thread.

Guidelines:
- Be professional but warm
- Address the key points raised
- Keep it concise (2-4 paragraphs max)
- Do NOT include a subject line
- Do NOT include "Dear" or overly formal greetings — use first name if available
- Sign off naturally
{extra_context}{style_context}

Email thread:
Subject: {subject}
From: {sender}

{messages}

Draft reply:"""


async def generate_draft_stream(
    subject: str,
    sender: str,
    messages: list[dict],
    on_token: Callable[[str], Awaitable[None]] | None = None,
    extra_context: str = "",
    user_profile: dict | None = None,
) -> tuple[str, str, float, int]:
    """Generate a draft reply with token-by-token streaming.

    Calls on_token(chunk) for each text chunk as it arrives.
    Returns (draft_text, summary, confidence, tokens_used).
    """
    client = _get_async_client()

    messages_text = ""
    for m in messages:
        messages_text += (
            f"\nFrom: {m.get('from', '')}\n"
            f"Date: {m.get('date', '')}\n"
            f"{m.get('body', m.get('snippet', ''))}\n---\n"
        )

    extra = ""
    if extra_context:
        extra = f"\n\nAdditional context from the user:\n{extra_context}"

    # Build style context from user profile
    style_context = ""
    if user_profile:
        try:
            from backend.services.profiler import build_style_prompt
            style_context = build_style_prompt(user_profile)
        except Exception:
            pass

    prompt = _DRAFT_STREAM_PROMPT.format(
        subject=subject,
        sender=sender,
        messages=messages_text[:6000],
        extra_context=extra,
        style_context=style_context,
    )

    # Stream the draft with rate-limit retry
    draft_parts: list[str] = []
    input_tokens = 0
    output_tokens = 0

    for attempt in range(MAX_RETRIES):
        try:
            async with client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    draft_parts.append(text)
                    if on_token:
                        await on_token(text)

                final = await stream.get_final_message()
                input_tokens = final.usage.input_tokens or 0
                output_tokens = final.usage.output_tokens or 0
            break  # success
        except anthropic.RateLimitError:
            if attempt == MAX_RETRIES - 1:
                raise
            draft_parts.clear()  # reset on retry
            wait = 15 * (attempt + 1) + random.uniform(0, 5)
            print(f"[LLM] Stream rate limited (attempt {attempt + 1}/{MAX_RETRIES}), waiting {wait:.0f}s...")
            await asyncio.sleep(wait)

    draft_text = "".join(draft_parts).strip()
    tokens = input_tokens + output_tokens

    # Generate summary locally (avoid extra LLM call to save rate limit)
    summary = _local_summary(draft_text)

    confidence = min(0.95, max(0.5, len(draft_text) / 500))
    return draft_text, summary, confidence, tokens


def _local_summary(draft_text: str) -> str:
    """Generate a short summary from the draft text without an LLM call."""
    if not draft_text:
        return "Empty draft"
    # Take the first sentence or first 80 chars
    first_line = draft_text.split("\n")[0].strip()
    # Find first sentence end
    for end in (".", "!", "?"):
        idx = first_line.find(end)
        if idx > 0 and idx < 100:
            return first_line[: idx + 1]
    # Truncate if too long
    if len(first_line) > 80:
        return first_line[:77] + "..."
    return first_line or draft_text[:80]


# ---------------------------------------------------------------------------
# Check if user input is needed before drafting
# ---------------------------------------------------------------------------

_NEEDS_INFO_PROMPT = """\
You are an email triage assistant. Before drafting a reply, you should almost \
ALWAYS ask the user a question to make sure the reply matches their intent.

Ask a question whenever:
- The email involves scheduling, meetings, invitations, RSVPs
- There's a decision to make (yes/no, option A vs B)
- The sender is asking for an opinion, feedback, or preference
- The reply could go in multiple directions depending on context
- The tone or stance of the reply matters
- There's any project, task, or commitment being discussed
- The user might have additional context that would improve the reply

Only skip questions for truly trivial cases like "thanks for the update" where \
the reply is completely obvious and there's zero ambiguity.

When in doubt, ASK. It's always better to ask than to guess.

Email:
Subject: {subject}
From: {sender}
Content: {content}

Respond in EXACTLY this format (two lines):
NEEDS_INFO: yes OR no
QUESTION: (if yes, write a specific, concise question to ask the user, else write "none")"""


async def check_needs_info(
    subject: str, sender: str, messages: list[dict]
) -> tuple[bool, str]:
    """Check if drafting a reply requires user input.

    Returns (needs_info, question_text).
    """
    client = _get_async_client()

    content = ""
    for m in messages[-3:]:
        content += f"{m.get('body', m.get('snippet', ''))}\n---\n"

    prompt = _NEEDS_INFO_PROMPT.format(
        subject=subject,
        sender=sender,
        content=content[:3000],
    )

    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    lines = text.split("\n")

    needs_info = False
    question = ""

    for line in lines:
        line = line.strip()
        if line.upper().startswith("NEEDS_INFO:"):
            val = line.split(":", 1)[1].strip().lower()
            needs_info = val in ("yes", "true")
        elif line.upper().startswith("QUESTION:"):
            question = line.split(":", 1)[1].strip()
            if question.lower() == "none":
                question = ""

    return needs_info, question


# ---------------------------------------------------------------------------
# Demo recording synthesis (browser actions -> natural-language procedure)
# ---------------------------------------------------------------------------

DEMO_SYNTHESIS_PROMPT_TEMPLATE = """\
The user performed the following recorded actions in a browser. Turn this into a clear, \
specific procedure that another browser agent could follow to replicate the same workflow.

Each click action may include:
- The element text (what was directly clicked)
- A [surrounding text: ...] block showing the full text of the containing card/item/section

Use the [surrounding text] to understand WHAT the user actually selected. For example:
- If the element says "4.2 • 1.2 mi" but the surrounding text says "Taco Bell 4.2 • 1.2 mi \
$ Fast Food 20-35 min", the user selected the TACO BELL restaurant.
- If the element says "Add to cart" but the surrounding text mentions "Crunchwrap Supreme $7.99", \
the user added a CRUNCHWRAP SUPREME to their cart.
- "Select option" actions represent radio button / checkbox selections. The element text IS the \
option the user chose. If there's an [option group context], it shows ALL options in the group. \
Example: "Select option 'Large — +$2.00'" means the user chose the Large size at +$2.00.

Rules:
- Be MAXIMALLY SPECIFIC: always include the exact restaurant name, item name, option name, etc. \
These details are the entire point of the procedure — without them it's useless.
- Use the URLs to name pages (e.g. "Go to DoorDash at www.doordash.com").
- Group related actions into logical steps when appropriate.
- Write as continuous numbered instructions. Output only the procedure text, no preamble.
- If a click's purpose is unclear even from context, describe it by its position and page."""

_ACTIONS_BLOCK = """\
Recorded actions:
{actions_text}
"""


def get_demo_synthesis_prompt_template() -> str:
    """Return the full prompt template with placeholder, for display in UI."""
    return DEMO_SYNTHESIS_PROMPT_TEMPLATE + "\n\n" + _ACTIONS_BLOCK.format(
        actions_text="<recorded actions go here>"
    )


async def synthesize_demo_actions_to_procedure(actions_text: str) -> str:
    """Turn a text summary of recorded browser actions into natural-language procedure for agents.

    actions_text: output of demo_recording.actions_to_summary_text(normalized_actions).
    Returns the instruction_summary string to store and prepend to future tasks.
    """
    if not actions_text or actions_text.strip() == "No recorded actions.":
        return "The user opened a browser but no meaningful actions were recorded."
    client = _get_async_client()
    prompt = DEMO_SYNTHESIS_PROMPT_TEMPLATE + "\n\n" + _ACTIONS_BLOCK.format(
        actions_text=actions_text
    )
    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ---------------------------------------------------------------------------
# Pick best demo for a task (for auto-matching demos to instructions)
# ---------------------------------------------------------------------------

_PICK_DEMO_PROMPT = """\
You are matching a new task to previously-recorded demo procedures.
Pick the one demo whose recorded steps would be DIRECTLY useful for completing this task.

A demo is a good match when:
- It targets the same application or service the task mentions
- Its recorded steps cover the same type of operation (e.g., creating vs editing vs deleting)
- Following the demo's procedure would meaningfully help accomplish the task

A demo is NOT a match when:
- It's for a different service or application
- The operations are fundamentally different (e.g., demo creates a doc, task edits a sheet)
- The similarity is only superficial (e.g., both mention "Google" but different products)

Task: {instruction}

Demos (id, name, summary):
{demos_list}

Respond with ONLY the demo id (e.g. abc123def) or the word "none" if no demo is relevant."""


async def pick_best_demo_for_task(instruction: str, demos: list[dict]) -> str | None:
    """Return the id of the demo that best matches the task, or None if none match.

    demos: list of dicts with keys id, name, instruction_summary.
    """
    if not instruction.strip() or not demos:
        return None
    lines = []
    for d in demos:
        did = d.get("id") or ""
        name = d.get("name") or "Untitled"
        summary = (d.get("instruction_summary") or "")[:500]
        lines.append(f"- id: {did} | name: {name} | summary: {summary}")
    demos_list = "\n".join(lines)
    prompt = _PICK_DEMO_PROMPT.format(
        instruction=instruction.strip()[:1000], demos_list=demos_list
    )
    client = _get_async_client()
    resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip().lower()
    if not text or text == "none":
        return None
    # Find which demo id appears in the response
    for d in demos:
        did = d.get("id")
        if not did:
            continue
        if did.lower() in text or text.startswith(did.lower()):
            return d["id"]
    # Fallback: first token might be the id
    first = text.split()[0].strip(".,;") if text.split() else ""
    for d in demos:
        if d.get("id") and (d["id"].lower() == first or first in d["id"].lower()):
            return d["id"]
    return None
