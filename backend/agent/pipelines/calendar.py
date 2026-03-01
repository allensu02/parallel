"""Google Calendar pipeline — manage events via the Calendar API.

Operations are thin async wrappers around the Google Calendar API.
The LLM plans which operations to call; the gsuite_executor runs them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend.agent.gsuite_executor import plan_and_execute, OpDef
from backend.services.google_api import get_calendar_service, _run_sync


# ---------------------------------------------------------------------------
# API Operations
# ---------------------------------------------------------------------------


async def list_events(
    time_min: str = "",
    time_max: str = "",
    calendar_id: str = "primary",
    max_results: int = 20,
) -> dict:
    """List calendar events in a time range. Returns {events: [{id, summary, start, end, location, attendees}]}.

    time_min/time_max should be ISO 8601 strings. Defaults to next 7 days.
    """
    service = await get_calendar_service()
    if not service:
        raise RuntimeError("Calendar API not available — not authenticated")

    now = datetime.now(timezone.utc)
    if not time_min:
        time_min = now.isoformat()
    if not time_max:
        time_max = (now + timedelta(days=7)).isoformat()

    def _list():
        return service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

    result = await _run_sync(_list)
    events = []
    for e in result.get("items", []):
        attendees = [
            {
                "email": a.get("email", ""),
                "responseStatus": a.get("responseStatus", ""),
            }
            for a in e.get("attendees", [])
        ]
        events.append({
            "id": e.get("id", ""),
            "summary": e.get("summary", "(no title)"),
            "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
            "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
            "location": e.get("location", ""),
            "description": e.get("description", ""),
            "status": e.get("status", "confirmed"),
            "attendees": attendees,
            "htmlLink": e.get("htmlLink", ""),
        })

    return {"events": events}


async def create_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    attendees: list[str] | None = None,
    location: str = "",
    calendar_id: str = "primary",
    timezone_str: str = "America/Los_Angeles",
) -> dict:
    """Create a calendar event. Returns {eventId, summary, start, end, htmlLink}.

    start/end should be ISO 8601 datetime strings (e.g. '2026-02-15T10:00:00').
    attendees is a list of email addresses.
    """
    service = await get_calendar_service()
    if not service:
        raise RuntimeError("Calendar API not available — not authenticated")

    event_body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": timezone_str},
        "end": {"dateTime": end, "timeZone": timezone_str},
    }

    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location
    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    def _create():
        return service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            sendUpdates="all" if attendees else "none",
        ).execute()

    event = await _run_sync(_create)
    return {
        "eventId": event.get("id", ""),
        "summary": event.get("summary", summary),
        "start": event.get("start", {}).get("dateTime", start),
        "end": event.get("end", {}).get("dateTime", end),
        "htmlLink": event.get("htmlLink", ""),
    }


async def update_event(
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
    timezone_str: str = "America/Los_Angeles",
) -> dict:
    """Update an existing calendar event. Only provided fields are changed.
    Returns {eventId, summary, start, end, htmlLink}.
    """
    service = await get_calendar_service()
    if not service:
        raise RuntimeError("Calendar API not available — not authenticated")

    # Get current event first
    def _get():
        return service.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()

    current = await _run_sync(_get)

    # Apply updates
    if summary:
        current["summary"] = summary
    if start:
        current["start"] = {"dateTime": start, "timeZone": timezone_str}
    if end:
        current["end"] = {"dateTime": end, "timeZone": timezone_str}
    if description:
        current["description"] = description
    if location:
        current["location"] = location
    if attendees is not None:
        current["attendees"] = [{"email": email} for email in attendees]

    def _update():
        return service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=current,
            sendUpdates="all" if attendees else "none",
        ).execute()

    event = await _run_sync(_update)
    return {
        "eventId": event.get("id", event_id),
        "summary": event.get("summary", ""),
        "start": event.get("start", {}).get("dateTime", ""),
        "end": event.get("end", {}).get("dateTime", ""),
        "htmlLink": event.get("htmlLink", ""),
    }


async def delete_event(
    event_id: str,
    calendar_id: str = "primary",
) -> dict:
    """Delete a calendar event. Returns {eventId, deleted}."""
    service = await get_calendar_service()
    if not service:
        raise RuntimeError("Calendar API not available — not authenticated")

    def _delete():
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates="all",
        ).execute()

    await _run_sync(_delete)
    return {"eventId": event_id, "deleted": True}


async def get_event(
    event_id: str,
    calendar_id: str = "primary",
) -> dict:
    """Get details of a specific event. Returns full event details."""
    service = await get_calendar_service()
    if not service:
        raise RuntimeError("Calendar API not available — not authenticated")

    def _get():
        return service.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()

    e = await _run_sync(_get)
    attendees = [
        {"email": a.get("email", ""), "responseStatus": a.get("responseStatus", "")}
        for a in e.get("attendees", [])
    ]
    return {
        "eventId": e.get("id", event_id),
        "summary": e.get("summary", ""),
        "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
        "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
        "location": e.get("location", ""),
        "description": e.get("description", ""),
        "status": e.get("status", ""),
        "attendees": attendees,
        "htmlLink": e.get("htmlLink", ""),
    }


async def find_free_time(
    date: str = "",
    duration_minutes: int = 60,
    calendar_id: str = "primary",
) -> dict:
    """Find available time slots on a given date.

    date should be YYYY-MM-DD format. Defaults to today.
    Returns {date, freeSlots: [{start, end}]}.
    """
    service = await get_calendar_service()
    if not service:
        raise RuntimeError("Calendar API not available — not authenticated")

    now = datetime.now(timezone.utc)
    if not date:
        date = now.strftime("%Y-%m-%d")

    # Parse the date and set time range for the full day (9 AM to 6 PM)
    day_start = datetime.fromisoformat(f"{date}T09:00:00")
    day_end = datetime.fromisoformat(f"{date}T18:00:00")

    # Get events for that day
    def _list():
        return service.events().list(
            calendarId=calendar_id,
            timeMin=day_start.isoformat() + "Z",
            timeMax=day_end.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        ).execute()

    result = await _run_sync(_list)
    events = result.get("items", [])

    # Build busy intervals
    busy_intervals = []
    for e in events:
        start_str = e.get("start", {}).get("dateTime", "")
        end_str = e.get("end", {}).get("dateTime", "")
        if start_str and end_str:
            try:
                s = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                en = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                busy_intervals.append((s, en))
            except ValueError:
                continue

    # Sort by start time
    busy_intervals.sort(key=lambda x: x[0])

    # Find free slots
    free_slots = []
    current_time = day_start.replace(tzinfo=timezone.utc)
    required = timedelta(minutes=duration_minutes)

    for busy_start, busy_end in busy_intervals:
        # Normalize to UTC if needed
        if busy_start.tzinfo is None:
            busy_start = busy_start.replace(tzinfo=timezone.utc)
        if busy_end.tzinfo is None:
            busy_end = busy_end.replace(tzinfo=timezone.utc)

        gap = busy_start - current_time
        if gap >= required:
            free_slots.append({
                "start": current_time.isoformat(),
                "end": busy_start.isoformat(),
                "duration_minutes": int(gap.total_seconds() / 60),
            })
        current_time = max(current_time, busy_end)

    # Check remaining time after last event
    day_end_utc = day_end.replace(tzinfo=timezone.utc)
    remaining = day_end_utc - current_time
    if remaining >= required:
        free_slots.append({
            "start": current_time.isoformat(),
            "end": day_end_utc.isoformat(),
            "duration_minutes": int(remaining.total_seconds() / 60),
        })

    return {
        "date": date,
        "freeSlots": free_slots,
        "busyCount": len(busy_intervals),
    }


# ---------------------------------------------------------------------------
# Operation definitions for the LLM planner
# ---------------------------------------------------------------------------

CALENDAR_OPERATIONS: list[OpDef] = [
    {
        "name": "list_events",
        "description": "List calendar events in a time range (defaults to next 7 days)",
        "parameters": "time_min: str = '', time_max: str = '', calendar_id: str = 'primary', max_results: int = 20",
        "fn": list_events,
    },
    {
        "name": "create_event",
        "description": "Create a new calendar event with optional attendees and location. start/end are ISO 8601 datetimes.",
        "parameters": "summary: str, start: str, end: str, description: str = '', attendees: list[str] = None, location: str = '', calendar_id: str = 'primary', timezone_str: str = 'America/Los_Angeles'",
        "fn": create_event,
    },
    {
        "name": "update_event",
        "description": "Update an existing event. Only provide fields that should change.",
        "parameters": "event_id: str, summary: str = '', start: str = '', end: str = '', description: str = '', location: str = '', attendees: list[str] = None, calendar_id: str = 'primary', timezone_str: str = 'America/Los_Angeles'",
        "fn": update_event,
    },
    {
        "name": "delete_event",
        "description": "Delete a calendar event",
        "parameters": "event_id: str, calendar_id: str = 'primary'",
        "fn": delete_event,
    },
    {
        "name": "get_event",
        "description": "Get full details of a specific event",
        "parameters": "event_id: str, calendar_id: str = 'primary'",
        "fn": get_event,
    },
    {
        "name": "find_free_time",
        "description": "Find available time slots on a given date (9 AM - 6 PM). Returns free slots of at least the specified duration.",
        "parameters": "date: str = '' (YYYY-MM-DD), duration_minutes: int = 60, calendar_id: str = 'primary'",
        "fn": find_free_time,
    },
]

CALENDAR_OP_MAP = {op["name"]: op["fn"] for op in CALENDAR_OPERATIONS}


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class CalendarPipeline(Pipeline):
    pipeline_type = "calendar"
    display_name = "Google Calendar"
    description = (
        "Manage Google Calendar events. Can create, update, delete events, "
        "find free time slots, and check availability."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        import re
        keywords = [
            r"\bgoogle calendar\b", r"\bschedule a\b", r"\bcreate.{0,10}meeting\b",
            r"\bcheck.{0,10}calendar\b", r"\bfree time\b", r"\bavailability\b",
            r"\bcreate.{0,10}event\b", r"\bappointment\b",
        ]
        desc_lower = task_description.lower()
        return any(re.search(kw, desc_lower) for kw in keywords)

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        task_description = params.get("instruction", params.get("description", ""))
        await plan_and_execute(
            run_id=run_id,
            job_id=job_id,
            pipeline_type="calendar",
            service_name="Google Calendar",
            task_description=task_description,
            available_ops=CALENDAR_OPERATIONS,
            op_map=CALENDAR_OP_MAP,
        )
