"""
Load-monotony check: has the weekly load stopped varying?

Read on every plan build (so on every sync that generates a plan, and on Re-sync
plan). Over the last WEEKS complete calendar weeks, the load is monotonous when
it is either

- flat ("plateau"): every week within PLATEAU_TOLERANCE_TSS of the week before, or
- only climbing ("rising"): every week above the one before, except at most
  MAX_ANOMALIES weeks that are level or lower, by any amount.

Either way the next 7 days are planned REDUCTION lighter than the last complete
week (prompt rule 32), and the athlete is told why in a sentence written here,
not by Claude.

Weeks are Monday-start calendar weeks, the same buckets as the dashboard's TSS
by Week chart, and the current partial week is left out. Only swim, bike, run
and strength count, as on that chart; Garmin's "other" activities do not. Pure:
no I/O, `today` is passed in.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

WEEKS = 6
PLATEAU_TOLERANCE_TSS = 25
MAX_ANOMALIES = 1
REDUCTION = 0.15
# How far over target_tss the plan's sessions may land before it is flagged.
TARGET_TOLERANCE = 0.10
# Enough stored history for WEEKS complete weeks plus the current partial one.
HISTORY_DAYS = (WEEKS + 1) * 7

COUNTED = ("swim", "bike", "run", "strength")


def _day(w: dict) -> date | None:
    """The athlete's calendar day: the stored `date` field, never the UTC instant."""
    stored = w.get("date")
    if stored:
        try:
            return date.fromisoformat(str(stored)[:10])
        except ValueError:
            return None
    t = w.get("time")
    return t.astimezone().date() if isinstance(t, datetime) else None


def weekly_tss(workouts: list[dict], today: date, weeks: int = WEEKS) -> list[float] | None:
    """
    TSS per complete Monday week, oldest first, or None when any of them is 0
    -- an empty week means no history (or a real break), not a flat load.
    """
    this_monday = today - timedelta(days=today.weekday())
    first = this_monday - timedelta(weeks=weeks)
    totals = [0.0] * weeks
    for w in workouts:
        if w.get("discipline") not in COUNTED:
            continue
        d = _day(w)
        if d is None or d < first or d >= this_monday:
            continue
        totals[(d - first).days // 7] += w.get("tss") or 0
    if any(t <= 0 for t in totals):
        return None
    return [round(t) for t in totals]


def detect(weekly: list[float] | None) -> str | None:
    """'plateau', 'rising' or None. Plateau is checked first."""
    if not weekly or len(weekly) < 2:
        return None
    steps = [b - a for a, b in zip(weekly, weekly[1:])]
    if all(abs(s) <= PLATEAU_TOLERANCE_TSS for s in steps):
        return "plateau"
    if sum(1 for s in steps if s <= 0) <= MAX_ANOMALIES:
        return "rising"
    return None


def reduction_block(workouts: list[dict], today: date) -> dict | None:
    """
    The payload's load_reduction block, or None when the load is not
    monotonous. `message` is what the athlete reads.
    """
    weekly = weekly_tss(workouts, today)
    pattern = detect(weekly)
    if pattern is None:
        return None
    last = weekly[-1]
    target = round(last * (1 - REDUCTION))
    pct = round(REDUCTION * 100)
    if pattern == "plateau":
        lead = (
            f"Your weekly load has stayed around {round(sum(weekly) / len(weekly))} TSS "
            f"for {len(weekly)} weeks"
        )
    else:
        lead = (
            f"Your weekly load has only gone up for {len(weekly)} weeks "
            f"({weekly[0]} to {last} TSS)"
        )
    message = (
        f"{lead}. The next 7 days are planned {pct}% lighter, about {target} TSS, "
        "with easier sessions, so your body can absorb the work."
    )
    return {
        "pattern": pattern,
        "weekly_tss": weekly,
        "last_week_tss": last,
        "reduction_pct": pct,
        "target_tss": target,
        "message": message,
    }
