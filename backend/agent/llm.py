"""Anthropic Claude connector — intent classification + draft generation."""

from __future__ import annotations

from typing import Any

import anthropic

from backend.config import ANTHROPIC_API_KEY
from backend.models import IntentType

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — add it to backend/.env"
            )
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


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
    client = _get_client()

    messages_text = ""
    for m in messages:
        messages_text += f"\nFrom: {m.get('from', '')}\nDate: {m.get('date', '')}\n{m.get('body', m.get('snippet', ''))}\n---\n"

    prompt = _CLASSIFY_PROMPT.format(
        subject=subject,
        sender=sender,
        messages=messages_text[:4000],
    )

    resp = client.messages.create(
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
# Draft generation
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
    """Generate a draft reply.

    Returns (draft_text, summary, confidence, tokens_used).
    """
    client = _get_client()

    messages_text = ""
    for m in messages:
        messages_text += f"\nFrom: {m.get('from', '')}\nDate: {m.get('date', '')}\n{m.get('body', m.get('snippet', ''))}\n---\n"

    prompt = _DRAFT_PROMPT.format(
        subject=subject,
        sender=sender,
        messages=messages_text[:6000],
    )

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    draft_text = resp.content[0].text.strip()
    tokens = (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)

    # Generate a one-line summary
    summary_resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=60,
        messages=[
            {
                "role": "user",
                "content": f"Summarize this email draft in one sentence (max 15 words):\n\n{draft_text}",
            }
        ],
    )
    summary = summary_resp.content[0].text.strip()
    tokens += (summary_resp.usage.input_tokens or 0) + (summary_resp.usage.output_tokens or 0)

    # Simple confidence heuristic based on draft length
    confidence = min(0.95, max(0.5, len(draft_text) / 500))

    return draft_text, summary, confidence, tokens
