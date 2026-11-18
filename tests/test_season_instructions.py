"""
The season plan reading the athlete's coach instructions
(app/season/instructions.py): the cleaned read, the cache, a failed call, the
sports and load a read changes at creation, and a later save of the
instructions changing the plan from next week on.

No network: the Claude call is replaced per test (tests/conftest.py stubs it
with an empty read for every other test), and the instructions file, the
season plan and the history are temp files and fakes.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import instructions as app_instructions
from app.season import config as cfg
from app.season import instructions as instr
from app.season import service, split, store
from app.season.builder import build_event_plan, start_week
from main import app

api = TestClient(app, base_url="http://testserver/api")

TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())
# Where a plan made today starts: next week when today is a Sunday. MONDAY stays
# the current week, which the tests of a plan made earlier count from.
FIRST_WEEK = start_week(TODAY, "monday")
ROOT = Path(__file__).resolve().parents[1]

BIKE_READ = {
    "summary": "Adds one aerobic ride a week to the run plan.",
    "sports": [{"discipline": "bike", "change": "add", "share_pct": 12, "sessions_per_week": 1, "note": "aerobic"}],
    "load": {"weekly_hours_cap": None, "ramp": None, "recovery_cadence_weeks": None, "peak_ctl": None},
    "left_to_weekly_plan": ["Long run on Friday", "The ride on Saturday, instead of a run"],
}


def _race(runway: int) -> str:
    return (FIRST_WEEK + timedelta(days=7 * (runway - 1) + 5)).isoformat()


@pytest.fixture()
def claude(monkeypatch):
    """The read Claude returns, and every call made: (text, context)."""
    box = {"read": BIKE_READ, "calls": [], "error": None}

    def ask(text, context):
        box["calls"].append((text, context))
        if box["error"]:
            raise box["error"]
        return box["read"]

    monkeypatch.setattr(instr, "_ask_claude", ask)
    return box


@pytest.fixture()
def world(monkeypatch):
    """No history; the event the form names is a 15K (or what the test sets)."""
    event = {"event_id": "e1", "name": "City 15K", "type": "15K", "race_date": _race(16)}
    monkeypatch.setattr(service, "_read_history", lambda: ([], {}))
    monkeypatch.setattr(service, "_target_event", lambda eid: dict(event))
    return event


def _say(text: str) -> None:
    app_instructions.save_instructions(text)


def _create(**body) -> dict:
    r = api.post("/season-plan", json={"mode": "event", "event_id": "e1", **body})
    assert r.status_code == 201, r.text
    return r.json()


# --- the read ------------------------------------------------------------------------
def test_clean_read_keeps_known_keys_and_clamps():
    read = instr.clean_read({
        "summary": "x" * 500,
        "sports": [
            {"discipline": "bike", "change": "add", "share_pct": 90},
            {"discipline": "swim", "change": "add", "share_pct": 25},
            {"discipline": "strength", "change": "add", "share_pct": 10},
            {"discipline": "run", "change": "explode", "share_pct": 10},
            {"discipline": "bike", "change": "more", "share_pct": 5},
        ],
        "load": {"weekly_hours_cap": 99, "ramp": "reckless", "recovery_cadence_weeks": 1, "peak_ctl": "high"},
        "left_to_weekly_plan": ["Long run on Friday", ""],
        "extra": "ignored",
    })
    assert len(read["summary"]) == 200
    # Bike clamped to 40; swim gets what is left of the 40 the extras may take.
    assert [(s["discipline"], s["share_pct"]) for s in read["sports"]] == [("bike", 40), ("swim", 0)]
    assert read["load"] == {"weekly_hours_cap": 40.0, "ramp": None, "recovery_cadence_weeks": 2, "peak_ctl": None}
    assert read["left_to_weekly_plan"] == ["Long run on Friday"]
    assert "extra" not in read
    assert instr.clean_read("not a dict") == instr.empty_read()


def test_one_call_per_text_and_context_and_none_for_empty_text(claude):
    ctx = instr.context_for("event", "15K", None)
    assert instr.read_instructions("", ctx) == instr.empty_read()
    a = instr.read_instructions("one ride a week", ctx)
    b = instr.read_instructions("one ride a week", ctx)
    assert a == b and len(claude["calls"]) == 1
    instr.read_instructions("one ride a week", instr.context_for("event", "70.3", None))
    assert len(claude["calls"]) == 2
    assert claude["calls"][0][1] == {"mode": "event", "event_type": "15K", "race_sports": ["run"]}


def test_a_failed_read_still_builds_the_plan(claude, world):
    _say("one ride a week")
    claude["error"] = RuntimeError("API down")
    plan = _create()
    assert plan["instructions_view"]["error"] == "API down"
    assert plan["instructions_view"]["applied"] == []
    assert all(w.get("split") is None for w in plan["weeks"])
    # A failed read is not remembered: the next one tries again.
    claude["error"] = None
    assert instr.read_instructions("one ride a week", instr.context_for("event", "15K", None))["sports"]


# --- at creation --------------------------------------------------------------------------
def test_a_run_plan_gets_the_ride_the_instructions_ask_for(claude, world):
    _say("Even if I am training for a run event, add one bike workout on Saturday.")
    plan = _create()
    view = plan["instructions_view"]
    assert view["summary"] == BIKE_READ["summary"]
    assert view["applied"] == ["Bike 12% of each week's TSS (1 session a week, aerobic)"]
    assert view["left_to_weekly_plan"] == BIKE_READ["left_to_weekly_plan"]
    for w in plan["weeks"]:
        if w["phase"] == "race":
            assert w["split"] == {"swim": 0, "bike": 0, "run": 100}  # never on race week
        else:
            assert w["split"] == {"swim": 0, "bike": 12, "run": 88}
            assert sum(w["discipline_tss"].values()) == round(w["target_tss"])
    stored = store.load_plan()
    assert stored["instructions"]["applied"] is True
    assert stored["instructions"]["text_sha"] == instr.text_sha(app_instructions.get_instructions())
    # The 7-day plan's budget follows: a bike share in the run week.
    block = service.payload_block(FIRST_WEEK, [(FIRST_WEEK + timedelta(days=d)).isoformat() for d in range(7)])
    assert block["weeks"][0]["split_pct"]["bike"] == 12


def test_use_instructions_off_ignores_them(claude, world):
    _say("one ride a week")
    plan = _create(use_instructions=False)
    assert all(w.get("split") is None for w in plan["weeks"])
    assert plan["instructions_view"]["used"] is False and plan["instructions_view"]["opted_out"] is True
    assert claude["calls"] == []


def test_without_instructions_nothing_is_read(claude, world):
    plan = _create()
    assert plan["instructions_view"] is None and claude["calls"] == []


def test_a_triathlon_can_drop_a_sport(claude, world):
    world.update(type="olympic")
    claude["read"] = {"summary": "No swimming for now.", "sports": [{"discipline": "swim", "change": "drop"}], "load": {}}
    _say("no swimming for now, shoulder injury")
    plan = _create()
    base = next(w for w in plan["weeks"] if w["phase"] == "base")
    assert base["split"]["swim"] == 0 and sum(base["split"].values()) == 100
    row = cfg.DISCIPLINE_SPLIT["olympic"]["base"]
    assert base["split"]["bike"] / base["split"]["run"] == pytest.approx(row["bike"] / row["run"], rel=0.05)
    race = next(w for w in plan["weeks"] if w["phase"] == "race")
    assert race["split"] == cfg.DISCIPLINE_SPLIT["olympic"]["taper"]  # the race itself has a swim


def test_load_fills_what_the_form_left_blank_and_the_form_wins(claude, world):
    claude["read"] = {**BIKE_READ, "load": {"weekly_hours_cap": 6, "ramp": "gentle", "recovery_cadence_weeks": 2, "peak_ctl": 50}}
    _say("max 6 hours a week, build gently, easy week every third week")
    plan = _create()
    assert plan["settings"]["weekly_hours_cap"] == 6
    assert plan["settings"]["ramp"] == "gentle"
    assert plan["settings"]["recovery_cadence_weeks"] == 2
    assert plan["targets"]["peak_ctl"] == 50
    assert plan["settings_source"] == {
        "weekly_hours_cap": "instructions", "ramp": "instructions",
        "recovery_cadence_weeks": "instructions", "peak_ctl": "instructions",
    }
    # Two loading weeks, then a recovery week.
    kinds = [w["kind"] for w in plan["weeks"][:6]]
    assert kinds[:3] == ["loading", "loading", "recovery"]
    assert "Weekly hours available: 6" in plan["instructions_view"]["applied"]

    plan = _create(weekly_hours_cap=9, ramp="ambitious")
    assert plan["settings"]["weekly_hours_cap"] == 9 and plan["settings"]["ramp"] == "ambitious"
    assert plan["settings_source"]["weekly_hours_cap"] == "user"


# --- saved again later ------------------------------------------------------------------
def _plan_from_two_weeks_ago(claude, world):
    """A 15K plan with the ride, made two weeks ago, saved."""
    claude["read"] = BIKE_READ
    _say("one ride a week")
    made = MONDAY - timedelta(days=14)
    plan = build_event_plan(
        today=made,
        event={"event_id": "e1", "name": "City 15K", "type": "15K", "race_date": _race(16)},
        baseline={"ctl": 40.0, "atl": 42.0, "tsb": -2.0, "avg_weekly_tss_4w": 300, "avg_weekly_hours_4w": 5.5,
                  "tss_per_hour": 55, "captured_at": made.isoformat(), "limited_history": False},
        first_week=made,
        now_iso="2026-01-01T00:00:00+00:00",
        plan_id="p1",
    )
    plan["instructions"] = instr.record("one ride a week", instr.clean_read(BIKE_READ), applied=True)
    plan["settings_source"] = {k: "default" for k in instr.LOAD_FIELDS}
    store.save_plan(service._stored(plan))
    return store.load_plan()


def test_a_new_text_changes_the_plan_from_next_week_only(claude, world):
    before = _plan_from_two_weeks_ago(claude, world)
    service.edit_split(before["weeks"][5]["week_start"], "set_split", {"swim": 0, "bike": 30, "run": 70}, today=TODAY)
    claude["read"] = {**BIKE_READ, "summary": "Two rides a week.",
                      "sports": [{"discipline": "bike", "change": "add", "share_pct": 22, "sessions_per_week": 2}]}
    _say("two rides a week")
    r = api.put("/coach/instructions", json={"text": "two rides a week"})
    assert r.status_code == 200
    note = r.json()["season_plan_note"]
    assert note.startswith(f"Season plan updated from your coach instructions from the week of {before['weeks'][3]['week_start']}")
    weeks = store.load_plan()["weeks"]
    # Past weeks and this week keep the old split; from next week the new one.
    assert [w["split"]["bike"] for w in weeks[:3]] == [12, 12, 12]
    assert [w["split_source"] for w in weeks[:3]] == ["kept"] * 3
    assert weeks[3]["split"]["bike"] == 22 and weeks[3]["split_source"] == "table"
    # A week split by hand keeps its own.
    assert weeks[5]["split"] == {"swim": 0, "bike": 30, "run": 70}
    saved = store.load_plan()
    rev = saved["revisions"][-1]
    assert rev["reason_code"] == "INSTRUCTIONS" and rev["trigger"] == "instructions"
    assert saved["payload_changed_at"] != before["payload_changed_at"]


def test_the_same_text_or_no_plan_makes_no_call(claude, world):
    r = api.put("/coach/instructions", json={"text": "one ride a week"})
    assert r.json()["season_plan_note"] is None and claude["calls"] == []
    _plan_from_two_weeks_ago(claude, world)
    calls = len(claude["calls"])
    assert api.put("/coach/instructions", json={"text": "one ride a week"}).json()["season_plan_note"] is None
    assert len(claude["calls"]) == calls


def test_clearing_the_text_removes_the_ride_from_next_week(claude, world):
    _plan_from_two_weeks_ago(claude, world)
    api.put("/coach/instructions", json={"text": ""})
    weeks = store.load_plan()["weeks"]
    assert weeks[0]["split"]["bike"] == 12  # kept
    assert all(w.get("split") is None for w in weeks[3:])


def test_load_follows_the_instructions_but_never_the_athletes_own_settings(claude, world):
    _plan_from_two_weeks_ago(claude, world)
    service.update_settings({"ramp": "gentle"}, today=TODAY)  # the athlete's own
    claude["read"] = {**BIKE_READ, "load": {"weekly_hours_cap": 5, "ramp": "ambitious", "recovery_cadence_weeks": 2}}
    api.put("/coach/instructions", json={"text": "5 hours a week max, push the build, easy week every third"})
    saved = store.load_plan()
    assert saved["settings"]["weekly_hours_cap"] == 5
    assert saved["settings"]["recovery_cadence_weeks"] == 2
    assert saved["settings"]["ramp"] == "gentle"  # set by hand: the instructions do not touch it
    assert saved["settings_source"]["weekly_hours_cap"] == "instructions"
    assert saved["settings_source"]["ramp"] == "user"
    # Taken out of the text again: back to the default, no limit.
    claude["read"] = BIKE_READ
    api.put("/coach/instructions", json={"text": "one ride a week, nothing else"})
    saved = store.load_plan()
    assert saved["settings"]["weekly_hours_cap"] is None
    assert saved["settings"]["recovery_cadence_weeks"] == cfg.RECOVERY_CADENCE_WEEKS
    assert saved["settings_source"]["weekly_hours_cap"] == "default"


def test_a_failing_read_never_fails_the_save(claude, world):
    _plan_from_two_weeks_ago(claude, world)
    claude["error"] = RuntimeError("API down")
    r = api.put("/coach/instructions", json={"text": "three rides a week"})
    assert r.status_code == 200 and r.json()["text"] == "three rides a week"
    assert "could not read" in r.json()["season_plan_note"]
    assert store.load_plan()["weeks"][5]["split"]["bike"] == 12  # unchanged


def test_an_opted_out_plan_does_not_follow_later_edits(claude, world):
    _say("one ride a week")
    _create(use_instructions=False)
    assert api.put("/coach/instructions", json={"text": "two rides"}).json()["season_plan_note"] is None
    assert claude["calls"] == []


def test_split_rows_always_add_up():
    for share in range(0, 41):
        read = {"sports": [{"discipline": "bike", "change": "add", "share_pct": share}]}
        for row in [{"swim": 0, "bike": 0, "run": 100}, {"swim": 17, "bike": 50, "run": 33}]:
            out = instr.apply_to_row(row, read)
            assert sum(out.values()) == 100 and out["bike"] == share


# --- the prompt --------------------------------------------------------------------------
def test_the_prompt_files_exist_and_name_every_field():
    system = (ROOT / "prompts" / "season_instructions_system.txt").read_text(encoding="utf-8")
    fmt = (ROOT / "prompts" / "season_instructions_format.json").read_text(encoding="utf-8")
    for key in ("sports", "share_pct", "left_to_weekly_plan", *instr.LOAD_FIELDS):
        assert key in fmt
    assert "Never change" in system and "left_to_weekly_plan" in system
    assert split.has_split({"mode": "event", "event": {"type": "15K"},
                            "instructions": {"applied": True, "read": instr.clean_read(BIKE_READ)}})
