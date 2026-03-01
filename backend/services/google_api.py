"""Google API wrappers — Gmail, Calendar, Docs, Sheets, Slides, Forms, Drive.

Provides a clean interface for interacting with all Google Workspace APIs
using stored OAuth2 credentials.

Performance notes:
  - Service objects are built fresh each time because httplib2 is NOT
    thread-safe: sharing a service across thread-pool workers causes
    SSL errors when concurrent batch requests re-use the same socket.
  - BatchHttpRequest is used for bulk operations (1 HTTP round-trip for N calls).
  - All synchronous google-api-python-client calls are offloaded to a thread
    pool via asyncio.run_in_executor so they never block the event loop.
"""

from __future__ import annotations

import asyncio
import base64
import email.mime.text
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from backend import database as db
from backend.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET


# ---------------------------------------------------------------------------
# Thread-pool for offloading synchronous google-api-python-client calls
# ---------------------------------------------------------------------------
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gmail")


async def _run_sync(fn, *args):
    """Run a synchronous function in the thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn, *args)


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


# ---------------------------------------------------------------------------
# Cached credentials — avoid DB reads + token refresh on every call.
# Service objects are built fresh each time because httplib2 is NOT
# thread-safe: sharing a service across thread-pool workers causes
# SSL errors when concurrent batch requests re-use the same socket.
# Building a service is very fast (~1 ms); the credential refresh is
# what was slow and is now cached.
# ---------------------------------------------------------------------------
_cached_creds = None
_cached_creds_ts: float = 0
_CREDS_TTL = 240  # 4 min (refresh before 5-min Google token expiry)


async def _get_cached_creds():
    """Return cached credentials, refreshing from DB only when stale."""
    global _cached_creds, _cached_creds_ts
    now = time.time()
    if _cached_creds and (now - _cached_creds_ts) < _CREDS_TTL:
        # Quick expiry check (no DB hit)
        if not _cached_creds.expired:
            return _cached_creds
    # Rebuild from DB
    creds = await _get_credentials()
    if creds:
        _cached_creds = creds
        _cached_creds_ts = now
    return creds


async def get_gmail_service():
    """Build a fresh Gmail API client (thread-safe: each gets own HTTP pool)."""
    creds = await _get_cached_creds()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds)


async def get_calendar_service():
    """Build a fresh Calendar API client."""
    creds = await _get_cached_creds()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds)


async def get_docs_service():
    """Build a fresh Google Docs API client."""
    creds = await _get_cached_creds()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("docs", "v1", credentials=creds)


async def get_sheets_service():
    """Build a fresh Google Sheets API client."""
    creds = await _get_cached_creds()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=creds)


async def get_slides_service():
    """Build a fresh Google Slides API client."""
    creds = await _get_cached_creds()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("slides", "v1", credentials=creds)


async def get_forms_service():
    """Build a fresh Google Forms API client."""
    creds = await _get_cached_creds()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("forms", "v1", credentials=creds)


async def get_drive_service():
    """Build a fresh Google Drive API client."""
    creds = await _get_cached_creds()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Gmail — Inbox listing (FAST: BatchHttpRequest)
# ---------------------------------------------------------------------------

async def fetch_inbox_threads_api(max_results: int = 50) -> list[dict]:
    """Fetch inbox threads via Gmail API using BatchHttpRequest.

    1.  threads().list()  — single call to get thread IDs + snippets  (~200 ms)
    2.  BatchHttpRequest   — 1 HTTP round-trip for ALL metadata      (~400 ms)
    Total: ~0.6 s instead of ~12 s for 50 threads.

    Returns list of dicts: {id, subject, sender, snippet, date, unread}
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available — not authenticated")

    # Step 1 — List thread IDs (fast, single call)
    def _list():
        return service.users().threads().list(
            userId="me",
            maxResults=max_results,
            labelIds=["INBOX"],
        ).execute()

    results = await _run_sync(_list)
    threads_raw = results.get("threads", [])[:max_results]

    if not threads_raw:
        return []

    # Step 2 — Batch-fetch metadata in chunks to avoid Gmail concurrency limits
    thread_data: dict[str, dict] = {}
    CHUNK_SIZE = 20  # Gmail rate-limits concurrent requests per user

    def _batch_callback(request_id: str, response: dict, exception):
        if exception:
            # Silently skip rate-limited threads (they just won't appear in list)
            return
        thread_data[request_id] = response

    def _batch_fetch_metadata():
        for i in range(0, len(threads_raw), CHUNK_SIZE):
            chunk = threads_raw[i:i + CHUNK_SIZE]
            batch = service.new_batch_http_request(callback=_batch_callback)
            for t in chunk:
                batch.add(
                    service.users().threads().get(
                        userId="me",
                        id=t["id"],
                        format="metadata",
                        metadataHeaders=["Subject", "From", "Date"],
                    ),
                    request_id=t["id"],
                )
            batch.execute()

    await _run_sync(_batch_fetch_metadata)

    # Step 3 — Parse results in order
    threads = []
    for t in threads_raw:
        td = thread_data.get(t["id"])
        if not td:
            continue
        messages = td.get("messages", [])
        if not messages:
            continue

        first_msg = messages[0]
        headers = {
            h["name"]: h["value"]
            for h in first_msg.get("payload", {}).get("headers", [])
        }
        labels = first_msg.get("labelIds", [])

        threads.append({
            "id": t["id"],
            "subject": headers.get("Subject", "(no subject)"),
            "sender": headers.get("From", ""),
            "snippet": td.get("snippet", "")[:200],
            "date": headers.get("Date", ""),
            "unread": "UNREAD" in labels,
        })

    return threads


# ---------------------------------------------------------------------------
# Gmail — Thread content
# ---------------------------------------------------------------------------

async def fetch_thread_content_api(thread_id: str) -> dict:
    """Fetch full content of a single thread via Gmail API.

    Returns {thread_id, subject, sender, messages: [{from, date, body}], message_count}
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available")

    def _fetch():
        return service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full",
        ).execute()

    thread = await _run_sync(_fetch)
    return _parse_thread_content(thread_id, thread)


async def batch_fetch_thread_contents(thread_ids: list[str]) -> dict[str, dict | None]:
    """Fetch full content of multiple threads in ONE HTTP request via BatchHttpRequest.

    Returns {thread_id: parsed_content_or_None}
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available")

    raw_data: dict[str, dict | None] = {}

    def _batch_cb(request_id: str, response: dict, exception):
        if exception:
            print(f"[GoogleAPI] Batch content error for {request_id}: {exception}")
            raw_data[request_id] = None
            return
        raw_data[request_id] = response

    def _batch_fetch():
        for i in range(0, len(thread_ids), CHUNK_SIZE):
            chunk = thread_ids[i:i + CHUNK_SIZE]
            batch = service.new_batch_http_request(callback=_batch_cb)
            for tid in chunk:
                batch.add(
                    service.users().threads().get(
                        userId="me",
                        id=tid,
                        format="full",
                    ),
                    request_id=tid,
                )
            batch.execute()

    CHUNK_SIZE = 15  # Smaller chunks for full content (heavier payloads)
    await _run_sync(_batch_fetch)

    # Parse all results
    results: dict[str, dict | None] = {}
    for tid in thread_ids:
        raw = raw_data.get(tid)
        if raw:
            try:
                results[tid] = _parse_thread_content(tid, raw)
            except Exception:
                results[tid] = None
        else:
            results[tid] = None

    return results


def _parse_thread_content(thread_id: str, thread: dict) -> dict:
    """Parse a threads().get(format='full') response into our standard shape."""
    messages_raw = thread.get("messages", [])
    messages = []
    subject = ""
    sender = ""

    for msg in messages_raw:
        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
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
# Gmail — Sent emails (for profiling) — also batch-optimised
# ---------------------------------------------------------------------------

async def fetch_sent_emails(max_results: int = 20) -> list[dict]:
    """Fetch recent sent emails for user profiling using BatchHttpRequest.

    Returns list of {subject, to, body, date}
    """
    service = await get_gmail_service()
    if not service:
        raise RuntimeError("Gmail API not available")

    def _list_sent():
        return service.users().messages().list(
            userId="me",
            maxResults=max_results,
            labelIds=["SENT"],
        ).execute()

    results = await _run_sync(_list_sent)
    messages_raw = results.get("messages", [])[:max_results]

    if not messages_raw:
        return []

    msg_data: dict[str, dict] = {}

    def _batch_cb(request_id: str, response: dict, exception):
        if exception:
            return
        msg_data[request_id] = response

    def _batch_fetch():
        batch = service.new_batch_http_request(callback=_batch_cb)
        for m in messages_raw:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=m["id"],
                    format="full",
                ),
                request_id=m["id"],
            )
        batch.execute()

    await _run_sync(_batch_fetch)

    sent_emails = []
    for m in messages_raw:
        msg = msg_data.get(m["id"])
        if not msg:
            continue
        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        body = _extract_body(msg.get("payload", {}))
        sent_emails.append({
            "subject": headers.get("Subject", ""),
            "to": headers.get("To", ""),
            "body": body[:2000],
            "date": headers.get("Date", ""),
        })

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
        def _get_thread():
            return service.users().threads().get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["Message-Id", "Subject", "From"],
            ).execute()

        thread = await _run_sync(_get_thread)

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

    def _create():
        return service.users().drafts().create(
            userId="me",
            body={
                "message": {
                    "raw": raw,
                    "threadId": thread_id,
                }
            },
        ).execute()

    draft = await _run_sync(_create)

    return {
        "draft_id": draft.get("id", ""),
        "message_id": draft.get("message", {}).get("id", ""),
    }


async def delete_duplicate_drafts(thread_id: str, keep_draft_id: str) -> int:
    """Delete any drafts for `thread_id` EXCEPT the one with `keep_draft_id`.

    Returns the number of deleted drafts.
    """
    service = await get_gmail_service()
    if not service:
        return 0

    try:
        def _list_drafts():
            return service.users().drafts().list(userId="me").execute()

        result = await _run_sync(_list_drafts)
        drafts = result.get("drafts", [])

        deleted = 0
        for d in drafts:
            d_id = d.get("id", "")
            if d_id == keep_draft_id:
                continue  # keep this one

            # Fetch draft detail to check if it belongs to this thread
            try:
                def _get_draft(did=d_id):
                    return service.users().drafts().get(userId="me", id=did).execute()

                detail = await _run_sync(_get_draft)
                msg = detail.get("message", {})
                if msg.get("threadId") == thread_id:
                    def _delete_draft(did=d_id):
                        service.users().drafts().delete(userId="me", id=did).execute()

                    await _run_sync(_delete_draft)
                    deleted += 1
                    print(f"[GoogleAPI] Deleted duplicate draft {d_id} for thread {thread_id}")
            except Exception as e:
                print(f"[GoogleAPI] Error checking/deleting draft {d_id}: {e}")

        return deleted
    except Exception as e:
        print(f"[GoogleAPI] Error listing drafts for dedup: {e}")
        return 0


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

    def _list_events():
        return service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

    events_result = await _run_sync(_list_events)

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
