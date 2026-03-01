"""Gmail API connector — fetch threads, create drafts, apply labels."""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.database import get_oauth_token


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

async def _get_credentials() -> Credentials:
    token_data = await get_oauth_token()
    if not token_data or not token_data.get("access_token"):
        raise RuntimeError("Not authenticated — no Gmail OAuth token stored.")
    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token") or None,
        token_uri=token_data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=token_data.get("client_id") or None,
        client_secret=token_data.get("client_secret") or None,
    )
    return creds


def _build_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Fetch thread list
# ---------------------------------------------------------------------------

async def fetch_thread_list(max_results: int = 100) -> list[dict[str, str]]:
    """Return a list of {id, snippet} dicts for recent threads."""
    creds = await _get_credentials()
    service = _build_service(creds)
    resp = (
        service.users()
        .threads()
        .list(userId="me", maxResults=max_results, labelIds=["INBOX"])
        .execute()
    )
    threads = resp.get("threads", [])
    return [{"id": t["id"], "snippet": t.get("snippet", "")} for t in threads]


# ---------------------------------------------------------------------------
# Fetch full thread (messages)
# ---------------------------------------------------------------------------

def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from a message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text
    return ""


async def fetch_thread(thread_id: str) -> dict[str, Any]:
    """Fetch a thread and return structured data."""
    creds = await _get_credentials()
    service = _build_service(creds)
    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    messages = thread.get("messages", [])
    result_messages = []
    subject = ""
    sender = ""
    recipients: list[str] = []

    for msg in messages:
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        if not subject:
            subject = headers.get("subject", "(no subject)")
        if not sender:
            sender = headers.get("from", "")
        to = headers.get("to", "")
        if to and to not in recipients:
            recipients.append(to)

        body = _decode_body(msg.get("payload", {}))
        result_messages.append({
            "id": msg["id"],
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "body": body[:3000],  # Trim to avoid huge payloads
        })

    return {
        "thread_id": thread_id,
        "subject": subject,
        "sender": sender,
        "recipients": recipients,
        "message_count": len(messages),
        "messages": result_messages[-5:],  # Last 5 messages only
    }


# ---------------------------------------------------------------------------
# Create a draft reply
# ---------------------------------------------------------------------------

async def create_draft(thread_id: str, to: str, subject: str, body_html: str) -> str:
    """Create a Gmail draft reply. Returns the draft ID."""
    creds = await _get_credentials()
    service = _build_service(creds)

    message = MIMEText(body_html, "html")
    message["to"] = to
    message["subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    draft = (
        service.users()
        .drafts()
        .create(
            userId="me",
            body={
                "message": {
                    "raw": raw,
                    "threadId": thread_id,
                }
            },
        )
        .execute()
    )
    return draft["id"]


# ---------------------------------------------------------------------------
# Apply / ensure a label
# ---------------------------------------------------------------------------

_label_cache: dict[str, str] = {}


async def _ensure_label(label_name: str) -> str:
    """Get or create a label, return its ID."""
    if label_name in _label_cache:
        return _label_cache[label_name]

    creds = await _get_credentials()
    service = _build_service(creds)

    # Check existing labels
    resp = service.users().labels().list(userId="me").execute()
    for lbl in resp.get("labels", []):
        if lbl["name"] == label_name:
            _label_cache[label_name] = lbl["id"]
            return lbl["id"]

    # Create the label
    new_label = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    _label_cache[label_name] = new_label["id"]
    return new_label["id"]


async def apply_label(thread_id: str, label_name: str) -> None:
    """Apply a label to a thread."""
    creds = await _get_credentials()
    service = _build_service(creds)
    label_id = await _ensure_label(label_name)

    # Get the first message id in the thread
    thread = service.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
    messages = thread.get("messages", [])
    for msg in messages:
        service.users().messages().modify(
            userId="me",
            id=msg["id"],
            body={"addLabelIds": [label_id]},
        ).execute()
