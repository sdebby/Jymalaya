"""
Plan days the athlete asked the coach to change, kept across a plan rebuild.

Every successful coach plan edit saves the edited day here -- the day's full
session list AFTER the edit, keyed by its date (a move saves both days). Saving
the day's final state rather than the edit operations is deliberate: the tools
address sessions by (date, session_index) in the plan they edited, and those
indices mean nothing in a freshly generated plan. An empty session list is a
day off the athlete asked for.

When the plan is regenerated with the requests kept, the payload carries these
days as fixed (athlete_requested_days, rule 24 of the plan prompt) and
recommend_service copies them back over Claude's output -- the same
enforce-in-code split as _enforce_rest_days.

A request lives until its day has passed, and never longer than MAX_AGE_DAYS:
a request for a day that is over can no longer shape any plan.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import date, datetime, timedelta, timezone

from app.config import ROOT

log = logging.getLogger("jymalaya.plan_requests")

REQUESTS_FILE = ROOT / "data" / "plan_requests.json"

MAX_AGE_DAYS = 7
# The notes are what the athlete asked for, in their terms. A day edited over
# several turns collects one per turn; only the latest few are worth sending.
MAX_NOTES = 3
MAX_NOTE_CHARS = 300


def _load() -> dict:
    """The stored requests keyed by date; empty when absent or unreadable."""
    try:
        data = json.loads(REQUESTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    if not data:
        clear_requests()
        return
    REQUESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def pin_days(plan: dict, dates: list[str], note: str | None = None) -> None:
    """
    Save each date's current sessions from `plan` as an athlete request.

    Called after the edited plan is written, so the day is stored exactly as
    the athlete now sees it. Re-editing a day replaces its sessions and adds
    the new note; the request's age restarts from the latest edit.
    """
    data = _load()
    now = datetime.now(timezone.utc).isoformat()
    note = (note or "").strip()[:MAX_NOTE_CHARS]
    by_date = {d.get("date"): d for d in plan.get("days") or [] if isinstance(d, dict)}
    for day_date in dict.fromkeys(dates):
        day = by_date.get(day_date)
        if day is None:
            continue
        entry = data.get(day_date) if isinstance(data.get(day_date), dict) else {}
        notes = [n for n in entry.get("notes") or [] if isinstance(n, str)]
        if note and note not in notes:
            notes.append(note)
        data[day_date] = {
            "sessions": copy.deepcopy(day.get("sessions") or []),
            "notes": notes[-MAX_NOTES:],
            "requested_at": now,
        }
    _save(data)


def _is_active(day_date: str, entry: dict, today: date, now: datetime) -> bool:
    try:
        if date.fromisoformat(day_date) < today:
            return False
        requested_at = datetime.fromisoformat(entry["requested_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    return now - requested_at < timedelta(days=MAX_AGE_DAYS)


def active_requests(today: date | None = None) -> list[dict]:
    """
    The requests still in force, oldest date first. Expired ones -- their day
    has passed, or they are older than MAX_AGE_DAYS -- are deleted from the
    file on the way.
    """
    today = today or date.today()
    now = datetime.now(timezone.utc)
    data = _load()
    active = {
        d: e
        for d, e in data.items()
        if isinstance(e, dict) and _is_active(d, e, today, now)
    }
    if len(active) != len(data):
        _save(active)
    return [
        {
            "date": d,
            "day_name": date.fromisoformat(d).strftime("%A"),
            "sessions": e.get("sessions") or [],
            "notes": e.get("notes") or [],
            "requested_at": e.get("requested_at"),
        }
        for d, e in sorted(active.items())
    ]


def clear_requests() -> bool:
    """Delete every saved request. True when there was a file to delete."""
    try:
        REQUESTS_FILE.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        log.warning("Could not delete %s: %s", REQUESTS_FILE, e)
        return False
