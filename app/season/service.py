"""
The season plan's I/O edge: reads the athlete's history and events, calls the
pure builder, and reads/writes the store. Routers and (later) the sync call
into here; nothing in builder.py or summary.py touches InfluxDB or the clock.

GET never adapts the plan -- it only works out statuses and summary lines for
today. The plan changes on sync and on the athlete's own edits, nowhere else.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from app import config as app_config
from app import events_service
from app.ai.prompt_builder import _latest_loads, _workout_date
from app.influx.client import InfluxRepo, drop_split_brick_parents
from app.influx.queries import daily_load_last_n_days, workouts_last_n_days
from app.metrics.calculator import _row_date
from app.season import config as cfg
from app.season import instructions as instr
from app.season import split, store
from app.instructions import get_instructions
from app.season.adapt import (
    _set,
    _streak_before,
    current_index,
    diff_revisions,
    project,
    reconcile,
    reflow,
    retaper,
    snapshot,
)
from app.season.builder import (
    SeasonPlanError,
    build_event_plan,
    cadence,
    start_week,
    sub_event_record,
    week_start_of,
)
from app.season.checkin import (
    CHOICE_LABEL,
    COUNT_WORD,
    FOCUS_DISCIPLINES,
    build_checkin,
    build_fitness_plan,
    decide,
    fitness_lines,
    option,
    projection,
)
from app.season.checkin import project_ctl as project_fitness_ctl
from app.season.summary import annotate, band_for

log = logging.getLogger("jymalaya.season")

# Enough history for the four completed weeks before the plan's first week,
# whichever weekday the week starts on.
BASELINE_DAYS = 42
BASELINE_WEEKS = 4


class EventGoneError(LookupError):
    """The event a plan targets no longer exists (the router answers 409)."""


class NoPlanError(LookupError):
    """No active plan, or no such week in it (the router answers 404)."""


class SeasonEditError(ValueError):
    """An edit the plan cannot take (the router answers 422)."""


def _read_history() -> tuple[list[dict], dict]:
    """Stored workouts of the last BASELINE_DAYS and today's combined load."""
    repo = InfluxRepo()
    try:
        workouts = drop_split_brick_parents(repo.query(workouts_last_n_days(BASELINE_DAYS)))
        load = _latest_loads(repo).get("all", {})
    finally:
        repo.close()
    return workouts, load


def weekly_totals(workouts: list[dict], week_start_day: str) -> dict[str, dict]:
    """
    TSS and hours per plan week, keyed by the week's first day, with the same
    per discipline in `by_discipline`. Keyed on the athlete's LOCAL calendar
    day (_workout_date), never the UTC date, and the rows have already lost
    their unsplit brick parents -- so a brick's bike and run legs land in their
    own disciplines.
    """
    out: dict[str, dict] = {}
    for w in workouts:
        day = _workout_date(w)
        if not day:
            continue
        ws = week_start_of(date.fromisoformat(day), week_start_day).isoformat()
        bucket = out.setdefault(ws, {"tss": 0.0, "hours": 0.0, "by_discipline": {}})
        tss = w.get("tss") or 0
        hours = (w.get("duration_sec") or 0) / 3600.0
        bucket["tss"] += tss
        bucket["hours"] += hours
        part = bucket["by_discipline"].setdefault(w.get("discipline") or "other", {"tss": 0.0, "hours": 0.0})
        part["tss"] += tss
        part["hours"] += hours
    return out


def compute_baseline(
    workouts: list[dict], load: dict, today: date, week_start_day: str, first_week: date
) -> dict:
    """
    Where the athlete stands: the average of the BASELINE_WEEKS completed weeks
    before the plan's first week, their own TSS per hour, and today's load.
    Fewer than MIN_HISTORY_WEEKS weeks with any training is "limited history",
    and the builder floors the start at MIN_START_TSS.
    """
    totals = weekly_totals(workouts, week_start_day)
    this_week = week_start_of(today, week_start_day)
    last_complete = min(first_week, this_week) - timedelta(days=7)
    weeks = [
        totals.get((last_complete - timedelta(days=7 * i)).isoformat(), {"tss": 0.0, "hours": 0.0})
        for i in range(BASELINE_WEEKS)
    ]
    tss = sum(w["tss"] for w in weeks)
    hours = sum(w["hours"] for w in weeks)
    trained = sum(1 for w in weeks if w["tss"] > 0)
    # Each discipline's own rate, for the hours of a week's split; a discipline
    # with under an hour in four weeks falls back to the overall rate.
    by: dict[str, list[float]] = {}
    for w in weeks:
        for d, v in (w.get("by_discipline") or {}).items():
            acc = by.setdefault(d, [0.0, 0.0])
            acc[0] += v["tss"]
            acc[1] += v["hours"]
    return {
        "ctl": load.get("ctl"),
        "atl": load.get("atl"),
        "tsb": load.get("tsb"),
        "avg_weekly_tss_4w": round(tss / BASELINE_WEEKS),
        "avg_weekly_hours_4w": round(hours / BASELINE_WEEKS, 1),
        "tss_per_hour": round(tss / hours, 1) if hours > 0 and tss > 0 else cfg.DEFAULT_TSS_PER_HOUR,
        "tss_per_hour_by_discipline": {
            d: round(t / h, 1) for d, (t, h) in by.items() if h >= 1 and t > 0 and d in split.ACTUAL_DISCIPLINES
        },
        "captured_at": today.isoformat(),
        "limited_history": trained < cfg.MIN_HISTORY_WEEKS,
    }


def _target_event(event_id: str | None) -> dict:
    """
    The event a new plan aims at: the one named, else the primary participating
    event. Only an event the athlete is racing can carry a plan.
    """
    if event_id:
        event = events_service.get_event(event_id)
        if event is None:
            raise EventGoneError("That event no longer exists.")
    else:
        event = events_service.active_event()
        if event is None:
            raise SeasonPlanError("Add an event you are racing first.")
    if not events_service.is_participating(event):
        raise SeasonPlanError("You are not racing that event; switch Participating on first.")
    return event


def sub_event_problem(event: dict | None, main: dict, today: date) -> str | None:
    """Why `event` cannot be a sub event of a plan for `main`, or None."""
    if event is None:
        return "That event no longer exists."
    if event.get("event_id") == main.get("event_id"):
        return "The main event cannot also be a sub event."
    if not events_service.is_participating(event):
        return f"You are not racing {event.get('name') or 'that event'}; switch Participating on first."
    try:
        race = date.fromisoformat(event.get("race_date") or "")
        main_race = date.fromisoformat(main.get("race_date") or "")
    except (TypeError, ValueError):
        return f"{event.get('name') or 'That event'} has no valid race date."
    if race < today:
        return f"{event.get('name')} is already over."
    if race >= main_race:
        return f"{event.get('name')} is not before {main.get('name') or 'the main event'}."
    return None


def _resolve_sub_events(ids: list[str] | None, main: dict, today: date) -> list[dict]:
    """The sub events a new plan eases off around, checked; by date."""
    out, seen = [], set()
    for event_id in ids or []:
        if event_id in seen:
            continue
        seen.add(event_id)
        event = events_service.get_event(event_id)
        problem = sub_event_problem(event, main, today)
        if problem:
            raise SeasonPlanError(problem)
        out.append(sub_event_record(event))
    return sorted(out, key=lambda e: e["race_date"])


def _with_instructions(body: dict, mode: str, event_type: str | None) -> tuple[dict, dict | None, dict]:
    """
    The create form's settings with the coach instructions' load filled in
    where the form left a field blank (the form always wins), the plan's
    `instructions` record, and where each setting came from.
    """
    text = get_instructions()
    sources = {k: ("user" if body.get(k) is not None else "default") for k in instr.LOAD_FIELDS}
    if not text:
        return dict(body), None, sources
    if not body.get("use_instructions", True):
        rec = instr.record(text, instr.empty_read(), applied=False)
        rec["opted_out"] = True
        return dict(body), rec, sources
    focus = body.get("focus") or "triathlon"
    read = instr.read_instructions(text, instr.context_for(mode, event_type, focus))
    if read.get("error"):
        # Not applied; no text_sha either, so the next save of the text retries.
        return dict(body), {**instr.record(text, read, applied=False), "text_sha": None}, sources
    filled = dict(body)
    for k, v in instr.load_values(read).items():
        if k in ("ramp", "peak_ctl") and mode == "fitness":
            continue
        if filled.get(k) is None:
            filled[k] = v
            sources[k] = "instructions"
    return filled, instr.record(text, read, applied=True), sources


def _activity_class() -> int | None:
    """The athlete's activity class from the profile (default 4), None if the
    profile cannot be read -- the plan then falls back to the constants."""
    try:
        return app_config.get_athlete_profile().get("activity_class")
    except Exception:  # noqa: BLE001 -- a profile problem must not block a plan
        log.warning("season: could not read the activity class", exc_info=True)
        return None


def profile_default(key: str, event_type: str | None, activity_class) -> float | str | int | None:
    """What the activity class picks for one load setting, None when it picks nothing."""
    band = cfg.activity_band(activity_class)
    if band is None:
        return None
    ramp, every, peak_factor = band
    if key == "ramp":
        return ramp
    if key == "recovery_cadence_weeks":
        return every
    if key == "peak_ctl":
        base = cfg.PEAK_CTL.get(event_type or cfg.DEFAULT_EVENT_TYPE, cfg.PEAK_CTL[cfg.DEFAULT_EVENT_TYPE])
        return round(base * peak_factor)
    return None


def _with_profile(body: dict, mode: str, event_type: str | None, sources: dict, activity_class) -> dict:
    """
    The activity class fills the load settings still blank after the form and
    the coach instructions (ramp, recovery spacing, peak CTL), marking each
    "profile". A fitness plan has no ramp or peak.
    """
    filled = dict(body)
    for k in ("ramp", "recovery_cadence_weeks", "peak_ctl"):
        if mode == "fitness" and k in ("ramp", "peak_ctl"):
            continue
        if filled.get(k) is not None:
            continue
        v = profile_default(k, event_type, activity_class)
        if v is not None:
            filled[k] = v
            sources[k] = "profile"
    return filled


def preview(body: dict, today: date | None = None) -> dict:
    """A full plan from the create form, not saved. No side effects but the
    one cached read of the coach instructions."""
    today = today or date.today()
    week_start_day = body.get("week_start_day") or "monday"
    workouts, load = _read_history()
    totals = weekly_totals(workouts, week_start_day)
    activity_class = _activity_class()
    if body.get("mode") == "fitness":
        body, rec, sources = _with_instructions(body, "fitness", None)
        body = _with_profile(body, "fitness", None, sources, activity_class)
        plan = _fitness_from(body, today, week_start_day, workouts, load, activity_class)
    else:
        event = _target_event(body.get("event_id"))
        body, rec, sources = _with_instructions(body, "event", event.get("type"))
        body = _with_profile(body, "event", event.get("type"), sources, activity_class)
        first = start_week(today, week_start_day, body.get("start"))
        plan = build_event_plan(
            today=today,
            event=event,
            baseline=compute_baseline(workouts, load, today, week_start_day, first),
            week_start_day=week_start_day,
            ramp=body.get("ramp"),
            weekly_hours_cap=body.get("weekly_hours_cap"),
            peak_ctl=body.get("peak_ctl"),
            start=body.get("start"),
            recovery_cadence_weeks=body.get("recovery_cadence_weeks"),
            sub_events=_resolve_sub_events(body.get("sub_event_ids"), event, today),
            activity_class=activity_class,
        )
    plan["instructions"] = rec
    plan["settings_source"] = sources
    plan["last_form"] = {"ctl": load.get("ctl"), "atl": load.get("atl"), "tsb": load.get("tsb")}
    if body.get("split_by_phase") and split.has_split(plan):
        try:
            plan["settings"]["split_by_phase"] = split.validate_table(plan, body["split_by_phase"])
        except split.SplitError as e:
            raise SeasonPlanError(str(e)) from e
    fill_actuals(plan, totals, today)
    return view(plan, today)


def _fitness_from(
    body: dict, today: date, week_start_day: str, workouts: list[dict], load: dict, activity_class: int | None = None
) -> dict:
    """
    A new fitness plan. With `convert` and a plan already there (its event was
    removed, typically), the weeks already over are carried over as the new
    plan's history, so the check-ins build on what the athlete actually did.
    """
    now = _now_iso()
    old = active_plan() if body.get("convert") else None
    first = start_week(today, week_start_day, "this" if old else body.get("start"))
    plan = build_fitness_plan(
        today=today,
        baseline=compute_baseline(workouts, load, today, week_start_day, first),
        week_start_day=week_start_day,
        weekly_hours_cap=body.get("weekly_hours_cap"),
        focus=body.get("focus"),
        start=body.get("start"),
        now_iso=now,
        first_week=first,
        recovery_cadence_weeks=body.get("recovery_cadence_weeks"),
        activity_class=activity_class,
    )
    if old:
        history = [
            {**w, "pinned": False, "pin_note": None}
            for w in old.get("weeks") or []
            if date.fromisoformat(w["week_start"]) < first and w["kind"] in ("loading", "recovery", "return")
        ]
        current = next((w for w in old.get("weeks") or [] if w["week_start"] == first.isoformat()), None)
        if current is not None and current["kind"] in ("loading", "recovery", "return"):
            plan["weeks"][0].update(kind=current["kind"], target_tss=current["target_tss"], target_hours=current["target_hours"])
        plan["weeks"] = history + plan["weeks"]
        for i, w in enumerate(plan["weeks"]):
            w["index"] = i + 1
        plan["revisions"] = [
            _note_revision(now, "user", "plan", "EVENT_CHANGED", f"Converted to a fitness plan from {((old.get('event') or {}).get('name')) or 'the event plan'}")
        ]
    return plan


def fill_actuals(plan: dict, totals: dict[str, dict], today: date) -> None:
    """
    Each started week's actual TSS and hours, in place. A plan made mid-week
    already has training in its first week; without this it would read
    "0 done" until the next sync.
    """
    for w in plan.get("weeks") or []:
        if date.fromisoformat(w["week_start"]) > today:
            continue
        if w["week_start"] not in totals and w.get("actual_tss") is not None:
            continue  # a week carried over from an older plan, before this read's window
        split.set_actual(w, totals.get(w["week_start"]))


def create(body: dict, today: date | None = None) -> dict:
    """
    Build and save a plan, replacing any plan already there. The old plan is
    not kept, only noted as the new plan's first revision (a conversion notes
    itself).
    """
    old = active_plan()
    plan = preview(body, today)
    if old is not None and not body.get("convert"):
        what = f"the plan for {old['event']['name']}" if old.get("event") else "the fitness plan"
        made = (old.get("created_at") or "")[:10]
        plan["revisions"] = [
            _note_revision(plan["created_at"], "user", "plan", "USER_PIN", f"Replaced {what} (made {made})")
        ] + (plan.get("revisions") or [])
    store.save_plan(_stored(plan))
    return plan


def _stored(plan: dict) -> dict:
    """The plan without what view() adds for reading (virtual weeks and check-ins)."""
    out = dict(plan)
    out["weeks"] = [w for w in plan.get("weeks") or [] if not w.get("_virtual")]
    split.apply_splits(out)
    out["weeks"] = [{k: v for k, v in w.items() if k not in split.VIEW_FIELDS} for w in out["weeks"]]
    out["checkins"] = [c for c in plan.get("checkins") or [] if c.get("status") != "pending"]
    for key in ("fatigue_banner", "warnings", "instructions_view"):
        out.pop(key, None)
    return out


def active_plan() -> dict | None:
    """The stored plan unless it was cancelled."""
    plan = store.load_plan()
    if plan is None or plan.get("status") == "cancelled":
        return None
    return plan


def get_plan(today: date | None = None) -> dict | None:
    """The stored plan as of today, or None. Read-only: nothing is adapted."""
    plan = active_plan()
    if plan is None:
        return None
    today = today or date.today()
    event = plan.get("event") or {}
    if plan.get("status") == "active" and event.get("race_date"):
        try:
            if date.fromisoformat(event["race_date"]) < today:
                plan["status"] = "completed"
        except ValueError:
            pass
    return view(plan, today)


def _planned_remaining(week: dict, today: date, last_workout: str | None) -> float:
    """
    What the cached 7-day plan still has in this week: its sessions from today
    to the week's end. Today's count only while nothing is logged today -- once
    it is, today's work is already in the week's actual.
    """
    from app.recommend_service import latest_recommendation  # it imports the prompt builder

    start = date.fromisoformat(week["week_start"])
    end = start + timedelta(days=6)
    first = today + timedelta(days=1) if last_workout == today.isoformat() else today
    total = 0.0
    for day in (latest_recommendation() or {}).get("days") or []:
        try:
            d = date.fromisoformat(day.get("date") or "")
        except ValueError:
            continue
        if first <= d <= end:
            total += sum((s.get("estimated_tss") or 0) for s in day.get("sessions") or [] if isinstance(s, dict))
    return total


def _days_off(plan: dict, today: date) -> int | None:
    """Days since the last training day, counted no earlier than the plan's start."""
    last = plan.get("last_workout")
    if not last:
        return None
    created = date.fromisoformat((plan.get("created_at") or today.isoformat())[:10])
    return (today - max(date.fromisoformat(last), created)).days


def pending_checkin(plan: dict, today: date) -> dict | None:
    """
    The check-in open now, or None. It opens on the last day of the current
    week for the week after it, until the athlete answers. It is worked out on
    every read, never stored until answered -- an unanswered one is applied
    automatically by the first sync of the new week.
    """
    if plan.get("mode") != "fitness" or plan.get("status") != "active":
        return None
    weeks = plan.get("weeks") or []
    c = current_index(weeks, today)
    if c is None and _undecided_this_week(weeks, today):
        # A new week started and no sync has decided it yet (the dashboard
        # opened on Monday morning): the check-in is still open, on last
        # week's final total, until the athlete answers or a sync applies it.
        return build_checkin(
            plan, len(weeks) - 1, today, plan.get("last_form") or plan.get("baseline") or {}, 0,
            _days_off(plan, today), closed=True, opened_at=plan.get("updated_at") or _now_iso(),
        )
    if c is None or c + 1 < len(weeks):
        return None
    if today != date.fromisoformat(weeks[c]["week_start"]) + timedelta(days=6):
        return None
    return build_checkin(
        plan,
        c,
        today,
        plan.get("last_form") or plan.get("baseline") or {},
        _planned_remaining(weeks[c], today, plan.get("last_workout")),
        _days_off(plan, today),
        closed=False,
        opened_at=plan.get("updated_at") or _now_iso(),
    )


def _undecided_this_week(weeks: list[dict], today: date) -> bool:
    """True when today falls in the week right after the last decided one."""
    if not weeks:
        return False
    start = date.fromisoformat(weeks[-1]["week_start"]) + timedelta(days=7)
    return start <= today <= start + timedelta(days=6)


def fatigue_banner(plan: dict, today: date) -> dict | None:
    """
    The mid-week question "make the rest of this week easy?": TSB below -30 in
    a loading week, asked once per week (POST /season-plan/week-easy answers it).
    """
    if plan.get("status") != "active":
        return None
    weeks = plan.get("weeks") or []
    c = current_index(weeks, today)
    tsb = (plan.get("last_form") or {}).get("tsb")
    if c is None or tsb is None or tsb >= cfg.MID_WEEK_BANNER_TSB:
        return None
    w = weeks[c]
    if w["kind"] != "loading" or plan.get("easy_answered") == w["week_start"]:
        return None
    if date.fromisoformat(w["week_start"]) + timedelta(days=6) == today:
        return None  # the week is ending; nothing left to make easy
    return {"week_start": w["week_start"], "tsb": round(tsb)}


def view(plan: dict, today: date) -> dict:
    """
    The plan as the dashboard reads it today: statuses and summary lines; for a
    fitness plan also the pending check-in (as the last of `checkins`), the
    week it would decide (status pending_checkin, at the recommended target)
    and the indicative projection; and the mid-week fatigue question. Nothing
    here is stored -- _stored() strips it before a save.
    """
    plan = annotate(plan, today)
    if plan.get("mode") == "fitness":
        pending = pending_checkin(plan, today)
        weeks = [w for w in plan["weeks"] if not w.get("_virtual")]
        if pending:
            plan["checkins"] = (plan.get("checkins") or []) + [pending]
            # The week the check-in decides, shown at the recommended target.
            scratch = {**plan, "weeks": [dict(w) for w in weeks]}
            virtual = decide(scratch, pending, pending["recommended"], "checkin_auto")
            weeks = weeks + [dict(virtual, status="pending_checkin", _virtual=True)]
        decided = [w for w in weeks if w.get("source") != "projection"]
        projected = [dict(w, status="projected", _virtual=True) for w in projection(plan, decided)]
        plan["weeks"] = decided + projected
        project_fitness_ctl(plan["weeks"], today, plan.get("last_form") or plan.get("baseline") or {})
        plan["summary"] = fitness_lines(plan, today, pending)
        # The check-in's week and the projection are built after annotate().
        split.add_view(split.apply_splits(plan))
    plan["fatigue_banner"] = fatigue_banner(plan, today)
    rec = plan.get("instructions")
    plan["instructions_view"] = (
        {**instr.summary_view(rec.get("read"), bool(rec.get("applied"))), "opted_out": bool(rec.get("opted_out"))}
        if rec
        else None
    )
    return plan


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cancel() -> bool:
    """
    Cancel the plan. True when there was one. The file stays, marked
    cancelled, so its payload_changed_at tells _plan_is_current that the 7-day
    plan was built against a season plan that no longer exists.
    """
    plan = active_plan()
    if plan is None:
        return False
    now = _now_iso()
    plan.update(status="cancelled", updated_at=now, payload_changed_at=now)
    store.save_plan(plan)
    return True


def revisions() -> list[dict]:
    """The change log, newest first."""
    plan = active_plan() or {}
    return list(reversed(plan.get("revisions") or []))


# ---- Sync ---------------------------------------------------------------------

# Never read more history than this for a sync, whatever the plan's age.
MAX_SYNC_DAYS = 400


def _payload_view(plan: dict, today: date) -> tuple:
    """What the 7-day plan payload reads: status, the cap, this and next week."""
    weeks = plan.get("weeks") or []
    c = current_index(weeks, today)
    if c is None and weeks and date.fromisoformat(weeks[0]["week_start"]) > today:
        c = 0  # not started yet: the window reaches into the first week
    settings = plan.get("settings") or {}
    view: list = [
        plan.get("status"),
        settings.get("weekly_hours_cap"),
        settings.get("focus"),
        tuple((e.get("event_id"), e.get("name"), e.get("type"), e.get("race_date")) for e in plan.get("sub_events") or []),
    ]
    if c is not None:
        view += [
            (
                w["week_start"],
                w["phase"],
                w["kind"],
                round(w["target_tss"]),
                tuple(sorted((w.get("split") or {}).items())),
                (w.get("sub_event") or {}).get("role"),
            )
            for w in weeks[c : c + 2]
        ]
    return tuple(view)


def _sync_reads(repo: InfluxRepo, plan: dict, today: date):
    """Everything reconcile needs: workouts, today's load, the daily CTL series."""
    weeks = plan.get("weeks") or []
    first = date.fromisoformat(weeks[0]["week_start"]) if weeks else today
    days = min(max((today - first).days + 8, BASELINE_DAYS), MAX_SYNC_DAYS)
    workouts = drop_split_brick_parents(repo.query(workouts_last_n_days(days)))
    load = _latest_loads(repo).get("all", {})
    ctl_by_date: dict[str, float] = {}
    for row in repo.query(daily_load_last_n_days(days, "all")):
        d = _row_date(row)
        if d is not None and row.get("ctl") is not None:
            ctl_by_date[str(d)] = float(row["ctl"])
    trained = [
        date.fromisoformat(d)
        for d in (_workout_date(w) for w in workouts if (w.get("duration_sec") or 0) > 0 or (w.get("tss") or 0) > 0)
        if d
    ]
    return workouts, load, ctl_by_date, (max(trained) if trained else None)


def notice_for(plan: dict, added: list[dict]) -> str | None:
    """The dashboard's one-line notice for the revisions this sync added."""
    direct = [r for r in added if r.get("field") == "target_tss" and r.get("week_start")]
    if not direct:
        return None
    index = {w["week_start"]: w["index"] for w in plan.get("weeks") or []}
    r = direct[0]
    frm, to = r.get("from") or 0, r.get("to") or 0
    change = "lowered" if to < frm else "raised"
    pct = f" {abs(round(100 * (to - frm) / frm))}%" if frm else ""
    # The cause only: the reason texts read "<cause>: <what changed>", and the
    # change is already in the sentence.
    cause = r["reason_text"].split(":")[0]
    text = f"Plan adjusted: week {index.get(r['week_start'], '?')} {change}{pct} ({cause})."
    more = len(direct) - 1
    if more:
        text += f" {more} more change{'s' if more > 1 else ''} in the Changes list."
    return text


def on_sync(repo: InfluxRepo | None = None, today: date | None = None) -> str | None:
    """
    Bring the plan up to date after a sync: actuals and form, the adaptation
    rules (app/season/adapt.py), a finished race. Returns the dashboard notice,
    or None.

    With no active plan it returns at once, before any read -- so a sync for an
    athlete without a season plan is exactly what it was before.
    """
    plan = active_plan()
    if plan is None:
        return None
    today = today or date.today()

    # Backstop for an event edit the events router did not see through (an
    # older client, a failed hook): re-check the plan's own event first.
    event_id = (plan.get("event") or {}).get("event_id")
    if plan.get("mode") == "event" and event_id:
        try:
            _apply_event(plan, events_service.get_event(event_id), today)
        except Exception as e:  # noqa: BLE001 -- an unreadable event changes nothing
            log.warning("Could not re-check the season plan's event: %s", e)
        plan = active_plan()
        if plan is None:
            return None

    own = repo is None
    repo = repo or InfluxRepo()
    try:
        workouts, load, ctl_by_date, last_workout = _sync_reads(repo, plan, today)
    finally:
        if own:
            repo.close()

    now = _now_iso()
    before = _payload_view(plan, today)
    totals = weekly_totals(workouts, plan.get("week_start_day") or "monday")
    plan, added = reconcile(plan, today, totals, load, last_workout, now, ctl_by_date)
    if last_workout is not None:
        plan["last_workout"] = last_workout.isoformat()
    notices = []
    if plan.get("mode") == "fitness" and plan.get("status") == "active":
        rolled, notices = roll_fitness(plan, today, totals, now)
        added += rolled

    race = (plan.get("event") or {}).get("race_date")
    if plan.get("status") == "active" and race:
        try:
            if date.fromisoformat(race) < today:
                plan["status"] = "completed"
        except ValueError:
            pass

    plan["updated_at"] = now
    split.apply_splits(plan)
    if _payload_view(plan, today) != before:
        plan["payload_changed_at"] = now
    store.save_plan(plan)
    if added:
        log.info("Season plan adjusted: %s", "; ".join(r["reason_text"] for r in added))
    if notices:
        return " ".join(notices)
    return notice_for(plan, added)


def roll_fitness(plan: dict, today: date, totals: dict[str, dict], now: str) -> tuple[list[dict], list[str]]:
    """
    Bring a fitness plan up to today, in place:
    - every week that has started without a decision is decided by the
      check-in the athlete did not answer -- its recommended choice, applied
      for them (checkin_auto), with a notice;
    - a check-in answered on the last day of a week was resolved from that
      week's projected total; once the new week starts it is resolved again
      from the final actual, then frozen.
    Returns the revisions and the notices.
    """
    weeks = plan["weeks"]
    form = plan.get("last_form") or plan.get("baseline") or {}
    days_off = _days_off(plan, today)
    added: list[dict] = []
    notices: list[str] = []

    for ci in plan.get("checkins") or []:
        if ci.get("status") != "answered" or ci.get("resolved"):
            continue
        if date.fromisoformat(ci["week_start"]) > today:
            continue
        closing = next((i for i, w in enumerate(weeks) if w["week_start"] == ci["closing_week"]["week_start"]), None)
        if closing is None:
            continue
        final = build_checkin(plan, closing, today, ci.get("form") or form, 0, days_off, closed=True, opened_at=ci["opened_at"])
        if option(final, ci["choice"])["enabled"]:
            w = decide(plan, final, ci["choice"], "checkin_user")
            ci.update(options=final["options"], closing_week=final["closing_week"])
            added.append(_note_revision(now, "sync", "target_tss", "CHECKIN", f"{CHOICE_LABEL[ci['choice']]}, resolved from last week's final total", w["week_start"], to=w["target_tss"]))
        ci["resolved"] = True

    # A week that started undecided: apply the recommendation, one week at a
    # time if several were missed (each decided from the one before it).
    while weeks and date.fromisoformat(weeks[-1]["week_start"]) + timedelta(days=6) < today:
        closing = len(weeks) - 1
        ci = build_checkin(plan, closing, today, form, 0, days_off, closed=True, opened_at=now)
        choice = ci["recommended"]
        w = decide(plan, ci, choice, "checkin_auto")
        got = totals.get(w["week_start"])
        if got and date.fromisoformat(w["week_start"]) <= today:
            split.set_actual(w, got)
        ci.update(status="auto_applied", source="auto", choice=choice, chosen_at=now, resolved=True)
        plan.setdefault("checkins", []).append(ci)
        added.append(_note_revision(now, "sync", "target_tss", "CHECKIN", f"{CHOICE_LABEL[choice]} set automatically: {ci['reason']}", w["week_start"], to=w["target_tss"]))
        start = date.fromisoformat(w["week_start"])
        when = "This week" if start <= today else "Next week"
        notices.append(f"{when} set to {CHOICE_LABEL[choice]} (auto). Change it any time that week.")
    return added, notices[-1:]


# ---- The 7-day plan payload and the coach ---------------------------------------


def payload_block(today: date, plan_dates: list[str]) -> dict | None:
    """
    The season plan as the 7-day plan sees it: every season week the rolling
    window touches. The window starts today, so it straddles two season weeks;
    each carries the window dates that belong to it. The current week's budget
    is exactly what is left of its target. A later week's is its pro-rata share
    of the days in the window -- a guide only, since its long sessions may fall
    outside the window. None without an active plan.
    """
    plan = active_plan()
    if plan is None or plan.get("status") != "active" or not plan_dates:
        return None
    weeks = plan.get("weeks") or []
    out = []
    for w in weeks:
        start = date.fromisoformat(w["week_start"])
        end = start + timedelta(days=6)
        dates = [d for d in plan_dates if start.isoformat() <= d <= end.isoformat()]
        if not dates:
            continue
        current = start <= today <= end
        target = round(w["target_tss"])
        done = round(w.get("actual_tss") or 0) if current else 0
        remaining = max(target - done, 0)
        entry = {
            "week_start": w["week_start"],
            "week_end": end.isoformat(),
            "week_index": w["index"],
            "is_current_week": current,
            "phase": w["phase"],
            "kind": w["kind"],
            "target_tss": target,
            "done_tss": done,
            "remaining_tss": remaining,
            "plan_dates": dates,
            "budget_tss": remaining if current else min(target, round(target * len(dates) / 7)),
        }
        if w.get("sub_event"):
            # Rule 33: the week eases off around a sub event; the main event
            # stays the goal.
            entry["sub_event"] = {k: w["sub_event"].get(k) for k in ("name", "type", "race_date", "role")}
        if w.get("split"):
            # Rule 29: each discipline's share of the week, budgeted like the
            # week -- the current week by what is left in that discipline, a
            # later week pro rata.
            parts = split.discipline_tss(target, w["split"])
            done_by = (w.get("actual_by_discipline") or {}) if current else {}
            entry["split_pct"] = dict(w["split"])
            budgets = {
                d: max(v - round((done_by.get(d) or {}).get("tss") or 0), 0)
                if current
                else min(v, round(v * len(dates) / 7))
                for d, v in parts.items()
            }
            # Never more than the week has left: a sport already over its share
            # leaves less for the others, shared out by what each still lacks.
            total = sum(budgets.values())
            if total > entry["budget_tss"]:
                pct = {d: 100 * v / total for d, v in budgets.items()}
                budgets = split.discipline_tss(entry["budget_tss"], pct)
            entry["discipline_budget_tss"] = budgets
        out.append(entry)
    if not out:
        return None
    c = current_index(weeks, today)
    following = weeks[c + 1] if c is not None and c + 1 < len(weeks) else None
    form = plan.get("last_form") or plan.get("baseline") or {}
    block = {
        "mode": plan.get("mode"),
        "weeks_in_plan": len(weeks),
        "weeks": out,
        "form_band": band_for(form.get("tsb")),
        "next_week_kind": following["kind"] if following else None,
        "weekly_hours_cap": (plan.get("settings") or {}).get("weekly_hours_cap"),
    }
    if plan.get("event"):
        block["event"] = {k: plan["event"].get(k) for k in ("name", "type", "race_date")}
    if plan.get("mode") == "fitness":
        # No race picks the sports: the plan's focus does (rule 28).
        focus = (plan.get("settings") or {}).get("focus") or "triathlon"
        block["focus_disciplines"] = FOCUS_DISCIPLINES.get(focus)
        # Next week is decided at this week's check-in; until then it has no entry.
        block["next_week_decided"] = following is not None
    return block


def coach_line(today: date | None = None) -> str | None:
    """One line for the coach: where the athlete stands on the season plan."""
    plan = active_plan()
    if plan is None or plan.get("status") != "active":
        return None
    today = today or date.today()
    weeks = plan.get("weeks") or []
    c = current_index(weeks, today)
    if c is None:
        return None
    w = weeks[c]
    nxt = weeks[c + 1] if c + 1 < len(weeks) else None
    if plan.get("mode") == "fitness":
        focus = (plan.get("settings") or {}).get("focus") or "triathlon"
        line = (
            f"Fitness season plan ({focus} focus, no race): week {w['index']}, {w['kind']} week, "
            f"{round(w['target_tss'])} TSS target, {round(w.get('actual_tss') or 0)} done; "
        )
        line += (
            f"next week {nxt['kind']}, {round(nxt['target_tss'])} TSS."
            if nxt
            else "next week is not chosen yet: the athlete picks Increase, Stay, Decrease or "
            "Recovery at the check-in on the last day of this week."
        )
        return line
    event = plan.get("event") or {}
    aim = f" toward {event['name']} on {event['race_date']}" if event.get("name") else ""
    line = (
        f"Season plan{aim}: week {w['index']} of {len(weeks)}, {w['phase']} phase, "
        f"{w['kind']} week, {round(w['target_tss'])} TSS target, "
        f"{round(w.get('actual_tss') or 0)} done"
    )
    if w.get("split"):
        line += f", split {split.label(w['split'])} percent of the target"
    if nxt:
        line += f"; next week {nxt['phase']} {nxt['kind']}, {round(nxt['target_tss'])} TSS"
    subs = upcoming_sub_events(today)
    if subs:
        line += "; sub events (races before the main event, which stays the goal): " + ", ".join(
            f"{e['name']} ({e['type']}) on {e['race_date']}" for e in subs
        )
    return line + ". The season plan sets each week's load; the 7-day plan fills it."


def goal_event() -> dict | None:
    """
    The event the 7-day plan, the coach and the analysis treat as the goal:
    the active event plan's main event, so a nearer sub event never takes over
    rule 9. Without one (or once the athlete stops racing it), the nearest
    event they are racing, as before.
    """
    plan = active_plan()
    if plan and plan.get("mode") == "event" and plan.get("status") == "active":
        event_id = (plan.get("event") or {}).get("event_id")
        event = events_service.get_event(event_id) if event_id else None
        if event is not None and events_service.is_participating(event):
            if (event.get("race_date") or "") >= date.today().isoformat():
                return event
    return events_service.active_event()


def upcoming_sub_events(today: date | None = None) -> list[dict]:
    """The active event plan's sub events from today on, by date."""
    plan = active_plan()
    if not plan or plan.get("mode") != "event" or plan.get("status") != "active":
        return []
    today = today or date.today()
    return [e for e in plan.get("sub_events") or [] if (e.get("race_date") or "") >= today.isoformat()]


# ---- The athlete's own changes: week pins and settings ---------------------------

# How a week-level edit reads in the change log.
ACTION_VERB = {
    "set_target": "set",
    "make_recovery": "made a recovery week of",
    "lighter": "lightened",
    "heavier": "raised",
    "skip": "skipped",
    "unpin": "unpinned",
}
SPLIT_ACTIONS = ("set_split", "reset_split")


def _tss_per_hour(plan: dict) -> float:
    return float((plan.get("baseline") or {}).get("tss_per_hour") or cfg.DEFAULT_TSS_PER_HOUR)


def _note_revision(now: str, trigger: str, field: str, code: str, text: str, week_start=None, frm=None, to=None) -> dict:
    return {
        "at": now,
        "trigger": trigger,
        "week_start": week_start,
        "field": field,
        "from": frm,
        "to": to,
        "reason_code": code,
        "reason_text": text,
    }


def rhythm_warnings(weeks: list[dict], i: int, every: int = cfg.RECOVERY_CADENCE_WEEKS) -> list[str]:
    """
    The coaching guardrails an edit to week `i` breaks. Allowed with a warning,
    never refused: the structure is enforced in code, the coaching stays soft --
    the same split as the rest day against the 80/20 rule.
    """
    out = []
    w = weeks[i]
    last, _ = _streak_before(weeks, i)
    if w["kind"] == "loading" and last and w["target_tss"] > round(last * (1 + cfg.MAX_WEEKLY_GROWTH)) + 1:
        out.append("Raises load more than 10% over the last loading week.")
    streak = longest = 0
    for wk in weeks:
        streak = streak + 1 if wk["kind"] == "loading" else 0
        longest = max(longest, streak)
    if longest > every + 1:
        out.append(f"Runs more than {COUNT_WORD.get(every, every).lower()} loading weeks without a recovery week.")
    return out


def _editable_plan() -> dict:
    plan = active_plan()
    if plan is None or plan.get("status") != "active":
        raise NoPlanError("No active season plan.")
    return plan


def _week_index(plan: dict, week_start: str) -> int:
    for i, w in enumerate(plan.get("weeks") or []):
        if w["week_start"] == week_start:
            return i
    raise NoPlanError(f"No plan week starts on {week_start}.")


def _commit(plan: dict, before_view: tuple, today: date, now: str, added: list[dict]) -> dict:
    """
    Save an edited plan: its revisions, the payload stamp, the timestamps, and
    the CTL projection for the new targets (from the form of the last sync).
    """
    project(plan, today, plan.get("last_form") or plan.get("baseline") or {}, {})
    plan["revisions"] = (plan.get("revisions") or []) + added
    plan["updated_at"] = now
    if _payload_view(plan, today) != before_view:
        plan["payload_changed_at"] = now
    plan.pop("warnings", None)
    store.save_plan(_stored(plan))
    return plan


def edit_split(
    week_start: str,
    action: str,
    new_split: dict | None = None,
    trigger: str = "user",
    today: date | None = None,
) -> dict:
    """
    One week's swim/bike/run split, set by hand ("set_split", split_source
    "user") or handed back to the plan's phase table ("reset_split"). Separate
    from the TSS pin: the week's target still adapts, and a split set by hand
    survives every rebuild, reflow and settings change. A past week, or a plan
    that is not a triathlon, cannot take one.
    """
    today = today or date.today()
    plan = _editable_plan()
    if not split.has_split(plan):
        raise SeasonEditError("Only a triathlon plan, or one whose coach instructions add a sport, splits its weeks.")
    i = _week_index(plan, week_start)
    w = plan["weeks"][i]
    if date.fromisoformat(w["week_start"]) + timedelta(days=6) < today:
        raise SeasonEditError("That week is over.")
    now = _now_iso()
    view_before = _payload_view(plan, today)
    who = "The coach, at your request," if trigger == "coach" else "You"
    if action == "set_split":
        try:
            w["split"] = split.validate_split(new_split)
        except split.SplitError as e:
            raise SeasonEditError(str(e)) from e
        w["split_source"] = "user"
        text = f"{who} set this week's split to {split.label(w['split'])}"
    elif action == "reset_split":
        w.pop("split_source", None)
        w.pop("split", None)
        split.apply_splits(plan)
        text = f"{who} put this week's split back on the plan's table ({split.label(w['split'])})"
    else:
        raise SeasonEditError(f"Unknown action {action!r}.")
    added = [_note_revision(now, trigger, "split", "USER_PIN", text, w["week_start"])]
    plan = _commit(plan, view_before, today, now, added)
    return view(plan, today)


def edit_week(
    week_start: str,
    action: str,
    value: float | None = None,
    note: str | None = None,
    trigger: str = "user",
    today: date | None = None,
) -> dict:
    """
    One week's target, set by the athlete (or the coach at their request) and
    PINNED: no sync adaptation moves it again. The later unpinned weeks re-flow
    around it; unpin hands the week back to the plan. The resulting target is
    what is stored -- operations are never replayed.

    Returns the plan for today, with `warnings` for any guardrail the edit
    breaks. A past week or a taper/race week cannot be edited: the taper is
    the race date's, and moves only with it.
    """
    today = today or date.today()
    plan = _editable_plan()
    i = _week_index(plan, week_start)
    weeks = plan["weeks"]
    w = weeks[i]
    start = date.fromisoformat(w["week_start"])
    if start + timedelta(days=6) < today:
        raise SeasonEditError("That week is over.")
    if w["kind"] in ("taper", "race"):
        raise SeasonEditError("The taper and race week follow the race date; change the event to move them.")
    if action not in ACTION_VERB:
        raise SeasonEditError(f"Unknown action {action!r}.")
    if action == "set_target" and value is None:
        raise SeasonEditError("set_target needs a TSS value.")

    now = _now_iso()
    view_before = _payload_view(plan, today)
    before = snapshot(weeks)
    note = (note or "").strip()[:300] or None

    if action == "unpin":
        w["pinned"], w["pin_note"] = False, None
        if plan.get("mode") == "event":
            reflow(plan, i)
    else:
        last, _ = _streak_before(weeks, i)
        target, kind = w["target_tss"], w["kind"]
        if action == "set_target":
            target = value
        elif action == "lighter":
            target = target * 0.9
        elif action == "heavier":
            target = target * 1.1
        elif action == "make_recovery":
            target, kind = (last or target) * cfg.RECOVERY_FACTOR, "recovery"
        elif action == "skip":
            # What is already done this week stands; nothing more is planned.
            target, kind = (w.get("actual_tss") or 0) if start <= today else 0, "recovery"
        _set(w, kind, target, _tss_per_hour(plan), source="user")
        w["pinned"], w["pin_note"] = True, note
        if plan.get("mode") == "event":
            reflow(plan, i + 1, fixed=frozenset({i}))

    who = "The coach, at your request," if trigger == "coach" else "You"
    text = f"{who} {ACTION_VERB[action]} this week" + (f": {note}" if note else "")
    added = diff_revisions(before, weeks, [(i, "USER_PIN", text)], now, trigger)
    if not any(r.get("week_start") == w["week_start"] for r in added):
        # A pin or unpin that moved no number is still a change to log.
        added.insert(0, _note_revision(now, trigger, "pinned", "USER_PIN", text, w["week_start"]))
    plan = _commit(plan, view_before, today, now, added)
    out = view(plan, today)
    out["warnings"] = [] if action == "unpin" or plan.get("mode") != "event" else rhythm_warnings(weeks, i, cadence(plan))
    return out


def update_settings(changes: dict, trigger: str = "user", today: date | None = None) -> dict:
    """
    Change the weekly hours cap and/or the build-up pace, and re-flow the
    unpinned weeks from next week on (this week is already under way). The
    peak goes back to 7 x peak CTL and is lowered again only if the new path
    cannot reach it. `changes` holds only the keys to change; a None
    weekly_hours_cap removes the limit.
    """
    today = today or date.today()
    plan = _editable_plan()
    settings = plan.setdefault("settings", {})
    unknown = set(changes) - {"weekly_hours_cap", "ramp", "focus", "split_by_phase", "recovery_cadence_weeks", "peak_ctl"}
    if unknown:
        raise SeasonEditError(f"Unknown settings: {', '.join(sorted(unknown))}.")
    if "split_by_phase" in changes:
        if set(changes) != {"split_by_phase"}:
            raise SeasonEditError("Change the split table on its own.")
        return _update_split_table(plan, changes["split_by_phase"], trigger, today)
    fitness = plan.get("mode") == "fitness"
    if fitness and changes.get("ramp"):
        raise SeasonEditError("A fitness plan has no build-up pace: each week is chosen at its check-in.")
    if not fitness and changes.get("focus"):
        raise SeasonEditError("An event plan's sports come from the race.")
    if fitness and changes.get("peak_ctl"):
        raise SeasonEditError("A fitness plan has no peak.")
    cad = changes.get("recovery_cadence_weeks")
    if cad is not None and not (isinstance(cad, int) and 2 <= cad <= 4):
        raise SeasonEditError("A recovery week comes after 2 to 4 loading weeks.")
    if not changes:
        return view(plan, today)

    now = _now_iso()
    view_before = _payload_view(plan, today)
    weeks = plan["weeks"]
    before = snapshot(weeks)
    # Where each changed setting now comes from. A setting the instructions no
    # longer mention goes back to its default (a null here), and that default
    # is theirs to change again later.
    # A cleared ramp, spacing or peak goes back to what the activity class
    # picks ("profile"), and only without one to the constant.
    sources = plan.setdefault("settings_source", {})
    event_type = (plan.get("event") or {}).get("type") or cfg.DEFAULT_EVENT_TYPE
    activity_class = _activity_class()
    for k in changes:
        if k in instr.LOAD_FIELDS:
            from_profile = not (fitness and k in ("ramp", "peak_ctl"))
            if changes[k] is None and from_profile and profile_default(k, event_type, activity_class) is not None:
                sources[k] = "profile"
            elif trigger != "instructions":
                sources[k] = "user"
            else:
                sources[k] = "instructions" if changes[k] is not None else "default"
    if trigger == "instructions" and "ramp" in changes and changes["ramp"] is None:
        changes = {**changes, "ramp": profile_default("ramp", event_type, activity_class) or cfg.DEFAULT_RAMP}
    if "weekly_hours_cap" in changes:
        settings["weekly_hours_cap"] = changes["weekly_hours_cap"]
    if "recovery_cadence_weeks" in changes:
        settings["recovery_cadence_weeks"] = (
            changes["recovery_cadence_weeks"]
            or profile_default("recovery_cadence_weeks", event_type, activity_class)
            or cfg.RECOVERY_CADENCE_WEEKS
        )
    if "peak_ctl" in changes:
        plan.setdefault("targets", {})["peak_ctl"] = float(
            changes["peak_ctl"]
            or profile_default("peak_ctl", event_type, activity_class)
            or cfg.PEAK_CTL.get(event_type, cfg.PEAK_CTL[cfg.DEFAULT_EVENT_TYPE])
        )
    if changes.get("focus"):
        if changes["focus"] not in FOCUS_DISCIPLINES:
            raise SeasonEditError(f"Unknown focus {changes['focus']!r}.")
        settings["focus"] = changes["focus"]
    if fitness:
        # The decided weeks stand; the cap and focus apply from the next check-in.
        what = ", ".join(
            f"{k.replace('_', ' ')} {v if v is not None else 'removed'}" for k, v in changes.items()
        )
        code = "INSTRUCTIONS" if trigger == "instructions" else "SETTINGS"
        added = [_note_revision(now, trigger, "settings", code, f"Fitness plan settings changed: {what}")]
        plan = _commit(plan, view_before, today, now, added)
        return view(plan, today)
    if changes.get("ramp"):
        if changes["ramp"] not in cfg.RAMP_GROWTH:
            raise SeasonEditError(f"Unknown build-up pace {changes['ramp']!r}.")
        settings["ramp"] = changes["ramp"]
        growth = min(cfg.RAMP_GROWTH[changes["ramp"]], cfg.MAX_WEEKLY_GROWTH)
        settings["growth_pct_per_week"] = round(growth * 100, 1)

    targets = plan.get("targets") or {}
    peak = round(cfg.PEAK_WEEKLY_FACTOR * targets["peak_ctl"]) if targets.get("peak_ctl") else None
    if peak:
        targets["peak_weekly_tss"] = peak
    c = current_index(weeks, today)
    first_future = 0 if c is None else c + 1
    reflow(plan, first_future)
    # The athlete changed the whole plan's ceiling: the taper follows the new
    # peak (it is sized off the highest loading week, as when it was built).
    retaper(plan, today)

    extra = []
    top = max((wk["target_tss"] for wk in weeks[first_future:] if wk["kind"] == "loading"), default=None)
    if peak and top is not None and top < peak:
        targets["peak_weekly_tss"] = top
        extra.append(_note_revision(
            now, trigger, "peak_weekly_tss", "PEAK_LOWERED", f"Peak lowered to {top} TSS a week", frm=peak, to=top
        ))

    cap = settings.get("weekly_hours_cap")
    parts = []
    if "weekly_hours_cap" in changes:
        parts.append(f"weekly hours set to {cap:g} h" if cap else "weekly hours limit removed")
    if changes.get("ramp"):
        parts.append(f"build-up pace {changes['ramp']}")
    if "recovery_cadence_weeks" in changes:
        parts.append(f"a recovery week after every {settings['recovery_cadence_weeks']} loading weeks")
    if "peak_ctl" in changes:
        parts.append(f"peak fitness CTL {targets['peak_ctl']:g}")
    who = {
        "coach": "The coach, at your request, changed",
        "instructions": "Your coach instructions changed",
    }.get(trigger, "You changed")
    code = (
        "INSTRUCTIONS" if trigger == "instructions" else "HOURS_CAP" if "weekly_hours_cap" in changes else "SETTINGS"
    )
    text = f"{who} the plan: {', '.join(parts)}"
    added = [_note_revision(now, trigger, "settings", code, text)]
    added += [
        {**r, "reason_code": code} for r in diff_revisions(before, weeks, [], now, trigger)
    ]
    plan = _commit(plan, view_before, today, now, added + extra)
    return view(plan, today)


def _update_split_table(plan: dict, table: dict | None, trigger: str, today: date) -> dict:
    """
    The plan's own swim/bike/run table, per phase (None goes back to the code
    table). Every week still on the table follows it; a split set by hand stays.
    """
    if not split.has_split(plan):
        raise SeasonEditError("Only a triathlon plan, or one whose coach instructions add a sport, splits its weeks.")
    try:
        rows = split.validate_table(plan, table) if table else None
    except split.SplitError as e:
        raise SeasonEditError(str(e)) from e
    now = _now_iso()
    view_before = _payload_view(plan, today)
    settings = plan.setdefault("settings", {})
    if rows:
        settings["split_by_phase"] = {**(settings.get("split_by_phase") or {}), **rows}
    else:
        settings.pop("split_by_phase", None)
    split.apply_splits(plan)
    who = "The coach, at your request, changed" if trigger == "coach" else "You changed"
    what = "; ".join(f"{phase} {split.label(row)}" for phase, row in (rows or {}).items()) or "back to the default"
    added = [_note_revision(now, trigger, "settings", "SETTINGS", f"{who} the split table: {what}")]
    plan = _commit(plan, view_before, today, now, added)
    return view(plan, today)


def pause(days: int, note: str | None = None, trigger: str = "coach", today: date | None = None) -> dict:
    """
    A break of `days` days from today: every week that ends inside the break
    keeps only what is already done, and the week the athlete comes back in is
    a return week at 60% of the last loading week. All of them are pinned; the
    rest re-flows from the return. The taper is never touched.
    """
    today = today or date.today()
    if not 1 <= days <= 60:
        raise SeasonEditError("A pause is 1 to 60 days.")
    plan = _editable_plan()
    weeks = plan["weeks"]
    now = _now_iso()
    view_before = _payload_view(plan, today)
    before = snapshot(weeks)
    back = today + timedelta(days=days)
    note = (note or "").strip()[:300] or f"{days}-day break"
    tph = _tss_per_hour(plan)
    # The return is sized off the loading week as planned BEFORE the break --
    # the rested weeks no longer count as loading once they are set.
    original = [dict(w) for w in weeks]
    primary = []
    returned = None
    for i, w in enumerate(weeks):
        start = date.fromisoformat(w["week_start"])
        end = start + timedelta(days=6)
        if end < today or w["kind"] in ("taper", "race"):
            continue
        if end < back:
            done = (w.get("actual_tss") or 0) if start <= today else 0
            _set(w, "recovery", done, tph, source="user")
            w["pinned"], w["pin_note"] = True, note
            primary.append((i, "LAYOFF_RETURN", f"A {days}-day break: this week rests"))
            continue
        last, _ = _streak_before(original, i)
        _set(w, "return", (last or w["target_tss"]) * cfg.RETURN_FACTOR, tph, source="user")
        w["pinned"], w["pin_note"] = True, note
        primary.append((i, "LAYOFF_RETURN", f"Back from a {days}-day break: a return week at 60%"))
        returned = i
        break
    if returned is not None:
        reflow(plan, returned + 1, fixed=frozenset({returned}))
    added = diff_revisions(before, weeks, primary, now, trigger)
    plan = _commit(plan, view_before, today, now, added)
    return view(plan, today)


def coach_adjust(tool_input: dict, note: str | None) -> dict:
    """
    The adjust_season_plan coach tool (app/ai/claude.py). Every operation ends
    in the same service call the dashboard's week panel uses. Returns what the
    coach needs to answer: what changed, any warnings, where the week stands.
    """
    op = tool_input.get("operation")
    if op == "set_week_target":
        plan = edit_week(tool_input["week_start"], "set_target", tool_input.get("tss"), note, trigger="coach")
    elif op == "make_recovery":
        plan = edit_week(tool_input["week_start"], "make_recovery", None, note, trigger="coach")
    elif op == "pause":
        plan = pause(int(tool_input["days"]), note, trigger="coach")
    elif op == "set_hours_cap":
        hours = tool_input.get("hours")
        plan = update_settings({"weekly_hours_cap": float(hours) if hours else None}, trigger="coach")
    elif op == "set_ramp":
        plan = update_settings({"ramp": tool_input.get("ramp")}, trigger="coach")
    elif op == "set_split":
        new = {d: tool_input.get(f"{d}_pct") for d in cfg.SPLIT_DISCIPLINES}
        plan = edit_split(tool_input["week_start"], "set_split", new, trigger="coach")
    else:
        raise SeasonEditError(f"Unknown operation {op!r}.")
    changed = [r for r in plan["revisions"] if r["at"] == plan["updated_at"]]
    return {
        "changed": [r["reason_text"] for r in changed],
        "warnings": plan.get("warnings") or [],
        "now": plan["summary"]["line2"],
        "note": "Takes effect in the 7-day plan at the next Sync or Re-sync plan.",
    }


# ---- The event a plan aims at ------------------------------------------------------


def on_event_changed(event_id: str, today: date | None = None) -> None:
    """
    Called after an event is saved or deleted (app/routers/events.py), and by
    the sync as a backstop. Only the plan's own event matters:
    - deleted, or switched to not participating: the plan stays as it is but is
      marked event_missing, and the dashboard asks what to do with it;
    - participating again: active again;
    - a new date or distance: rebuilt from the plan's own first week, with past
      weeks and still-valid pins carried over (EVENT_CHANGED);
    - a new name: renamed.
    """
    plan = store.load_plan()
    if not plan or plan.get("mode") != "event" or plan.get("status") not in ("active", "event_missing"):
        return
    today = today or date.today()
    if (plan.get("event") or {}).get("event_id") == event_id:
        _apply_event(plan, events_service.get_event(event_id), today)
    elif plan.get("status") == "active" and any(e.get("event_id") == event_id for e in plan.get("sub_events") or []):
        # A sub event changed: checked against the main event as stored.
        now = _now_iso()
        view_before = _payload_view(plan, today)
        plan, added = _follow_sub_events(plan, plan.get("event") or {}, today, now)
        if added:
            _commit(plan, view_before, today, now, added)


def _apply_event(plan: dict, event: dict | None, today: date) -> None:
    now = _now_iso()
    view_before = _payload_view(plan, today)
    old = plan.get("event") or {}

    def note(text: str) -> dict:
        return _note_revision(now, "event_change", "event", "EVENT_CHANGED", text)

    if event is None or not events_service.is_participating(event):
        if plan.get("status") == "event_missing":
            return
        plan["status"] = "event_missing"
        why = "was deleted" if event is None else "is no longer one you are racing"
        _commit(plan, view_before, today, now, [note(f"{old.get('name') or 'The event'} {why}")])
        return

    added = []
    if plan.get("status") == "event_missing":
        plan["status"] = "active"
        added.append(note(f"Racing {event.get('name')} again"))
    if (event.get("race_date"), event.get("type")) != (old.get("race_date"), old.get("type")):
        # The sub events are checked against the new date first, so the one
        # rebuild already drops a sub event the main race now comes before.
        subs, sub_notes, _ = _checked_sub_events(plan, event, today, now)
        plan = _rebuild(plan, event, today, now, sub_events=subs)
        added.append(note(f"{event.get('name')} is now {event.get('type')} on {event.get('race_date')}: plan rebuilt"))
        added += sub_notes
    else:
        if event.get("name") != old.get("name"):
            plan["event"]["name"] = event.get("name") or ""
            added.append(note(f"Event renamed to {event.get('name')}"))
        if plan.get("status") == "active":
            plan, more = _follow_sub_events(plan, event, today, now)
            added += more
    if added:
        _commit(plan, view_before, today, now, added)


# ---- Sub events: earlier races the plan eases off around --------------------------


def _sub_note(now: str, text: str, trigger: str = "event_change") -> dict:
    return _note_revision(now, trigger, "sub_events", "SUB_EVENT", text)


def _checked_sub_events(plan: dict, main: dict, today: date, now: str) -> tuple[list[dict], list[dict], bool]:
    """
    The plan's sub events read again from the calendar: the ones that still
    qualify (a finished one stays, as history), a revision for each one dropped
    or changed, and whether a date or distance moved (a rebuild).
    """
    keep: list[dict] = []
    notes: list[dict] = []
    rebuild = False
    try:
        main_race = date.fromisoformat(main.get("race_date") or "")
    except (TypeError, ValueError):
        main_race = None
    for old in plan.get("sub_events") or []:
        event = events_service.get_event(old.get("event_id"))
        name = (event or old).get("name") or "A sub event"
        why = None
        if event is None:
            why = "was deleted"
        elif not events_service.is_participating(event):
            why = "is no longer one you are racing"
        else:
            try:
                race = date.fromisoformat(event.get("race_date") or "")
            except (TypeError, ValueError):
                race = None
            if race is None:
                why = "has no valid race date"
            elif main_race is not None and race >= main_race:
                why = f"is no longer before {main.get('name') or 'the main event'}"
        if why:
            notes.append(_sub_note(now, f"{name} {why}: no longer a sub event"))
            rebuild = True
            continue
        fresh = sub_event_record(event)
        if (fresh["race_date"], fresh["type"]) != (old.get("race_date"), old.get("type")):
            notes.append(_sub_note(now, f"Sub event {name} is now {fresh['type']} on {fresh['race_date']}: plan rebuilt"))
            rebuild = True
        elif fresh["name"] != old.get("name"):
            notes.append(_sub_note(now, f"Sub event renamed to {fresh['name']}"))
        keep.append(fresh)
    return sorted(keep, key=lambda e: e["race_date"]), notes, rebuild


def _follow_sub_events(plan: dict, main: dict, today: date, now: str) -> tuple[dict, list[dict]]:
    """The plan with its sub events brought in line with the calendar, and the
    revisions for what changed."""
    subs, notes, rebuild = _checked_sub_events(plan, main, today, now)
    if not notes:
        return plan, []
    if rebuild:
        return _rebuild(plan, main, today, now, sub_events=subs), notes
    plan["sub_events"] = subs
    names = {e["event_id"]: e["name"] for e in subs}
    for w in plan.get("weeks") or []:
        sub = w.get("sub_event")
        if sub and sub.get("event_id") in names and sub.get("name") != names[sub["event_id"]]:
            sub["name"] = names[sub["event_id"]]
            if not w.get("pinned"):
                w["focus"] = cfg.SUB_FOCUS_TEXT[sub["role"]].format(name=sub["name"])
    return plan, notes


def _main_event(plan: dict) -> dict:
    """The plan's main event as the calendar has it now, else as stored."""
    stored = plan.get("event") or {}
    return (events_service.get_event(stored.get("event_id")) if stored.get("event_id") else None) or stored


def add_sub_event(event_id: str, today: date | None = None) -> dict:
    """Make an earlier event a sub event of the active event plan. The plan is
    rebuilt around it like an event change: past weeks and pins are kept."""
    today = today or date.today()
    plan = _editable_plan()
    if plan.get("mode") != "event":
        raise SeasonEditError("Only an event plan has sub events.")
    if any(e.get("event_id") == event_id for e in plan.get("sub_events") or []):
        return view(plan, today)
    main = _main_event(plan)
    event = events_service.get_event(event_id)
    problem = sub_event_problem(event, main, today)
    if problem:
        raise SeasonEditError(problem)
    now = _now_iso()
    view_before = _payload_view(plan, today)
    subs = sorted((plan.get("sub_events") or []) + [sub_event_record(event)], key=lambda e: e["race_date"])
    plan = _rebuild(plan, main, today, now, sub_events=subs)
    text = f"{event.get('name')} on {event.get('race_date')} added as a sub event"
    plan = _commit(plan, view_before, today, now, [_sub_note(now, text, "user")])
    return view(plan, today)


def remove_sub_event(event_id: str, today: date | None = None) -> dict:
    """Take a sub event off the active event plan; its weeks go back to the path."""
    today = today or date.today()
    plan = _editable_plan()
    subs = plan.get("sub_events") or []
    gone = next((e for e in subs if e.get("event_id") == event_id), None)
    if gone is None:
        raise NoPlanError("That event is not a sub event of the plan.")
    now = _now_iso()
    view_before = _payload_view(plan, today)
    plan = _rebuild(plan, _main_event(plan), today, now, sub_events=[e for e in subs if e.get("event_id") != event_id])
    text = f"{gone.get('name')} is no longer a sub event"
    plan = _commit(plan, view_before, today, now, [_sub_note(now, text, "user")])
    return view(plan, today)


def _rebuild(plan: dict, event: dict, today: date, now: str, sub_events: list[dict] | None = None) -> dict:
    """
    The plan rebuilt for the event's new date or distance, from its own first
    week. Weeks already over keep what they were; a pin on a week the new plan
    still trains (not its taper) is carried over. A race now in the past
    completes the plan instead.
    """
    weeks = plan.get("weeks") or []
    first = date.fromisoformat(weeks[0]["week_start"]) if weeks else None
    settings = plan.get("settings") or {}
    targets = plan.get("targets") or {}
    same_type = (plan.get("event") or {}).get("type") == event.get("type")
    try:
        new = build_event_plan(
            today=today,
            event=event,
            baseline=plan.get("baseline") or {},
            week_start_day=plan.get("week_start_day") or "monday",
            ramp=settings.get("ramp"),
            weekly_hours_cap=settings.get("weekly_hours_cap"),
            peak_ctl=targets.get("peak_ctl") if same_type else None,
            now_iso=now,
            plan_id=plan.get("plan_id"),
            first_week=first,
            recovery_cadence_weeks=settings.get("recovery_cadence_weeks"),
            sub_events=plan.get("sub_events") if sub_events is None else sub_events,
        )
    except SeasonPlanError:
        plan["status"] = "completed"
        plan["event"] = {k: event.get(k) for k in ("event_id", "name", "type", "race_date")}
        return plan

    old_by_start = {w["week_start"]: w for w in weeks}
    for i, w in enumerate(new["weeks"]):
        old = old_by_start.get(w["week_start"])
        if old is None:
            continue
        if date.fromisoformat(w["week_start"]) + timedelta(days=6) < today:
            new["weeks"][i] = {**old, "index": w["index"]}
            continue
        w["actual_tss"], w["actual_hours"] = old.get("actual_tss"), old.get("actual_hours")
        if old.get("actual_by_discipline") is not None:
            w["actual_by_discipline"] = old["actual_by_discipline"]
        if old.get("split_source") in split.FIXED_SOURCES:
            w["split"], w["split_source"] = old.get("split"), old["split_source"]
        if old.get("pinned") and w["kind"] not in ("taper", "race"):
            for k in ("kind", "target_tss", "target_hours", "pinned", "pin_note", "source", "focus"):
                w[k] = old.get(k)
    if same_type and settings.get("split_by_phase"):
        new["settings"]["split_by_phase"] = settings["split_by_phase"]
    for k in ("instructions", "settings_source"):
        if k in plan:
            new[k] = plan[k]
    split.apply_splits(new)
    c = current_index(new["weeks"], today)
    reflow(new, c if c is not None else 0)
    for k in ("created_at", "revisions", "adapt_marks", "last_form", "checkins"):
        if k in plan:
            new[k] = plan[k]
    return new


# ---- Fitness check-ins and the mid-week question ----------------------------------------


def answer_checkin(week_start: str, choice: str, today: date | None = None) -> dict:
    """
    The athlete's answer to a check-in. Two cases:
    - the open check-in (today is the last day of the week before
      `week_start`): the week is set from this week's projected total now, and
      resolved again from the final total when it starts;
    - a change during the week it decided: the week is set from last week's
      final total at once.
    Only an enabled option can be chosen. The stored choice is the intent; the
    number follows from it.
    """
    today = today or date.today()
    plan = _editable_plan()
    if plan.get("mode") != "fitness":
        raise SeasonEditError("Check-ins belong to fitness plans.")
    weeks = plan["weeks"]
    c = current_index(weeks, today)
    now = _now_iso()
    view_before = _payload_view(plan, today)
    if c is None:
        # This week started undecided (no sync yet): answer it on last week's
        # final total, settled at once.
        pending = pending_checkin(plan, today)
        if pending is None or pending["week_start"] != week_start:
            raise SeasonEditError("The plan has no week for today; sync first.")
        ci, closing_done = pending, True
        current_start = None
    else:
        current_start = weeks[c]["week_start"]

    last_day = current_start is not None and today == date.fromisoformat(current_start) + timedelta(days=6)
    next_start = (date.fromisoformat(current_start) + timedelta(days=7)).isoformat() if current_start else None
    if current_start is None:
        pass
    elif last_day and week_start == next_start:
        # The open check-in -- or changing an answer already given today.
        ci = build_checkin(
            plan, c, today, plan.get("last_form") or plan.get("baseline") or {},
            _planned_remaining(weeks[c], today, plan.get("last_workout")),
            _days_off(plan, today), closed=False, opened_at=now,
        )
        closing_done = False
    elif week_start == current_start:
        if c == 0:
            raise SeasonEditError("The first week was set when the plan was made; change it from its week panel.")
        ci = build_checkin(
            plan, c - 1, today, plan.get("last_form") or plan.get("baseline") or {}, 0,
            _days_off(plan, today), closed=True, opened_at=now,
        )
        ci["week_start"], closing_done = week_start, True
    else:
        raise SeasonEditError("No check-in is open for that week.")

    o = option(ci, choice)
    if not o["enabled"]:
        raise SeasonEditError(o["disabled_reason"] or "That choice is not offered.")
    old = next((x["target_tss"] for x in weeks if x["week_start"] == week_start), None)
    w = decide(plan, ci, choice, "checkin_user")
    ci.update(status="answered", source="user", choice=choice, chosen_at=now, resolved=closing_done)
    plan["checkins"] = [x for x in plan.get("checkins") or [] if x.get("week_start") != week_start] + [ci]
    text = f"You chose {CHOICE_LABEL[choice]}" + (
        " (the recommendation)" if choice == ci["recommended"] else f"; the recommendation was {CHOICE_LABEL[ci['recommended']]}"
    )
    added = [_note_revision(now, "user", "target_tss", "CHECKIN", text, w["week_start"], frm=old, to=w["target_tss"])]
    plan = _commit(plan, view_before, today, now, added)
    return view(plan, today)


def week_easy(accept: bool, today: date | None = None) -> dict:
    """
    The answer to the mid-week fatigue question. Yes halves what is left of
    this week's target and pins the week (no sync rule moves it back up); no
    changes nothing. Either way the question is not asked again this week.
    """
    today = today or date.today()
    plan = _editable_plan()
    banner = fatigue_banner(plan, today)
    if banner is None:
        raise SeasonEditError("No fatigue question is open this week.")
    weeks = plan["weeks"]
    c = current_index(weeks, today)
    w = weeks[c]
    now = _now_iso()
    view_before = _payload_view(plan, today)
    plan["easy_answered"] = w["week_start"]
    added = []
    if accept:
        before = snapshot(weeks)
        done = w.get("actual_tss") or 0
        target = done + max(w["target_tss"] - done, 0) * cfg.GATE_RECOVERY_FACTOR
        _set(w, "loading", target, _tss_per_hour(plan), source="user")
        w["pinned"], w["pin_note"] = True, f"Eased for fatigue (TSB {banner['tsb']})"
        added = diff_revisions(before, weeks, [(c, "FATIGUE_GATE", f"TSB {banner['tsb']}: you made the rest of this week easy")], now, "user")
    plan = _commit(plan, view_before, today, now, added)
    return view(plan, today)


# ---- The coach instructions, saved again later -------------------------------------


def on_instructions_changed(today: date | None = None) -> str | None:
    """
    Called after the athlete saves new coach instructions (PUT
    /coach/instructions). Re-reads them and applies the difference from NEXT
    week on: this week and past weeks keep their split ("kept"), a week split
    by hand keeps its own, and only the settings the instructions set (or that
    were left at the default) change -- through update_settings, so the
    reflow and taper rules are the usual ones. Returns the note for the
    athlete, or None when nothing was read: no active plan, the same text, or
    a plan created with "use my instructions" off.
    """
    plan = active_plan()
    if plan is None or plan.get("status") != "active":
        return None
    rec = plan.get("instructions") or {}
    if rec.get("opted_out"):
        return None
    text = get_instructions()
    if text and rec.get("text_sha") == instr.text_sha(text):
        return None
    if not text and not rec.get("applied"):
        return None
    today = today or date.today()
    event_type = (plan.get("event") or {}).get("type")
    focus = (plan.get("settings") or {}).get("focus")
    read = instr.read_instructions(text, instr.context_for(plan.get("mode") or "event", event_type, focus))
    if read.get("error"):
        return f"The season plan could not read your new coach instructions ({read['error'][:120]}); it is unchanged."

    weeks = plan.get("weeks") or []
    c = current_index(weeks, today)
    if c is None:
        started = weeks and date.fromisoformat(weeks[0]["week_start"]) <= today
        c = len(weeks) - 1 if started else -1
    first = c + 1
    now = _now_iso()
    view_before = _payload_view(plan, today)
    split.keep_splits(plan, first)
    plan["instructions"] = instr.record(text, read, applied=bool(text))
    split.apply_splits(plan)  # before _commit compares what the 7-day plan reads
    summary = read.get("summary") or ("Coach instructions cleared." if not text else "Coach instructions read again.")
    added = [_note_revision(now, "instructions", "instructions", "INSTRUCTIONS", f"Your coach instructions changed: {summary}")]
    plan = _commit(plan, view_before, today, now, added)

    # Load: only what the instructions set before, or what is still the default.
    sources = plan.get("settings_source") or {}
    new_load = instr.load_values(read)
    settings = plan.get("settings") or {}
    changes = {}
    for k in instr.LOAD_FIELDS:
        if sources.get(k, "default") == "user":
            continue
        if plan.get("mode") == "fitness" and k in ("ramp", "peak_ctl"):
            continue
        new = new_load.get(k)
        if new is None and sources.get(k) != "instructions":
            continue  # never set by the instructions, and still not: leave it
        current = (plan.get("targets") or {}).get("peak_ctl") if k == "peak_ctl" else settings.get(k)
        if new != current:
            changes[k] = new
    if changes:
        update_settings(changes, trigger="instructions", today=today)

    when = weeks[first]["week_start"] if 0 <= first < len(weeks) else None
    lines = instr.applied_lines(read)
    what = "; ".join(lines) if lines else summary
    return f"Season plan updated from your coach instructions{f' from the week of {when}' if when else ''}: {what}"
