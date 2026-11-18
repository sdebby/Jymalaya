"""
The season plan routes (app/routers/season.py) and the baseline they build from.

No network: the history read and the events are faked; the store is the temp
file tests/conftest.py gives every test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import events_service
from app.routers import data as data_router
from app.season import service, store
from main import app

api = TestClient(app, base_url="http://testserver/api")

TODAY = date.today()
RACE = (TODAY + timedelta(days=7 * 20)).isoformat()
EVENT = {
    "event_id": "ev1",
    "name": "Spring Olympic",
    "type": "olympic",
    "race_date": RACE,
    "participating": True,
}
BODY = {"mode": "event", "event_id": "ev1", "ramp": "standard", "week_start_day": "monday"}


def _workout(day: date, tss: float, hours: float) -> dict:
    return {
        "date": day.isoformat(),
        "time": datetime(day.year, day.month, day.day, 7, tzinfo=timezone.utc),
        "discipline": "run",
        "tss": tss,
        "duration_sec": hours * 3600,
    }


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    """Five weeks of 300 TSS / 5 h, and one racing event."""
    workouts = [_workout(TODAY - timedelta(days=d), 60, 1.0) for d in range(1, 36)]
    monkeypatch.setattr(
        service, "_read_history", lambda: (workouts, {"ctl": 40.0, "atl": 44.0, "tsb": -4.0})
    )
    events = {"ev1": dict(EVENT)}
    monkeypatch.setattr(events_service, "get_event", lambda eid: events.get(eid))
    monkeypatch.setattr(
        events_service, "active_event", lambda: next(iter(events.values()), None)
    )
    return events


def test_no_plan_is_null():
    r = api.get("/season-plan")
    assert r.status_code == 200
    assert r.json() is None


def test_preview_has_no_side_effects():
    r = api.post("/season-plan/preview", json=BODY)
    assert r.status_code == 200
    assert r.json()["mode"] == "event"
    assert not store.SEASON_PLAN_FILE.exists()
    assert api.get("/season-plan").json() is None


def test_create_then_get_matches_the_preview():
    preview = api.post("/season-plan/preview", json=BODY).json()
    r = api.post("/season-plan", json=BODY)
    assert r.status_code == 201
    saved = api.get("/season-plan").json()
    for key in ("weeks", "targets", "summary", "event", "baseline", "settings"):
        assert saved[key] == preview[key], key


def test_create_without_event_id_uses_the_primary_event():
    r = api.post("/season-plan", json={"mode": "event"})
    assert r.status_code == 201
    assert r.json()["event"]["event_id"] == "ev1"


def test_create_replaces_the_previous_plan():
    first = api.post("/season-plan", json=BODY).json()
    second = api.post("/season-plan", json={**BODY, "ramp": "gentle"}).json()
    assert second["plan_id"] != first["plan_id"]
    assert api.get("/season-plan").json()["plan_id"] == second["plan_id"]


def test_replacing_a_plan_notes_the_old_one():
    first = api.post("/season-plan", json=BODY).json()
    assert not any(r["field"] == "plan" for r in first["revisions"])
    second = api.post("/season-plan", json={**BODY, "ramp": "gentle"}).json()
    note = second["revisions"][0]
    assert note["field"] == "plan"
    assert note["reason_text"].startswith("Replaced the plan for Spring Olympic (made ")


def test_gone_event_is_409(fakes):
    fakes.clear()
    assert api.post("/season-plan", json=BODY).status_code == 409


def test_no_event_at_all_is_422(fakes):
    fakes.clear()
    assert api.post("/season-plan", json={"mode": "event"}).status_code == 422


def test_calendar_only_event_is_422(fakes):
    fakes["ev1"]["participating"] = False
    assert api.post("/season-plan", json=BODY).status_code == 422


def test_past_race_is_422(fakes):
    fakes["ev1"]["race_date"] = (TODAY - timedelta(days=3)).isoformat()
    assert api.post("/season-plan", json=BODY).status_code == 422


def test_a_fitness_plan_needs_no_event(fakes):
    fakes.clear()
    r = api.post("/season-plan", json={"mode": "fitness", "focus": "run"})
    assert r.status_code == 201
    assert r.json()["mode"] == "fitness" and r.json()["event"] is None


@pytest.mark.parametrize(
    "patch",
    [{"mode": "race"}, {"ramp": "fast"}, {"week_start_day": "friday"}, {"weekly_hours_cap": 0}, {"peak_ctl": -5}],
)
def test_bad_body_is_422(patch):
    assert api.post("/season-plan", json={**BODY, **patch}).status_code == 422


def test_delete_cancels_and_a_second_delete_is_404():
    api.post("/season-plan", json=BODY)
    assert api.delete("/season-plan").status_code == 204
    assert api.get("/season-plan").json() is None
    assert api.delete("/season-plan").status_code == 404


def test_revisions_newest_first_with_the_from_key():
    api.post("/season-plan", json={**BODY, "weekly_hours_cap": 3.0})
    revs = api.get("/season-plan/revisions").json()
    assert revs and revs[0]["reason_code"] == "PEAK_LOWERED"
    assert "from" in revs[0]


def test_a_finished_race_reads_as_completed():
    api.post("/season-plan", json=BODY)
    plan = store.load_plan()
    plan["event"]["race_date"] = (TODAY - timedelta(days=1)).isoformat()
    store.save_plan(plan)
    assert api.get("/season-plan").json()["status"] == "completed"
    # Read-only: the stored plan is not rewritten by a GET.
    assert store.load_plan()["status"] == "active"


def test_baseline_from_completed_calendar_weeks():
    r = api.post("/season-plan/preview", json=BODY).json()
    b = r["baseline"]
    assert b["avg_weekly_tss_4w"] == 420  # 7 days x 60 TSS
    assert b["tss_per_hour"] == 60
    assert b["limited_history"] is False
    assert b["ctl"] == 40.0


def test_a_started_week_carries_its_actuals():
    plan = api.post("/season-plan/preview", json={**BODY, "start": "this"}).json()
    first = plan["weeks"][0]
    assert first["status"] == "current"
    # Every day of this week before today has a 60 TSS workout.
    days_in = (TODAY - date.fromisoformat(first["week_start"])).days
    assert first["actual_tss"] == 60 * days_in
    assert all(w["actual_tss"] is None for w in plan["weeks"][1:])


def test_limited_history_floors_the_start(monkeypatch):
    monkeypatch.setattr(service, "_read_history", lambda: ([], {}))
    r = api.post("/season-plan/preview", json=BODY).json()
    assert r["baseline"]["limited_history"] is True
    assert r["baseline"]["tss_per_hour"] == 55
    assert r["weeks"][0]["target_tss"] == 150


def test_weekly_totals_use_the_local_calendar_day_and_week_start():
    # Sunday 8 Nov 2026, 23:30 local, stored at 21:30 UTC: the local date wins.
    w = _workout(date(2026, 11, 8), 50, 1)
    w["time"] = datetime(2026, 11, 8, 21, 30, tzinfo=timezone.utc)
    run = {"run": {"tss": 50, "hours": 1.0}}
    assert service.weekly_totals([w], "monday") == {"2026-11-02": {"tss": 50, "hours": 1.0, "by_discipline": run}}
    assert service.weekly_totals([w], "sunday") == {"2026-11-08": {"tss": 50, "hours": 1.0, "by_discipline": run}}


def test_data_reset_clears_the_season_plan(monkeypatch):
    api.post("/season-plan", json=BODY)

    class FakeRepo:
        def delete_all_data(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(data_router, "InfluxRepo", FakeRepo)
    # Keep the reset away from the real data/ folder.
    monkeypatch.setattr(data_router, "clear_analyses", lambda: 0)
    monkeypatch.setattr(data_router, "LATEST_FILE", store.SEASON_PLAN_FILE.with_name("latest.json"))
    r = api.delete("/data")
    assert r.status_code == 200
    assert r.json()["season_plan"] == "deleted"
    assert not store.SEASON_PLAN_FILE.exists()
