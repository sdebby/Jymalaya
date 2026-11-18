"""
The athlete's own season-plan changes: week pins (edit_week), settings, a
pause, and the plan following its event (on_event_changed and the events
router hook).

No network: plans come from the builder, events are faked, the store is the
temp file tests/conftest.py gives every test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import events_service
from app.season import config as cfg
from app.season import service, store
from app.season.adapt import reconcile
from app.season.builder import build_event_plan
from main import app

api = TestClient(app, base_url="http://testserver/api")

TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())
EVENT = {"event_id": "e1", "name": "Spring Olympic", "type": "olympic"}


def _race(runway: int) -> str:
    return (MONDAY + timedelta(days=7 * (runway - 1) + 5)).isoformat()


def _plan(runway: int = 20, cap: float | None = None) -> dict:
    plan = build_event_plan(
        today=MONDAY,
        event={**EVENT, "race_date": _race(runway)},
        baseline={
            "ctl": 40.0, "atl": 42.0, "tsb": -2.0, "avg_weekly_tss_4w": 300,
            "avg_weekly_hours_4w": 5.5, "tss_per_hour": 55, "captured_at": MONDAY.isoformat(),
            "limited_history": False,
        },
        weekly_hours_cap=cap,
        now_iso=datetime.combine(MONDAY, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
        plan_id="p1",
    )
    store.save_plan(plan)
    return plan


def _ws(i: int) -> str:
    """The first day of 0-based plan week i."""
    return (MONDAY + timedelta(days=7 * i)).isoformat()


def _weeks():
    return store.load_plan()["weeks"]


def _shape(weeks):
    """What an edit is about: each week's kind, target and pin (not its CTL)."""
    return [(w["week_start"], w["kind"], w["target_tss"], w["pinned"]) for w in weeks]


# --- edit_week --------------------------------------------------------------------
def test_lighter_pins_the_week_and_reflows_after_it():
    before = _plan()["weeks"]
    out = service.edit_week(_ws(2), "lighter", note="travel")
    w = _weeks()[2]
    assert w["target_tss"] == round(before[2]["target_tss"] * 0.9)
    assert w["pinned"] is True and w["pin_note"] == "travel" and w["source"] == "user"
    # The next loading week grows from the pinned one.
    assert _weeks()[4]["target_tss"] < before[4]["target_tss"]
    rev = next(r for r in out["revisions"] if r["week_start"] == _ws(2) and r["field"] == "target_tss")
    assert rev["reason_code"] == "USER_PIN" and rev["trigger"] == "user"
    assert rev["reason_text"] == "You lightened this week: travel"
    assert out["warnings"] == []


def test_a_big_raise_is_allowed_with_a_warning():
    _plan()
    out = service.edit_week(_ws(1), "set_target", 900)
    assert _weeks()[1]["target_tss"] == 900
    assert "Raises load more than 10% over the last loading week." in out["warnings"]


def test_heavier_keeps_a_recovery_week_a_recovery_week():
    plan = _plan()
    rec = next(i for i, w in enumerate(plan["weeks"]) if w["kind"] == "recovery")
    service.edit_week(_ws(rec), "heavier")
    w = _weeks()[rec]
    assert w["kind"] == "recovery" and w["pinned"]
    assert w["target_tss"] == round(plan["weeks"][rec]["target_tss"] * 1.1)


def test_rhythm_warnings_name_a_missed_recovery_week():
    weeks = [{"kind": "loading", "target_tss": 300 + 10 * i} for i in range(6)]
    assert service.rhythm_warnings(weeks, 5) == [
        "Runs more than three loading weeks without a recovery week."
    ]
    weeks[3]["kind"] = "recovery"
    assert service.rhythm_warnings(weeks, 5) == []


def test_make_recovery_uses_the_last_loading_week():
    plan = _plan()
    service.edit_week(_ws(2), "make_recovery")
    w = _weeks()[2]
    assert w["kind"] == "recovery"
    assert w["target_tss"] == round(plan["weeks"][1]["target_tss"] * cfg.RECOVERY_FACTOR)


def test_skip_keeps_only_what_is_already_done_this_week():
    plan = _plan()
    plan["weeks"][0]["actual_tss"] = 120
    store.save_plan(plan)
    service.edit_week(_ws(0), "skip")
    assert _weeks()[0]["target_tss"] == 120
    service.edit_week(_ws(3), "skip")
    assert _weeks()[3]["target_tss"] == 0


def test_unpin_hands_the_week_back_to_the_plan():
    plan = _plan()
    service.edit_week(_ws(2), "set_target", 100)
    service.edit_week(_ws(2), "unpin")
    after = _weeks()
    assert after[2]["pinned"] is False
    assert [w["target_tss"] for w in after] == [w["target_tss"] for w in plan["weeks"]]


def test_a_pinned_week_survives_the_sync_adaptation():
    _plan()
    service.edit_week(_ws(1), "set_target", 333)
    plan = store.load_plan()
    after, _ = reconcile(plan, MONDAY + timedelta(days=2), {}, {"ctl": 40, "atl": 75, "tsb": -35}, MONDAY, "x")
    assert after["weeks"][1]["target_tss"] == 333


def test_editing_next_week_makes_the_7_day_plan_stale_but_a_far_week_does_not():
    plan = _plan()
    service.edit_week(_ws(8), "lighter")
    assert store.load_plan()["payload_changed_at"] == plan["payload_changed_at"]
    service.edit_week(_ws(1), "lighter")
    assert store.load_plan()["payload_changed_at"] != plan["payload_changed_at"]


@pytest.mark.parametrize(
    "week,error",
    [(-1, service.SeasonEditError), (19, service.SeasonEditError), (40, service.NoPlanError)],
)
def test_past_taper_and_unknown_weeks_are_refused(week, error):
    plan = _plan()
    if week == -1:
        plan["weeks"].insert(0, {**plan["weeks"][0], "week_start": _ws(-1)})
        store.save_plan(plan)
    with pytest.raises(error):
        service.edit_week(_ws(week), "lighter")


def test_no_plan_is_refused():
    with pytest.raises(service.NoPlanError):
        service.edit_week(_ws(1), "lighter")


# --- settings and pause --------------------------------------------------------------
def test_a_lower_hours_cap_clamps_the_future_but_not_this_week():
    plan = _plan()
    service.update_settings({"weekly_hours_cap": 6.0})
    after = _weeks()
    assert after[0]["target_tss"] == plan["weeks"][0]["target_tss"]
    assert all(w["target_tss"] <= 330 for w in after[1:])
    saved = store.load_plan()
    assert saved["targets"]["peak_weekly_tss"] == 330
    assert saved["revisions"][-2]["reason_code"] in ("HOURS_CAP", "PEAK_LOWERED")


def test_the_taper_follows_a_lower_peak():
    _plan()
    service.update_settings({"weekly_hours_cap": 6.0})
    weeks = _weeks()
    top = max(w["target_tss"] for w in weeks if w["kind"] == "loading")
    taper = [w["target_tss"] for w in weeks if w["kind"] in ("taper", "race")]
    assert taper == [round(top * f) for f in cfg.TAPER_FACTORS[2]]


def test_removing_the_cap_lets_the_path_grow_again():
    _plan(cap=6.0)
    service.update_settings({"weekly_hours_cap": None})
    saved = store.load_plan()
    assert saved["settings"]["weekly_hours_cap"] is None
    assert max(w["target_tss"] for w in saved["weeks"]) == 7 * 72


def test_a_new_ramp_changes_the_growth():
    _plan()
    service.update_settings({"ramp": "gentle"})
    saved = store.load_plan()
    assert saved["settings"]["growth_pct_per_week"] == 5.0
    assert saved["weeks"][2]["target_tss"] == round(saved["weeks"][1]["target_tss"] * 1.05)


def test_pause_rests_the_break_and_returns_at_sixty_percent():
    plan = _plan()
    out = service.pause(10, "knee niggle", today=MONDAY + timedelta(days=1))
    after = out["weeks"]
    assert after[0]["target_tss"] == 0 and after[0]["pinned"]
    assert after[1]["kind"] == "return" and after[1]["pinned"]
    assert after[1]["target_tss"] == round(plan["weeks"][0]["target_tss"] * cfg.RETURN_FACTOR)
    assert after[1]["pin_note"] == "knee niggle"
    assert _shape(after[-2:]) == _shape(plan["weeks"][-2:])


# --- routes ------------------------------------------------------------------------------
def test_patch_week_route():
    _plan()
    r = api.patch(f"/season-plan/weeks/{_ws(1)}", json={"action": "set_target", "value": 900, "note": "big week"})
    assert r.status_code == 200
    assert r.json()["warnings"]
    assert api.patch(f"/season-plan/weeks/{_ws(19)}", json={"action": "lighter"}).status_code == 422
    assert api.patch("/season-plan/weeks/2001-01-01", json={"action": "lighter"}).status_code == 404
    assert api.patch(f"/season-plan/weeks/{_ws(1)}", json={"action": "double"}).status_code == 422
    assert api.patch(f"/season-plan/weeks/{_ws(1)}", json={"action": "set_target"}).status_code == 422


def test_patch_settings_route_only_changes_what_is_sent():
    _plan(cap=8.0)
    r = api.patch("/season-plan/settings", json={"ramp": "gentle"})
    assert r.status_code == 200
    assert r.json()["settings"]["weekly_hours_cap"] == 8.0
    r = api.patch("/season-plan/settings", json={"weekly_hours_cap": None})
    assert r.json()["settings"]["weekly_hours_cap"] is None
    assert api.patch("/season-plan/settings", json={"weekly_hours_cap": 99}).status_code == 422


def test_routes_404_without_a_plan():
    assert api.patch(f"/season-plan/weeks/{_ws(1)}", json={"action": "lighter"}).status_code == 404
    assert api.patch("/season-plan/settings", json={"ramp": "gentle"}).status_code == 404


# --- the event --------------------------------------------------------------------------
@pytest.fixture()
def event(monkeypatch):
    """The stored event, editable by the test; None = deleted."""
    box = {"event": None}
    monkeypatch.setattr(events_service, "get_event", lambda eid: box["event"] if box["event"] and box["event"]["event_id"] == eid else None)
    return box


def test_deleting_the_event_marks_the_plan_event_missing(event):
    plan = _plan()
    service.on_event_changed("e1")
    saved = store.load_plan()
    assert saved["status"] == "event_missing"
    assert _shape(saved["weeks"]) == _shape(plan["weeks"])
    assert service.get_plan()["status"] == "event_missing"
    assert service.payload_block(TODAY, [TODAY.isoformat()]) is None
    with pytest.raises(service.NoPlanError):
        service.edit_week(_ws(1), "lighter")


def test_not_participating_then_back_again(event):
    _plan()
    event["event"] = {**EVENT, "race_date": _race(20), "participating": False}
    service.on_event_changed("e1")
    assert store.load_plan()["status"] == "event_missing"
    event["event"]["participating"] = True
    service.on_event_changed("e1")
    assert store.load_plan()["status"] == "active"


def test_a_new_race_date_rebuilds_from_the_plans_first_week_keeping_pins(event):
    _plan(runway=20)
    service.edit_week(_ws(2), "set_target", 250, note="exams")
    event["event"] = {**EVENT, "race_date": _race(24), "participating": True}
    service.on_event_changed("e1")
    saved = store.load_plan()
    assert len(saved["weeks"]) == 24
    assert saved["weeks"][0]["week_start"] == _ws(0)
    assert saved["weeks"][2]["target_tss"] == 250 and saved["weeks"][2]["pin_note"] == "exams"
    assert saved["revisions"][-1]["reason_code"] == "EVENT_CHANGED"
    assert saved["plan_id"] == "p1"


def test_a_new_distance_takes_its_own_peak(event):
    _plan(runway=20)
    event["event"] = {**EVENT, "type": "140.6", "race_date": _race(20), "participating": True}
    service.on_event_changed("e1")
    saved = store.load_plan()
    assert saved["targets"]["peak_ctl"] == cfg.PEAK_CTL["140.6"]
    assert [w["phase"] for w in saved["weeks"]].count("taper") == cfg.PHASE_WEEKS_OUT["140.6"]["taper"] - 1


def test_a_rename_only_renames(event):
    plan = _plan()
    event["event"] = {**EVENT, "name": "Spring Olympic 2027", "race_date": _race(20), "participating": True}
    service.on_event_changed("e1")
    saved = store.load_plan()
    assert saved["event"]["name"] == "Spring Olympic 2027"
    assert _shape(saved["weeks"]) == _shape(plan["weeks"])


def test_a_race_moved_into_the_past_completes_the_plan(event):
    _plan()
    event["event"] = {**EVENT, "race_date": (TODAY - timedelta(days=2)).isoformat(), "participating": True}
    service.on_event_changed("e1")
    assert store.load_plan()["status"] == "completed"


def test_another_event_changes_nothing(event):
    plan = _plan()
    service.on_event_changed("someone-else")
    assert store.load_plan() == plan


def test_the_events_router_hook_follows_and_never_fails_the_edit(event, monkeypatch):
    _plan()
    monkeypatch.setattr(events_service, "delete_event", lambda eid: True)
    assert api.delete("/events/e1").status_code == 204
    assert store.load_plan()["status"] == "event_missing"

    def boom(eid):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(service, "on_event_changed", boom)
    assert api.delete("/events/e1").status_code == 204
