"""
The athlete's weekly training schedule: per-day availability, preferred
disciplines, session count and session type. The week-level constraints
(the two limits, long ride/run days, brick days) are not stored — they are
computed from the days by _derive_constraints and ride along read-only.

Single source of truth, edited from the dashboard Settings and stored as
data/schedule.json. This replaced config/schedule.yaml on 2026-07-27 — the
schedule used to be hand-authored YAML with only rest days and the two week
limits overridable from the UI (the old app/schedule_overrides.py), which meant
the same values lived in three places and a saved rest-day selection could
silently reopen a YAML rest day with no per-day detail to fall back on.

DEFAULT_SCHEDULE is the factory default, so a fresh clone or a wiped data/
directory still boots. The one-shot migration off the old YAML + overrides pair
was dropped on 2026-08-12, once no install predated the move.

Editing the file makes the cached plan stale (see _plan_is_current).
"""

from __future__ import annotations

import json

from app.config import ROOT

SCHEDULE_FILE = ROOT / "data" / "schedule.json"

WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

REST_DAY = {"available": False, "rest": True}
# A day the athlete opens up without saying anything else about it.
DEFAULT_TRAINING_DAY = {
    "available": True,
    "rest": False,
    "preferred_disciplines": [],
    "max_sessions": 1,
    "session_type": "any",
}

DEFAULT_SCHEDULE = {
    "schedule": {
        "monday": {
            "available": True,
            "rest": False,
            "preferred_disciplines": ["swim", "run"],
            "max_sessions": 2,
            "session_type": "quality",
        },
        "tuesday": {
            "available": True,
            "rest": False,
            "preferred_disciplines": ["bike"],
            "max_sessions": 1,
            "session_type": "endurance",
        },
        "wednesday": dict(REST_DAY),
        "thursday": {
            "available": True,
            "rest": False,
            "preferred_disciplines": ["run"],
            "max_sessions": 1,
            "session_type": "quality",
        },
        "friday": {
            "available": True,
            "rest": False,
            "preferred_disciplines": ["swim", "bike"],
            "max_sessions": 2,
            "session_type": "any",
        },
        "saturday": {
            "available": True,
            "rest": False,
            "preferred_disciplines": ["bike", "run"],
            "max_sessions": 2,
            "session_type": "long",
        },
        "sunday": dict(REST_DAY),
    },
}


def _normalize(cfg: dict) -> dict:
    """
    Force the stored shape: all seven weekdays present and in order, rest days
    carrying nothing but the rest flags, training days carrying every key.
    The week-level constraints are always recomputed from the days — any
    constraints in the input are ignored.

    Rest is the one thing here that is a hard constraint, so a rest day must not
    keep stale disciplines or a session count that could reach Claude.
    """
    week = cfg.get("schedule") or {}
    fallback = DEFAULT_SCHEDULE["schedule"]
    out_week = {}
    for day in WEEKDAY_NAMES:
        base = week.get(day) or fallback[day]
        if not base.get("available", True) or base.get("rest"):
            out_week[day] = dict(REST_DAY)
            continue
        disciplines = [
            d for d in base.get("preferred_disciplines") or [] if isinstance(d, str)
        ]
        max_sessions = int(base.get("max_sessions") or 1)
        session_type = base.get("session_type") or "any"
        if session_type == "brick":
            if len(disciplines) < 2:
                session_type = "any"  # a brick needs two disciplines to alternate
            else:
                max_sessions = max(max_sessions, 2)  # a brick is at least 2 sessions
        out_week[day] = {
            "available": True,
            "rest": False,
            "preferred_disciplines": disciplines,
            "max_sessions": max_sessions,
            "session_type": session_type,
        }

    return {"schedule": out_week, "constraints": _derive_constraints(out_week)}


def _derive_constraints(week: dict) -> dict:
    """
    The week-level constraints, computed from the days rather than stored:
    the two limits fall out of the rest days, the long ride/run land on a
    "long" day preferring that discipline, and bricks are allowed exactly on
    the days the athlete typed as "brick".
    """
    training = [d for d in WEEKDAY_NAMES if not week[d]["rest"]]
    flags = [not week[d]["rest"] for d in WEEKDAY_NAMES]
    if all(flags):
        consecutive = 7
    else:
        best = cur = 0
        for open_day in flags * 2:  # doubled so a run of days may wrap the weekend
            cur = cur + 1 if open_day else 0
            best = max(best, cur)
        consecutive = best

    def long_day(discipline: str) -> str | None:
        return next(
            (
                d
                for d in training
                if week[d]["session_type"] == "long"
                and discipline in week[d]["preferred_disciplines"]
            ),
            None,
        )

    return {
        "max_training_days_per_week": len(training),
        "max_consecutive_training_days": consecutive,
        "long_ride_day": long_day("bike"),
        "long_run_day": long_day("run"),
        "brick_allowed_days": [
            d for d in training if week[d]["session_type"] == "brick"
        ],
    }


def get_schedule() -> dict:
    """
    The weekly schedule + constraints as Claude will see them.

    Read fresh every call: the file is written by the API, so caching it would
    serve a long-running server stale data.
    """
    try:
        stored = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None

    if not isinstance(stored, dict):
        cfg = _normalize(DEFAULT_SCHEDULE)
        save_schedule(cfg)
        return cfg

    return _normalize(stored)


def save_schedule(cfg: dict) -> dict:
    """Persist the schedule (normalized) and return what was stored."""
    normalized = _normalize(cfg)
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized
