"""
Plan changes the athlete asked the coach for survive a plan rebuild, until
their day has passed. Fakes only: no Claude, no InfluxDB, no Garmin.

conftest.py points plan_requests.REQUESTS_FILE at a temp file for every test.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.recommend_service as reco
import app.schedule_store as store
from app import plan_editor, plan_requests
from app.ai import claude
from app.ai import prompt_builder as pb
from app.routers import recommend as recommend_router
from app.routers import sync as sync_router
from main import app

api = TestClient(app, base_url="http://testserver/api")

TODAY = date.today()


def _iso(days_from_today: int) -> str:
    return (TODAY + timedelta(days=days_from_today)).isoformat()


def _run(minutes=45, tss=40, title="Easy run"):
    return {"discipline": "run", "title": title, "duration_min": minutes, "estimated_tss": tss}


def _plan() -> dict:
    return {
        "days": [
            {"date": _iso(0), "day_name": "A", "is_rest_day": False, "sessions": [_run()]},
            {"date": _iso(1), "day_name": "B", "is_rest_day": False, "sessions": [_run(60, 50)]},
            {"date": _iso(2), "day_name": "C", "is_rest_day": True, "sessions": []},
        ],
        "_meta": {"plan_id": "abc"},
    }


@pytest.fixture()
def plan_file(tmp_path, monkeypatch):
    f = tmp_path / "latest_recommendation.json"
    f.write_text(json.dumps(_plan()), encoding="utf-8")
    monkeypatch.setattr(plan_editor, "LATEST_FILE", f)
    return f


def _stored() -> dict:
    return json.loads(plan_requests.REQUESTS_FILE.read_text(encoding="utf-8"))


def _write_store(data: dict) -> None:
    plan_requests.REQUESTS_FILE.write_text(json.dumps(data), encoding="utf-8")


def _now_iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# --- every coach edit is saved ----------------------------------------------
def test_update_saves_the_day_as_the_edit_left_it(plan_file):
    plan_editor.update_session(_iso(0), 0, _run(30, 25, "Short run"), note="Tired legs")

    saved = _stored()[_iso(0)]
    assert saved["sessions"][0]["duration_min"] == 30
    assert saved["notes"] == ["Tired legs"]
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    assert plan["days"][0]["athlete_requested"] is True
    assert "athlete_requested" not in plan["days"][1]


def test_add_and_remove_are_saved(plan_file):
    plan_editor.add_session(_iso(1), _run(20, 10, "Strides"), note="Add strides")
    assert len(_stored()[_iso(1)]["sessions"]) == 2

    plan_editor.remove_session(_iso(0), 0, note="Day off, I have a wedding")
    # An empty list is a day off the athlete asked for, and it is kept as such.
    assert _stored()[_iso(0)]["sessions"] == []


def test_a_move_saves_both_days(plan_file):
    plan_editor.move_session(_iso(1), 0, _iso(2), note="Long run on the free day")

    stored = _stored()
    assert stored[_iso(1)]["sessions"] == []
    assert stored[_iso(2)]["sessions"][0]["duration_min"] == 60
    assert stored[_iso(1)]["notes"] == stored[_iso(2)]["notes"] == ["Long run on the free day"]


def test_re_editing_a_day_replaces_its_sessions_and_keeps_the_notes(plan_file):
    plan_editor.update_session(_iso(0), 0, _run(30, 25), note="Shorter")
    plan_editor.update_session(_iso(0), 0, _run(35, 28), note="A bit longer")
    plan_editor.update_session(_iso(0), 0, _run(35, 28), note="A bit longer")

    saved = _stored()[_iso(0)]
    assert saved["sessions"][0]["duration_min"] == 35
    # De-duplicated, oldest first.
    assert saved["notes"] == ["Shorter", "A bit longer"]


def test_notes_are_capped(plan_file):
    for i in range(plan_requests.MAX_NOTES + 2):
        plan_editor.update_session(_iso(0), 0, _run(30 + i), note=f"note {i}" + "x" * 400)
    notes = _stored()[_iso(0)]["notes"]
    assert len(notes) == plan_requests.MAX_NOTES
    assert all(len(n) == plan_requests.MAX_NOTE_CHARS for n in notes)
    assert notes[-1].startswith(f"note {plan_requests.MAX_NOTES + 1}")


def test_a_failed_edit_saves_nothing(plan_file):
    with pytest.raises(plan_editor.PlanEditError):
        plan_editor.remove_session(_iso(0), 5, note="bad index")
    assert not plan_requests.REQUESTS_FILE.exists()


# --- expiry -----------------------------------------------------------------
def test_a_request_for_a_past_day_is_dropped_and_deleted():
    _write_store(
        {
            _iso(-1): {"sessions": [_run()], "notes": [], "requested_at": _now_iso(1)},
            _iso(0): {"sessions": [_run()], "notes": [], "requested_at": _now_iso()},
        }
    )
    active = plan_requests.active_requests()
    assert [r["date"] for r in active] == [_iso(0)]
    assert list(_stored()) == [_iso(0)]


def test_a_request_older_than_seven_days_is_dropped():
    _write_store(
        {_iso(1): {"sessions": [_run()], "notes": [], "requested_at": _now_iso(8)}}
    )
    assert plan_requests.active_requests() == []
    # Nothing left: the file itself goes.
    assert not plan_requests.REQUESTS_FILE.exists()


def test_active_requests_are_sorted_and_carry_the_weekday():
    _write_store(
        {
            _iso(3): {"sessions": [], "notes": ["b"], "requested_at": _now_iso()},
            _iso(1): {"sessions": [_run()], "notes": ["a"], "requested_at": _now_iso()},
        }
    )
    active = plan_requests.active_requests()
    assert [r["date"] for r in active] == [_iso(1), _iso(3)]
    assert active[0]["day_name"] == (TODAY + timedelta(days=1)).strftime("%A")


def test_malformed_entries_and_files_count_as_no_requests():
    _write_store({"not-a-date": {"sessions": [], "requested_at": _now_iso()}, _iso(1): "junk"})
    assert plan_requests.active_requests() == []

    plan_requests.REQUESTS_FILE.write_text("{not json", encoding="utf-8")
    assert plan_requests.active_requests() == []


def test_clear_requests():
    assert plan_requests.clear_requests() is False
    _write_store({_iso(1): {"sessions": [], "notes": [], "requested_at": _now_iso()}})
    assert plan_requests.clear_requests() is True
    assert plan_requests.active_requests() == []


# --- enforcement after generation -------------------------------------------
def test_requested_days_are_restored_over_claudes_version():
    plan = {
        "days": [
            # Claude drifted: shortened the requested session.
            {"date": _iso(0), "is_rest_day": False, "sessions": [_run(20, 10)]},
            # Claude dropped the requested session entirely.
            {"date": _iso(1), "is_rest_day": True, "sessions": []},
            # Claude filled the day the athlete asked to keep free.
            {"date": _iso(2), "is_rest_day": False, "sessions": [_run()]},
            {"date": _iso(3), "is_rest_day": False, "sessions": [_run()]},
        ]
    }
    requested = [
        {"date": _iso(0), "sessions": [_run(50, 45)]},
        {"date": _iso(1), "sessions": [_run(90, 80, "Long run")]},
        {"date": _iso(2), "sessions": []},
        {"date": "1999-01-01", "sessions": [_run()]},  # not in the plan: skipped
    ]
    drifted = reco._apply_athlete_requests(plan, requested)

    assert drifted == [_iso(0), _iso(1), _iso(2)]
    days = plan["days"]
    assert days[0]["sessions"] == [_run(50, 45)]
    assert days[1]["sessions"][0]["title"] == "Long run"
    assert days[1]["is_rest_day"] is False
    assert days[2]["sessions"] == [] and days[2]["is_rest_day"] is True
    assert all(d["athlete_requested"] for d in days[:3])
    assert "athlete_requested" not in days[3]
    # A deep copy: editing the plan later must not reach into the request.
    days[0]["sessions"][0]["duration_min"] = 1
    assert requested[0]["sessions"][0]["duration_min"] == 50


def test_rest_day_enforcement_leaves_a_requested_day_alone():
    payload = {"plan_days": [{"date": _iso(2), "is_rest_day": True}]}
    plan = {"days": [{"date": _iso(2), "is_rest_day": False, "sessions": [_run()]}]}

    assert reco._enforce_rest_days(plan, payload, keep={_iso(2)}) == []
    assert plan["days"][0]["sessions"] == [_run()]
    # Without the request it is still cleared, exactly as before.
    assert reco._enforce_rest_days(plan, payload) == [_iso(2)]


# --- run_recommendation -----------------------------------------------------
@pytest.fixture()
def fake_generation(tmp_path, monkeypatch):
    """run_recommendation with the payload, Claude and Influx faked."""
    monkeypatch.setattr(reco, "LATEST_FILE", tmp_path / "latest_recommendation.json")
    seen: dict = {}

    def fake_payload(include_athlete_requests=True):
        seen["include"] = include_athlete_requests
        payload = {"plan_days": [{"date": _iso(i), "is_rest_day": i == 2} for i in range(7)]}
        requested = pb._athlete_requested_block(
            plan_requests.active_requests() if include_athlete_requests else [],
            {d["date"] for d in payload["plan_days"]},
        )
        if requested:
            payload["athlete_requested_days"] = requested
        return payload

    def fake_generate(payload):
        return {"days": [{"date": _iso(i), "is_rest_day": False, "sessions": [_run(30, 20)]} for i in range(7)]}

    monkeypatch.setattr(reco, "build_plan_payload", fake_payload)
    monkeypatch.setattr(reco, "generate_plan", fake_generate)
    return seen


def _seed_request():
    _write_store(
        {_iso(2): {"sessions": [_run(90, 80, "Long run")], "notes": ["Long run on my rest day"], "requested_at": _now_iso()}}
    )


def test_keep_builds_the_week_around_the_request(fake_generation):
    _seed_request()
    plan = reco.run_recommendation(keep_requests=True)

    assert fake_generation["include"] is True
    day = plan["days"][2]
    assert day["sessions"][0]["title"] == "Long run"
    assert day["athlete_requested"] is True
    # A requested rest-day session is the athlete's own: no rest-day warning.
    assert not plan.get("warning_flags")
    assert plan["_meta"]["athlete_requests"] == "kept"
    # The request stays in force for the next rebuild.
    assert [r["date"] for r in plan_requests.active_requests()] == [_iso(2)]


def test_overwrite_builds_a_fresh_plan_and_deletes_the_requests(fake_generation):
    _seed_request()
    plan = reco.run_recommendation(keep_requests=False)

    assert fake_generation["include"] is False
    # The rest day is enforced again, since nothing requested it.
    assert plan["days"][2]["sessions"] == []
    assert plan["warning_flags"]
    assert plan["_meta"]["athlete_requests"] == "overwritten"
    assert plan_requests.active_requests() == []


def test_a_failed_generation_keeps_the_requests(fake_generation, monkeypatch):
    _seed_request()

    def boom(payload):
        raise claude.ClaudeError("no key")

    monkeypatch.setattr(reco, "generate_plan", boom)
    with pytest.raises(claude.ClaudeError):
        reco.run_recommendation(keep_requests=False)
    assert [r["date"] for r in plan_requests.active_requests()] == [_iso(2)]


def test_no_requests_leaves_meta_unmarked(fake_generation):
    plan = reco.run_recommendation()
    assert "athlete_requests" not in plan["_meta"]


# --- the payload ------------------------------------------------------------
def _payload(monkeypatch, **kwargs):
    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_plan_payload(**kwargs)


def test_payload_omits_the_block_without_requests(monkeypatch):
    assert "athlete_requested_days" not in _payload(monkeypatch)


def test_payload_carries_the_requests_inside_the_window(monkeypatch):
    _write_store(
        {
            _iso(1): {"sessions": [_run(90, 80), _run(20, 10)], "notes": ["Group run", "Add strides"], "requested_at": _now_iso()},
            _iso(4): {"sessions": [], "notes": ["Travelling"], "requested_at": _now_iso()},
            # Past the 7-day window: cannot shape this plan.
            _iso(9): {"sessions": [_run()], "notes": [], "requested_at": _now_iso()},
        }
    )
    block = _payload(monkeypatch)["athlete_requested_days"]

    assert [d["date"] for d in block["days"]] == [_iso(1), _iso(4)]
    assert block["days"][0]["athlete_request"] == "Group run Add strides"
    assert block["days"][1]["sessions"] == []
    assert block["totals"] == {
        "training_days": 1,
        "days_off": 1,
        "sessions": 2,
        "duration_min": 110,
        "estimated_tss": 90,
    }


def test_payload_leaves_the_requests_out_when_overwriting(monkeypatch):
    _write_store({_iso(1): {"sessions": [_run()], "notes": [], "requested_at": _now_iso()}})
    payload = _payload(monkeypatch, include_athlete_requests=False)
    assert "athlete_requested_days" not in payload


# --- the coach tools --------------------------------------------------------
def test_every_coach_tool_asks_for_the_athletes_request():
    for tool in claude.COACH_TOOLS:
        schema = tool["input_schema"]
        assert "athlete_request" in schema["properties"], tool["name"]
        assert "athlete_request" in schema["required"], tool["name"]


def test_coach_tool_saves_the_models_account_of_the_request(plan_file):
    text, is_error = claude._run_coach_tool(
        "remove_plan_session",
        {"date": _iso(0), "session_index": 0, "athlete_request": "Wedding on Friday"},
        fallback_note="yes",
    )
    assert is_error is False
    assert _stored()[_iso(0)]["notes"] == ["Wedding on Friday"]


def test_coach_tool_falls_back_to_the_athletes_message(plan_file):
    claude._run_coach_tool(
        "remove_plan_session",
        {"date": _iso(0), "session_index": 0},
        fallback_note="drop today's run please",
    )
    assert _stored()[_iso(0)]["notes"] == ["drop today's run please"]


# --- routes -----------------------------------------------------------------
def test_requests_route_lists_the_active_requests():
    assert api.get("/recommend/requests").json() == {"requests": []}
    _write_store({_iso(1): {"sessions": [_run()], "notes": ["x"], "requested_at": _now_iso()}})
    body = api.get("/recommend/requests").json()
    assert [r["date"] for r in body["requests"]] == [_iso(1)]


def test_trigger_passes_the_athletes_answer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        recommend_router, "run_recommendation", lambda **kw: calls.append(kw) or {}
    )
    api.post("/recommend/trigger")
    api.post("/recommend/trigger?keep_requests=false")
    assert [c["keep_requests"] for c in calls] == [True, False]


def test_sync_passes_the_athletes_answer(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_router, "run_sync", lambda **kw: calls.append(kw) or {})
    api.post("/sync")
    api.post("/sync?keep_requests=false")
    assert [c["keep_requests"] for c in calls] == [True, False]


# --- the prompt -------------------------------------------------------------
def test_rule_24_sits_after_rule_23_and_before_the_json_instruction():
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    heading = text.index("Athlete-requested days (athlete_requested_days):")
    assert text.index("\n23. ") < heading < text.index("\n24. ") < text.index("Respond ONLY in the JSON")
    assert len(re.findall(r"^24\. ", text, re.MULTILINE)) == 1


def test_rule_24_reaches_the_built_prompt():
    system = claude._build_plan_system({"primary_goal": None})
    assert "athlete_requested_days.totals" in system
