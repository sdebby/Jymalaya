"""
The athlete's weekly training schedule: defaults, storage roundtrips and
normalization, the GET/PUT endpoints, what reaches the Claude payload, and the
plan-staleness check that forces a regeneration after an edit. Fakes only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.instructions as instructions
import app.schedule_store as store
from app import sync_service
from main import app

# base_url carries the /api prefix the routers are mounted under, so the paths
# below stay the bare route paths.
client = TestClient(app, base_url="http://testserver/api")


def _isolate(monkeypatch, tmp_path):
    """Point the store at a temp file, away from the repo's data/ dir."""
    monkeypatch.setattr(store, "SCHEDULE_FILE", tmp_path / "schedule.json")
    monkeypatch.setattr(store, "ROOT", tmp_path)


def _training_day(**over):
    return {**store.DEFAULT_TRAINING_DAY, **over}


# --- defaults ---------------------------------------------------------------
def test_defaults_when_nothing_stored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cfg = store.get_schedule()
    assert cfg == store._normalize(store.DEFAULT_SCHEDULE)
    # The default week (rest wed + sun, long bike/run saturday) derives the
    # PRD's original limits; no day is typed "brick" so none allow bricks.
    assert cfg["constraints"] == {
        "max_training_days_per_week": 5,
        "max_consecutive_training_days": 3,
        "long_ride_day": "saturday",
        "long_run_day": "saturday",
        "brick_allowed_days": [],
    }
    # First read persists them, so the file is the source of truth from then on.
    assert (tmp_path / "schedule.json").exists()


# --- storage and normalization ----------------------------------------------
def test_roundtrip_and_weekday_order(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cfg = store.get_schedule()
    cfg["schedule"]["tuesday"] = _training_day(
        preferred_disciplines=["run", "strength"], max_sessions=3, session_type="quality"
    )
    store.save_schedule(cfg)

    stored = store.get_schedule()
    assert list(stored["schedule"]) == list(store.WEEKDAY_NAMES)
    assert stored["schedule"]["tuesday"]["preferred_disciplines"] == ["run", "strength"]
    assert stored["schedule"]["tuesday"]["max_sessions"] == 3
    assert stored["schedule"]["tuesday"]["session_type"] == "quality"


def test_rest_day_is_stripped_of_training_detail(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    stored = store.save_schedule(
        {
            **store.DEFAULT_SCHEDULE,
            "schedule": {
                **store.DEFAULT_SCHEDULE["schedule"],
                # Rest is the hard constraint, so stale detail must not survive
                # and reach Claude.
                "friday": {
                    "available": False,
                    "rest": True,
                    "preferred_disciplines": ["swim"],
                    "max_sessions": 2,
                },
            },
        }
    )
    assert stored["schedule"]["friday"] == store.REST_DAY


def test_missing_days_fall_back_and_constraints_derive(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    out = store._normalize({"schedule": {"monday": _training_day()}})
    assert list(out["schedule"]) == list(store.WEEKDAY_NAMES)
    # Missing days come from the default week (rest wed + sun), so the derived
    # limits match the default week's.
    assert out["constraints"]["max_training_days_per_week"] == 5
    assert out["constraints"]["max_consecutive_training_days"] == 3


def test_constraint_derivation():
    def week(rest_days, **special):
        return {
            d: dict(store.REST_DAY) if d in rest_days else special.get(d, _training_day())
            for d in store.WEEKDAY_NAMES
        }

    # A training run that wraps the weekend: only wednesday breaks it.
    c = store._derive_constraints(week({"wednesday"}))
    assert c["max_training_days_per_week"] == 6
    assert c["max_consecutive_training_days"] == 6

    # Every day open / every day rest.
    assert store._derive_constraints(week(set()))["max_consecutive_training_days"] == 7
    all_rest = store._derive_constraints(week(set(store.WEEKDAY_NAMES)))
    assert all_rest["max_training_days_per_week"] == 0
    assert all_rest["max_consecutive_training_days"] == 0

    # Long ride/run land on a "long" day preferring that discipline; bricks
    # exactly on the days typed "brick".
    c = store._derive_constraints(
        week(
            {"sunday"},
            friday=_training_day(
                preferred_disciplines=["swim", "bike"], max_sessions=2, session_type="brick"
            ),
            saturday=_training_day(preferred_disciplines=["bike", "run"], session_type="long"),
        )
    )
    assert c["long_ride_day"] == "saturday"
    assert c["long_run_day"] == "saturday"
    assert c["brick_allowed_days"] == ["friday"]


def test_brick_day_normalization(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    stored = store.save_schedule(
        {
            "schedule": {
                **store.DEFAULT_SCHEDULE["schedule"],
                # A brick that could not fit its two sessions gets room for them.
                "friday": _training_day(
                    preferred_disciplines=["swim", "bike"], max_sessions=1, session_type="brick"
                ),
                # A "brick" with one discipline is meaningless -> plain day.
                "monday": _training_day(preferred_disciplines=["run"], session_type="brick"),
            }
        }
    )
    assert stored["schedule"]["friday"]["max_sessions"] == 2
    assert stored["schedule"]["monday"]["session_type"] == "any"
    assert stored["constraints"]["brick_allowed_days"] == ["friday"]


# --- API --------------------------------------------------------------------
def test_schedule_endpoints_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cfg = client.get("/schedule").json()
    assert set(cfg["schedule"]) == set(store.WEEKDAY_NAMES)

    cfg["schedule"]["monday"] = _training_day(preferred_disciplines=["bike"], max_sessions=2)
    r = client.put("/schedule", json=cfg)
    assert r.status_code == 200
    assert r.json()["schedule"]["monday"]["preferred_disciplines"] == ["bike"]
    assert client.get("/schedule").json()["schedule"]["monday"]["max_sessions"] == 2


def test_schedule_endpoint_computes_constraints_and_ignores_sent_ones(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cfg = client.get("/schedule").json()
    cfg["schedule"]["monday"] = dict(store.REST_DAY)
    # A client cannot set the limits directly — they derive from the days.
    cfg["constraints"] = {"max_training_days_per_week": 1, "max_consecutive_training_days": 1}
    r = client.put("/schedule", json=cfg)
    assert r.status_code == 200
    # Default rest wed + sun, plus monday: training tue, thu, fri, sat.
    assert r.json()["constraints"]["max_training_days_per_week"] == 4
    assert r.json()["constraints"]["max_consecutive_training_days"] == 3


def test_schedule_endpoint_rejects_bad_input(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    good = client.get("/schedule").json()

    bad_day = {**good, "schedule": {**good["schedule"], "caturday": _training_day()}}
    assert client.put("/schedule", json=bad_day).status_code == 422

    missing = {**good, "schedule": {k: v for k, v in good["schedule"].items() if k != "friday"}}
    assert client.put("/schedule", json=missing).status_code == 422

    zero_sessions = {
        **good,
        "schedule": {**good["schedule"], "monday": _training_day(max_sessions=0)},
    }
    assert client.put("/schedule", json=zero_sessions).status_code == 422

    bad_discipline = {
        **good,
        "schedule": {**good["schedule"], "monday": _training_day(preferred_disciplines=["yoga"])},
    }
    assert client.put("/schedule", json=bad_discipline).status_code == 422

    brick_one_discipline = {
        **good,
        "schedule": {
            **good["schedule"],
            "monday": _training_day(preferred_disciplines=["run"], session_type="brick"),
        },
    }
    assert client.put("/schedule", json=brick_one_discipline).status_code == 422


# --- plan payload -----------------------------------------------------------
def test_payload_reflects_the_saved_schedule(monkeypatch, tmp_path):
    from app.ai import prompt_builder as pb

    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    # last_race reaches events_service.InfluxRepo directly, which patching
    # pb.InfluxRepo does not cover -- without this the test needs a live DB.
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    _isolate(monkeypatch, tmp_path)
    cfg = store.get_schedule()
    cfg["schedule"]["monday"] = dict(store.REST_DAY)
    cfg["schedule"]["wednesday"] = _training_day(preferred_disciplines=["bike"])
    store.save_schedule(cfg)

    payload = pb.build_plan_payload()
    # `rest` is the only availability signal Claude gets — no `available`.
    assert payload["training_schedule"]["monday"] == {"rest": True}
    wednesday = payload["training_schedule"]["wednesday"]
    assert "available" not in wednesday
    assert wednesday["preferred_disciplines"] == ["bike"]
    # Rest mon + sun leaves tue..sat open: the derived limits reach Claude.
    assert payload["schedule_constraints"]["max_training_days_per_week"] == 5
    assert payload["schedule_constraints"]["max_consecutive_training_days"] == 5
    # Every plan day carries its own rest flag, so the model never has to map
    # a date back to a weekday.
    assert len(payload["plan_days"]) == 7
    for day in payload["plan_days"]:
        weekday = datetime.strptime(day["date"], "%Y-%m-%d").strftime("%A").lower()
        assert day["is_rest_day"] is (weekday in ("monday", "sunday"))


# --- plan staleness after a schedule edit -----------------------------------
def _fake_plan(monkeypatch, generated_at: datetime):
    import app.recommend_service as reco

    plan = {"_meta": {"generated_at": generated_at.isoformat()}}
    monkeypatch.setattr(reco, "latest_recommendation", lambda: plan)


def test_plan_stale_after_schedule_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    _isolate(monkeypatch, tmp_path)
    _fake_plan(monkeypatch, datetime.now(timezone.utc) - timedelta(hours=1))
    store.save_schedule(store.DEFAULT_SCHEDULE)
    assert sync_service._plan_is_current() is False


def test_plan_current_when_generated_after_schedule_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    _isolate(monkeypatch, tmp_path)
    store.save_schedule(store.DEFAULT_SCHEDULE)
    _fake_plan(monkeypatch, datetime.now(timezone.utc) + timedelta(minutes=1))
    assert sync_service._plan_is_current() is True
