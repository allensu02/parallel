"""User style profiler — analyzes sent emails to extract writing style.

Uses Claude to analyze sent emails and build a style profile that
can be injected into draft generation prompts for personalization.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from backend.config import ANTHROPIC_API_KEY
from backend.services.google_api import fetch_sent_emails
from backend import database as db


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Analyze sent emails → style profile
# ---------------------------------------------------------------------------

_ANALYZE_PROMPT = """\
You are analyzing a user's email writing style. Below are {count} recent sent emails.
Extract the following characteristics:

1. **tone**: formal, casual, professional, friendly, or mixed
2. **formality**: 1-5 scale (1=very casual, 5=very formal)
3. **greeting_style**: How they typically start emails (e.g., "Hi [name],", "Hey!", "Dear [name],")
4. **signoff_style**: How they typically end emails (e.g., "Best,", "Thanks!", "Cheers,", "Regards,")
5. **avg_length**: short (1-2 sentences), medium (1-2 paragraphs), or long (3+ paragraphs)
6. **uses_emoji**: true/false
7. **vocabulary_notes**: Any notable patterns (e.g., "uses lots of exclamation marks", "tends to be concise")
8. **sample_phrases**: 3-5 characteristic phrases this person uses often
9. **special_rules**: Any other patterns worth noting for generating emails in their style

Respond in VALID JSON only. No markdown, no explanation — just the JSON object.

Emails:
{emails}"""


async def analyze_sent_emails(count: int = 20) -> dict:
    """Fetch recent sent emails and analyze writing style.

    Returns a style profile dict.
    """
    try:
        sent = await fetch_sent_emails(max_results=count)
    except Exception as e:
        print(f"[Profiler] Failed to fetch sent emails: {e}")
        return _default_profile()

    if not sent:
        print("[Profiler] No sent emails found")
        return _default_profile()

    # Format emails for the prompt
    emails_text = ""
    for i, email in enumerate(sent[:count], 1):
        emails_text += f"\n--- Email {i} ---\n"
        emails_text += f"To: {email.get('to', '')}\n"
        emails_text += f"Subject: {email.get('subject', '')}\n"
        emails_text += f"Body:\n{email.get('body', '')[:1000]}\n"

    prompt = _ANALYZE_PROMPT.format(count=len(sent), emails=emails_text)

    client = _get_client()
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()

    # Parse JSON
    try:
        # Handle potential markdown wrapping
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        profile = json.loads(text)
    except json.JSONDecodeError:
        print(f"[Profiler] Failed to parse LLM response as JSON: {text[:200]}")
        profile = _default_profile()

    return profile


def _default_profile() -> dict:
    """Return a sensible default profile when analysis isn't possible."""
    return {
        "tone": "professional",
        "formality": 3,
        "greeting_style": "Hi [name],",
        "signoff_style": "Best,",
        "avg_length": "medium",
        "uses_emoji": False,
        "vocabulary_notes": "No data available",
        "sample_phrases": [],
        "special_rules": "No specific patterns detected",
    }


# ---------------------------------------------------------------------------
# Update profile from edit diffs
# ---------------------------------------------------------------------------

_UPDATE_PROMPT = """\
You are updating a user's email writing style profile based on edits they made to AI-drafted emails.

Current profile:
{current_profile}

Recent edits the user made to AI drafts (showing what was changed):
{diffs}

Based on these edits, update the style profile. Return the FULL updated profile as VALID JSON only.
Preserve fields that don't need updating. Only modify what the edits suggest."""


async def update_profile_from_diffs(profile_id: str = "default") -> dict:
    """Re-analyze profile based on accumulated edit diffs."""
    profile_data = await db.get_user_profile(profile_id)
    if not profile_data:
        return _default_profile()

    current_profile = json.loads(profile_data.get("style_profile", "{}"))
    diffs = json.loads(profile_data.get("edit_diffs", "[]"))

    if not diffs:
        return current_profile

    client = _get_client()
    diffs_text = ""
    for i, d in enumerate(diffs[-10:], 1):  # Last 10 diffs
        diffs_text += f"\n--- Edit {i} ---\n"
        diffs_text += f"Original: {d.get('original', '')[:500]}\n"
        diffs_text += f"Edited: {d.get('edited', '')[:500]}\n"

    prompt = _UPDATE_PROMPT.format(
        current_profile=json.dumps(current_profile, indent=2),
        diffs=diffs_text,
    )

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    try:
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        updated = json.loads(text)
    except json.JSONDecodeError:
        updated = current_profile

    # Save updated profile
    await db.update_user_profile(profile_id, style_profile=json.dumps(updated))
    return updated


# ---------------------------------------------------------------------------
# Build prompt injection string from profile
# ---------------------------------------------------------------------------

def build_style_prompt(profile: dict) -> str:
    """Convert a style profile into a prompt injection string."""
    if not profile or profile == _default_profile():
        return ""

    parts = []
    tone = profile.get("tone", "")
    if tone:
        parts.append(f"- Write in a {tone} tone")

    formality = profile.get("formality", 3)
    if formality <= 2:
        parts.append("- Keep it casual and relaxed")
    elif formality >= 4:
        parts.append("- Maintain a formal, polished style")

    greeting = profile.get("greeting_style", "")
    if greeting:
        parts.append(f"- Start emails like: {greeting}")

    signoff = profile.get("signoff_style", "")
    if signoff:
        parts.append(f"- Sign off with: {signoff}")

    length = profile.get("avg_length", "")
    if length == "short":
        parts.append("- Keep it brief (1-2 sentences)")
    elif length == "long":
        parts.append("- It's okay to be detailed (3+ paragraphs)")

    uses_emoji = profile.get("uses_emoji", False)
    if uses_emoji:
        parts.append("- Feel free to use emoji naturally")

    phrases = profile.get("sample_phrases", [])
    if phrases:
        parts.append(f"- Characteristic phrases to use naturally: {', '.join(phrases[:5])}")

    rules = profile.get("special_rules", "")
    if rules and rules != "No specific patterns detected":
        parts.append(f"- Note: {rules}")

    if not parts:
        return ""

    return "\n\nMatch this user's writing style:\n" + "\n".join(parts)
