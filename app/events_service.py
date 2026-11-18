"""
Events CRUD orchestration over InfluxDB. Events live in the `goals` measurement
(PRD section 5.1); each is a point tagged by goal_id, reads take the latest per id.
The storage names predate the goal -> event rename and are kept on purpose:
renaming them would orphan every saved event. The API calls the id event_id.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

from app.influx.client import InfluxRepo
from app.influx.queries import all_events, event_by_id


def _row_to_event(row: dict) -> dict:
    """Turn a pivoted Influx row into the Event API shape (deserializing types)."""
    sub_raw = row.get("sub_goals")
    try:
        sub_goals = json.loads(sub_raw) if sub_raw else []
    except (ValueError, TypeError):
        sub_goals = []

    def _float(key):
        v = row.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    tfs = _float("target_finish_time_sec")
    return {
        "event_id": row.get("goal_id"),
        "name": row.get("name") or "",
        "type": row.get("type") or "custom",
        "race_date": row.get("race_date") or "",
        "location_lat": _float("location_lat"),
        "location_lon": _float("location_lon"),
        "location_name": row.get("location_name") or None,
        "target_finish_time_sec": int(tfs) if tfs is not None else None,
        "sub_goals": sub_goals,
        "icon": row.get("icon") or None,
        "website": row.get("website") or None,
        "notes": row.get("notes") or None,
        # Every read value is stringified by the query, and an event saved before
        # the field existed has no column at all: only an explicit "false" opts
        # out, so no migration is needed.
        "participating": str(row.get("participating")).strip().lower() != "false",
    }


def is_participating(event: dict) -> bool:
    """False only for an event the athlete marked as calendar-only."""
    return event.get("participating", True) is not False


def list_events() -> list[dict]:
    repo = InfluxRepo()
    try:
        rows = repo.query(all_events())
    finally:
        repo.close()
    events = [_row_to_event(r) for r in rows if r.get("goal_id")]
    events.sort(key=lambda e: e.get("race_date") or "9999")
    return events


def get_event(event_id: str) -> dict | None:
    repo = InfluxRepo()
    try:
        rows = repo.query(event_by_id(event_id))
    finally:
        repo.close()
    rows = [r for r in rows if r.get("goal_id") == event_id]
    return _row_to_event(rows[0]) if rows else None


def create_event(payload: dict) -> dict:
    event = dict(payload)
    event["event_id"] = uuid.uuid4().hex
    repo = InfluxRepo()
    try:
        repo.write_event(event)
    finally:
        repo.close()
    return event


def update_event(event_id: str, payload: dict) -> dict | None:
    if get_event(event_id) is None:
        return None
    event = dict(payload)
    event["event_id"] = event_id
    repo = InfluxRepo()
    try:
        # Replace, don't append: drop prior points first so cleared fields
        # (e.g. an unset target time, which isn't re-written) actually clear,
        # rather than the read picking up the stale last-per-field value.
        repo.delete_event(event_id)
        repo.write_event(event)
    finally:
        repo.close()
    return event


def delete_event(event_id: str) -> bool:
    if get_event(event_id) is None:
        return False
    repo = InfluxRepo()
    try:
        repo.delete_event(event_id)
    finally:
        repo.close()
    return True


def active_event() -> dict | None:
    """
    The nearest future race the athlete is racing (the primary_goal passed to
    Claude in M6). An event marked not participating stays on the calendar but is
    never a goal.
    """
    today = date.today().isoformat()
    upcoming = [
        e
        for e in list_events()
        if (e.get("race_date") or "") >= today and is_participating(e)
    ]
    return upcoming[0] if upcoming else None


def last_race(within_days: int | None = None) -> dict | None:
    """
    The most recently passed race, or None -- the mirror of active_event.

    An event has no completed flag: "past" is only race_date < today. The date is
    therefore parsed, not string-compared. _row_to_event defaults a missing
    race_date to "", and "" < today is True for strings, so a plain comparison
    would hand back a date-less event as the athlete's last race. (active_event's
    >= comparison excludes "" by the same accident, in its favour.)

    A race dated today is NOT a past race: it is still active_event, and rule 5
    of the plan prompt owns race day.

    `within_days` bounds how far back to look; None means no bound. The caller
    owns that number -- how long a finished race still shapes training is
    coaching policy, not storage.
    """
    today = date.today()
    past = []
    for event in list_events():
        if not is_participating(event):
            continue  # calendar-only: the athlete did not race it, nothing to recover from
        try:
            raced = date.fromisoformat(event.get("race_date") or "")
        except (ValueError, TypeError):
            continue  # malformed or unset date: not a race that happened
        if raced >= today:
            continue
        if within_days is not None and (today - raced).days > within_days:
            continue
        past.append((raced, event.get("event_id") or "", event))
    if not past:
        return None
    # Newest first; event_id breaks a same-day tie so the payload is stable
    # between runs. Two races on one day is pathological anyway, and the
    # measured load aggregates both of them regardless of which name wins.
    return max(past, key=lambda item: (item[0], item[1]))[2]
