"""
Assemble the input data package for the Claude recommendation engine
and the system context for the coach chat, pulling live data from InfluxDB,
config YAML, and goals. (Weather is deliberately NOT sent to Claude — it stays
a dashboard-only panel.)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from app import events_service, plan_requests
from app.ai.claude import SETTINGS_APPROVAL_MARKER, training_zones_text
from app.config import DEFAULT_VO2MAX_DECLINE_THRESHOLD, get_athlete_profile
from app.instructions import get_instructions
from app.models import race_disciplines
from app.schedule_store import get_schedule
from app.influx.client import InfluxRepo, drop_split_brick_parents
from app.influx.queries import (
    daily_load_all_last_n_days,
    daily_load_last_n_days,
    vo2max_last_n_days,
    workout_by_activity_id,
    workouts_last_n_days,
)
from app.metrics import load_trend
from app.metrics.calculator import _row_date, pad_load_to

DISCIPLINES = ("swim", "bike", "run", "strength")

# The workout history every plan payload is built from. One Influx read feeds
# the weekly summary, the last-7-days list and the race-day lookup.
PLAN_HISTORY_DAYS = 28
# How long a finished race still shapes the plan. The recovery table in
# prompts/recommendation_system.txt runs out at day 22, so a race older than
# this has nothing left to say. MUST stay under PLAN_HISTORY_DAYS: the race-day
# activity is looked up in that same list, so a longer window would silently
# report every older race as "nothing logged".
RECENT_RACE_DAYS = 21

# Garmin's own VO2max reaches the plan as a 4-week TREND, never as the 28 daily
# points -- those stay the dashboard's panel (GET /metrics/vo2max). 28 days is
# exactly four seven-day buckets, listed oldest first like _weekly_summary's TSS.
VO2MAX_TREND_DAYS = 28
VO2MAX_TREND_WEEKS = 4
# How recent the newest week with a reading must be before a direction is
# claimed at all. Readings stop entirely when the qualifying activity stops
# (cycling VO2max needs a power meter), and a pair of three-week-old averages
# must not be what puts Z5 intervals in the week. How big a drop counts is the
# athlete's own vo2max_decline_threshold, not a constant here.
VO2MAX_TREND_FRESH_WEEKS = 2


def _workout_date(w: dict) -> str | None:
    """
    The athlete's LOCAL calendar day for a stored workout row.

    The stored `date` field is authoritative -- a calendar day must never be
    derived from the UTC timestamp, which falls on the next day for an evening
    session. Rows written before that field existed fall back to the local date
    of the UTC instant.
    """
    stored = w.get("date")
    if stored:
        return stored
    t = w.get("time")
    return t.astimezone().date().isoformat() if isinstance(t, datetime) else None


def _latest_load(repo: InfluxRepo, discipline: str) -> dict:
    """
    Load as of TODAY, not as of the last workout. The stored series is padded
    forward with zero-TSS rest days first (pad_load_to returns them in order),
    so a rest streak reaches Claude as recovered fatigue and rising form rather
    than the frozen peak-ATL day it last trained on.
    """
    rows = pad_load_to(repo.query(daily_load_last_n_days(60, discipline)))
    return rows[-1] if rows else {}


def _latest_loads(repo: InfluxRepo) -> dict[str, dict]:
    """
    _latest_load for every discipline at once, keyed by discipline ("all" is
    the combined series). One Influx read instead of one per discipline -- this
    runs on every plan build and every coach-chat turn. A discipline with no
    stored series is simply absent.
    """
    by_discipline: dict[str, list[dict]] = {}
    for row in repo.query(daily_load_all_last_n_days(60)):
        by_discipline.setdefault(row.get("discipline") or "all", []).append(row)
    return {d: pad_load_to(rows)[-1] for d, rows in by_discipline.items()}


def _weekly_summary(workouts: list[dict], weeks: int = 4) -> dict:
    """Weekly TSS + hours for the last `weeks` weeks, oldest first."""
    tss = [0.0] * weeks
    hours = [0.0] * weeks
    now = datetime.now(timezone.utc)
    for w in workouts:
        t = w.get("time")
        if not isinstance(t, datetime):
            continue
        wi = int((now - t).total_seconds() // (7 * 86400))
        if 0 <= wi < weeks:
            pos = weeks - 1 - wi
            tss[pos] += w.get("tss") or 0
            hours[pos] += (w.get("duration_sec") or 0) / 3600.0
    return {
        "weekly_tss": [round(x) for x in tss],
        "weekly_hours": [round(x, 1) for x in hours],
    }


def _vo2max_trend(rows: list[dict], today: date, threshold: float) -> dict:
    """
    Garmin's 4-week VO2max trend per discipline -- the TREND ONLY, never the 28
    daily readings: the plan reacts to a direction, and the daily series is the
    dashboard panel's job (GET /metrics/vo2max).

    `rows` is vo2max_last_n_days as read: one row per (day, discipline), both
    disciplines interleaved in one list, each stamped midnight UTC. _row_date is
    the canonical reader for that convention -- the point's UTC date IS the
    calendar day, and converting through the local zone shifts it west -- and it
    also accepts the `date` string a hand-built row may carry.

    The series is sparse: Garmin emits a day only when it has an estimate, and
    cycling VO2max needs a power meter. So a week can be empty (null in
    weekly_avg) and a discipline with no readings at all is left OUT rather than
    zero-filled, the same call the dashboard's vo2maxTrend makes. A row with no
    numeric `vo2max` is skipped in silence -- a fake repo that ignores the flux
    answers this read with workout rows, and a workout row carries a
    `discipline` too.

    `direction` is decided HERE, not by Claude: derived metrics in code, Claude
    interprets them. It gates rule 22 of the plan prompt, so it has to be
    deterministic and testable. `threshold` is the athlete's own
    vo2max_decline_threshold in ml/kg/min -- below it the change is Garmin's
    noise and the direction is "stable".

    Per discipline: {"weekly_avg": [4 values, oldest first, null for an empty
    week], "current": the newest reading, "change": newest populated week minus
    oldest populated week, "direction": "declining" | "rising" | "stable" |
    "insufficient_data"}.
    """
    weeks = VO2MAX_TREND_WEEKS
    per_week: dict[str, list[list[float]]] = {}
    newest: dict[str, tuple[date, float]] = {}

    for row in rows:
        try:
            value = round(float(row["vo2max"]), 1)
        except (KeyError, TypeError, ValueError):
            continue  # not a vo2max row, or a day Garmin had no estimate for
        discipline = row.get("discipline")
        day = _row_date(row)
        if not discipline or day is None:
            continue
        # 0 = the last 7 days, 3 = 21-27 days ago; stored the other way round,
        # oldest first, exactly as _weekly_summary does.
        wi = (today - day).days // 7
        if not 0 <= wi < weeks:
            continue
        per_week.setdefault(discipline, [[] for _ in range(weeks)])[
            weeks - 1 - wi
        ].append(value)
        if discipline not in newest or day >= newest[discipline][0]:
            newest[discipline] = (day, value)

    out: dict[str, dict] = {}
    # Iterating DISCIPLINES fixes the key order and drops a tag that is not one
    # of the app's four; Garmin only ever yields run and bike here.
    for discipline in DISCIPLINES:
        buckets = per_week.get(discipline)
        if not buckets:
            continue
        avg = [round(sum(v) / len(v), 1) if v else None for v in buckets]
        seen = [i for i, a in enumerate(avg) if a is not None]
        if len(seen) >= 2 and seen[-1] >= weeks - VO2MAX_TREND_FRESH_WEEKS:
            change = round(avg[seen[-1]] - avg[seen[0]], 1)
            if change <= -threshold:
                direction = "declining"
            elif change >= threshold:
                direction = "rising"
            else:
                direction = "stable"
        else:
            # One week to go on, or nothing recent enough: no claim either way.
            # `change` stays null rather than 0.0, which would read as a
            # measured "no change" and invite the model to call it stable.
            change, direction = None, "insufficient_data"
        out[discipline] = {
            "weekly_avg": avg,
            "current": newest[discipline][1],
            "change": change,
            "direction": direction,
        }
    return out


def _recent_workouts(workouts: list[dict], days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for w in workouts:
        t = w.get("time")
        if isinstance(t, datetime) and t >= cutoff:
            out.append(
                {
                    "date": _workout_date(w),
                    "discipline": w.get("discipline"),
                    "title": w.get("title"),
                    "duration_min": round((w.get("duration_sec") or 0) / 60),
                    "distance_km": round((w.get("distance_m") or 0) / 1000, 2),
                    "tss": w.get("tss"),
                    "avg_hr": w.get("avg_hr"),
                    "intensity_factor": w.get("intensity_factor"),
                }
            )
    return sorted(out, key=lambda x: x["date"])


def _goal_block(goal: dict | None, today: date) -> dict | None:
    if not goal:
        return None
    weeks_until = None
    try:
        race = datetime.strptime(goal["race_date"], "%Y-%m-%d").date()
        weeks_until = max(0, (race - today).days // 7)
    except (ValueError, KeyError, TypeError):
        pass
    return {
        "name": goal.get("name"),
        "type": goal.get("type"),
        "race_disciplines": race_disciplines(goal.get("type")),
        "race_date": goal.get("race_date"),
        "weeks_until_race": weeks_until,
        "target_finish_time_sec": goal.get("target_finish_time_sec"),
        "sub_goals": goal.get("sub_goals", []),
    }


def _sub_events_block(sub_events: list[dict], today: date) -> list[dict]:
    """The season plan's sub events as the 7-day plan reads them (rule 33)."""
    out = []
    for e in sub_events:
        try:
            days = (date.fromisoformat(e.get("race_date") or "") - today).days
        except ValueError:
            continue
        out.append(
            {
                "name": e.get("name"),
                "type": e.get("type"),
                "race_disciplines": race_disciplines(e.get("type")),
                "race_date": e.get("race_date"),
                "days_until": days,
            }
        )
    return out


def _race_day_activity(workouts: list[dict], race_date: str) -> dict | None:
    """
    What the athlete ACTUALLY did on race day, from the same history the rest of
    the payload is built from -- post-race recovery is sized from the measured
    load, not from what the distance usually costs.

    A triathlon race day is several rows (bricks are stored split, sharing a
    session_id), so everything is aggregated. `workouts` has already been
    through drop_split_brick_parents, so a leg is never counted twice.
    """
    rows = [w for w in workouts if _workout_date(w) == race_date]
    if not rows:
        return None
    # The stored order is not chronological (the brick parent is dropped and the
    # legs come back grouped), so sort explicitly: a triathlon must read swim,
    # bike, run. Rows with no timestamp keep their relative position at the end.
    rows.sort(key=lambda w: (w.get("time") is None, w.get("time")))

    duration_sec = sum(w.get("duration_sec") or 0 for w in rows)
    tss = sum(w.get("tss") or 0 for w in rows)

    # Duration-weighted IF: a 6-min transition jog must not average equally with
    # a 5-hour ride. Rows with no IF stay out of both sides of the ratio rather
    # than counting as zero.
    rated = [w for w in rows if w.get("intensity_factor") and w.get("duration_sec")]
    rated_sec = sum(w["duration_sec"] for w in rated)
    intensity = (
        round(
            sum(w["intensity_factor"] * w["duration_sec"] for w in rated) / rated_sec,
            2,
        )
        if rated_sec
        else None
    )

    # In the order they were done, de-duplicated.
    disciplines = list(
        dict.fromkeys(w.get("discipline") for w in rows if w.get("discipline"))
    )

    return {
        "tss": round(tss) or None,
        "duration_min": round(duration_sec / 60) or None,
        "intensity_factor": intensity,
        "disciplines": disciplines,
        "session_count": len(rows),
    }


def _recent_race_block(
    goal: dict | None, today: date, workouts: list[dict]
) -> dict | None:
    """
    A race the athlete has already done, for the post-race recovery rules. Not a
    goal to train for -- deliberately NOT built from _goal_block, whose
    weeks_until_race floors at 0 and would read as a race still to come.
    """
    if not goal:
        return None
    try:
        raced = date.fromisoformat(goal.get("race_date") or "")
    except (ValueError, TypeError):
        return None
    return {
        "name": goal.get("name"),
        "type": goal.get("type"),
        "race_disciplines": race_disciplines(goal.get("type")),
        "race_date": goal["race_date"],
        "days_since_race": (today - raced).days,
        # null when nothing was logged that day -- the prompt tests this by name.
        "measured": _race_day_activity(workouts, goal["race_date"]),
    }


def _profile_for_claude(profile: dict) -> dict:
    """
    The athlete profile as Claude sees it. Age is what coaching reasons about;
    the birthday it is derived from stays local.
    """
    return {k: v for k, v in profile.items() if k != "birthday"}


def _schedule_for_claude(week: dict) -> dict:
    """
    The training week as Claude sees it: `rest` is the only availability
    signal, so drop the redundant `available` (two ways to say the same thing
    invited the model to read one and ignore the other) and strip a rest day
    down to that single flag.
    """
    out = {}
    for day, cfg in week.items():
        if cfg.get("rest"):
            out[day] = {"rest": True}
        else:
            out[day] = {k: v for k, v in cfg.items() if k != "available"}
    return out


def _athlete_requested_block(requests: list[dict], window: set[str]) -> dict | None:
    """
    The days the athlete asked the coach to change, as fixed input for rule 24.

    Only dates inside this plan's window: a request for a later day cannot
    exist (edits target the current plan) and an earlier one has expired. The
    totals are worked out here rather than left to Claude -- they are what the
    free days get budgeted against, so they must be exact.
    """
    days = [r for r in requests if r.get("date") in window]
    if not days:
        return None
    sessions = [s for d in days for s in d.get("sessions") or [] if isinstance(s, dict)]
    return {
        "days": [
            {
                "date": d["date"],
                "day_name": d["day_name"],
                "sessions": d.get("sessions") or [],
                "athlete_request": " ".join(d.get("notes") or []) or None,
            }
            for d in days
        ],
        "totals": {
            "training_days": sum(1 for d in days if d.get("sessions")),
            "days_off": sum(1 for d in days if not d.get("sessions")),
            "sessions": len(sessions),
            "duration_min": round(sum(s.get("duration_min") or 0 for s in sessions)),
            "estimated_tss": round(sum(s.get("estimated_tss") or 0 for s in sessions)),
        },
    }


def build_plan_payload(include_athlete_requests: bool = True) -> dict:
    """
    The full data package sent to Claude for plan generation.

    `include_athlete_requests=False` leaves out the days the athlete asked the
    coach to change -- the athlete chose to overwrite them with a fresh plan.
    """
    today = date.today()
    profile = get_athlete_profile()
    # The training week the athlete configured in Settings.
    schedule = get_schedule()
    week = schedule.get("schedule", {})

    repo = InfluxRepo()
    try:
        workouts = drop_split_brick_parents(
            repo.query(workouts_last_n_days(PLAN_HISTORY_DAYS))
        )
        # Rule 32: six complete weeks of TSS, a longer read than the 28 days
        # above, which must stay as it is (RECENT_RACE_DAYS depends on it).
        load_reduction = load_trend.reduction_block(
            drop_split_brick_parents(
                repo.query(workouts_last_n_days(load_trend.HISTORY_DAYS))
            ),
            today,
        )
        loads = _latest_loads(repo)
        load_all = loads.get("all", {})
        ctl_per_discipline = {d: loads.get(d, {}).get("ctl") for d in DISCIPLINES}
        # Trend only -- the 28 daily points stay the dashboard's panel.
        vo2max_trend = _vo2max_trend(
            repo.query(vo2max_last_n_days(VO2MAX_TREND_DAYS)),
            today,
            # get_athlete_profile fills this in, but not every caller of this
            # module gets its profile from there -- fall back rather than
            # failing a whole plan over one missing tunable.
            profile.get("vo2max_decline_threshold")
            or DEFAULT_VO2MAX_DECLINE_THRESHOLD,
        )
    finally:
        repo.close()

    # With an active event season plan the goal is its main event, so a nearer
    # sub event never takes over rule 9; otherwise the nearest race, as before.
    from app.season import service as season_service  # imports this module

    goal = season_service.goal_event()
    primary_goal = _goal_block(goal, today)
    sub_events = _sub_events_block(season_service.upcoming_sub_events(today), today)
    recent_race = _recent_race_block(
        events_service.last_race(within_days=RECENT_RACE_DAYS), today, workouts
    )

    payload = {
        "athlete_profile": _profile_for_claude(profile),
        "training_schedule": _schedule_for_claude(week),
        "schedule_constraints": schedule.get("constraints", {}),
        "current_load": {
            "ctl": load_all.get("ctl"),
            "atl": load_all.get("atl"),
            "tsb": load_all.get("tsb"),
            "ctl_per_discipline": ctl_per_discipline,
        },
        "last_4_weeks_summary": _weekly_summary(workouts),
        # Present only when six weeks of TSS were flat or only climbing (rule
        # 32): the next 7 days are planned lighter. Absent otherwise.
        **({"load_reduction": load_reduction} if load_reduction else {}),
        # Garmin's VO2max direction, next to the TSS trend it is read against.
        # Absent entirely when there are no readings at all -- the recent_race
        # precedent -- since an empty block invites the model to narrate a trend
        # that is not there.
        **({"vo2max_trend": vo2max_trend} if vo2max_trend else {}),
        "last_7_days_workouts": _recent_workouts(workouts),
        "primary_goal": primary_goal,
        # Present only when the athlete raced in the last RECENT_RACE_DAYS days;
        # absent otherwise, like athlete_custom_instructions. Sits next to
        # primary_goal so the model sees both race facts together.
        **({"recent_race": recent_race} if recent_race else {}),
        # The season plan's sub events from today on (rule 33). Absent when
        # there are none, like recent_race.
        **({"sub_events": sub_events} if sub_events else {}),
        "today_date": today.isoformat(),
        # Rolling 7-day window starting today. The explicit date list saves the
        # model from doing weekday arithmetic when the window starts mid-week,
        # and carries each date's rest flag so a rest day cannot be missed by
        # cross-referencing the weekday against training_schedule.
        "plan_week_start": today.isoformat(),
        "plan_days": [
            {
                "date": day.isoformat(),
                "day_name": day.strftime("%A"),
                "is_rest_day": bool(
                    (week.get(day.strftime("%A").lower()) or {}).get("rest")
                ),
            }
            for day in (today + timedelta(days=i) for i in range(7))
        ],
    }

    # Days the athlete asked the coach to change (rule 24): fixed input the rest
    # of the week is planned around. Absent when there are none, like
    # recent_race; recommend_service copies them back over Claude's output.
    requested = (
        _athlete_requested_block(
            plan_requests.active_requests(today),
            {d["date"] for d in payload["plan_days"]},
        )
        if include_athlete_requests
        else None
    )
    if requested:
        payload["athlete_requested_days"] = requested

    # The season plan's weeks that this window touches (rules 25-28): the
    # week's phase, kind and TSS budget come from code, not from rule 9's
    # weeks_until_race. Absent with no active plan, like recent_race.
    from app.season import service as season_service  # imports this module

    season = season_service.payload_block(today, [d["date"] for d in payload["plan_days"]])
    if season:
        payload["season_plan"] = season

    # Athlete's standing coach instructions; the key is absent when none are
    # set, so nothing extra reaches Claude.
    instructions = get_instructions()
    if instructions:
        payload["athlete_custom_instructions"] = instructions
    return payload


# How far back to look for comparable sessions when reviewing one workout.
SIMILAR_SESSION_DAYS = 28
MAX_SIMILAR_SESSIONS = 6


def load_stored_workout(repo: InfluxRepo, activity_id: str) -> dict | None:
    """One stored workout row by activity_id, or None when it isn't stored."""
    rows = drop_split_brick_parents(repo.query(workout_by_activity_id(activity_id)))
    return rows[-1] if rows else None


def _workout_block(workout: dict) -> dict:
    """The stored workout, shaped for the analysis payload."""
    out = {
        "activity_id": workout.get("activity_id"),
        "date": _workout_date(workout),
        "discipline": workout.get("discipline"),
        "title": workout.get("title"),
        "is_brick_split": workout.get("is_brick_split"),
        "duration_min": round((workout.get("duration_sec") or 0) / 60, 1) or None,
        "distance_km": round((workout.get("distance_m") or 0) / 1000, 2) or None,
    }
    for key in (
        "tss",
        "intensity_factor",
        "avg_hr",
        "max_hr",
        "avg_power",
        "max_power",
        "normalized_power",
        "avg_cadence",
        "avg_pace_sec_per_km",
        # Normalized Graded Pace: the pace the run's TSS was computed from.
        "ngp_sec_per_km",
        "avg_pace_sec_per_100m",
        "elevation_gain_m",
        "elevation_loss_m",
        "calories",
    ):
        if workout.get(key) is not None:
            out[key] = workout[key]
    zones = workout.get("zones_time_sec")
    if zones:
        try:
            out["time_in_hr_zones_sec"] = json.loads(zones)
        except (TypeError, ValueError):
            pass
    return {k: v for k, v in out.items() if v is not None}


def _similar_sessions(workouts: list[dict], workout: dict) -> list[dict]:
    """
    Recent sessions in the same discipline, for a like-for-like comparison --
    the reference the analysis needs to call a session a step forward or a
    plateau. The workout under review is excluded.
    """
    discipline = workout.get("discipline")
    activity_id = workout.get("activity_id")
    cutoff = datetime.now(timezone.utc) - timedelta(days=SIMILAR_SESSION_DAYS)
    out = []
    for w in workouts:
        t = w.get("time")
        if w.get("discipline") != discipline or w.get("activity_id") == activity_id:
            continue
        if not isinstance(t, datetime) or t < cutoff:
            continue
        out.append(
            {
                "date": _workout_date(w),
                "title": w.get("title"),
                "duration_min": round((w.get("duration_sec") or 0) / 60),
                "distance_km": round((w.get("distance_m") or 0) / 1000, 2),
                "tss": w.get("tss"),
                "avg_hr": w.get("avg_hr"),
                "avg_power": w.get("avg_power"),
                "avg_pace_sec_per_km": w.get("avg_pace_sec_per_km"),
                "intensity_factor": w.get("intensity_factor"),
            }
        )
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:MAX_SIMILAR_SESSIONS]


# Column order for the analysis stream; any other key follows, sorted.
STREAM_COLUMNS = ("t", "hr", "speed_mps", "power_w", "cadence", "elevation_m", "distance_m")


def _columnar_samples(samples: list[dict]) -> dict:
    """
    The analysis stream as column names once plus one value list per row.

    A list of objects repeats every field name in every row; measured on
    2026-11-07 (162 rows, 7 fields) this layout is 56% fewer tokens for the
    same values. A column appears only when some row has it, and a value a row
    lacks is null.
    """
    present = {k for s in samples for k in s}
    columns = [c for c in STREAM_COLUMNS if c in present]
    columns += sorted(present - set(STREAM_COLUMNS))
    return {"columns": columns, "rows": [[s.get(c) for c in columns] for s in samples]}


def build_analysis_payload(
    workout: dict,
    *,
    stream: dict | None,
    laps: list[dict],
    planned_workout: dict | None,
    derived_metrics: dict,
) -> dict:
    """
    The data package for a single-workout analysis (PRD section 7.9).

    Mirrors build_plan_payload's sourcing -- athlete profile, current load padded
    to today, recent history from Influx -- but scoped to one session, with the
    Garmin detail and the pre-computed derived metrics the prompt reads. Weather
    stays out here too.
    """
    profile = get_athlete_profile()
    repo = InfluxRepo()
    try:
        history = drop_split_brick_parents(
            repo.query(workouts_last_n_days(SIMILAR_SESSION_DAYS))
        )
        load = _latest_load(repo, "all")
    finally:
        repo.close()

    payload = {
        "today_date": date.today().isoformat(),
        "athlete_profile": _profile_for_claude(profile),
        "workout": _workout_block(workout),
        "current_load": {
            "ctl": load.get("ctl"),
            "atl": load.get("atl"),
            "tsb": load.get("tsb"),
        },
        "recent_similar_sessions": _similar_sessions(history, workout),
        "planned_workout": planned_workout,
        "derived_metrics": derived_metrics,
        "laps": laps,
    }
    if stream and stream.get("samples"):
        payload["sample_stream"] = {
            "bucket_sec": stream.get("bucket_sec"),
            "note": (
                "One row per bucket_sec seconds, averaged. Each row lists its "
                "values in the order of columns; null means not recorded. "
                "Recording pauses are removed, so t is time spent training. No "
                "coordinates are collected."
            ),
            **_columnar_samples(stream["samples"]),
        }
    from app.season import service as season_service  # imports this module

    goal = season_service.goal_event()
    if goal:
        payload["primary_goal"] = _goal_block(goal, date.today())
    instructions = get_instructions()
    if instructions:
        payload["athlete_custom_instructions"] = instructions
    return payload


def build_general_coach_context() -> str:
    """
    The coach's system prompt with the athlete's data left out — used when the
    athlete unticks "Include context" in the chat to ask a general question
    (nutrition, transitions, technique, race-day logistics).

    Nothing is read from InfluxDB, the profile, the schedule or the cached plan,
    and the caller must run the turn without the plan-editing tools: a model
    that cannot see the plan must not be able to rewrite it. Today's date is
    still anchored — it is not training data, and without it "the day before my
    race" style questions go wrong the same way they did in the full context.
    """
    today = date.today()
    return (
        "You are the athlete's personal triathlon coach, grounded in the 80/20 "
        "methodology. Be specific and actionable. Keep answers short — a few "
        "concise sentences; simple markdown (bold, short lists) is fine, but no "
        "headers or tables unless asked.\n\n"
        f"Today is {today.strftime('%A')}, {today.isoformat()}.\n\n"
        "When you talk about pace, remember it is time per distance: a larger "
        "min:sec is slower, so easy zones are always slower than threshold "
        "pace. Give running pace in min:sec per km and swim pace in min:sec per "
        "100 m -- never in seconds -- and cycling speed in km/h.\n\n"
        "For this question the athlete has turned OFF sharing their training "
        "data, so you have NO access to their profile, thresholds, training "
        "history, current form or plan. Answer from general triathlon coaching "
        "knowledge — nutrition, hydration, transitions, technique, pacing "
        "principles, gear, race-day logistics, recovery, physiology. Never "
        "invent or assume their numbers, schedule or planned sessions, and "
        "never claim to have looked at their data. If the question genuinely "
        "needs it, say so and tell them to switch \"Include context\" on and ask "
        "again. You have no tools this turn, so you cannot change their plan; "
        "if they ask for a plan change, say the same thing."
    )


def build_coach_context() -> str:
    """Compact system prompt giving the coach the athlete's current situation."""
    payload = build_plan_payload()
    load = payload["current_load"]
    goal = payload.get("primary_goal")
    goal_line = (
        f"{goal['name']} ({goal['type']}), {goal['weeks_until_race']} weeks out"
        if goal
        else "no race goal set"
    )

    # Where the athlete stands on the season plan, so "what's my plan this
    # week?" has an answer without a tool call. Absent without a plan.
    from app.season import service as season_service  # imports this module

    season = season_service.coach_line(date.fromisoformat(payload["today_date"]))
    season_line = f"{season}\n" if season else ""
    # Rule 33's races, so "how should I run the 10K?" is answered against the
    # main event, not as if the 10K were the goal.
    subs = payload.get("sub_events") or []
    sub_events_line = (
        "Sub events (stepping stones before the goal, which stays the goal): "
        + "; ".join(f"{e['name']} ({e['type']}) on {e['race_date']}, in {e['days_until']} days" for e in subs)
        + ".\n"
        if subs
        else ""
    )

    # The most likely question the day after a race is "can I run tomorrow?".
    # Without this the coach sees "no race goal set" and a TSB that has not
    # caught up yet, and brings quality work forward into the recovery window.
    raced = payload.get("recent_race")
    if raced:
        measured = raced.get("measured")
        effort = (
            f"measured {measured.get('tss')} TSS over "
            f"{measured.get('duration_min')} min"
            if measured
            else "nothing was logged on race day"
        )
        recent_race_line = (
            f"\nRaced {raced['days_since_race']} days ago: {raced['name']} "
            f"({raced['type']}) on {raced['race_date']}, {effort}. The plan is in "
            "post-race recovery -- do not bring quality work forward of what the "
            "plan schedules unless the athlete insists after you say so.\n"
        )
    else:
        recent_race_line = ""

    from app.recommend_service import latest_recommendation

    plan = latest_recommendation()
    # The lighter week the plan was built as (rule 32), so the coach does not
    # argue it back up. Read off the cached plan: it is the week the athlete has.
    reduction = (plan or {}).get("load_reduction") or {}
    reduction_line = (
        f"Planned lighter week: {reduction['message']} Keep the 7-day plan near "
        f"{reduction['target_tss']} TSS unless the athlete insists after you say why.\n"
        if reduction.get("message") and reduction.get("target_tss")
        else ""
    )
    if plan:
        plan_block = (
            f"\nThis week's plan: {plan.get('summary', '')} "
            f"(phase: {plan.get('periodization_phase', '?')}, "
            f"target {plan.get('weekly_tss_target', '?')} TSS).\n"
            "Plan days JSON (sessions are addressed by day date + zero-based index "
            "within the day):\n"
            + json.dumps(plan.get("days", []), separators=(",", ":"))
        )
    else:
        plan_block = (
            "\nNo generated plan exists yet — tell the athlete to trigger a "
            "recommendation if they ask about it."
        )

    rest_dates = [
        f"{d['day_name']} {d['date']}" for d in payload["plan_days"] if d["is_rest_day"]
    ]
    rest_line = (
        f"\nRest days in this window (never plan work on these): {', '.join(rest_dates)}."
        if rest_dates
        else "\nThe athlete has no rest days set this window."
    )

    # The date anchor. Without it the model has only the plan's ISO dates to go
    # on and guesses which one is "today" -- it read a mid-week question as if it
    # were the long-run day and gave advice for the wrong day. The cached plan
    # can also start on an earlier date than today, so say both.
    today = date.fromisoformat(payload["today_date"])
    today_line = (
        f"Today is {today.strftime('%A')}, {today.isoformat()}. "
        "Anchor every reference to \"today\", \"tomorrow\" and \"this week\" to that "
        "date -- never infer the current day from the plan, which may start on an "
        "earlier date.\n"
    )

    tools_rules = (
        "\n\nYou have tools to edit the stored plan (update/move/remove/add a "
        "session). Use them ONLY when the athlete explicitly asks to change the "
        "plan — never for questions or advice. When editing: keep the week close "
        "to 80/20 easy/hard; never exceed the athlete's max consecutive training "
        "days; "
        "recompute estimated_tss and the structure segment durations so they sum "
        "to the session duration, and list the segments in the order they are "
        "performed: warmup first, one cooldown last with nothing after it -- "
        "balance a duration by lengthening a segment, never by appending one "
        "(no \"Cooldown extra\"); write an interval set as N x (work, recovery), "
        "work first and the last rep keeping its recovery, every rep with the "
        "same name, duration, zone, target and notes -- never a note like \"same "
        "as Interval 1\"; set bike target_power_w from the bike power "
        "zones and any run or swim pace in the notes from the pace factors, and "
        "make each segment's zone label match its target. Never add or move a "
        "session onto one of the rest days "
        "listed above, and never propose one: if the athlete asks for work on a "
        "rest day, say it is a rest day in their schedule and offer the nearest "
        "training day instead — only go ahead if they insist after that. After a "
        "successful edit, state what changed in one sentence. If a tool call "
        "returns an error, tell the athlete it failed — never claim a change you "
        "did not make. Every edit is saved as the athlete's request and kept when "
        "the plan is rebuilt, if they choose to keep their changes: always fill "
        "athlete_request with what they asked for. Days marked athlete_requested "
        "in the plan days JSON already hold such changes."
    )
    if season:
        # Only offered in words when there is a season plan to change.
        tools_rules += (
            " adjust_season_plan changes the season plan's weekly targets, not "
            "sessions: use it only when the athlete asks to change a week's overall "
            "load, take a break, or change their weekly hours or build-up pace, and "
            "tell them it reaches the 7-day plan at the next Sync or Re-sync plan."
        )

    tools_rules += (
        "\n\nupdate_athlete_settings changes the athlete's settings: bike FTP, "
        "LTHR, run threshold pace, critical swim speed, weight, the VO2max decline "
        "threshold and their standing instructions. Use it ONLY when the athlete "
        "asks to change one of these. Never save in the turn they ask: first reply "
        "with the approval message -- the exact line "
        f"{SETTINGS_APPROVAL_MARKER} on its own, then one bold line per change "
        "with the old and the new value (e.g. **Bike FTP: 260 W -> 265 W**; a pace "
        "in min:sec, never seconds), then ask them to reply yes. Call the tool only "
        "after they approve in their next message, with exactly what they "
        "approved; anything new or different needs a new approval message. The "
        "tool takes run and swim pace in seconds: convert only there. For the "
        "instructions send the complete new text, keeping what is saved unless "
        "they asked to remove it, and show the change in the approval message. "
        "The birthday, activity class and training schedule are changed in "
        "Settings, not here: say so. After saving, say what changed, how many "
        "workouts' TSS was recalculated, and that the 7-day plan follows at the "
        "next Sync or Re-sync plan."
    )

    instructions = payload.get("athlete_custom_instructions")
    instructions_line = (
        f"\nAthlete's standing instructions: {instructions}" if instructions else ""
    )

    # The trend that may have put a Z5 session in the week (plan rule 22).
    # Without it the coach cannot say why that session is there, and its
    # plan-editing tools would soften it as an ordinary hard day. Only a
    # classified direction says anything -- "insufficient_data" is noise here.
    # Each entry ends with its own newline rather than starting with one: this
    # sits between two lines that already terminate, unlike rest_line and
    # instructions_line further down, which follow an unterminated clause.
    trend_line = "".join(
        f"{d.capitalize()} VO2max {v['direction']} over 4 weeks: "
        f"{v['weekly_avg']} ml/kg/min, now {v['current']}."
        # Only a decline has a session riding on it; saying this of a stable
        # trend would have the coach defending a session nothing asked for.
        + (
            " The plan may hold a VO2max (Z5) session for it -- do not remove "
            "or soften one without saying why.\n"
            if v.get("direction") == "declining"
            else "\n"
        )
        for d, v in (payload.get("vo2max_trend") or {}).items()
        if v.get("direction") != "insufficient_data"
    )

    # The same profile the plan and analysis payloads carry, whole rather than
    # key by key, so a field added to the profile later reaches the chat too.
    profile_line = (
        "Athlete profile (paces are stored in seconds per km / per 100 m -- "
        "convert to min:sec before quoting any, as rule P8 below says; "
        "activity_class is 0 inactive to 10 competitive, and caps the quality "
        "(Z3-Z5) sessions a 7-day window holds -- 0-3: one, 4-6: two, never on "
        "consecutive days, 7-10: three -- so keep your plan edits inside it; "
        "vo2max_decline_threshold is in ml/kg/min): "
        + json.dumps(payload["athlete_profile"], separators=(",", ":"))
        + "\n"
    )

    # The zone bands and strict pace rules the plan and analysis prompts carry
    # too, right under the thresholds they are applied to. Without them the
    # coach improvised the zones and took a percent OF the threshold pace,
    # quoting Z2 at 4:20/km against a 5:21/km threshold.
    zones_block = (
        training_zones_text()
        + "\nIf the athlete questions a number, recompute it from the bands before "
        "answering; if it was wrong, say so in one clause and give the corrected "
        "range -- do not defend it.\n"
    )

    return (
        "You are the athlete's personal triathlon coach, grounded in the 80/20 "
        "methodology. Be specific and actionable: give durations, zones/paces, and "
        "reasoning tied to their thresholds, current form and goal. Keep answers "
        "short — a few concise sentences; simple markdown (bold, short lists) is "
        "fine, but no headers or tables unless asked.\n\n"
        f"{today_line}"
        f"Goal: {goal_line}.\n"
        f"{sub_events_line}"
        f"{season_line}"
        f"{recent_race_line}"
        f"{profile_line}"
        f"{zones_block}"
        f"Current form: Fitness/CTL {load.get('ctl')}, Fatigue/ATL {load.get('atl')}, "
        f"Form/TSB {load.get('tsb')}.\n"
        f"Last 4 weeks TSS: {payload['last_4_weeks_summary']['weekly_tss']}.\n"
        f"{trend_line}"
        f"{reduction_line}"
        f"Week limits: max {payload['schedule_constraints'].get('max_training_days_per_week')} "
        f"training days, max {payload['schedule_constraints'].get('max_consecutive_training_days')} "
        "consecutive."
        f"{rest_line}"
        f"{instructions_line}"
        f"{plan_block}"
        f"{tools_rules}"
    )
