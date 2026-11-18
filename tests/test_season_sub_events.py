"""
Sub events: races before the season plan's main event that the plan eases off
around (a mini-taper sized by distance, config.SUB_EVENT_TAPER), with the main
event still the goal everywhere -- the 7-day plan's primary_goal included.

No network: plans come from the builder, events are faked, the store is the
temp file tests/conftest.py gives every test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import events_service
from app import schedule_store
from app.ai import prompt_builder as pb
from app.season import adapt
from app.season import config as cfg
from app.season import service, store
from app.season.builder import build_event_plan
from main import app

api = TestClient(app, base_url="http://testserver/api")

# --- the builder (pure, fixed dates) ----------------------------------------------
B_MONDAY = date(2026, 11, 2)
BASELINE = {
    "ctl": 40.0, "atl": 42.0, "tsb": -2.0, "avg_weekly_tss_4w": 300,
    "avg_weekly_hours_4w": 5.5, "tss_per_hour": 55, "captured_at": B_MONDAY.isoformat(),
    "limited_history": False,
}


def _day(week: int, weekday: int = 2, monday: date = B_MONDAY) -> str:
    return (monday + timedelta(days=7 * week + weekday)).isoformat()


def _sub(event_id: str, type_: str, week: int, weekday: int = 2, monday: date = B_MONDAY) -> dict:
    return {"event_id": event_id, "name": f"Sub {event_id}", "type": type_, "race_date": _day(week, weekday, monday)}


def _build(subs: list[dict], runway: int = 20, event_type: str = "olympic") -> dict:
    return build_event_plan(
        today=B_MONDAY,
        event={"event_id": "main", "name": "Main race", "type": event_type, "race_date": _day(runway - 1, 5)},
        baseline=dict(BASELINE),
        now_iso="2026-11-02T08:00:00+00:00",
        plan_id="p1",
        sub_events=subs,
    )


def _last_loading_before(weeks: list[dict], i: int) -> float:
    return next(w["target_tss"] for w in reversed(weeks[:i]) if w["kind"] == "loading")


def test_a_10k_sub_event_week_is_eased_and_growth_resumes_after_it():
    plan = _build([_sub("s1", "10K", 6)])
    weeks = plan["weeks"]
    w = weeks[6]
    assert w["kind"] == "sub_race"
    assert w["sub_event"]["event_id"] == "s1" and w["sub_event"]["role"] == "race"
    last = _last_loading_before(weeks, 6)
    assert w["target_tss"] == round(last * 0.80)
    # The phase stays the main event's: a sub event changes the load, not the phase.
    assert w["phase"] == _build([])["weeks"][6]["phase"]
    nxt = weeks[7]
    assert nxt["kind"] == "loading"
    assert nxt["target_tss"] == min(round(last * 1.08), plan["targets"]["peak_weekly_tss"])
    assert plan["sub_events"] == [_sub("s1", "10K", 6)]
    assert w["focus"] == "Sub s1 race week"


def test_a_marathon_sub_event_gets_a_week_before_and_a_recovery_week_after():
    weeks = _build([_sub("m", "marathon", 8, 6)], runway=24, event_type="70.3")["weeks"]
    assert [weeks[i]["kind"] for i in (7, 8, 9)] == ["sub_taper", "sub_race", "recovery"]
    assert [weeks[i]["sub_event"]["role"] for i in (7, 8, 9)] == ["pre", "race", "post"]
    last = _last_loading_before(weeks, 7)
    assert [weeks[i]["target_tss"] for i in (7, 8, 9)] == [round(last * f) for f in (0.85, 0.60, 0.60)]


def test_no_recovery_week_lands_right_before_a_mini_taper():
    plain = _build([])["weeks"]
    assert plain[3]["kind"] == "recovery"  # 3 loading weeks, then recovery
    weeks = _build([_sub("s1", "10K", 4)])["weeks"]
    assert weeks[3]["kind"] == "loading"
    assert weeks[4]["kind"] == "sub_race"


def test_a_sub_event_in_the_main_taper_marks_no_week_but_is_listed():
    plan = _build([_sub("s1", "5K", 18)])  # olympic tapers 2 weeks: index 18 is taper
    assert not any(w.get("sub_event") for w in plan["weeks"])
    assert [e["event_id"] for e in plan["sub_events"]] == ["s1"]


def test_two_sub_events_in_one_week_take_the_lower_factor():
    weeks = _build([_sub("a", "10K", 8, 1), _sub("b", "marathon", 8, 6)], runway=24, event_type="70.3")["weeks"]
    assert weeks[8]["kind"] == "sub_race" and weeks[8]["sub_factor"] == 0.60
    assert weeks[8]["sub_event"]["event_id"] == "b"
    assert weeks[7]["sub_event"]["role"] == "pre"


def test_a_plan_without_sub_events_has_no_key():
    assert "sub_events" not in _build([])


# --- adaptation -------------------------------------------------------------------
def test_reflow_resizes_a_sub_event_week_with_the_path():
    plan = _build([_sub("s1", "10K", 6)])
    adapt.reflow(plan, 0, level=150)
    weeks = plan["weeks"]
    assert weeks[6]["kind"] == "sub_race"
    assert weeks[6]["target_tss"] == round(_last_loading_before(weeks, 6) * 0.80)
    assert not adapt._editable(weeks[6])


def test_reconcile_twice_adds_nothing_the_second_time():
    plan = _build([_sub("s1", "10K", 2)])
    load = {"ctl": 40.0, "atl": 80.0, "tsb": -40.5}
    first, _ = adapt.reconcile(plan, B_MONDAY + timedelta(days=1), {}, load, None, "now")
    second, added = adapt.reconcile(first, B_MONDAY + timedelta(days=1), {}, load, None, "now")
    assert added == []
    assert first["weeks"][2]["kind"] == "sub_race"


# --- the service and the API (today-based dates) ----------------------------------
TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())
MAIN = {"event_id": "main", "name": "Spring 70.3", "type": "70.3", "race_date": _day(19, 5, MONDAY), "participating": True}
TEN_K = {"event_id": "tenk", "name": "City 10K", "type": "10K", "race_date": _day(6, 2, MONDAY), "participating": True}
BODY = {"mode": "event", "event_id": "main", "week_start_day": "monday", "start": "this"}


@pytest.fixture(autouse=True)
def events(monkeypatch):
    workouts = [
        {"date": (TODAY - timedelta(days=d)).isoformat(), "time": datetime(2026, 1, 1, tzinfo=timezone.utc),
         "discipline": "run", "tss": 60, "duration_sec": 3600}
        for d in range(1, 36)
    ]
    monkeypatch.setattr(service, "_read_history", lambda: (workouts, {"ctl": 40.0, "atl": 44.0, "tsb": -4.0}))
    evs = {"main": dict(MAIN), "tenk": dict(TEN_K)}
    monkeypatch.setattr(events_service, "get_event", lambda eid: dict(evs[eid]) if eid in evs else None)

    def nearest():
        upcoming = sorted(
            (e for e in evs.values() if e["race_date"] >= TODAY.isoformat() and e.get("participating", True)),
            key=lambda e: e["race_date"],
        )
        return dict(upcoming[0]) if upcoming else None

    monkeypatch.setattr(events_service, "active_event", nearest)
    return evs


def _sub_week(plan: dict, event_id: str) -> dict | None:
    return next((w for w in plan["weeks"] if (w.get("sub_event") or {}).get("event_id") == event_id), None)


def test_create_with_a_sub_event():
    r = api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    assert r.status_code == 201, r.text
    plan = store.load_plan()
    assert [e["event_id"] for e in plan["sub_events"]] == ["tenk"]
    assert _sub_week(plan, "tenk")["kind"] == "sub_race"


@pytest.mark.parametrize("change", [{"race_date": _day(25, 2, MONDAY)}, {"participating": False}])
def test_an_ineligible_sub_event_is_422(events, change):
    events["tenk"].update(change)
    r = api.post("/season-plan/preview", json={**BODY, "sub_event_ids": ["tenk"]})
    assert r.status_code == 422


def test_add_and_remove_through_the_api():
    api.post("/season-plan", json=BODY)
    stamp = store.load_plan()["payload_changed_at"]
    r = api.post("/season-plan/sub-events", json={"event_id": "tenk"})
    assert r.status_code == 200, r.text
    plan = store.load_plan()
    assert _sub_week(plan, "tenk") is not None
    assert plan["revisions"][-1]["reason_code"] == "SUB_EVENT"
    assert plan["payload_changed_at"] >= stamp

    r = api.delete("/season-plan/sub-events/tenk")
    assert r.status_code == 200
    plan = store.load_plan()
    assert not plan.get("sub_events") and _sub_week(plan, "tenk") is None
    assert api.delete("/season-plan/sub-events/tenk").status_code == 404


def test_the_main_event_cannot_be_its_own_sub_event():
    api.post("/season-plan", json=BODY)
    assert api.post("/season-plan/sub-events", json={"event_id": "main"}).status_code == 422


def test_no_plan_is_404():
    assert api.post("/season-plan/sub-events", json={"event_id": "tenk"}).status_code == 404


def test_a_pin_on_a_sub_event_week_survives_adding_it():
    api.post("/season-plan", json=BODY)
    week = _day(6, 0, MONDAY)
    service.edit_week(week, "set_target", 123)
    service.add_sub_event("tenk")
    w = next(w for w in store.load_plan()["weeks"] if w["week_start"] == week)
    assert w["pinned"] and w["target_tss"] == 123


def test_a_deleted_sub_event_is_dropped(events):
    api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    events.pop("tenk")
    service.on_event_changed("tenk")
    plan = store.load_plan()
    assert not plan.get("sub_events") and _sub_week(plan, "tenk") is None
    assert plan["revisions"][-1]["reason_text"] == "City 10K was deleted: no longer a sub event"


def test_a_sub_event_moved_past_the_main_race_is_dropped(events):
    api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    events["tenk"]["race_date"] = _day(22, 2, MONDAY)
    service.on_event_changed("tenk")
    assert not store.load_plan().get("sub_events")


def test_a_sub_event_date_change_moves_its_week(events):
    api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    events["tenk"]["race_date"] = _day(9, 2, MONDAY)
    service.on_event_changed("tenk")
    plan = store.load_plan()
    assert _sub_week(plan, "tenk")["week_start"] == _day(9, 0, MONDAY)
    assert plan["sub_events"][0]["race_date"] == _day(9, 2, MONDAY)


def test_a_rename_renames_without_a_rebuild(events):
    api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    events["tenk"]["name"] = "Harbour 10K"
    service.on_event_changed("tenk")
    plan = store.load_plan()
    assert plan["sub_events"][0]["name"] == "Harbour 10K"
    assert _sub_week(plan, "tenk")["focus"] == "Harbour 10K race week"


def test_moving_the_main_race_before_a_sub_event_drops_it(events):
    api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    events["main"]["race_date"] = _day(5, 5, MONDAY)
    service.on_event_changed("main")
    plan = store.load_plan()
    assert not plan.get("sub_events")
    assert any(r["reason_code"] == "SUB_EVENT" for r in plan["revisions"])


# --- the 7-day plan and the coach -------------------------------------------------
class FakeRepo:
    def query(self, flux):
        if "daily_load" in flux:
            return [{"date": TODAY.isoformat(), "discipline": "all", "ctl": 45.0, "atl": 50.0, "tsb": -5.0, "tss": 0}]
        return []

    def close(self):
        pass


def _payload(monkeypatch):
    monkeypatch.setattr(pb, "InfluxRepo", lambda: FakeRepo())
    monkeypatch.setattr(pb, "drop_split_brick_parents", lambda rows: rows)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    monkeypatch.setattr(pb, "get_schedule", lambda: schedule_store._normalize(schedule_store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_plan_payload()


def test_without_a_plan_the_goal_is_the_nearest_event(monkeypatch):
    p = _payload(monkeypatch)
    assert p["primary_goal"]["name"] == "City 10K"
    assert "sub_events" not in p


def test_with_a_plan_the_goal_is_the_main_event_and_sub_events_are_listed(monkeypatch):
    api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    p = _payload(monkeypatch)
    assert p["primary_goal"]["name"] == "Spring 70.3"
    assert p["sub_events"] == [{
        "name": "City 10K", "type": "10K", "race_disciplines": ["run"],
        "race_date": TEN_K["race_date"], "days_until": (date.fromisoformat(TEN_K["race_date"]) - TODAY).days,
    }]


def test_a_plan_without_sub_events_still_aims_at_its_main_event(monkeypatch):
    api.post("/season-plan", json=BODY)
    p = _payload(monkeypatch)
    assert p["primary_goal"]["name"] == "Spring 70.3"
    assert "sub_events" not in p


def test_the_coach_line_names_the_sub_events():
    api.post("/season-plan", json={**BODY, "sub_event_ids": ["tenk"]})
    line = service.coach_line(TODAY)
    assert "sub events" in line and "City 10K (10K)" in line


def test_rule_33_is_in_the_prompt():
    text = (Path(__file__).parent.parent / "prompts" / "recommendation_system.txt").read_text(encoding="utf-8")
    assert "\n33. sub_events lists races" in text
    assert "rule 31 is for primary_goal's race only" in text


def test_the_sub_event_mini_taper_table_covers_every_distance():
    import typing

    from app.models import EventType

    assert set(cfg.SUB_EVENT_TAPER) == set(typing.get_args(EventType))
