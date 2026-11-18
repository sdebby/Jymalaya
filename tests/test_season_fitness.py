"""
Fitness mode (app/season/checkin.py and its service half): every row of the
recommendation table, the Friel band edges, the recovery counter and the
skip-once rule, the options' arithmetic, the bootstrap, and the lifecycle --
open on the last day, answer, resolve at week start, auto-apply, change
mid-week -- plus the mid-week fatigue question and converting an event plan.

Pure where it can be; the service half uses the temp store tests/conftest.py
gives every test, and every date is passed in.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.recommend_service as reco
from app.season import config as cfg
from app.season import service, store
from app.season.checkin import build_checkin, build_fitness_plan, decide, projection
from app.season.summary import band_for
from main import app

api = TestClient(app, base_url="http://testserver/api")
FIXTURE = Path(__file__).parent / "fixtures" / "season" / "checkin.pending.json"
MON = date(2026, 9, 21)  # a Monday
NOW = "2026-10-11T04:50:00+00:00"


def _w(i: int, kind: str, target: float, actual: float | None) -> dict:
    return {
        "week_start": (MON + timedelta(days=7 * i)).isoformat(),
        "index": i + 1,
        "phase": "fitness",
        "kind": kind,
        "target_tss": target,
        "target_hours": round(target / 55, 1),
        "actual_tss": actual,
        "actual_hours": None if actual is None else round(actual / 55, 1),
        "ctl_end": None,
        "status": "past",
        "pinned": False,
        "pin_note": None,
        "source": "checkin_user",
        "focus": None,
    }


def _plan(weeks, cap=None, checkins=None) -> dict:
    plan = build_fitness_plan(
        today=MON,
        baseline={"ctl": 36.0, "atl": 38.0, "tsb": -2.0, "avg_weekly_tss_4w": 300,
                  "avg_weekly_hours_4w": 5.5, "tss_per_hour": 55, "captured_at": MON.isoformat(),
                  "limited_history": False},
        weekly_hours_cap=cap,
        now_iso="2026-09-20T18:02:00+00:00",
        plan_id="f1",
        first_week=MON,
    )
    plan["weeks"] = weeks
    plan["checkins"] = checkins or []
    return plan


def _ci(plan, tsb=-5.0, remaining=0.0, days_off=1, closed=False):
    closing = len(plan["weeks"]) - 1
    form = {"ctl": 44.1, "atl": 44.1 - tsb, "tsb": tsb}
    return build_checkin(plan, closing, MON, form, remaining, days_off, closed, NOW)


def _opt(ci, choice):
    return next(o for o in ci["options"] if o["choice"] == choice)


# --- the handoff's sample, corrected --------------------------------------------------
def test_the_sample_check_in_is_reproduced():
    """tests/fixtures/season/checkin.pending.json (the handoff's, hours fixed: 377 TSS is 6.9 h)."""
    plan = _plan([_w(0, "loading", 300, 305), _w(1, "loading", 327, 330), _w(2, "loading", 353, 270)])
    ci = build_checkin(plan, 2, MON, {"ctl": 44.1, "atl": 53.0, "tsb": -8.9}, 82, 1, False, NOW)
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert ci == expected
    assert ci["closing_week"]["projected_tss"] == 352
    assert ci["recommended"] == "recovery"
    assert [(o["choice"], o["target_tss"], o["delta_pct_vs_last"]) for o in ci["options"]] == [
        ("increase", 377, 7), ("stay", 352, 0), ("decrease", 310, -12), ("recovery", 230, -35),
    ]


# --- bands ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tsb,band",
    [(25, "fresh"), (5, "fresh"), (4.9, "ok"), (-10, "ok"), (-10.1, "tired"), (-30, "tired"), (-30.1, "high")],
)
def test_friel_band_edges(tsb, band):
    assert band_for(tsb) == band


def test_two_check_ins_in_a_row_below_minus_20_is_high():
    assert band_for(-21, -22) == "high"
    assert band_for(-21, -19) == "tired"


# --- the recommendation table, row by row -------------------------------------------
def test_row_1_high_fatigue_recommends_recovery_and_disables_increase():
    ci = _ci(_plan([_w(0, "loading", 300, 300)]), tsb=-35)
    assert ci["recommended"] == "recovery"
    assert _opt(ci, "increase")["enabled"] is False
    assert _opt(ci, "increase")["disabled_reason"] == "Not offered while fatigue is high."
    assert _opt(ci, "recovery")["enabled"] is True


def test_row_1_high_from_two_check_ins_in_a_row():
    prev = {"form": {"tsb": -24}}
    ci = _ci(_plan([_w(0, "loading", 300, 300)], checkins=[prev]), tsb=-21)
    assert ci["form"]["band"] == "high"
    assert ci["recommended"] == "recovery"


def test_row_1_three_loading_weeks_recommend_recovery_with_a_skip_when_fresh():
    plan = _plan([_w(i, "loading", 300 + 20 * i, 300 + 20 * i) for i in range(3)])
    ci = _ci(plan, tsb=6)
    assert ci["loading_weeks_since_recovery"] == 3
    assert ci["recommended"] == "recovery"
    assert "put it off once" in ci["reason"]
    assert all(o["enabled"] for o in ci["options"])


def test_a_recovery_week_due_on_tired_legs_cannot_be_skipped():
    plan = _plan([_w(i, "loading", 300, 300) for i in range(3)])
    ci = _ci(plan, tsb=-15)
    assert [o["choice"] for o in ci["options"] if o["enabled"]] == ["recovery"]
    assert "fatigue says take it" in _opt(ci, "stay")["disabled_reason"]


def test_a_recovery_week_is_put_off_once_at_most():
    plan = _plan([_w(i, "loading", 300, 300) for i in range(4)])
    ci = _ci(plan, tsb=6)
    assert ci["recommended"] == "recovery"
    assert [o["choice"] for o in ci["options"] if o["enabled"]] == ["recovery"]


def test_row_2_a_layoff_offers_a_return_week():
    plan = _plan([_w(0, "loading", 300, 300), _w(1, "loading", 320, 40)])
    ci = _ci(plan, tsb=10, days_off=9, closed=True)
    assert ci["recommended"] == "recovery"
    assert ci["return_week"] is True
    assert _opt(ci, "recovery")["target_tss"] == round(320 * cfg.RETURN_FACTOR)
    assert decide(plan, ci, "recovery", "checkin_auto")["kind"] == "return"


def test_row_3_a_short_week_holds_on_what_was_done():
    plan = _plan([_w(0, "loading", 300, 300), _w(1, "loading", 320, 200)])
    ci = _ci(plan, tsb=0, closed=True)
    assert ci["recommended"] == "stay"
    assert _opt(ci, "stay")["target_tss"] == 200  # no catch-up
    assert "no catch-up" in ci["reason"]


@pytest.mark.parametrize("done,expected", [(320, "stay"), (370, "decrease")])
def test_row_4_tired(done, expected):
    plan = _plan([_w(0, "loading", 300, 300), _w(1, "loading", 320, done)])
    assert _ci(plan, tsb=-20, closed=True)["recommended"] == expected


@pytest.mark.parametrize("tsb", [0, 8])
def test_row_5_good_form_and_a_full_week_steps_up(tsb):
    plan = _plan([_w(0, "loading", 300, 300), _w(1, "loading", 320, 300)])
    assert _ci(plan, tsb=tsb, closed=True)["recommended"] == "increase"


def test_row_6_otherwise_stay():
    plan = _plan([_w(0, "loading", 300, 300), _w(1, "loading", 320, 270)])  # 84%
    assert _ci(plan, tsb=8, closed=True)["recommended"] == "stay"


# --- the options' arithmetic ------------------------------------------------------------
def test_after_a_recovery_week_stay_and_increase_build_on_the_last_loading_week():
    plan = _plan([_w(0, "loading", 300, 310), _w(1, "recovery", 210, 200)])
    ci = _ci(plan, tsb=8, closed=True)
    assert _opt(ci, "stay")["target_tss"] == 310
    assert _opt(ci, "increase")["target_tss"] == round(310 * 1.07)


def test_the_hours_cap_disables_increase():
    plan = _plan([_w(0, "loading", 330, 330)], cap=6.2)  # 341 TSS
    ci = _ci(plan, tsb=0, closed=True)
    assert _opt(ci, "increase")["enabled"] is False
    assert "6.2 h" in _opt(ci, "increase")["disabled_reason"]
    assert ci["recommended"] != "increase"


def test_bootstrap_starts_at_the_average_floored_for_a_new_athlete():
    base = {"ctl": 5.0, "atl": 5.0, "tsb": 0.0, "avg_weekly_tss_4w": 40, "avg_weekly_hours_4w": 0.8,
            "tss_per_hour": 55, "captured_at": MON.isoformat(), "limited_history": True}
    plan = build_fitness_plan(today=MON, baseline=base, now_iso=NOW)
    assert plan["weeks"][0]["target_tss"] == cfg.MIN_START_TSS
    capped = build_fitness_plan(today=MON, baseline={**base, "limited_history": False, "avg_weekly_tss_4w": 500}, weekly_hours_cap=5, now_iso=NOW)
    assert capped["weeks"][0]["target_tss"] == 275


def test_projection_continues_the_rhythm():
    weeks = [_w(0, "loading", 300, 300), _w(1, "loading", 321, 321)]
    out = projection(_plan(weeks), weeks)
    assert [(w["kind"], w["target_tss"]) for w in out] == [("loading", 343), ("recovery", 240), ("loading", 367)]


# --- the lifecycle -----------------------------------------------------------------------
SUN = MON + timedelta(days=6)


@pytest.fixture()
def stored(monkeypatch):
    """A fitness plan in its first week, nothing planned in the 7-day plan."""
    monkeypatch.setattr(reco, "latest_recommendation", lambda: None)
    plan = _plan([_w(0, "loading", 300, 250)])
    plan["created_at"] = MON.isoformat() + "T06:00:00+00:00"
    plan["last_form"] = {"ctl": 40.0, "atl": 40.0, "tsb": 0.0}
    plan["last_workout"] = (SUN - timedelta(days=1)).isoformat()
    store.save_plan(plan)
    return plan


def test_no_check_in_before_the_last_day(stored):
    assert service.pending_checkin(store.load_plan(), MON + timedelta(days=3)) is None


def test_the_check_in_opens_on_the_last_day_and_is_never_stored(stored):
    plan = service.get_plan(SUN)
    ci = plan["checkins"][-1]
    assert ci["status"] == "pending"
    assert ci["week_start"] == (MON + timedelta(days=7)).isoformat()
    statuses = [w["status"] for w in plan["weeks"]]
    assert statuses == ["current", "pending_checkin", "projected", "projected", "projected"]
    assert store.load_plan()["checkins"] == []
    assert len(store.load_plan()["weeks"]) == 1
    assert "Choose next week's load." in plan["summary"]["line2"]


def test_the_planned_rest_of_the_week_counts_until_today_is_logged(stored, monkeypatch):
    monkeypatch.setattr(reco, "latest_recommendation", lambda: {"days": [
        {"date": SUN.isoformat(), "sessions": [{"estimated_tss": 50}]},
        {"date": (SUN + timedelta(days=1)).isoformat(), "sessions": [{"estimated_tss": 99}]},
    ]})
    assert service.pending_checkin(store.load_plan(), SUN)["closing_week"]["projected_tss"] == 300
    plan = store.load_plan()
    plan["last_workout"] = SUN.isoformat()
    store.save_plan(plan)
    assert service.pending_checkin(store.load_plan(), SUN)["closing_week"]["projected_tss"] == 250


def test_answering_sets_next_week_then_resolves_it_from_the_final_total(stored):
    plan = service.answer_checkin((MON + timedelta(days=7)).isoformat(), "stay", today=SUN)
    nxt = plan["weeks"][1]
    assert (nxt["kind"], nxt["target_tss"], nxt["source"], nxt["status"]) == ("loading", 250, "checkin_user", "next")
    saved = store.load_plan()
    assert saved["checkins"][-1]["status"] == "answered" and saved["checkins"][-1]["resolved"] is False
    assert saved["payload_changed_at"] != stored["payload_changed_at"]

    # The last workout of the week landed late: 280 in the end.
    saved["weeks"][0]["actual_tss"] = 280
    added, notices = service.roll_fitness(saved, MON + timedelta(days=8), {}, NOW)
    assert saved["weeks"][1]["target_tss"] == 280
    assert saved["checkins"][-1]["resolved"] is True
    assert notices == []
    again, _ = service.roll_fitness(saved, MON + timedelta(days=9), {}, NOW)
    assert again == []


def test_an_answer_can_be_changed_on_the_same_day(stored):
    nxt = (MON + timedelta(days=7)).isoformat()
    service.answer_checkin(nxt, "stay", today=SUN)
    out = service.answer_checkin(nxt, "decrease", today=SUN)
    assert out["weeks"][1]["target_tss"] == round(250 * 0.88)
    assert [c["choice"] for c in store.load_plan()["checkins"]] == ["decrease"]


def test_an_unanswered_check_in_is_applied_at_week_start(stored):
    plan = store.load_plan()
    added, notices = service.roll_fitness(plan, MON + timedelta(days=8), {}, NOW)
    ci = plan["checkins"][-1]
    assert ci["status"] == "auto_applied" and ci["source"] == "auto"
    assert plan["weeks"][1]["source"] == "checkin_auto"
    assert notices == [f"This week set to {ci['choice'].capitalize()} (auto). Change it any time that week."]
    assert added[0]["reason_code"] == "CHECKIN"


def test_a_week_that_started_before_a_sync_can_still_be_chosen(stored):
    """Monday morning, no sync yet: the check-in stays open for this week."""
    tue = MON + timedelta(days=8)
    plan = service.get_plan(tue)
    ci = plan["checkins"][-1]
    assert ci["status"] == "pending" and ci["week_start"] == (MON + timedelta(days=7)).isoformat()
    assert ci["closing_week"]["planned_remaining_tss"] == 0  # last week is over
    assert plan["summary"]["line2"].startswith("This week's load is not chosen yet")
    out = service.answer_checkin(ci["week_start"], "stay", today=tue)
    assert out["weeks"][1]["target_tss"] == 250
    assert store.load_plan()["checkins"][-1]["resolved"] is True
    # Nothing is left for the next sync to apply.
    saved = store.load_plan()
    added, notices = service.roll_fitness(saved, tue, {}, NOW)
    assert notices == [] and not [r for r in added if r["reason_text"].endswith("automatically")]


def test_missed_weeks_are_each_decided_in_turn(stored):
    plan = store.load_plan()
    service.roll_fitness(plan, MON + timedelta(days=22), {}, NOW)
    assert len(plan["weeks"]) == 4
    assert [c["status"] for c in plan["checkins"]] == ["auto_applied"] * 3


def test_the_choice_can_change_during_the_week_it_decided(stored):
    plan = store.load_plan()
    service.roll_fitness(plan, MON + timedelta(days=8), {}, NOW)
    store.save_plan(plan)
    out = service.answer_checkin((MON + timedelta(days=7)).isoformat(), "decrease", today=MON + timedelta(days=9))
    assert out["weeks"][1]["target_tss"] == round(250 * 0.88)
    assert out["checkins"][-1]["choice"] == "decrease" and out["checkins"][-1]["resolved"] is True


def test_a_disabled_choice_is_refused(stored):
    plan = store.load_plan()
    plan["last_form"]["tsb"] = -35
    store.save_plan(plan)
    with pytest.raises(service.SeasonEditError, match="fatigue is high"):
        service.answer_checkin((MON + timedelta(days=7)).isoformat(), "increase", today=SUN)


def test_no_check_in_open_is_refused(stored):
    with pytest.raises(service.SeasonEditError):
        service.answer_checkin((MON + timedelta(days=14)).isoformat(), "stay", today=SUN)
    with pytest.raises(service.SeasonEditError, match="first week"):
        service.answer_checkin(MON.isoformat(), "stay", today=MON + timedelta(days=2))


# --- the mid-week fatigue question -----------------------------------------------------
def test_the_fatigue_question_halves_the_rest_of_the_week(stored):
    plan = store.load_plan()
    plan["last_form"]["tsb"] = -32
    store.save_plan(plan)
    wed = MON + timedelta(days=2)
    assert service.get_plan(wed)["fatigue_banner"] == {"week_start": MON.isoformat(), "tsb": -32}
    out = service.week_easy(True, today=wed)
    w = out["weeks"][0]
    assert w["target_tss"] == round(250 + 50 * 0.5) and w["pinned"]
    assert out["fatigue_banner"] is None
    with pytest.raises(service.SeasonEditError):
        service.week_easy(True, today=wed)


def test_declining_changes_nothing_and_does_not_ask_again(stored):
    plan = store.load_plan()
    plan["last_form"]["tsb"] = -32
    store.save_plan(plan)
    out = service.week_easy(False, today=MON + timedelta(days=2))
    assert out["weeks"][0]["target_tss"] == 300
    assert out["fatigue_banner"] is None


def test_no_fatigue_question_on_fresh_legs_or_a_recovery_week(stored):
    wed = MON + timedelta(days=2)
    assert service.get_plan(wed)["fatigue_banner"] is None
    plan = store.load_plan()
    plan["last_form"]["tsb"] = -35
    plan["weeks"][0]["kind"] = "recovery"
    store.save_plan(plan)
    assert service.get_plan(wed)["fatigue_banner"] is None


# --- the payload, settings and conversion ---------------------------------------------
def test_the_payload_carries_the_focus_and_says_next_week_is_open(stored):
    plan = store.load_plan()
    plan["settings"]["focus"] = "run"
    store.save_plan(plan)
    wed = MON + timedelta(days=2)
    block = service.payload_block(wed, [(wed + timedelta(days=i)).isoformat() for i in range(7)])
    assert block["focus_disciplines"] == ["run"]
    assert block["next_week_decided"] is False
    assert block["mode"] == "fitness" and "event" not in block


def test_fitness_settings_take_the_focus_and_refuse_a_ramp(stored):
    out = service.update_settings({"focus": "bike", "weekly_hours_cap": 7.0}, today=MON + timedelta(days=2))
    assert out["settings"]["focus"] == "bike" and out["settings"]["weekly_hours_cap"] == 7.0
    assert [w["target_tss"] for w in out["weeks"] if w["status"] != "projected"] == [300]
    with pytest.raises(service.SeasonEditError):
        service.update_settings({"ramp": "gentle"})


def test_converting_an_event_plan_keeps_its_history(monkeypatch):
    from app.season.builder import build_event_plan

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(days=14)
    old = build_event_plan(
        today=first,
        event={"event_id": "e1", "name": "Gone Race", "type": "10K", "race_date": (first + timedelta(days=60)).isoformat()},
        baseline={"ctl": 40, "atl": 40, "tsb": 0, "avg_weekly_tss_4w": 280, "avg_weekly_hours_4w": 5,
                  "tss_per_hour": 55, "captured_at": first.isoformat(), "limited_history": False},
        now_iso=first.isoformat() + "T06:00:00+00:00",
    )
    old["weeks"][0]["actual_tss"] = 270
    old["status"] = "event_missing"
    store.save_plan(old)
    monkeypatch.setattr(service, "_read_history", lambda: ([], {"ctl": 40.0, "atl": 41.0, "tsb": -1.0}))
    monkeypatch.setattr(reco, "latest_recommendation", lambda: None)
    plan = service.create({"mode": "fitness", "convert": True, "focus": "run", "week_start_day": "monday"})
    saved = store.load_plan()
    assert saved["mode"] == "fitness" and saved["event"] is None
    assert [w["week_start"] for w in saved["weeks"]][:3] == [(first + timedelta(days=7 * i)).isoformat() for i in range(3)]
    assert saved["weeks"][0]["actual_tss"] == 270
    assert saved["weeks"][2]["target_tss"] == old["weeks"][2]["target_tss"]
    assert plan["revisions"][0]["reason_text"].startswith("Converted to a fitness plan from Gone Race")


def test_routes(stored):
    # Open or not depends on today's weekday; the route answers either way.
    assert api.get("/season-plan/checkin").status_code == 200
    assert api.post("/season-plan/checkin", json={"week_start": "2001-01-01", "choice": "stay"}).status_code == 422
    assert api.post("/season-plan/checkin", json={"week_start": "2001-01-01", "choice": "more"}).status_code == 422
    assert api.post("/season-plan/week-easy", json={"accept": True}).status_code == 422


def test_routes_404_without_a_plan():
    assert api.post("/season-plan/week-easy", json={"accept": True}).status_code == 404
    assert api.post("/season-plan/checkin", json={"week_start": "2001-01-01", "choice": "stay"}).status_code == 404
    assert api.get("/season-plan/checkin").json() is None
