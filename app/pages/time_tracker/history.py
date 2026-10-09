#!/usr/bin/env python3
"""
The "This Project" and "History" lists - which entries a scope shows, the
summary line above them, each row as the web page draws it, and checking
an added/edited entry. No Qt (tests/test_time_tracker_reports.py).

Scopes: SCOPE_CURRENT follows whatever project Resolve has open (all "This
Project" ever shows, and History's default), SCOPE_ALL is every project,
anything else is one pinned project name. Picking a project or All is a
deliberate override that Resolve's own project changes don't undo.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from .reports import _day_segments
from .ui_utils import format_clock, format_date, format_duration_words

SCOPE_CURRENT = "__current__"
SCOPE_ALL = "__all__"
WEEKS_SHOWN = 6     # This Project's chart: its last this many weeks (Monday-based)


def entries_for(data_mgr, scope, current_project):
    entries = data_mgr.closed_entries_sorted()
    if scope == SCOPE_ALL:
        return entries
    if scope == SCOPE_CURRENT:
        # Nothing detected yet (Resolve still on the Project Manager, say):
        # show nothing rather than every project's history at once.
        return [e for e in entries if current_project and e.get("project") == current_project]
    return [e for e in entries if e.get("project") == scope]


def summary(data_mgr, scope, current_project, entries):
    if scope == SCOPE_CURRENT and not current_project:
        return "No project detected in Resolve"
    where = ("all projects" if scope == SCOPE_ALL
             else f'"{current_project if scope == SCOPE_CURRENT else scope}"')
    total = sum(data_mgr.duration_seconds(e) for e in entries if not e.get("hidden", False))
    return f"{format_duration_words(total)} tracked on {where}" if total else f"No time tracked yet on {where}"


def _counted(data_mgr, project):
    """A project's finished entries the totals count: not hidden, as summary() counts them."""
    return [e for e in data_mgr.closed_entries_sorted() if e.get("project") == project and not e.get("hidden")]


def _week_label(start):
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.day} – {end.day} {end:%b}"
    return f"{start.day} {start:%b} – {end.day} {end:%b}"


def project_card(data_mgr, project, today=None):
    """This Project's card: the project's total, sessions, days worked,
    since when, and its last WEEKS_SHOWN weeks (oldest first, empty ones
    too) - from finished entries; the page adds a running session. None
    without a project."""
    if not project:
        return None
    today = today or datetime.now().date()
    entries = _counted(data_mgr, project)
    days = defaultdict(float)
    for entry in entries:
        for day, seconds in _day_segments(entry, data_mgr):
            days[day] += seconds
    worked = sorted(d for d, s in days.items() if s > 0)
    this_week = today - timedelta(days=today.weekday())
    weeks = []
    if worked:
        first = worked[0] - timedelta(days=worked[0].weekday())
        start = max(first, this_week - timedelta(weeks=WEEKS_SHOWN - 1))
        while start <= this_week:
            seconds = sum(s for d, s in days.items() if start <= d < start + timedelta(days=7))
            weeks.append({"label": _week_label(start), "seconds": seconds, "current": start == this_week})
            start += timedelta(weeks=1)
        cut = first < this_week - timedelta(weeks=WEEKS_SHOWN - 1)
    else:
        cut = False
    return {
        "project": project,
        "seconds": sum(days.values()),
        "sessions": len(entries),
        "days": len(worked),
        "since": f"{worked[0].day} {worked[0]:%b %Y}" if worked else "",
        "weeks": weeks,
        "weeks_title": f"Last {WEEKS_SHOWN} weeks" if cut else "By week",
        "this_week": _week_label(this_week),        # for a first session's bar, before any week has time
    }


def project_so_far(data_mgr, project, today=None):
    """(before today, earlier today) in seconds: a project's finished,
    counted entries split at midnight - the Tracker's running total, with
    the session going on now added by the page."""
    today = today or datetime.now().date()
    before = earlier = 0.0
    for entry in _counted(data_mgr, project):
        for day, seconds in _day_segments(entry, data_mgr):
            if day < today:
                before += seconds
            elif day == today:
                earlier += seconds
    return before, earlier


def row(data_mgr, entry):
    start = entry["start"]
    try:
        weekday = datetime.fromisoformat(start).strftime("%a")
    except ValueError:
        weekday = ""
    return {
        "id": entry["id"],
        "date": format_date(start),
        "weekday": weekday,
        "project": entry.get("project", ""),
        "start": format_clock(start),
        "end": format_clock(entry.get("end")),
        "duration": format_duration_words(data_mgr.duration_seconds(entry)),
        "notes": entry.get("notes", ""),
        "hidden": bool(entry.get("hidden", False)),
        # For the edit form: full timestamps, to the second.
        "start_iso": start,
        "end_iso": entry.get("end") or "",
    }


def view(data_mgr, scope, current_project):
    entries = entries_for(data_mgr, scope, current_project)
    return {
        "scope": scope,
        "summary": summary(data_mgr, scope, current_project, entries),
        "rows": [row(data_mgr, e) for e in entries],
    }


def _parse(value):
    """An <input type="datetime-local"> value ("2026-09-26T14:05" or with
    seconds) -> datetime, or None."""
    try:
        return datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def clean_entry(data):
    """(fields, None) ready for DataManager.add_manual_entry/update_entry,
    or (None, (field, message)) naming what's wrong."""
    data = data or {}
    project = str(data.get("project") or "").strip()
    if not project:
        return None, ("project", "Enter a project name.")
    start, end = _parse(data.get("start")), _parse(data.get("end"))
    if start is None:
        return None, ("start", "Enter a start date and time.")
    if end is None:
        return None, ("end", "Enter an end date and time.")
    if end <= start:
        return None, ("end", "End time must be after start time.")
    return {
        "project": project,
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "notes": str(data.get("notes") or "").strip(),
    }, None
