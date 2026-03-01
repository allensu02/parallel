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

    # Generate summary
    summary_resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=60,
        messages=[{
            "role": "user",
            "content": f"Summarize this email draft in one sentence (max 15 words):\n\n{draft_text}",
        }],
    )
    summary = summary_resp.content[0].text.strip()
    tokens += (summary_resp.usage.input_tokens or 0) + (summary_resp.usage.output_tokens or 0)

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

    # Generate summary (non-streaming, small call)
    summary_resp = await _retry_on_rate_limit(
        client.messages.create,
        model="claude-sonnet-4-20250514",
        max_tokens=60,
        messages=[{
            "role": "user",
            "content": f"Summarize this email draft in one sentence (max 15 words):\n\n{draft_text}",
        }],
    )
    summary = summary_resp.content[0].text.strip()
    tokens += (summary_resp.usage.input_tokens or 0) + (summary_resp.usage.output_tokens or 0)

    confidence = min(0.95, max(0.5, len(draft_text) / 500))
    return draft_text, summary, confidence, tokens


# ---------------------------------------------------------------------------
# Check if user input is needed before drafting
# ---------------------------------------------------------------------------

_NEEDS_INFO_PROMPT = """\
You are an email triage assistant. Determine if drafting a reply to this email \
requires information that only the recipient would know.

Examples of when info IS needed:
- Calendar invites / meeting requests (need to know availability)
- RSVPs (need to know if attending)
- Questions about personal preferences
- Requests that need a specific decision

Examples of when info is NOT needed:
- Thank you emails
- Informational updates
- Simple acknowledgements
- Questions you can give a generic helpful answer to

Email:
Subject: {subject}
From: {sender}
Content: {content}

Respond in EXACTLY this format (two lines):
NEEDS_INFO: yes OR no
QUESTION: (if yes, write the specific question to ask the user, else write "none")"""


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
