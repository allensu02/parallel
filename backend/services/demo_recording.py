"""Fetch Browserbase session recording and normalize to a compact action summary for LLM."""

from __future__ import annotations

import json
from typing import Any

import httpx

BROWSERBASE_RECORDING_URL = "https://api.browserbase.com/v1/sessions/{session_id}/recording"
MAX_EVENTS_TO_STORE = 500


async def fetch_session_recording(session_id: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch rrweb-style recording for a Browserbase session.
    Returns list of events: [{ type, data, sessionId?, timestamp? }, ...].
    """
    url = BROWSERBASE_RECORDING_URL.format(session_id=session_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            url,
            headers={"X-BB-API-Key": api_key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return []


def normalize_recording_to_actions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce rrweb events to a short list of high-level actions for LLM context.
    rrweb: type 2 = FullSnapshot, 3 = IncrementalSnapshot, 4 = Meta, 5 = Custom (e.g. payload).
    We extract URLs from meta/navigation and infer clicks/inputs from incremental where possible.
    For MVP we produce a reduced summary: URLs visited + event type counts + sampled actions.
    """
    actions: list[dict[str, Any]] = []
    urls_seen: set[str] = set()
    # rrweb event type constants (from rrweb)
    EVENT_TYPES = {"FullSnapshot": 2, "IncrementalSnapshot": 3, "Meta": 4, "Custom": 5}
    INCREMENTAL_SOURCE = {"Mutation": 0, "MouseMove": 1, "MouseInteraction": 2, "Scroll": 3, "Input": 4}

    # Process all events so we don't drop the last actions (we used to break after 100 events).
    for evt in events:
        evt_type = evt.get("type")
        data = evt.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}

        if evt_type == EVENT_TYPES.get("Meta", 4):
            url = data.get("href") or data.get("url")
            if url and url not in urls_seen:
                urls_seen.add(url)
                actions.append({"action": "navigate", "url": url})
            continue

        if evt_type == EVENT_TYPES.get("IncrementalSnapshot", 3):
            source = data.get("source")
            if source == INCREMENTAL_SOURCE.get("MouseInteraction", 2):
                x = data.get("x", 0)
                y = data.get("y", 0)
                actions.append({"action": "click", "x": x, "y": y})
            elif source == INCREMENTAL_SOURCE.get("Input", 4):
                text = data.get("text", "")
                if isinstance(text, str) and len(text.strip()) > 0:
                    actions.append({"action": "type", "text": text[:200]})
            continue

        if evt_type == EVENT_TYPES.get("Custom", 5):
            payload = data.get("payload") or data
            if isinstance(payload, dict):
                if payload.get("href"):
                    actions.append({"action": "navigate", "url": payload["href"]})
                elif payload.get("source") == "navigation":
                    url = payload.get("url")
                    if url and url not in urls_seen:
                        urls_seen.add(url)
                        actions.append({"action": "navigate", "url": url})

    if not actions and events:
        urls_from_snapshots: list[str] = []
        for evt in events[:50]:
            data = evt.get("data") or {}
            if isinstance(data, dict):
                if data.get("href"):
                    urls_from_snapshots.append(data["href"])
                node = data.get("root", {}).get("childNodes", [])
                if node and isinstance(node, list):
                    _collect_urls_from_node(node, urls_from_snapshots)
        for url in urls_from_snapshots[:20]:
            if url and url not in urls_seen:
                urls_seen.add(url)
                actions.append({"action": "navigate", "url": url})

    return actions


def _collect_urls_from_node(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("tagName") == "a":
            for prop in node.get("attributes", []) or []:
                if isinstance(prop, list) and len(prop) >= 2 and prop[0] == "href":
                    out.append(str(prop[1]))
                    break
        for child in node.get("childNodes", []) or []:
            _collect_urls_from_node(child, out)
    elif isinstance(node, list):
        for child in node:
            _collect_urls_from_node(child, out)


def _url_to_label(url: str) -> str:
    """Turn a URL into a short human-readable page label for the LLM."""
    if not url or not isinstance(url, str):
        return "a page"
    url = url.strip()
    try:
        from urllib.parse import urlparse
        p = urlparse(url if "://" in url else "https://" + url)
        host = (p.netloc or p.path or "").lower()
        path = (p.path or "").strip("/")
        if "login" in host or "login" in path or "signin" in path or "saml" in path.lower():
            return host.split(".")[-2] + " login page" if host else "login page"
        if "duo" in host or "duosecurity" in host:
            return "Duo Security / 2FA page"
        if "accounts.google" in host:
            return "Google sign-in page"
        if "mail.google" in host or "gmail" in host:
            return "Gmail"
        if host:
            # e.g. "dashboard.stanford.edu" -> "Stanford dashboard"
            parts = host.replace("www.", "").split(".")
            if len(parts) >= 2:
                name = parts[-2].replace("-", " ").title()
                return name + " (" + host + ")"
        return host or url[:60]
    except Exception:
        return url[:80]


def actions_to_summary_text(actions: list[dict[str, Any]]) -> str:
    """Turn normalized actions into a short text summary for the LLM prompt.
    Uses URL-derived labels so the LLM sees e.g. 'Stanford login page' instead of raw URLs only.
    """
    if not actions:
        return "No recorded actions."
    lines = []
    current_page = ""
    for i, a in enumerate(actions, 1):
        act = a.get("action", "unknown")
        if act == "navigate":
            url = a.get("url", "")
            current_page = _url_to_label(url)
            lines.append(f"{i}. Navigate to {current_page} | URL: {url[:120]}")
        elif act == "click":
            # Give context: which page we're on (from last navigate) so LLM can infer "login button" etc.
            ctx = f" (on: {current_page})" if current_page else ""
            lines.append(f"{i}. Click{ctx} | coordinates: ({a.get('x', 0)}, {a.get('y', 0)})")
        elif act == "type":
            text = (a.get("text") or "")[:100]
            ctx = f" (on: {current_page})" if current_page else ""
            lines.append(f"{i}. Type: {text!r}{ctx}")
        else:
            lines.append(f"{i}. {act}: {a}")
    return "\n".join(lines)
