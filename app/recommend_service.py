"""
Recommendation engine orchestration: build payload -> Claude -> persist.

The latest plan is cached to data/latest_recommendation.json for fast local reads
(GET /recommend/latest). It is the only copy: the InfluxDB `recommendations`
measurement it was once also written to had no reader and was dropped on
2026-11-07.

Days the athlete asked the coach to change (app/plan_requests.py) are kept by
default: Claude plans the week around them, and _apply_athlete_requests copies
them back over its output. keep_requests=False is the athlete's "overwrite"
answer -- a fresh plan, after which the requests are deleted.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from app import plan_requests
from app.ai.claude import generate_plan
from app.ai.prompt_builder import build_plan_payload
from app.metrics import load_trend
from app.plan_editor import LATEST_FILE, order_segments
from app.season import config as season_cfg

log = logging.getLogger("jymalaya.recommend")


def _enforce_rest_days(
    plan: dict, payload: dict, keep: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """
    Clear any session Claude put on a rest day and mark the day as rest.

    Rest days are the one guardrail the athlete cannot be talked out of, so it
    is enforced in code rather than left to the prompt: the model occasionally
    slips a "light" session onto one when the week is tight. Returns the dates
    that had to be cleared, for the log and the plan's warning_flags.

    `keep` are dates the athlete asked the coach to change: a session there is
    the athlete's own, insisted on through the coach, not one Claude slipped in,
    so it is neither cleared nor reported (_apply_athlete_requests owns them).
    """
    rest_dates = {
        d["date"] for d in payload.get("plan_days", []) if d.get("is_rest_day")
    } - set(keep)
    cleared = []
    for day in plan.get("days") or []:
        if day.get("date") not in rest_dates:
            continue
        if day.get("sessions"):
            cleared.append(day["date"])
            day["sessions"] = []
        day["is_rest_day"] = True
    return cleared


def _clear_pre_race_day(
    plan: dict, payload: dict, keep: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """
    Empty the day before the primary race (prompt rule 31).

    Whatever the distance or the disciplines, the athlete starts the race off a
    day with no session -- no shakeout, no openers -- and that holds even when
    athlete_custom_instructions ask for one, so it is enforced here as well as
    in the prompt. The day keeps its is_rest_day as generated: it is not a
    schedule rest day, it just has nothing on it. Returns the cleared dates, for
    the log and warning_flags. `keep` are athlete-requested dates, left alone as
    in _enforce_rest_days.
    """
    goal = payload.get("primary_goal") or {}
    try:
        race = date.fromisoformat(goal.get("race_date") or "")
    except (TypeError, ValueError):
        return []
    eve = (race - timedelta(days=1)).isoformat()
    if eve in keep:
        return []
    cleared = []
    for day in plan.get("days") or []:
        if isinstance(day, dict) and day.get("date") == eve and day.get("sessions"):
            day["sessions"] = []
            cleared.append(eve)
    return cleared


def _drop_completed_sessions(
    plan: dict, payload: dict, keep: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """
    Remove any session on today that restates a workout already logged today.

    Rule 5 asks Claude to leave finished work out, but a plan generated after a
    sync has the morning's workout in last_7_days_workouts, and the model has
    written it back as a session ("Completed earlier today", same minutes and
    TSS). The dashboard then draws the workout twice and the load forecast
    counts it twice, so this is enforced here as well. Returns the dropped
    session titles, for the log.

    A session matches a logged workout of the same discipline and a similar
    duration -- within the larger of 10 min or 25% of the logged one. Discipline
    alone is not enough: it would delete the race on race morning after a
    shakeout jog, or a real second session. Each logged workout cancels at most
    one session. `keep` are athlete-requested dates, left alone as in
    _enforce_rest_days.
    """
    today = payload.get("today_date")
    if not today or today in keep:
        return []
    done = [
        w
        for w in payload.get("last_7_days_workouts") or []
        if isinstance(w, dict) and w.get("date") == today
    ]
    if not done:
        return []
    dropped = []
    for day in plan.get("days") or []:
        if not isinstance(day, dict) or day.get("date") != today:
            continue
        remaining = []
        for session in day.get("sessions") or []:
            match = None
            if isinstance(session, dict):
                minutes = session.get("duration_min") or 0
                for w in done:
                    logged = w.get("duration_min") or 0
                    if w.get("discipline") == session.get("discipline") and abs(
                        minutes - logged
                    ) <= max(10, logged * 0.25):
                        match = w
                        break
            if match is None:
                remaining.append(session)
            else:
                done.remove(match)
                dropped.append(session.get("title") or session.get("discipline") or "session")
        day["sessions"] = remaining
    return dropped


def _apply_athlete_requests(plan: dict, requested: list[dict]) -> list[str]:
    """
    Put each day the athlete asked for back into the plan exactly as they left
    it -- enforced here, not trusted to the prompt, the same split as the rest
    days. Rule 24 asks Claude to copy these days verbatim and plan around them;
    this makes sure a drifting copy can never lose a request.

    Returns the dates where Claude's version differed, for the log. A date the
    plan does not have is skipped: build_plan_payload only sends dates inside
    plan_days, so it would mean a malformed plan, not a lost request.
    """
    by_date = {d.get("date"): d for d in plan.get("days") or [] if isinstance(d, dict)}
    drifted = []
    for req in requested:
        day = by_date.get(req.get("date"))
        if day is None:
            continue
        sessions = copy.deepcopy(req.get("sessions") or [])
        if day.get("sessions") != sessions:
            drifted.append(req["date"])
        day["sessions"] = sessions
        # Mirrors plan_editor._normalize_rest_flag: the day as the edit left it.
        day["is_rest_day"] = not sessions
        day["athlete_requested"] = True
    return drifted


def _check_season_target(plan: dict, payload: dict) -> str | None:
    """
    Compare the current season week's planned sessions with what is left of its
    target, and stamp the season plan's target and phase on the plan.

    Rule 26 asks Claude to land within TARGET_TOLERANCE of the budget. This only
    flags a miss -- a warning_flags entry and the log, never a silent rewrite of
    the sessions: the load inside a week is coaching, which stays in the prompt,
    the same split as the 80/20 rule. Under the budget is fine when rules 17-20
    or deep fatigue make it a ceiling (rule 28), so that is not flagged.

    weekly_tss_target and periodization_phase are overwritten from the season
    plan: the code owns them, whatever the model wrote. Returns the flag, if any.

    window_budget_tss is the budget of the plan's whole window, for the sync
    summary. It is stamped before the current-week check: a season plan starting
    later in the window has no current week, yet its days there have a budget.
    """
    season = payload.get("season_plan") or {}
    weeks = season.get("weeks") or []
    if weeks:
        plan["window_budget_tss"] = sum(w.get("budget_tss") or 0 for w in weeks)
    week = next((w for w in weeks if w.get("is_current_week")), None)
    if week is None:
        return None
    plan["weekly_tss_target"] = week["target_tss"]
    plan["periodization_phase"] = "taper" if week["phase"] == "race" else week["phase"]

    dates = set(week.get("plan_dates") or [])
    planned = round(
        sum(
            (s.get("estimated_tss") or 0)
            for d in plan.get("days") or []
            if isinstance(d, dict) and d.get("date") in dates
            for s in d.get("sessions") or []
            if isinstance(s, dict)
        )
    )
    budget = week.get("budget_tss") or 0
    tolerance = max(budget * season_cfg.TARGET_TOLERANCE, 1)
    tsb = (payload.get("current_load") or {}).get("tsb")
    # A lighter week after a monotonous load (rule 32) makes the budget a
    # ceiling too: planning under it is the point.
    ceiling_only = (
        bool(payload.get("recent_race"))
        or bool(payload.get("load_reduction"))
        or (tsb is not None and tsb < season_cfg.GATE_LOWER_TSB)
    )
    found = []
    if planned > budget + tolerance or (planned < budget - tolerance and not ceiling_only):
        found.append(
            f"This week's sessions add up to {planned} TSS against the {budget} TSS "
            f"left of the season plan's {week['target_tss']} TSS target."
        )
    split_flag = _check_discipline_split(plan, week, dates, ceiling_only)
    if split_flag:
        found.append(split_flag)
    if not found:
        return None
    for flag in found:
        log.warning("Season target missed: %s", flag)
    flags = plan.get("warning_flags")
    plan["warning_flags"] = (flags if isinstance(flags, list) else []) + found
    return " ".join(found)


def _check_discipline_split(plan: dict, week: dict, dates: set, ceiling_only: bool) -> str | None:
    """
    Rule 29: each discipline's sessions this week against its share of what is
    left. Off by more than SPLIT_TOLERANCE (never under SPLIT_MIN_TSS) is
    flagged, never rewritten; under is fine in rule 28's ceiling cases.
    """
    budgets = week.get("discipline_budget_tss")
    if not budgets:
        return None
    planned: dict[str, float] = {}
    for d in plan.get("days") or []:
        if not isinstance(d, dict) or d.get("date") not in dates:
            continue
        for s in d.get("sessions") or []:
            if isinstance(s, dict) and s.get("discipline") in budgets:
                planned[s["discipline"]] = planned.get(s["discipline"], 0) + (s.get("estimated_tss") or 0)
    off = []
    for disc, budget in budgets.items():
        got = round(planned.get(disc, 0))
        tolerance = max(budget * season_cfg.SPLIT_TOLERANCE, season_cfg.SPLIT_MIN_TSS)
        if got > budget + tolerance or (got < budget - tolerance and not ceiling_only):
            off.append(f"{disc} {got} TSS against {budget}")
    if not off:
        return None
    return "This week's split is off the season plan's: " + ", ".join(off) + "."


def _check_load_reduction(plan: dict, payload: dict) -> str | None:
    """
    Rule 32: after six flat or only-rising weeks the window's sessions should
    add up to at most target_tss. Stamps the block on the plan (the dashboard
    and the coach read it there) and flags a plan more than TARGET_TOLERANCE
    over it -- a warning_flags entry and the log, never a rewrite of the
    sessions, the same split as _check_season_target. Under is fine: the
    target is a ceiling. Returns the flag, if any.
    """
    block = payload.get("load_reduction")
    if not block:
        return None
    plan["load_reduction"] = copy.deepcopy(block)
    planned = round(
        sum(
            (s.get("estimated_tss") or 0)
            for d in plan.get("days") or []
            if isinstance(d, dict)
            for s in d.get("sessions") or []
            if isinstance(s, dict)
        )
    )
    plan["load_reduction"]["planned_tss"] = planned
    target = block.get("target_tss") or 0
    if planned <= target * (1 + load_trend.TARGET_TOLERANCE):
        return None
    flag = (
        f"The next 7 days add up to {planned} TSS against the lighter-week target "
        f"of {target} TSS ({block.get('reduction_pct')}% below last week)."
    )
    log.warning("Lighter-week target missed: %s", flag)
    flags = plan.get("warning_flags")
    plan["warning_flags"] = (flags if isinstance(flags, list) else []) + [flag]
    return flag


def _order_all_segments(plan: dict) -> None:
    """Warmup first, cooldown last in every session (see order_segments)."""
    for day in plan.get("days") or []:
        for session in day.get("sessions") or []:
            if isinstance(session, dict) and session.get("structure"):
                session["structure"] = order_segments(session["structure"])


def run_recommendation(trigger: str = "manual", keep_requests: bool = True) -> dict:
    """
    Generate next week's plan and persist it. Raises ClaudeError on API failure.

    `keep_requests` is the athlete's answer to "keep your plan changes?": True
    builds the week around the days they asked the coach to change; False
    builds a fresh plan and then deletes those requests -- only once the new
    plan is saved, so a failed Claude call loses nothing.
    """
    payload = build_plan_payload(include_athlete_requests=keep_requests)
    requested = (payload.get("athlete_requested_days") or {}).get("days") or []
    plan = generate_plan(payload)

    requested_dates = {r["date"] for r in requested}
    cleared = _enforce_rest_days(plan, payload, keep=requested_dates)
    if cleared:
        log.warning("Dropped sessions Claude scheduled on rest days: %s", ", ".join(cleared))
        flags = plan.get("warning_flags")
        plan["warning_flags"] = (flags if isinstance(flags, list) else []) + [
            f"Removed a session the coach placed on your rest day ({d})." for d in cleared
        ]
    eve = _clear_pre_race_day(plan, payload, keep=requested_dates)
    if eve:
        log.warning("Dropped sessions Claude scheduled the day before the race: %s", ", ".join(eve))
        flags = plan.get("warning_flags")
        plan["warning_flags"] = (flags if isinstance(flags, list) else []) + [
            f"Removed a session the coach placed the day before your race ({d})." for d in eve
        ]
    echoed = _drop_completed_sessions(plan, payload, keep=requested_dates)
    if echoed:
        log.warning(
            "Dropped sessions restating a workout already logged today: %s",
            ", ".join(echoed),
        )
    drifted = _apply_athlete_requests(plan, requested)
    if drifted:
        log.warning(
            "Restored athlete-requested days Claude did not copy verbatim: %s",
            ", ".join(drifted),
        )
    # After the requests are restored: they count toward the week's target.
    _check_season_target(plan, payload)
    _check_load_reduction(plan, payload)
    _order_all_segments(plan)

    plan["_meta"] = {
        "plan_id": uuid.uuid4().hex,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
    }
    if requested:
        plan["_meta"]["athlete_requests"] = "kept"
    elif not keep_requests:
        plan["_meta"]["athlete_requests"] = "overwritten"

    LATEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    if not keep_requests:
        plan_requests.clear_requests()

    return plan


def latest_recommendation() -> dict | None:
    if LATEST_FILE.exists():
        try:
            return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
    return None
