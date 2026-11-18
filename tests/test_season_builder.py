"""
The event-mode season plan builder (app/season/builder.py) and its store.

Pure functions, no network: every case passes today, the event and the baseline
in, so the whole table of distances x runways runs in milliseconds.
"""

from __future__ import annotations

import json
import typing
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.metrics.calculator import _step
from app.models import EventType
from app.season import config as cfg
from app.season import store
from app.season.builder import (
    SeasonPlanError,
    build_event_plan,
    phase_for,
    start_week,
    weeks_out,
)
from app.season.models import SeasonPlan

TYPES = typing.get_args(EventType)
RUNWAYS = (1, 2, 4, 8, 16, 30)
MONDAY = date(2026, 11, 2)
FIXTURE = Path(__file__).parent / "fixtures" / "season" / "season_plan.event.json"
PHASE_ORDER = ["base", "build", "peak", "taper", "race"]


def _baseline(avg=290, ctl=41.0, atl=45.0, tss_per_hour=55, limited=False):
    return {
        "ctl": ctl,
        "atl": atl,
        "tsb": ctl - atl,
        "avg_weekly_tss_4w": avg,
        "avg_weekly_hours_4w": round(avg / tss_per_hour, 1),
        "tss_per_hour": tss_per_hour,
        "captured_at": MONDAY.isoformat(),
        "limited_history": limited,
    }


def _plan(event_type="olympic", runway=16, today=MONDAY, **kw):
    """A plan whose race falls on the Thursday of week `runway`."""
    race = today + timedelta(days=7 * (runway - 1) + 3)
    kw.setdefault("baseline", _baseline())
    return build_event_plan(
        today=today,
        event={"event_id": "e1", "name": "Test race", "type": event_type, "race_date": race.isoformat()},
        now_iso="2026-11-02T08:00:00+00:00",
        plan_id="p1",
        **kw,
    )


@pytest.mark.parametrize("event_type", TYPES)
@pytest.mark.parametrize("runway", RUNWAYS)
def test_shape_holds_for_every_distance_and_runway(event_type, runway):
    plan = _plan(event_type, runway)
    weeks = plan["weeks"]
    SeasonPlan.model_validate(plan)

    # One week per calendar week, the race week last.
    assert len(weeks) == runway
    assert [w["index"] for w in weeks] == list(range(1, runway + 1))
    for a, b in zip(weeks, weeks[1:]):
        assert date.fromisoformat(b["week_start"]) - date.fromisoformat(a["week_start"]) == timedelta(days=7)
    assert weeks[-1]["kind"] == "race" and weeks[-1]["phase"] == "race"

    # Phases come from the files' weeks-out table, in order.
    race = date.fromisoformat(plan["event"]["race_date"])
    for w in weeks:
        assert w["phase"] == phase_for(event_type, weeks_out(race, date.fromisoformat(w["week_start"])))
    order = [PHASE_ORDER.index(w["phase"]) for w in weeks]
    assert order == sorted(order)

    # The taper is the last weeks, never longer than the type's table says.
    taper = [w for w in weeks if w["phase"] in ("taper", "race")]
    assert len(taper) == min(runway, cfg.PHASE_WEEKS_OUT[event_type]["taper"])
    assert weeks[-len(taper):] == taper
    assert all(a["target_tss"] > b["target_tss"] for a, b in zip(taper, taper[1:]))

    build = weeks[: len(weeks) - len(taper)]
    loading = [w for w in build if w["kind"] == "loading"]
    top = max((w["target_tss"] for w in loading), default=None)
    if top is not None:
        assert all(w["target_tss"] < top for w in taper)

    # Loading weeks never grow more than 10% over the previous loading week.
    for a, b in zip(loading, loading[1:]):
        assert b["target_tss"] <= round(a["target_tss"] * (1 + cfg.MAX_WEEKLY_GROWTH)) + 1

    # A recovery week follows 3 loading weeks, at 70% of the last one, and
    # never sits right before the taper (the taper is the recovery).
    streak = 0
    last_loading = None
    for i, w in enumerate(build):
        if w["kind"] == "recovery":
            assert streak == cfg.RECOVERY_CADENCE_WEEKS
            assert w["target_tss"] == round(last_loading * cfg.RECOVERY_FACTOR)
            assert i != len(build) - 1
            streak = 0
        else:
            assert w["kind"] == "loading"
            streak += 1
            last_loading = w["target_tss"]
            # The one recovery a plan may skip is the one right before the taper.
            assert streak <= cfg.RECOVERY_CADENCE_WEEKS or (
                streak == cfg.RECOVERY_CADENCE_WEEKS + 1 and i == len(build) - 1
            )

    assert all(len(line) < cfg.SUMMARY_MAX_CHARS for line in plan["summary"].values())


def test_growth_resumes_from_the_last_loading_week_not_the_recovery_week():
    weeks = _plan("olympic", 22, baseline=_baseline(avg=200))["weeks"]
    rec = next(i for i, w in enumerate(weeks) if w["kind"] == "recovery")
    before, after = weeks[rec - 1], weeks[rec + 1]
    assert after["kind"] == "loading"
    assert after["target_tss"] == round(before["target_tss"] * 1.08)


def test_first_loading_week_is_the_baseline():
    assert _plan("olympic", 16)["weeks"][0]["target_tss"] == 290


@pytest.mark.parametrize("ramp,growth", [("gentle", 0.05), ("standard", 0.08), ("ambitious", 0.10)])
def test_ramp_sets_the_growth(ramp, growth):
    weeks = _plan("140.6", 30, baseline=_baseline(avg=200), ramp=ramp)["weeks"]
    assert weeks[1]["target_tss"] == round(200 * (1 + growth))


def test_short_runway_compresses_and_lowers_the_peak():
    plan = _plan("olympic", 8, baseline=_baseline(avg=200))
    loading = [w["target_tss"] for w in plan["weeks"] if w["kind"] == "loading"]
    targets = plan["targets"]
    assert targets["compressed"] is True
    assert targets["peak_weekly_tss"] == max(loading) < 7 * 72
    codes = {r["reason_code"] for r in plan["revisions"]}
    assert {"COMPRESSED", "PEAK_LOWERED"} <= codes
    assert "Compressed: peak lowered to" in plan["summary"]["line1"]


def test_long_runway_reaches_the_peak_and_holds():
    plan = _plan("olympic", 30, baseline=_baseline(avg=290))
    loading = [w["target_tss"] for w in plan["weeks"] if w["kind"] == "loading"]
    assert max(loading) == 7 * 72
    assert plan["targets"]["compressed"] is False
    assert not plan["revisions"]


def test_hours_cap_clamps_loading_weeks_and_logs_once():
    plan = _plan("olympic", 30, weekly_hours_cap=7.0)
    cap = round(7.0 * 55)
    assert all(w["target_tss"] <= cap for w in plan["weeks"])
    assert max(w["target_hours"] for w in plan["weeks"]) <= 7.0
    codes = [r["reason_code"] for r in plan["revisions"]]
    assert codes.count("HOURS_CAP") == 1
    # The cap, not the runway, holds the peak down: not "compressed".
    assert plan["targets"]["compressed"] is False
    assert plan["targets"]["peak_weekly_tss"] == cap


def test_baseline_above_the_target_holds_rather_than_grows():
    plan = _plan("5K", 16, baseline=_baseline(avg=500))
    loading = [w["target_tss"] for w in plan["weeks"] if w["kind"] == "loading"]
    assert set(loading) == {500}
    assert plan["targets"]["compressed"] is False


def test_limited_history_starts_no_lower_than_the_floor():
    plan = _plan("olympic", 16, baseline=_baseline(avg=40, limited=True))
    assert plan["weeks"][0]["target_tss"] == cfg.MIN_START_TSS


def test_peak_ctl_override_sets_the_peak_weekly_tss():
    plan = _plan("olympic", 30, peak_ctl=60)
    assert plan["targets"]["peak_ctl"] == 60
    assert max(w["target_tss"] for w in plan["weeks"]) == 420


def test_taper_factors_follow_the_highest_loading_week():
    plan = _plan("140.6", 30)
    top = max(w["target_tss"] for w in plan["weeks"] if w["kind"] == "loading")
    taper = [w["target_tss"] for w in plan["weeks"] if w["phase"] in ("taper", "race")]
    assert taper == [round(top * f) for f in cfg.TAPER_FACTORS[5]]


def test_race_week_only_plan():
    plan = _plan("olympic", 1)
    assert [w["kind"] for w in plan["weeks"]] == ["race"]
    assert "taper and race only" in plan["summary"]["line1"]


def test_past_race_is_refused():
    with pytest.raises(SeasonPlanError):
        build_event_plan(
            today=MONDAY,
            event={"event_id": "e", "name": "x", "type": "10K", "race_date": "2026-10-01"},
            baseline=_baseline(),
        )


def test_missing_race_date_is_refused():
    with pytest.raises(SeasonPlanError):
        build_event_plan(
            today=MONDAY, event={"event_id": "e", "name": "x", "type": "10K", "race_date": ""}, baseline=_baseline()
        )


def test_race_today_is_a_one_week_plan():
    plan = build_event_plan(
        today=MONDAY,
        event={"event_id": "e", "name": "x", "type": "10K", "race_date": MONDAY.isoformat()},
        baseline=_baseline(),
    )
    assert [w["kind"] for w in plan["weeks"]] == ["race"]


def test_unknown_type_falls_back_to_custom():
    plan = _plan("relay", 16)
    assert plan["event"]["type"] == "custom"


@pytest.mark.parametrize(
    "today,week_start_day,start,expected",
    [
        (date(2026, 11, 4), "monday", None, date(2026, 11, 2)),  # Wednesday: this week
        (date(2026, 11, 8), "monday", None, date(2026, 11, 9)),  # Sunday, last day: next week
        (date(2026, 11, 4), "monday", "next", date(2026, 11, 9)),
        (date(2026, 11, 7), "sunday", None, date(2026, 11, 8)),  # Saturday ends a Sunday week
        (date(2026, 11, 8), "sunday", None, date(2026, 11, 8)),  # Sunday starts one
    ],
)
def test_start_week(today, week_start_day, start, expected):
    assert start_week(today, week_start_day, start) == expected


def test_sunday_plan_weeks_start_on_sunday():
    plan = _plan("10K", 8, today=date(2026, 11, 8), week_start_day="sunday")
    assert all(date.fromisoformat(w["week_start"]).weekday() == 6 for w in plan["weeks"])


def test_ctl_projection_uses_the_stored_series_recurrence():
    plan = _plan("olympic", 16)
    c, a = 41.0, 45.0
    for _ in range(7):
        c, a = _step(c, a, plan["weeks"][0]["target_tss"] / 7)
    assert plan["weeks"][0]["ctl_end"] == round(c, 1)


def test_statuses_for_today():
    plan = _plan("olympic", 16)
    statuses = [w["status"] for w in plan["weeks"]]
    assert statuses[:3] == ["current", "next", "projected"]
    assert "past" not in statuses


def test_long_event_name_keeps_both_lines_short():
    plan = build_event_plan(
        today=MONDAY,
        event={
            "event_id": "e",
            "name": "The Extremely Long Annual International Championship Triathlon of the Southern Red Sea Coast",
            "type": "140.6",
            "race_date": (MONDAY + timedelta(days=7 * 29)).isoformat(),
        },
        baseline=_baseline(avg=100),
    )
    assert all(len(line) < cfg.SUMMARY_MAX_CHARS for line in plan["summary"].values())


def test_contract_sample_is_reproduced_exactly():
    """tests/fixtures/season/season_plan.event.json -- see its README."""
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    SeasonPlan.model_validate(sample)
    built = build_event_plan(
        today=date.fromisoformat(sample["as_of"]),
        event=sample["event"],
        baseline=sample["baseline"],
        week_start_day=sample["week_start_day"],
        ramp=sample["settings"]["ramp"],
        weekly_hours_cap=sample["settings"]["weekly_hours_cap"],
        now_iso=sample["created_at"],
        plan_id=sample["plan_id"],
    )
    assert json.loads(json.dumps(built)) == sample


def test_store_round_trip_and_clear():
    assert store.load_plan() is None
    assert store.payload_changed_at() is None
    plan = _plan("olympic", 8)
    store.save_plan(plan)
    assert store.load_plan() == json.loads(json.dumps(plan))
    assert store.payload_changed_at() == plan["payload_changed_at"]
    assert not store.SEASON_PLAN_FILE.with_suffix(".json.tmp").exists()
    assert store.clear_plan() is True
    assert store.clear_plan() is False
    assert store.load_plan() is None


def test_store_ignores_an_unreadable_file():
    store.SEASON_PLAN_FILE.write_text("{not json", encoding="utf-8")
    assert store.load_plan() is None
