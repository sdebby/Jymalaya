"""
The athlete's coach instructions, as the season plan reads them.

The instructions are free text (data/coach_instructions.txt, edited in
Settings). The 7-day plan gets the text itself; the season plan gets what one
small Claude call reads out of it (app/ai/claude.py
generate_season_instructions, the athlete's call on 2026-10-26):

- sports: a sport to add, train more or less, or drop, with its share of each
  week's TSS -- e.g. one aerobic ride a week in a run plan;
- load: the weekly hours limit, the build-up pace, the recovery spacing and the
  peak fitness, when the text states them;
- left_to_weekly_plan: everything else (days, session content), listed so the
  athlete sees it was read.

Nothing Claude returns is trusted as is: clean_read() keeps only known keys and
clamps every number. A read is cached per text and plan context, so the create
modal's live preview pays for one call, not one per keystroke. A failed call
never blocks a plan: the read comes back with `error` and nothing applied.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from app.models import race_disciplines
from app.season import config as cfg

log = logging.getLogger("jymalaya.season")

SPORTS = cfg.SPLIT_DISCIPLINES
CHANGES = ("add", "more", "less", "drop")
RAMPS = tuple(cfg.RAMP_GROWTH)
# Every added or changed sport together never takes more than this of a week.
MAX_SHARE_PCT = 40
# Load settings the instructions may set, with their bounds.
LOAD_BOUNDS = {
    "weekly_hours_cap": (1.0, 40.0),
    "recovery_cadence_weeks": (2, 4),
    "peak_ctl": (20.0, 200.0),
}
LOAD_FIELDS = ("weekly_hours_cap", "ramp", "recovery_cadence_weeks", "peak_ctl")

# (sha of text + context) -> cleaned read. In-process: a restart re-reads once.
_CACHE: dict[str, dict] = {}


def text_sha(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def empty_read() -> dict:
    return {"summary": "", "sports": [], "load": {k: None for k in LOAD_FIELDS}, "left_to_weekly_plan": []}


def context_for(mode: str, event_type: str | None, focus: str | None) -> dict:
    """What the read depends on besides the text: the same words mean another
    share in a run plan than in a triathlon."""
    if mode == "fitness":
        f = focus or "triathlon"
        sports = list(SPORTS) if f == "triathlon" else [f]
        return {"mode": "fitness", "focus": f, "race_sports": sports}
    return {"mode": "event", "event_type": event_type, "race_sports": race_disciplines(event_type)}


def _num(v, lo, hi, as_int=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = min(max(v, lo), hi)
    return int(round(v)) if as_int else round(float(v), 1)


def clean_read(raw: dict | None) -> dict:
    """Only known keys, every number clamped (see the module docstring)."""
    out = empty_read()
    if not isinstance(raw, dict):
        return out
    out["summary"] = str(raw.get("summary") or "").strip()[:200]
    seen = set()
    total = 0
    for item in raw.get("sports") or []:
        if not isinstance(item, dict):
            continue
        d, change = item.get("discipline"), item.get("change")
        if d not in SPORTS or change not in CHANGES or d in seen:
            continue
        share = 0 if change == "drop" else _num(item.get("share_pct"), 0, MAX_SHARE_PCT, as_int=True)
        if share is None:
            continue
        share = min(share, MAX_SHARE_PCT - total) if change != "drop" else 0
        total += share
        seen.add(d)
        sessions = _num(item.get("sessions_per_week"), 0, 14, as_int=True)
        note = str(item.get("note") or "").strip()[:80] or None
        out["sports"].append(
            {"discipline": d, "change": change, "share_pct": share, "sessions_per_week": sessions, "note": note}
        )
    load = raw.get("load") if isinstance(raw.get("load"), dict) else {}
    for key, (lo, hi) in LOAD_BOUNDS.items():
        out["load"][key] = _num(load.get(key), lo, hi, as_int=key == "recovery_cadence_weeks")
    out["load"]["ramp"] = load.get("ramp") if load.get("ramp") in RAMPS else None
    out["left_to_weekly_plan"] = [
        str(x).strip()[:120] for x in (raw.get("left_to_weekly_plan") or []) if str(x).strip()
    ][:12]
    return out


def _ask_claude(text: str, context: dict) -> dict:
    """The call itself; tests stub this (tests/conftest.py)."""
    from app.ai.claude import generate_season_instructions

    return generate_season_instructions({"instructions": text, "season_plan": context})


def read_instructions(text: str, context: dict) -> dict:
    """
    The cleaned read of `text` for a plan of `context`, cached. Empty text is
    an empty read with no call; a failed call is an empty read with `error`.
    """
    text = (text or "").strip()
    if not text:
        return empty_read()
    key = text_sha(text) + "|" + json.dumps(context, sort_keys=True)
    if key in _CACHE:
        return _CACHE[key]
    try:
        read = clean_read(_ask_claude(text, context))
    except Exception as e:  # noqa: BLE001 -- a read never blocks the plan
        log.warning("Could not read the coach instructions for the season plan: %s", e)
        return {**empty_read(), "error": str(e)[:300]}
    _CACHE[key] = read
    return read


def record(text: str, read: dict, applied: bool) -> dict:
    """What the plan stores: which text was read, when, and what came of it."""
    return {
        "text_sha": text_sha(text),
        "read_at": datetime.now(timezone.utc).isoformat(),
        "applied": applied,
        "read": read,
    }


# ---- Applying a read (pure) --------------------------------------------------------


def changes_sports(read: dict | None) -> bool:
    return bool(read and read.get("sports"))


def apply_to_row(row: dict[str, int], read: dict | None) -> dict[str, int]:
    """
    A split row with the read's sports changes: each named sport gets its
    share (0 when dropped), the others share what is left in proportion to
    what they had. Whole percents adding up to 100.
    """
    if not changes_sports(read):
        return dict(row)
    fixed = {s["discipline"]: s["share_pct"] for s in read["sports"]}
    rest = [d for d in SPORTS if d not in fixed]
    left = max(100 - sum(fixed.values()), 0)
    rest_sum = sum(row.get(d, 0) for d in rest)
    raw: dict[str, float] = dict(fixed)
    if rest_sum > 0:
        for d in rest:
            raw[d] = left * row.get(d, 0) / rest_sum
    else:
        # Nothing else to share it with: scale the named sports up to 100.
        total = sum(fixed.values()) or 1
        raw = {d: 100 * v / total for d, v in fixed.items()}
        for d in rest:
            raw[d] = 0
    out = {d: int(raw.get(d, 0)) for d in SPORTS}
    short = 100 - sum(out.values())
    for d in sorted(SPORTS, key=lambda d: raw.get(d, 0) - out[d], reverse=True)[: max(short, 0)]:
        out[d] += 1
    return out


def load_values(read: dict | None) -> dict:
    """The load settings the read states (non-null only)."""
    return {k: v for k, v in ((read or {}).get("load") or {}).items() if v is not None}


RAMP_TEXT = {"gentle": "gentle (+5% a week)", "standard": "standard (+8% a week)", "ambitious": "ambitious (+10% a week)"}


def applied_lines(read: dict | None) -> list[str]:
    """The read as the athlete sees it: one plain line per change."""
    out = []
    for s in (read or {}).get("sports") or []:
        name = s["discipline"].capitalize()
        if s["change"] == "drop":
            out.append(f"{name}: none")
            continue
        extra = []
        if s.get("sessions_per_week"):
            n = s["sessions_per_week"]
            extra.append(f"{n} session{'s' if n != 1 else ''} a week")
        if s.get("note"):
            extra.append(s["note"])
        out.append(f"{name} {s['share_pct']}% of each week's TSS" + (f" ({', '.join(extra)})" if extra else ""))
    load = load_values(read)
    if "weekly_hours_cap" in load:
        out.append(f"Weekly hours available: {load['weekly_hours_cap']:g}")
    if "ramp" in load:
        out.append(f"Build-up pace: {RAMP_TEXT[load['ramp']]}")
    if "recovery_cadence_weeks" in load:
        out.append(f"A recovery week after every {load['recovery_cadence_weeks']} loading weeks")
    if "peak_ctl" in load:
        out.append(f"Peak fitness (CTL): {load['peak_ctl']:g}")
    return out


def summary_view(read: dict | None, applied: bool) -> dict:
    """The `instructions` block the API returns with a plan or a preview."""
    read = read or empty_read()
    return {
        "summary": read.get("summary") or "",
        "applied": applied_lines(read) if applied else [],
        "left_to_weekly_plan": read.get("left_to_weekly_plan") or [],
        "error": read.get("error"),
        "used": applied,
    }
