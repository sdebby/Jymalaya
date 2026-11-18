"""
Fitness mode: no race and no calendar. The plan holds the weeks already
decided -- past weeks, this week, and at most the next one -- and each week is
decided by a check-in the athlete answers on the last day of the week before:
Increase, Stay, Decrease or Recovery. Unanswered by the time the new week
starts, the recommended choice is applied for them (`checkin_auto`).

Pure: no I/O, no clock. app/season/service.py feeds it the stored plan, the
closing week's planned-but-not-done TSS (from the 7-day plan), the days since
the last workout and today's form, and stores what comes back.

The numbers (tests/test_season_fitness.py pins every row):
- P is what the closing week comes to: its actual so far plus what is still
  planned while it is open, its final actual once closed. Options build on
  what the athlete actually did, never on the target -- no catch-up.
- stay = P; increase = P x 1.07 (never more than +10%, never past the hours
  cap); decrease = P x 0.88; recovery = the mean of the last three loading
  weeks x 0.70. After a recovery week, stay and increase build on the last
  LOADING week instead.
- After a layoff (7 days or more without training) the recovery option is a
  return week at 60% of the last loading week's target.
- Bands are the Friel zones the Fatigue Zones chart draws (summary.band_for).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.metrics.calculator import _step
from app.season import config as cfg
from app.season.builder import cadence, start_level, start_week
from app.season.summary import BAND_LABEL, band_for

CHOICES = ("increase", "stay", "decrease", "recovery")
PROJECTED_WEEKS = 3

# Which sports a fitness plan's focus allows. Strength is support work and is
# in none of them -- rule 4 keeps it on any day that asks for it, as for a race.
FOCUS_DISCIPLINES = {
    "triathlon": ["swim", "bike", "run"],
    "run": ["run"],
    "bike": ["bike"],
    "swim": ["swim"],
}

CHOICE_LABEL = {"increase": "Increase", "stay": "Stay", "decrease": "Decrease", "recovery": "Recovery"}


def _ws(w: dict) -> date:
    return date.fromisoformat(w["week_start"])


def _tph(plan: dict) -> float:
    return float((plan.get("baseline") or {}).get("tss_per_hour") or cfg.DEFAULT_TSS_PER_HOUR)


def _week(ws: date, index: int, kind: str, tss: float, tph: float, source: str) -> dict:
    tss = round(tss)
    return {
        "week_start": ws.isoformat(),
        "index": index,
        "phase": "fitness",
        "kind": kind,
        "target_tss": tss,
        "target_hours": round(tss / tph, 1),
        "actual_tss": None,
        "actual_hours": None,
        "ctl_end": None,
        "status": "projected",
        "pinned": False,
        "pin_note": None,
        "source": source,
        "focus": cfg.FOCUS_TEXT.get(kind) if kind in ("recovery", "return") else None,
    }


def build_fitness_plan(
    *,
    today: date,
    baseline: dict,
    week_start_day: str = "monday",
    weekly_hours_cap: float | None = None,
    focus: str | None = None,
    start: str | None = None,
    now_iso: str,
    plan_id: str | None = None,
    first_week: date | None = None,
    recovery_cadence_weeks: int | None = None,
    activity_class: int | None = None,
) -> dict:
    """
    A new fitness plan: one decided week, at the athlete's recent weekly
    average (floored for a new athlete, by activity class). The first check-in,
    at the end of that week, decides the next one.
    """
    first = first_week or start_week(today, week_start_day, start)
    tph = float(baseline.get("tss_per_hour") or cfg.DEFAULT_TSS_PER_HOUR)
    level = start_level(baseline, activity_class)
    if weekly_hours_cap:
        level = min(level, weekly_hours_cap * tph)
    return {
        "schema_version": 1,
        "as_of": today.isoformat(),
        "plan_id": plan_id or uuid.uuid4().hex,
        "mode": "fitness",
        "status": "active",
        "created_at": now_iso,
        "updated_at": now_iso,
        "payload_changed_at": now_iso,
        "week_start_day": week_start_day,
        "event": None,
        "settings": {
            "weekly_hours_cap": weekly_hours_cap,
            "ramp": None,
            "growth_pct_per_week": None,
            "recovery_cadence_weeks": cadence({"settings": {"recovery_cadence_weeks": recovery_cadence_weeks}}),
            "recovery_factor": cfg.RECOVERY_FACTOR,
            "step_increase_pct": cfg.STEP_INCREASE_PCT,
            "step_decrease_pct": cfg.STEP_DECREASE_PCT,
            "focus": focus or "triathlon",
        },
        "baseline": dict(baseline),
        "targets": None,
        "summary": {"line1": "", "line2": ""},
        "weeks": [_week(first, 1, "loading", level, tph, "builder")],
        "checkins": [],
        "revisions": [],
    }


# ---- the check-in ---------------------------------------------------------------


def loading_since_recovery(weeks: list[dict], upto: int) -> int:
    """Loading weeks in a row up to and including weeks[upto]."""
    n = 0
    for w in reversed(weeks[: upto + 1]):
        if w["kind"] != "loading":
            break
        n += 1
    return n


def _done(w: dict) -> float:
    return float(w.get("actual_tss") or 0)


def build_checkin(
    plan: dict,
    closing: int,
    today: date,
    form: dict,
    planned_remaining: float,
    days_since_workout: int | None,
    closed: bool,
    opened_at: str,
) -> dict:
    """
    The check-in that decides the week after weeks[closing]. `closed` is True
    once that week is over: P is then its final actual, otherwise its actual so
    far plus `planned_remaining`.
    """
    weeks = plan["weeks"]
    settings = plan.get("settings") or {}
    tph = _tph(plan)
    cw = weeks[closing]
    remaining = 0.0 if closed else max(planned_remaining, 0.0)
    p = round(_done(cw) + remaining)
    completion = round(100 * p / cw["target_tss"]) if cw["target_tss"] else 100

    # The loading weeks the options build on: the closing week counts at P.
    loading_tss = [(_done(w) if i != closing else p) for i, w in enumerate(weeks[: closing + 1]) if w["kind"] == "loading"]
    last_loading_target = next((w["target_tss"] for w in reversed(weeks[: closing + 1]) if w["kind"] == "loading"), cw["target_tss"])
    base = p if cw["kind"] == "loading" else (loading_tss[-1] if loading_tss else p)
    if base <= 0:
        base = cw["target_tss"]
    since = loading_since_recovery(weeks, closing)

    previous = next((c for c in reversed(plan.get("checkins") or []) if c.get("form")), None)
    prev_tsb = (previous or {}).get("form", {}).get("tsb")
    band = band_for(form.get("tsb"), prev_tsb) or "ok"
    layoff = days_since_workout is not None and days_since_workout >= cfg.LAYOFF_DAYS

    inc = (settings.get("step_increase_pct") or cfg.STEP_INCREASE_PCT) / 100
    dec = (settings.get("step_decrease_pct") or cfg.STEP_DECREASE_PCT) / 100
    increase = base * (1 + min(inc, cfg.MAX_WEEKLY_GROWTH))
    every = cadence(plan)
    recent = loading_tss[-every:] or [base]
    recovery = sum(recent) / len(recent) * cfg.RECOVERY_FACTOR
    if layoff:
        recovery = last_loading_target * cfg.RETURN_FACTOR
    targets = {"increase": increase, "stay": base, "decrease": base * (1 - dec), "recovery": recovery}

    cap = settings.get("weekly_hours_cap")
    cap_tss = cap * tph if cap else None
    overdue = since > every
    due = since >= every
    can_skip = band in ("fresh", "ok")

    def disabled(choice: str) -> str | None:
        if choice == "recovery":
            return None
        if overdue:
            return "Recovery was already put off once: it is due now."
        if due and not can_skip:
            return "A recovery week is due, and fatigue says take it."
        if choice == "increase" and band == "high":
            return "Not offered while fatigue is high."
        if choice == "increase" and cap_tss is not None and targets["increase"] > cap_tss + 0.5:
            return f"More than your {cap:g} h a week."
        return None

    ref = p if p > 0 else base
    options = []
    for choice in CHOICES:
        tss = round(targets[choice])
        reason = disabled(choice)
        options.append(
            {
                "choice": choice,
                "target_tss": tss,
                "target_hours": round(tss / tph, 1),
                "delta_pct_vs_last": round(100 * (tss - ref) / ref) if ref else 0,
                "enabled": reason is None,
                "disabled_reason": reason,
            }
        )

    recommended, reason = _recommend(band, since, layoff, completion, days_since_workout, every)
    if recommended != "recovery" and not next(o for o in options if o["choice"] == recommended)["enabled"]:
        recommended, reason = "recovery", "A recovery week is due."

    return {
        "week_start": (_ws(cw) + timedelta(days=7)).isoformat(),
        "status": "pending",
        "opened_at": opened_at,
        "source": None,
        "choice": None,
        "chosen_at": None,
        "closing_week": {
            "week_start": cw["week_start"],
            "target_tss": round(cw["target_tss"]),
            "actual_tss_to_date": round(_done(cw)),
            "planned_remaining_tss": round(remaining),
            "projected_tss": p,
            "completion_pct": completion,
        },
        "form": {"ctl": form.get("ctl"), "atl": form.get("atl"), "tsb": form.get("tsb"), "band": band},
        "loading_weeks_since_recovery": since,
        "recommended": recommended,
        "reason": reason,
        "options": options,
        "return_week": layoff,
    }


COUNT_WORD = {2: "Two", 3: "Three", 4: "Four"}


def _recommend(
    band: str, since: int, layoff: bool, completion: int, days_off: int | None, every: int = cfg.RECOVERY_CADENCE_WEEKS
) -> tuple[str, str]:
    """The recommendation table, first match wins. `every`: the plan's recovery spacing."""
    if band == "high":
        return "recovery", "Fatigue is high: take a recovery week."
    if since >= every:
        text = f"{COUNT_WORD.get(every, every)} loading weeks done: a recovery week is due."
        if band in ("fresh", "ok") and since == every:
            text += f" Form is {BAND_LABEL[band]}, so you could put it off once."
        return "recovery", text
    if layoff:
        return "recovery", f"{days_off} days without training: ease back in with a return week."
    if completion < 80:
        return "stay", f"Last week came in at {completion}% of target: hold the load, no catch-up."
    if band == "tired":
        if completion > 110:
            return "decrease", "Tired after a big week: step back a little."
        return "stay", "You are tired: hold the load this week."
    if completion >= 90:
        return "increase", "A full week and good form: step up."
    return "stay", "Hold the load and build consistency."


def option(checkin: dict, choice: str) -> dict:
    return next(o for o in checkin["options"] if o["choice"] == choice)


def decide(plan: dict, checkin: dict, choice: str, source: str) -> dict:
    """
    Set the week the check-in decides, in place (adding it when it is not
    stored yet), and return it. A recovery choice after a layoff is a return
    week.
    """
    o = option(checkin, choice)
    kind = "loading"
    if choice == "recovery":
        kind = "return" if checkin.get("return_week") else "recovery"
    weeks = plan["weeks"]
    ws = checkin["week_start"]
    w = next((w for w in weeks if w["week_start"] == ws), None)
    tph = _tph(plan)
    if w is None:
        w = _week(date.fromisoformat(ws), len(weeks) + 1, kind, o["target_tss"], tph, source)
        weeks.append(w)
    else:
        w.update(_week(date.fromisoformat(ws), w["index"], kind, o["target_tss"], tph, source))
    return w


# ---- the view: projection and summary ---------------------------------------------


def projection(plan: dict, after: list[dict]) -> list[dict]:
    """
    PROJECTED_WEEKS indicative weeks after the last decided (or pending) one:
    the 3 + 1 rhythm continued at the +7% step from the last loading week's
    target. Recomputed on every read, never stored -- a fitness plan commits
    only one week ahead.
    """
    tph = _tph(plan)
    inc = ((plan.get("settings") or {}).get("step_increase_pct") or cfg.STEP_INCREASE_PCT) / 100
    every = cadence(plan)
    weeks = list(after)
    out = []
    for _ in range(PROJECTED_WEEKS):
        last = weeks[-1]
        last_loading = next((w["target_tss"] for w in reversed(weeks) if w["kind"] == "loading"), last["target_tss"])
        since = loading_since_recovery(weeks, len(weeks) - 1)
        if since >= every:
            w = _week(_ws(last) + timedelta(days=7), last["index"] + 1, "recovery", last_loading * cfg.RECOVERY_FACTOR, tph, "projection")
        else:
            w = _week(_ws(last) + timedelta(days=7), last["index"] + 1, "loading", last_loading * (1 + inc), tph, "projection")
        weeks.append(w)
        out.append(w)
    return out


def project_ctl(weeks: list[dict], today: date, form: dict) -> None:
    """ctl_end for this week and later, from today's form (past weeks keep theirs)."""
    c, a = form.get("ctl"), form.get("atl")
    if c is None or a is None:
        return
    for w in weeks:
        start, end = _ws(w), _ws(w) + timedelta(days=6)
        if end < today:
            continue
        if start <= today:
            days = (end - today).days + 1
            remaining = max(w["target_tss"] - _done(w), 0)
        else:
            days, remaining = 7, w["target_tss"]
        for _ in range(days):
            c, a = _step(c, a, remaining / days)
        w["ctl_end"] = round(c, 1)


def fitness_lines(plan: dict, today: date, pending: dict | None) -> dict:
    """The two summary lines of a fitness plan."""
    weeks = [w for w in plan["weeks"] if w.get("source") != "projection"]
    form = plan.get("last_form") or plan.get("baseline") or {}
    current = next((i for i, w in enumerate(weeks) if _ws(w) <= today <= _ws(w) + timedelta(days=6)), None)
    start_ctl = (plan.get("baseline") or {}).get("ctl")
    ctl = form.get("ctl")
    trend = ""
    if ctl is not None:
        trend = f" CTL {round(ctl)}"
        if start_ctl is not None:
            d = round(ctl - start_ctl)
            trend += f" ({'+' if d >= 0 else ''}{d} since the plan began)"
        trend += "."
    band = band_for(form.get("tsb"))
    form_text = f"form is {BAND_LABEL[band]} (TSB {round(form['tsb'])})" if band else ""

    if current is None:
        first = weeks[0]
        line1 = f"Fitness plan, starting {_ws(first).day} {_ws(first).strftime('%b')}.{trend}"
        line2 = f"First week: {round(first['target_tss'])} TSS" + (f"; {form_text}." if form_text else ".")
        return {"line1": line1, "line2": line2}

    w = weeks[current]
    if pending and pending["week_start"] == w["week_start"]:
        # This week started before a sync decided it.
        line1 = f"Fitness plan, week of {_ws(w).day} {_ws(w).strftime('%b')}.{trend}"
        line2 = (
            "This week's load is not chosen yet: pick it below, or the next sync applies "
            f"the recommendation ({CHOICE_LABEL[pending['recommended']]})."
        )
        return {"line1": line1, "line2": line2}
    if w["kind"] == "loading":
        since = loading_since_recovery(weeks, current)
        line1 = f"Fitness plan, loading week {since} of {cadence(plan)}"
        if since >= cadence(plan):
            line1 += ": a recovery week is due next"
        line1 += "." + trend
    else:
        line1 = f"Fitness plan, {w['kind']} week.{trend}"
    line2 = f"This week {round(w['target_tss'])} TSS target, {round(_done(w))} done"
    line2 += f"; {form_text}." if form_text else "."
    if pending:
        line2 += " Choose next week's load."
    else:
        nxt = weeks[current + 1] if current + 1 < len(weeks) else None
        if nxt:
            line2 += f" Next week {round(nxt['target_tss'])} TSS ({nxt['kind']})."
    return {"line1": line1, "line2": line2}
