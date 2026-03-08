"""
Calendar tool — check availability and add trips to calendar.
Uses in-memory mock; swap in Google Calendar API when configured.
"""

import os
import json
from datetime import datetime
from pathlib import Path

CALENDAR_FILE = Path.home() / ".travel_agent" / "calendar.json"


def _load_calendar() -> list[dict]:
    if CALENDAR_FILE.exists():
        return json.loads(CALENDAR_FILE.read_text())
    return []


def _save_calendar(events: list[dict]) -> None:
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_FILE.write_text(json.dumps(events, indent=2))


def check_availability(start_date: str, end_date: str) -> dict:
    """Check if the user is available for travel during a date range."""
    events = _load_calendar()
    conflicts = []

    try:
        fmt = "%Y-%m-%d"
        req_start = datetime.strptime(start_date, fmt)
        req_end = datetime.strptime(end_date, fmt)

        for event in events:
            ev_start = datetime.strptime(event["start_date"], fmt)
            ev_end = datetime.strptime(event["end_date"], fmt)
            if not (req_end < ev_start or req_start > ev_end):
                conflicts.append(event)
    except ValueError as e:
        return {"status": "error", "message": f"Invalid date format: {e}. Use YYYY-MM-DD."}

    return {
        "status": "success",
        "start_date": start_date,
        "end_date": end_date,
        "available": len(conflicts) == 0,
        "conflicts": conflicts,
        "message": "No conflicts found — you're free!" if not conflicts else f"{len(conflicts)} conflict(s) found.",
    }


def add_to_calendar(
    title: str,
    start_date: str,
    end_date: str,
    description: str = "",
    location: str = "",
) -> dict:
    """Add a trip or event to the user's calendar."""
    events = _load_calendar()
    event = {
        "id": f"EVT{len(events)+1:04d}",
        "title": title,
        "start_date": start_date,
        "end_date": end_date,
        "description": description,
        "location": location,
        "created_at": datetime.now().isoformat(),
    }
    events.append(event)
    _save_calendar(events)

    return {
        "status": "success",
        "event": event,
        "message": f"'{title}' added to your calendar ({start_date} → {end_date}).",
    }
