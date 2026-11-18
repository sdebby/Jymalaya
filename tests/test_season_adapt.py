"""
Event-plan adaptation on sync (app/season/adapt.py): one case per rule, a few
combinations, and the guarantees -- idempotent, pins and taper untouched, the
10% cap still holds after a re-flow.

Pure: a plan from the builder, then reconcile() with hand-made weekly totals,
load and last-workout day. No network, no store.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.season import config as cfg
from app.season.adapt import reconcile
from app.season.builder import build_event_plan

START = date(2026, 11, 2)  # a Monday
NOW = "2026-12-01T06:00:00+00:00"


def _plan(runway=20, avg=300):
    race = START + timedelta(days=7 * (runway - 1) + 5)
    return build_event_plan(
        today=START,
        event={"event_id": "e1", "name": "Race", "type": "olympic", "race_date": race.isoformat()},
        baseline={
            "ctl": 40.0, "atl": 42.0, "tsb": -2.0, "avg_weekly_tss_4w": avg,
            "avg_weekly_hours_4w": 5.5, "tss_per_hour": 55, "captured_at": START.isoformat(),
            "limited_history": False,
        },
        now_iso="2026-11-02T06:00:00+00:00",
        plan_id="p1",
    )


def _day(week: int, weekday: int = 0) -> date:
    """`weekday` (0 = Monday) of plan week `week` (1-based)."""
    return START + timedelta(days=7 * (week - 1) + weekday)


def _totals(plan, actuals: dict[int, float]) -> dict:
    """Weekly totals for the given 1-based week numbers."""
    return {
        plan["weeks"][i - 1]["week_start"]: {"tss": tss, "hours": tss / 55}
        for i, tss in actuals.items()
    }


def _run(plan, today, actuals=None, tsb=-5.0, last=None):
    load = {"ctl": 45.0, "atl": 45.0 - tsb, "tsb": tsb}
    return reconcile(
        plan, today, _totals(plan, actuals or {}), load,
        last if last is not None else today - timedelta(days=1), NOW,
    )


def _targets(plan):
    return [w["target_tss"] for w in plan["weeks"]]


def _codes(added):
    return [r["reason_code"] for r in added]


def test_nothing_to_do_adds_no_revision_but_fills_actuals_and_form():
    plan = _plan()
    after, added = _run(plan, _day(2, 2), {1: 300, 2: 150})
    assert added == []
    assert _targets(after) == _targets(plan)
    assert after["weeks"][0]["actual_tss"] == 300
    assert after["weeks"][1]["actual_tss"] == 150
    assert after["weeks"][2]["actual_tss"] is None
    assert after["last_form"]["tsb"] == -5.0
    # Not mutated in place.
    assert plan["weeks"][0]["actual_tss"] is None


def test_fatigue_gate_lowers_the_next_loading_week():
    plan = _plan()
    after, added = _run(plan, _day(2, 2), {1: 300}, tsb=-35)
    w3 = after["weeks"][2]
    assert w3["target_tss"] == round(plan["weeks"][2]["target_tss"] * cfg.GATE_LOWER_FACTOR)
    assert w3["source"] == "adapt"
    direct = [r for r in added if r["field"] == "target_tss"]
    assert [r["week_start"] for r in direct] == [w3["week_start"]]
    assert direct[0]["reason_code"] == "FATIGUE_GATE"
    assert "TSB -35" in direct[0]["reason_text"]
    # This week is left alone.
    assert after["weeks"][1]["target_tss"] == plan["weeks"][1]["target_tss"]


def test_deep_fatigue_halves_this_week_and_makes_next_week_recovery():
    plan = _plan()
    after, added = _run(plan, _day(2, 2), {1: 300, 2: 100}, tsb=-45)
    cur, nxt = after["weeks"][1], after["weeks"][2]
    target = plan["weeks"][1]["target_tss"]
    assert cur["kind"] == "recovery"
    assert cur["target_tss"] == round(100 + (target - 100) * cfg.GATE_RECOVERY_FACTOR)
    assert nxt["kind"] == "recovery"
    assert nxt["target_tss"] == round(plan["weeks"][0]["target_tss"] * cfg.RECOVERY_FACTOR)
    assert _codes(added).count("FATIGUE_GATE") >= 2


def test_under_delivery_rebuilds_from_the_mean_actual():
    plan = _plan()
    w1, w2 = plan["weeks"][0]["target_tss"], plan["weeks"][1]["target_tss"]
    a1, a2 = round(w1 * 0.6), round(w2 * 0.7)
    after, added = _run(plan, _day(3, 0), {1: a1, 2: a2})
    assert after["weeks"][2]["target_tss"] == round((a1 + a2) / 2)
    assert "UNDER_DELIVERY" in _codes(added)
    # No catch-up: nothing after it is above the old path.
    assert all(a <= b for a, b in zip(_targets(after)[2:], _targets(plan)[2:]))


def test_under_delivery_lowers_a_peak_it_can_no_longer_reach():
    plan = _plan(runway=12, avg=300)
    after, added = _run(plan, _day(3, 0), {1: 120, 2: 130})
    assert "PEAK_LOWERED" in _codes(added)
    # The peak still ahead, from this week on.
    top = max(w["target_tss"] for w in after["weeks"][2:] if w["kind"] == "loading")
    assert after["targets"]["peak_weekly_tss"] == top


def test_a_lowered_peak_never_leaves_the_taper_above_the_build():
    plan = _plan(runway=12, avg=300)
    after, _ = _run(plan, _day(3, 0), {1: 120, 2: 130})
    top = max(w["target_tss"] for w in after["weeks"] if w["kind"] == "loading")
    assert all(w["target_tss"] < top for w in after["weeks"] if w["kind"] in ("taper", "race"))


def test_an_empty_week_is_never_under_delivery():
    plan = _plan()
    after, added = _run(plan, _day(3, 0), {1: 0, 2: 0}, last=_day(3, 0) - timedelta(days=3))
    assert "UNDER_DELIVERY" not in _codes(added)


def test_over_delivery_holds_this_week():
    plan = _plan()
    w1 = plan["weeks"][0]["target_tss"]
    after, added = _run(plan, _day(2, 0), {1: round(w1 * 1.2)}, tsb=-15)
    assert after["weeks"][1]["target_tss"] == w1
    assert "OVER_DELIVERY_HOLD" in _codes(added)


def test_over_delivery_without_fatigue_changes_nothing():
    plan = _plan()
    w1 = plan["weeks"][0]["target_tss"]
    after, added = _run(plan, _day(2, 0), {1: round(w1 * 1.2)}, tsb=-5)
    assert added == []


def test_fresh_bonus_raises_this_week_within_the_cap():
    plan = _plan()
    w1, w2 = plan["weeks"][0]["target_tss"], plan["weeks"][1]["target_tss"]
    after, added = _run(plan, _day(2, 0), {1: w1}, tsb=12)
    raised = after["weeks"][1]["target_tss"]
    assert w2 < raised <= round(w1 * (1 + cfg.MAX_WEEKLY_GROWTH))
    assert raised <= round(w2 * (1 + cfg.FRESH_BONUS_PCT))
    assert _codes(added) == ["FRESH_BONUS"]
    # Only this week moves.
    assert _targets(after)[2:] == _targets(plan)[2:]


def test_fresh_but_short_week_gets_no_bonus():
    plan = _plan()
    w1 = plan["weeks"][0]["target_tss"]
    _, added = _run(plan, _day(2, 0), {1: round(w1 * 0.8)}, tsb=12)
    assert "FRESH_BONUS" not in _codes(added)


def test_layoff_makes_next_week_a_return_week():
    plan = _plan()
    today = _day(3, 3)
    after, added = _run(plan, today, {1: 300, 2: 50}, last=today - timedelta(days=9))
    nxt = after["weeks"][3]
    assert nxt["kind"] == "return"
    assert nxt["target_tss"] == round(plan["weeks"][1]["target_tss"] * cfg.RETURN_FACTOR)
    assert "LAYOFF_RETURN" in _codes(added)
    # Regrowth from the return week, at the plan's growth.
    after_return = after["weeks"][4]
    assert after_return["kind"] == "loading"
    assert after_return["target_tss"] == round(nxt["target_tss"] * 1.08)


def test_a_young_plan_is_not_a_layoff():
    """No training yet since a plan made today is not seven days off."""
    plan = _plan()
    _, added = _run(plan, _day(1, 2), {}, last=START - timedelta(days=30))
    assert "LAYOFF_RETURN" not in _codes(added)


def test_fatigue_and_over_delivery_combine():
    plan = _plan()
    w1 = plan["weeks"][0]["target_tss"]
    after, added = _run(plan, _day(2, 0), {1: round(w1 * 1.25)}, tsb=-33)
    assert after["weeks"][1]["target_tss"] == w1  # held
    assert after["weeks"][2]["target_tss"] < plan["weeks"][2]["target_tss"]  # gated
    assert {"FATIGUE_GATE", "OVER_DELIVERY_HOLD"} <= set(_codes(added))


@pytest.mark.parametrize(
    "case",
    [
        dict(today=(2, 2), actuals={1: 300}, tsb=-35),
        dict(today=(2, 2), actuals={1: 300, 2: 100}, tsb=-45),
        dict(today=(3, 0), actuals={1: 180, 2: 200}, tsb=-5),
        dict(today=(2, 0), actuals={1: 400}, tsb=-15),
        dict(today=(2, 0), actuals={1: 300}, tsb=12),
    ],
)
def test_second_run_with_the_same_inputs_changes_nothing(case):
    plan = _plan()
    today = _day(*case["today"])
    once, added = _run(plan, today, case["actuals"], tsb=case["tsb"])
    assert added
    twice, again = _run(once, today, case["actuals"], tsb=case["tsb"])
    assert again == []
    assert twice["weeks"] == once["weeks"]
    assert twice["revisions"] == once["revisions"]


def test_a_rule_fires_once_per_trigger_even_if_tsb_stays_low():
    plan = _plan()
    once, _ = _run(plan, _day(2, 1), {1: 300}, tsb=-35)
    later, again = _run(once, _day(2, 4), {1: 300}, tsb=-36)
    assert [r for r in again if r["field"] == "target_tss"] == []
    assert later["weeks"][2]["target_tss"] == once["weeks"][2]["target_tss"]


def test_a_pinned_week_is_never_changed():
    plan = _plan()
    plan["weeks"][2]["pinned"] = True
    plan["weeks"][2]["pin_note"] = "travel"
    after, added = _run(plan, _day(2, 2), {1: 300}, tsb=-35)
    assert after["weeks"][2]["target_tss"] == plan["weeks"][2]["target_tss"]
    # The gate moves to the next loading week the athlete did not pin.
    changed = next(r for r in added if r["field"] == "target_tss")
    assert changed["week_start"] != plan["weeks"][2]["week_start"]


def test_taper_and_race_weeks_are_never_changed():
    plan = _plan(runway=8)
    taper = [w for w in plan["weeks"] if w["kind"] in ("taper", "race")]
    last_build = len(plan["weeks"]) - len(taper)
    # Deep fatigue in the last build week reaches into the taper's neighbour.
    after, _ = _run(plan, _day(last_build, 2), {i: 300 for i in range(1, last_build + 1)}, tsb=-45)
    assert [w for w in after["weeks"] if w["phase"] in ("taper", "race")] == [
        {**w, "ctl_end": a["ctl_end"], "actual_tss": a["actual_tss"], "actual_hours": a["actual_hours"], "status": a["status"]}
        for w, a in zip(taper, after["weeks"][last_build:])
    ]


@pytest.mark.parametrize("tsb", [-45, -35, 12])
def test_the_cap_and_cadence_hold_after_a_reflow(tsb):
    plan = _plan(runway=24, avg=250)
    after, _ = _run(plan, _day(2, 2), {1: 250}, tsb=tsb)
    loading = [w for w in after["weeks"] if w["kind"] == "loading"]
    for a, b in zip(loading, loading[1:]):
        assert b["target_tss"] <= round(a["target_tss"] * (1 + cfg.MAX_WEEKLY_GROWTH)) + 1
    streak = 0
    build = [w for w in after["weeks"] if w["kind"] not in ("taper", "race")]
    for w in build:
        streak = streak + 1 if w["kind"] == "loading" else 0
        assert streak <= cfg.RECOVERY_CADENCE_WEEKS + 1


def test_past_weeks_take_the_stored_ctl():
    plan = _plan()
    end_w1 = (_day(1, 6)).isoformat()
    after, _ = reconcile(plan, _day(2, 2), {}, {"ctl": 45, "atl": 50, "tsb": -5}, _day(2, 1), NOW, {end_w1: 43.21})
    assert after["weeks"][0]["ctl_end"] == 43.2


def test_fitness_plans_only_get_the_refresh():
    plan = _plan()
    plan["mode"] = "fitness"
    after, added = _run(plan, _day(2, 2), {1: 300}, tsb=-45)
    assert added == []
    assert after["weeks"][0]["actual_tss"] == 300
