"""
Event-mode season plan builder. Pure: no I/O, no clock -- today, the event, the
baseline and the settings come in as arguments, so every rule is table-testable
(tests/test_season_builder.py).

The shape, from the race backwards:
- phases from the race distance's weeks-out table (config.PHASE_WEEKS_OUT, the
  prompt files' own boundaries), so this plan and rule 9 name the same phase;
- the last `taper` weeks are the taper, the race week last, sized off the
  highest loading week (config.TAPER_FACTORS);
- before that, a loading path from the athlete's recent weekly TSS growing by
  the ramp (never more than 10% a week) up to 7 x peak CTL, then holding, with a
  recovery week at 70% after every 3 loading weeks. Growth resumes from the
  last LOADING week, never from the recovery week, and a recovery week right
  before the taper is dropped (the taper is the recovery);
- the weekly hours cap clamps a loading week to cap x the athlete's TSS/hour;
- a sub event (an earlier race the athlete also runs) gets a mini-taper sized
  by its distance (config.SUB_EVENT_TAPER): its week, and for a long race the
  week before and a recovery week after, at a share of the last loading week.
  Growth resumes from the last loading week after it, and the main event's
  phases never change for it;
- CTL projected week by week with the same daily recurrence as the stored
  series (_step in app/metrics/calculator.py; frontend/src/forecast.ts mirrors it).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.metrics.calculator import _step
from app.season import config as cfg
from app.season.summary import annotate

WEEKDAY = {"monday": 0, "sunday": 6}


class SeasonPlanError(ValueError):
    """The plan cannot be built from these inputs (the router answers 422)."""


def week_start_of(d: date, week_start_day: str) -> date:
    """The first day of the plan week containing `d`."""
    return d - timedelta(days=(d.weekday() - WEEKDAY[week_start_day]) % 7)


def weeks_out(race: date, week_start: date) -> int:
    """Whole weeks from a week's first day to the race; the race week is 0."""
    return (race - week_start).days // 7


def phase_for(event_type: str, wo: int) -> str:
    """The phase of a week `wo` weeks out, from the prompt files' table."""
    table = cfg.PHASE_WEEKS_OUT.get(event_type) or cfg.PHASE_WEEKS_OUT[cfg.DEFAULT_EVENT_TYPE]
    if wo == 0:
        return "race"
    if wo < table["taper"]:
        return "taper"
    if wo < table["peak"]:
        return "peak"
    if wo <= table["build"]:
        return "build"
    return "base"


def cadence(plan: dict) -> int:
    """Loading weeks between recovery weeks: the plan's own setting (the coach
    instructions may set it, 2-4), else config.RECOVERY_CADENCE_WEEKS."""
    v = (plan.get("settings") or {}).get("recovery_cadence_weeks")
    return int(v) if isinstance(v, (int, float)) and 2 <= v <= 4 else cfg.RECOVERY_CADENCE_WEEKS


def focus_for(phase: str, kind: str) -> str:
    return cfg.FOCUS_TEXT.get(kind) if kind in ("recovery", "return") else cfg.FOCUS_TEXT[phase]


def start_week(today: date, week_start_day: str, start: str | None = None) -> date:
    """
    The plan's first week: this week, unless today is its last day (a plan made
    on Sunday evening of a Monday week starts tomorrow) or the athlete chose
    "next".
    """
    this_week = week_start_of(today, week_start_day)
    if start == "next" or (start is None and today == this_week + timedelta(days=6)):
        return this_week + timedelta(days=7)
    return this_week


def project_ctl(weeks: list[dict], ctl: float | None, atl: float | None) -> None:
    """
    Fill each week's ctl_end in place: the week's TSS spread evenly over its 7
    days through the stored series' own daily recurrence. Projected from the
    baseline at the plan's start, so it is honestly below the peak CTL target --
    7 x CTL a week is only the steady state CTL approaches.
    """
    c, a = float(ctl or 0.0), float(atl or 0.0)
    for w in weeks:
        daily = (w["target_tss"] or 0) / 7.0
        for _ in range(7):
            c, a = _step(c, a, daily)
        w["ctl_end"] = round(c, 1)


def _revision(at: str, week_start: str | None, field: str, frm, to, code: str, text: str) -> dict:
    return {
        "at": at,
        "trigger": "user",
        "week_start": week_start,
        "field": field,
        "from": frm,
        "to": to,
        "reason_code": code,
        "reason_text": text,
    }


def _week(ws: date, index: int, phase: str, kind: str, tss: float, tss_per_hour: float) -> dict:
    tss = round(tss)
    return {
        "week_start": ws.isoformat(),
        "index": index,
        "phase": phase,
        "kind": kind,
        "target_tss": tss,
        "target_hours": round(tss / tss_per_hour, 1),
        "actual_tss": None,
        "actual_hours": None,
        "ctl_end": None,
        "status": "projected",
        "pinned": False,
        "pin_note": None,
        "source": "builder",
        "focus": focus_for(phase, kind),
    }


SUB_KIND = {"pre": "sub_taper", "race": "sub_race", "post": "recovery"}
# When two sub events claim one week, the race week wins, then the lower factor.
_ROLE_RANK = {"race": 0, "pre": 1, "post": 2}


def sub_event_record(event: dict) -> dict:
    """The plan's copy of a sub event."""
    return {k: event.get(k) for k in ("event_id", "name", "type", "race_date")}


def sub_week_map(starts: list[date], until: int, sub_events: list[dict], week_start_day: str) -> dict[int, dict]:
    """
    The weeks (index < `until`, the main taper's start) a sub event marks:
    {index: {role, factor, event}}. A sub event inside the main taper, or before
    the plan's first week, marks nothing.
    """
    index = {ws: i for i, ws in enumerate(starts)}
    out: dict[int, dict] = {}
    for ev in sub_events or []:
        try:
            race = date.fromisoformat(ev.get("race_date") or "")
        except (TypeError, ValueError):
            continue
        row = cfg.SUB_EVENT_TAPER.get(ev.get("type")) or cfg.SUB_EVENT_TAPER[cfg.DEFAULT_EVENT_TYPE]
        ws = week_start_of(race, week_start_day)
        for role, offset in (("pre", -7), ("race", 0), ("post", 7)):
            factor = row.get(role)
            i = index.get(ws + timedelta(days=offset))
            if factor is None or i is None or i >= until:
                continue
            claim = {"role": role, "factor": factor, "event": sub_event_record(ev)}
            had = out.get(i)
            if had is None or (_ROLE_RANK[role], factor) < (_ROLE_RANK[had["role"]], had["factor"]):
                out[i] = claim
    return out


def mark_sub_week(w: dict, claim: dict, tss: float, tss_per_hour: float) -> None:
    """Set week `w` as a sub event's week, in place."""
    tss = round(tss)
    w["kind"] = SUB_KIND[claim["role"]]
    w["target_tss"] = tss
    w["target_hours"] = round(tss / tss_per_hour, 1)
    w["sub_event"] = {**claim["event"], "role": claim["role"]}
    w["sub_factor"] = claim["factor"]
    w["focus"] = cfg.SUB_FOCUS_TEXT[claim["role"]].format(name=claim["event"].get("name") or "the race")


def resets_recovery(w: dict) -> bool:
    """A week after which the 3 + 1 count starts again: a recovery week, or a
    sub event's race week."""
    return w["kind"] in ("recovery", "sub_race")


def eases_into_sub(w: dict | None) -> bool:
    """A sub event's mini-taper or race week: no recovery week right before it."""
    return bool(w) and w["kind"] in ("sub_taper", "sub_race")


def start_level(baseline: dict, activity_class: int | None = None) -> float:
    """The first loading week: the recent weekly average, floored for a new
    athlete (the floor follows the activity class when one is given)."""
    avg = float(baseline.get("avg_weekly_tss_4w") or 0)
    floor = cfg.start_floor(activity_class)
    if baseline.get("limited_history"):
        return max(avg, floor)
    return avg or floor


def build_event_plan(
    *,
    today: date,
    event: dict,
    baseline: dict,
    week_start_day: str = "monday",
    ramp: str | None = None,
    weekly_hours_cap: float | None = None,
    peak_ctl: float | None = None,
    start: str | None = None,
    now_iso: str | None = None,
    plan_id: str | None = None,
    first_week: date | None = None,
    recovery_cadence_weeks: int | None = None,
    sub_events: list[dict] | None = None,
    activity_class: int | None = None,
) -> dict:
    """
    A full event plan for `event` ({event_id, name, type, race_date}). Raises
    SeasonPlanError for a race with no date or one already past.

    `first_week` pins the plan's first week (a rebuild after the event's date
    or distance changed keeps the plan's own start); otherwise it follows
    start_week.
    """
    try:
        race = date.fromisoformat(event.get("race_date") or "")
    except (TypeError, ValueError) as e:
        raise SeasonPlanError("The event has no valid race date.") from e
    if race < today:
        raise SeasonPlanError("The race date is in the past.")

    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    event_type = event.get("type") if event.get("type") in cfg.PHASE_WEEKS_OUT else cfg.DEFAULT_EVENT_TYPE
    ramp = ramp or cfg.DEFAULT_RAMP
    growth = min(cfg.RAMP_GROWTH[ramp], cfg.MAX_WEEKLY_GROWTH)
    every = cadence({"settings": {"recovery_cadence_weeks": recovery_cadence_weeks}})
    peak_ctl = float(peak_ctl or cfg.PEAK_CTL[event_type])
    peak_weekly = round(cfg.PEAK_WEEKLY_FACTOR * peak_ctl)
    tss_per_hour = float(baseline.get("tss_per_hour") or cfg.DEFAULT_TSS_PER_HOUR)
    cap_tss = round(weekly_hours_cap * tss_per_hour) if weekly_hours_cap else None

    first = first_week or start_week(today, week_start_day, start)
    race_week = week_start_of(race, week_start_day)
    first = min(first, race_week)  # a race this week is never skipped
    starts = [first + timedelta(days=7 * i) for i in range((race_week - first).days // 7 + 1)]
    phases = [phase_for(event_type, weeks_out(race, ws)) for ws in starts]
    taper_start = next(i for i, p in enumerate(phases) if p in ("taper", "race"))
    subs = sorted(
        (sub_event_record(e) for e in sub_events or []), key=lambda e: e.get("race_date") or ""
    )
    sub_weeks = sub_week_map(starts, taper_start, subs, week_start_day)

    revisions: list[dict] = []
    level = start_level(baseline, activity_class)
    # Above the target already: hold at the athlete's level, no growth.
    ceiling = max(peak_weekly, level)
    weeks: list[dict] = []
    last_loading: float | None = None
    since_recovery = 0
    clamped = False

    for i in range(taper_start):
        ws, phase = starts[i], phases[i]
        claim = sub_weeks.get(i)
        if claim is not None:
            w = _week(ws, i + 1, phase, "loading", 0, tss_per_hour)
            mark_sub_week(w, claim, (last_loading if last_loading is not None else level) * claim["factor"], tss_per_hour)
            weeks.append(w)
            if resets_recovery(w):
                since_recovery = 0
            continue
        due = since_recovery >= every
        nxt = sub_weeks.get(i + 1)
        before_sub = nxt is not None and nxt["role"] in ("pre", "race")
        if due and taper_start - i > 1 and last_loading is not None and not before_sub:
            weeks.append(_week(ws, i + 1, phase, "recovery", last_loading * cfg.RECOVERY_FACTOR, tss_per_hour))
            since_recovery = 0
            continue
        target = level if last_loading is None else min(round(last_loading * (1 + growth)), ceiling)
        if cap_tss is not None and target > cap_tss:
            if not clamped:
                revisions.append(
                    _revision(
                        now_iso, ws.isoformat(), "target_tss", target, cap_tss, "HOURS_CAP",
                        f"Held to {weekly_hours_cap:g} h a week ({cap_tss} TSS)",
                    )
                )
            target, clamped = cap_tss, True
        target = round(target)
        weeks.append(_week(ws, i + 1, phase, "loading", target, tss_per_hour))
        last_loading = target
        since_recovery += 1

    loading = [w["target_tss"] for w in weeks if w["kind"] == "loading"]
    top = max(loading) if loading else round(min(level, cap_tss) if cap_tss else level)
    compressed = False
    if loading and top < peak_weekly and level < peak_weekly:
        if clamped and top >= cap_tss:
            pass  # the hours cap, already logged, is what holds the peak down
        else:
            compressed = True
            revisions.append(
                _revision(now_iso, None, "peak_weekly_tss", peak_weekly, top, "COMPRESSED",
                          "Short runway: the build cannot reach the peak in time")
            )
        revisions.append(
            _revision(now_iso, None, "peak_weekly_tss", peak_weekly, top, "PEAK_LOWERED",
                      f"Peak lowered to {top} TSS a week")
        )
        peak_weekly = top

    # Never more taper weeks than the type has (phase_for stops at its table), so
    # a plan that starts mid-taper just takes the tail of the factors.
    factors = cfg.TAPER_FACTORS[cfg.PHASE_WEEKS_OUT[event_type]["taper"]]
    factors = factors[len(factors) - (len(starts) - taper_start):]
    for j, i in enumerate(range(taper_start, len(starts))):
        kind = "race" if phases[i] == "race" else "taper"
        weeks.append(_week(starts[i], i + 1, phases[i], kind, top * factors[j], tss_per_hour))

    project_ctl(weeks, baseline.get("ctl"), baseline.get("atl"))

    plan = {
        "schema_version": 1,
        "as_of": today.isoformat(),
        "plan_id": plan_id or uuid.uuid4().hex,
        "mode": "event",
        "status": "active",
        "created_at": now_iso,
        "updated_at": now_iso,
        "payload_changed_at": now_iso,
        "week_start_day": week_start_day,
        "event": {
            "event_id": event.get("event_id"),
            "name": event.get("name") or "",
            "type": event_type,
            "race_date": race.isoformat(),
        },
        "settings": {
            "weekly_hours_cap": weekly_hours_cap,
            "ramp": ramp,
            "growth_pct_per_week": round(growth * 100, 1),
            "recovery_cadence_weeks": every,
            "recovery_factor": cfg.RECOVERY_FACTOR,
            "focus": None,
        },
        "baseline": dict(baseline),
        "targets": {"peak_ctl": peak_ctl, "peak_weekly_tss": peak_weekly, "compressed": compressed},
        "summary": {"line1": "", "line2": ""},
        "weeks": weeks,
        "checkins": [],
        "revisions": revisions,
    }
    if subs:
        # Only when there are any: a plan without sub events is exactly the
        # contract sample in tests/fixtures/season/.
        plan["sub_events"] = subs
    return annotate(plan, today)
