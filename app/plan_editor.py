"""
Plan mutation layer for the coach's plan-editing tools.

Operates directly on data/latest_recommendation.json (the source of truth read
by GET /recommend/latest). Sessions are addressed by (date, session_index) —
the plan JSON has no per-session IDs. Each public function is a complete
load -> validate -> mutate -> save cycle, so a failure in one tool call never
loses the edits of an earlier call in the same coach turn.

Every edit is also saved as an athlete request (app/plan_requests.py): the
edited day, as it stands after the edit, survives the next plan rebuild when
the athlete chooses to keep their changes. `note` is what the athlete asked
for, in their terms.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

from app import plan_requests
from app.config import ROOT
from app.models import PlanSessionEdit

# Owned here (not in recommend_service) to avoid a circular import:
# recommend_service -> ai.claude -> plan_editor. recommend_service re-imports it.
LATEST_FILE = ROOT / "data" / "latest_recommendation.json"


class PlanEditError(ValueError):
    """A plan edit could not be applied; the message is safe to show the model."""


def _load_plan() -> dict:
    if not LATEST_FILE.exists():
        raise PlanEditError(
            "No generated plan exists yet — ask the athlete to trigger a recommendation first."
        )
    try:
        plan = json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise PlanEditError(f"The stored plan could not be read: {e}") from e
    if not isinstance(plan, dict) or not isinstance(plan.get("days"), list):
        raise PlanEditError("The stored plan has no days[] — regenerate the plan.")
    return plan


def _save_plan(plan: dict, dates: list[str], note: str | None, by: str = "coach") -> None:
    """Write the edited plan, then save the touched days as athlete requests."""
    meta = plan.setdefault("_meta", {})
    meta["modified_at"] = datetime.now(timezone.utc).isoformat()
    meta["modified_by"] = by
    for day in plan["days"]:
        if day.get("date") in dates:
            day["athlete_requested"] = True
    LATEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    plan_requests.pin_days(plan, dates, note)


def _find_day(plan: dict, date: str) -> dict:
    for day in plan["days"]:
        if day.get("date") == date:
            day.setdefault("sessions", [])
            return day
    valid = ", ".join(str(d.get("date")) for d in plan["days"])
    raise PlanEditError(f"No plan day with date {date!r}. Valid dates: {valid}.")


def _get_session(day: dict, index: int) -> dict:
    sessions = day.get("sessions") or []
    if not isinstance(index, int) or index < 0 or index >= len(sessions):
        raise PlanEditError(
            f"Day {day.get('date')} has {len(sessions)} session(s); "
            f"session_index {index} is out of range (zero-based)."
        )
    return sessions[index]


def find_session(date: str, session_index: int) -> dict:
    """
    A copy of the session at (date, session_index), for readers outside the
    coach tools (Send to device). Read-only: nothing is saved.
    """
    return copy.deepcopy(_get_session(_find_day(_load_plan(), date), session_index))


def _normalize_rest_flag(day: dict) -> None:
    day["is_rest_day"] = not day["sessions"]


def _segment_kind(seg: dict) -> str | None:
    name = str(seg.get("segment") or "").lower()
    for ch in " -_":
        name = name.replace(ch, "")
    if "warmup" in name:
        return "warmup"
    if "cooldown" in name:
        return "cooldown"
    return None


def order_segments(structure: list | None) -> list | None:
    """
    Put a session's warmup first and its cooldown last, keeping the rest in order.

    The model balances segment durations against the session total by tacking a
    segment on wherever it ends, which once left "Recovery 5" after the cooldown.
    Applied only when there is at most one of each: a multi-leg session written
    as one (a cooldown per leg) can legitimately have one mid-session.
    """
    if not isinstance(structure, list) or not all(isinstance(s, dict) for s in structure):
        return structure
    kinds = [_segment_kind(s) for s in structure]
    if kinds.count("warmup") > 1 or kinds.count("cooldown") > 1:
        return structure
    return (
        [s for s, k in zip(structure, kinds) if k == "warmup"]
        + [s for s, k in zip(structure, kinds) if k is None]
        + [s for s, k in zip(structure, kinds) if k == "cooldown"]
    )


def _validated(session: dict) -> dict:
    out = PlanSessionEdit.model_validate(session).model_dump(exclude_none=True)
    if "structure" in out:
        out["structure"] = order_segments(out["structure"])
    return out


def _result(day: dict, **extra) -> dict:
    return {
        "ok": True,
        "date": day.get("date"),
        "day_is_rest_day": day.get("is_rest_day"),
        "day_sessions": len(day.get("sessions") or []),
        **extra,
    }


def update_session(
    date: str, session_index: int, session: dict, *, note: str | None = None
) -> dict:
    """Replace the whole session at (date, session_index) with `session`."""
    new_session = _validated(session)
    plan = _load_plan()
    day = _find_day(plan, date)
    _get_session(day, session_index)  # validates the index
    day["sessions"][session_index] = new_session
    _normalize_rest_flag(day)
    _save_plan(plan, [date], note)
    return _result(day, session_index=session_index)


def move_session(
    from_date: str, session_index: int, to_date: str, *, note: str | None = None, by: str = "coach"
) -> dict:
    """Move a session to another plan day (appended at the end of that day)."""
    plan = _load_plan()
    src = _find_day(plan, from_date)
    dst = _find_day(plan, to_date)
    _get_session(src, session_index)
    session = src["sessions"].pop(session_index)
    dst["sessions"].append(session)
    _normalize_rest_flag(src)
    _normalize_rest_flag(dst)
    # Both days changed: the source lost a session the athlete moved away.
    _save_plan(plan, [from_date, to_date], note, by)
    return _result(dst, moved_from=from_date, session_index=len(dst["sessions"]) - 1)


def remove_session(date: str, session_index: int, *, note: str | None = None) -> dict:
    """Delete the session at (date, session_index)."""
    plan = _load_plan()
    day = _find_day(plan, date)
    _get_session(day, session_index)
    day["sessions"].pop(session_index)
    _normalize_rest_flag(day)
    _save_plan(plan, [date], note)
    return _result(day)


def add_session(date: str, session: dict, *, note: str | None = None) -> dict:
    """Append a new session to the given plan day."""
    new_session = _validated(session)
    plan = _load_plan()
    day = _find_day(plan, date)
    day["sessions"].append(new_session)
    _normalize_rest_flag(day)
    _save_plan(plan, [date], note)
    return _result(day, session_index=len(day["sessions"]) - 1)
