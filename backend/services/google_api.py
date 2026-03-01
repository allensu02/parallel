"""Google API wrappers — Gmail and Calendar.

Provides a clean interface for reading/composing emails and
querying calendar events using stored OAuth2 credentials.
"""

from __future__ import annotations

import base64
import email.mime.text
from datetime import datetime, timedelta, timezone
from typing import Any

from backend import database as db
from backend.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

async def _get_credentials():
    """Rebuild google.oauth2.credentials.Credentials from stored tokens."""
    token_data = await db.get_oauth_token()
    if not token_data or not token_data.get("access_token"):
        return None

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", ""),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id", GOOGLE_CLIENT_ID),
        client_secret=token_data.get("client_secret", GOOGLE_CLIENT_SECRET),
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            await db.save_oauth_token({
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "expiry": creds.expiry.isoformat() if creds.expiry else "",
                "email": token_data.get("email", ""),
            })
        except Exception as e:
            print(f"[GoogleAPI] Token refresh failed: {e}")
            return None

    return creds


async def get_gmail_service():
    """Build and return an authorized Gmail API client."""
    creds = await _get_credentials()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds)


async def get_calendar_service():
    """Build and return an authorized Calendar API client."""
    creds = await _get_credentials()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Gmail — Inbox listing
# ---------------------------------------------------------------------------

async def fetch_inbox_threads_api(max_results: int = 50) -> list[dict]:
    """Fetch inbox threads via Gmail API.

    Returns list of dicts: {id, subject, sender, snippet, date, unread}
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available — not authenticated")

    results = service.users().threads().list(
        userId="me",
        maxResults=max_results,
        labelIds=["INBOX"],
    ).execute()

    threads_raw = results.get("threads", [])
    threads = []

    for t in threads_raw[:max_results]:
        try:
            thread = service.users().threads().get(
                userId="me",
                id=t["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()

            messages = thread.get("messages", [])
            if not messages:
                continue

            first_msg = messages[0]
            headers = {h["name"]: h["value"] for h in first_msg.get("payload", {}).get("headers", [])}

            # Check if unread
            labels = first_msg.get("labelIds", [])
            unread = "UNREAD" in labels

            threads.append({
                "id": t["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "sender": headers.get("From", ""),
                "snippet": thread.get("snippet", "")[:200],
                "date": headers.get("Date", ""),
                "unread": unread,
            })
        except Exception as e:
            print(f"[GoogleAPI] Error fetching thread {t['id']}: {e}")
            continue

    return threads


# ---------------------------------------------------------------------------
# Gmail — Thread content
# ---------------------------------------------------------------------------

async def fetch_thread_content_api(thread_id: str) -> dict:
    """Fetch full content of a thread via Gmail API.

    Returns {thread_id, subject, sender, messages: [{from, date, body}], message_count}
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available")

    thread = service.users().threads().get(
        userId="me",
        id=thread_id,
        format="full",
    ).execute()

    messages_raw = thread.get("messages", [])
    messages = []

    subject = ""
    sender = ""

    for msg in messages_raw:
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        if not subject:
            subject = headers.get("Subject", "(no subject)")
        if not sender:
            sender = headers.get("From", "")

        body = _extract_body(msg.get("payload", {}))
        messages.append({
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "body": body,
        })

    return {
        "thread_id": thread_id,
        "subject": subject,
        "sender": sender,
        "messages": messages,
        "message_count": len(messages),
    }


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Fallback: try first part recursively
    for part in parts:
        result = _extract_body(part)
        if result:
            return result

    return ""


# ---------------------------------------------------------------------------
# Gmail — Sent emails (for profiling)
# ---------------------------------------------------------------------------

async def fetch_sent_emails(max_results: int = 20) -> list[dict]:
    """Fetch recent sent emails for user profiling.

    Returns list of {subject, to, body, date}
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available")

    results = service.users().messages().list(
        userId="me",
        maxResults=max_results,
        labelIds=["SENT"],
    ).execute()

    messages_raw = results.get("messages", [])
    sent_emails = []

    for m in messages_raw[:max_results]:
        try:
            msg = service.users().messages().get(
                userId="me",
                id=m["id"],
                format="full",
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = _extract_body(msg.get("payload", {}))

            sent_emails.append({
                "subject": headers.get("Subject", ""),
                "to": headers.get("To", ""),
                "body": body[:2000],  # Limit body size
                "date": headers.get("Date", ""),
            })
        except Exception as e:
            print(f"[GoogleAPI] Error fetching sent email {m['id']}: {e}")
            continue

    return sent_emails


# ---------------------------------------------------------------------------
# Gmail — Create draft
# ---------------------------------------------------------------------------

async def create_draft_api(thread_id: str, body_text: str, to: str = "", subject: str = "") -> dict | None:
    """Create a draft reply in Gmail via API.

    Returns draft info or None on failure.
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available")

    # Build MIME message
    message = email.mime.text.MIMEText(body_text)
    if to:
        message["To"] = to
    if subject:
        message["Subject"] = subject

    # Get the thread to find the message to reply to
    try:
        thread = service.users().threads().get(
            userId="me",
            id=thread_id,
            format="metadata",
            metadataHeaders=["Message-Id", "Subject", "From"],
        ).execute()

        messages = thread.get("messages", [])
        if messages:
            last_msg = messages[-1]
            headers = {h["name"]: h["value"] for h in last_msg.get("payload", {}).get("headers", [])}

            # Set reply headers
            msg_id = headers.get("Message-Id", "")
            if msg_id:
                message["In-Reply-To"] = msg_id
                message["References"] = msg_id
            if not to:
                message["To"] = headers.get("From", "")
            if not subject:
                orig_subject = headers.get("Subject", "")
                if not orig_subject.lower().startswith("re:"):
                    orig_subject = f"Re: {orig_subject}"
                message["Subject"] = orig_subject
    except Exception as e:
        print(f"[GoogleAPI] Error fetching thread for draft: {e}")

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    draft = service.users().drafts().create(
        userId="me",
        body={
            "message": {
                "raw": raw,
                "threadId": thread_id,
            }
        },
    ).execute()

    return {
        "draft_id": draft.get("id", ""),
        "message_id": draft.get("message", {}).get("id", ""),
    }


# ---------------------------------------------------------------------------
# Calendar — Availability
# ---------------------------------------------------------------------------

async def get_calendar_events(
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Fetch calendar events for availability checking.

    Returns list of {summary, start, end, status}
    """
    service = await get_calendar_service()
    if not service:
        raise RuntimeError("Calendar API not available")

    now = datetime.now(timezone.utc)
    if not time_min:
        time_min = now.isoformat()
    if not time_max:
        time_max = (now + timedelta(days=7)).isoformat()

    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])
    return [
        {
            "summary": e.get("summary", "(no title)"),
            "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
            "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
            "status": e.get("status", "confirmed"),
        }
        for e in events
    ]
