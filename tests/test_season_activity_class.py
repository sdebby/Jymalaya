"""
The athlete's activity class picks a new season plan's blank defaults: the
build-up pace, the recovery spacing, the peak CTL and the new-athlete start
floor. The create form wins over the coach instructions, which win over the
class, which wins over the constants. Class 4 (the profile default) is the
constants' plan exactly.

No network: tests/conftest.py stubs the instructions read and pins the class
at 4; each test here sets its own.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.season import config as cfg
from app.season import instructions as instr
from app.season import service
from app.season.builder import start_level, start_week
from main import app

api = TestClient(app, base_url="http://testserver/api")

TODAY = date.today()
FIRST_WEEK = start_week(TODAY, "monday")


def _race(runway: int) -> str:
    return (FIRST_WEEK + timedelta(days=7 * (runway - 1) + 5)).isoformat()


@pytest.fixture()
def world(monkeypatch):
    """No history (so the start floor shows); the event is a 70.3, 20 weeks out."""
    event = {"event_id": "e1", "name": "Half", "type": "70.3", "race_date": _race(20)}
    monkeypatch.setattr(service, "_read_history", lambda: ([], {}))
    monkeypatch.setattr(service, "_target_event", lambda eid: dict(event))
    return event


def _class(monkeypatch, value):
    monkeypatch.setattr(service, "_activity_class", lambda: value)


def _preview(**body) -> dict:
    r = api.post("/season-plan/preview", json={"mode": "event", "event_id": "e1", **body})
    assert r.status_code == 200, r.text
    return r.json()


def _first_target(plan: dict) -> int:
    return next(w["target_tss"] for w in plan["weeks"] if w["kind"] == "loading")


# --- the mapping ---------------------------------------------------------------


@pytest.mark.parametrize(
    "cls, band",
    [(0, ("gentle", 2, 0.85)), (3, ("gentle", 2, 0.85)), (4, ("standard", 3, 1.0)),
     (6, ("standard", 3, 1.0)), (7, ("ambitious", 3, 1.10)), (10, ("ambitious", 3, 1.10))],
)
def test_bands(cls, band):
    assert cfg.activity_band(cls) == band


def test_no_class_picks_nothing():
    assert cfg.activity_band(None) is None
    assert cfg.activity_band("7") is None
    assert cfg.start_floor(None) == cfg.MIN_START_TSS


def test_class_4_floor_is_the_old_constant():
    assert cfg.start_floor(4) == cfg.MIN_START_TSS == 150
    assert cfg.start_floor(2) == 120
    assert cfg.start_floor(9) == 225


def test_start_level_floor_follows_the_class():
    new = {"avg_weekly_tss_4w": 0, "limited_history": True}
    assert start_level(new) == 150
    assert start_level(new, 2) == 120
    assert start_level(new, 9) == 225
    # A real history above the floor is left alone.
    assert start_level({"avg_weekly_tss_4w": 400, "limited_history": False}, 0) == 400


# --- a new plan ----------------------------------------------------------------


def test_class_4_is_the_constants_plan(world, monkeypatch):
    _class(monkeypatch, 4)
    with_class = _preview()
    _class(monkeypatch, None)
    without = _preview()
    assert with_class["settings"]["ramp"] == without["settings"]["ramp"] == cfg.DEFAULT_RAMP
    assert with_class["settings"]["recovery_cadence_weeks"] == without["settings"]["recovery_cadence_weeks"] == 3
    assert with_class["targets"]["peak_ctl"] == without["targets"]["peak_ctl"] == cfg.PEAK_CTL["70.3"]
    assert [(w["kind"], w["target_tss"]) for w in with_class["weeks"]] == [
        (w["kind"], w["target_tss"]) for w in without["weeks"]
    ]
    assert with_class["settings_source"]["ramp"] == "profile"
    assert without["settings_source"]["ramp"] == "default"


def test_low_class(world, monkeypatch):
    _class(monkeypatch, 2)
    plan = _preview()
    assert plan["settings"]["ramp"] == "gentle"
    assert plan["settings"]["recovery_cadence_weeks"] == 2
    assert plan["targets"]["peak_ctl"] == round(cfg.PEAK_CTL["70.3"] * 0.85)
    assert _first_target(plan) == 120
    assert {plan["settings_source"][k] for k in ("ramp", "recovery_cadence_weeks", "peak_ctl")} == {"profile"}
    # The hours cap is not the class's to pick.
    assert plan["settings_source"]["weekly_hours_cap"] == "default"


def test_high_class(world, monkeypatch):
    _class(monkeypatch, 9)
    plan = _preview()
    assert plan["settings"]["ramp"] == "ambitious"
    assert plan["settings"]["recovery_cadence_weeks"] == 3
    assert plan["targets"]["peak_ctl"] == round(cfg.PEAK_CTL["70.3"] * 1.10)
    assert _first_target(plan) == 225


def test_the_form_wins(world, monkeypatch):
    _class(monkeypatch, 2)
    plan = _preview(ramp="ambitious", peak_ctl=80)
    assert plan["settings"]["ramp"] == "ambitious"
    assert plan["targets"]["peak_ctl"] == 80
    assert {plan["settings_source"][k] for k in ("ramp", "peak_ctl")} == {"user"}
    # The form has no spacing field: that one still comes from the class.
    assert plan["settings"]["recovery_cadence_weeks"] == 2


def test_the_instructions_win(world, monkeypatch):
    from app import instructions as app_instructions

    _class(monkeypatch, 2)
    app_instructions.save_instructions("Build fast, I have done this for years.")
    read = {**instr.empty_read(), "load": {"weekly_hours_cap": None, "ramp": "ambitious",
                                           "recovery_cadence_weeks": None, "peak_ctl": None}}
    monkeypatch.setattr(instr, "_ask_claude", lambda text, context: read)
    plan = _preview()
    assert plan["settings"]["ramp"] == "ambitious"
    assert plan["settings_source"]["ramp"] == "instructions"
    # What the instructions leave blank still comes from the class.
    assert plan["settings_source"]["recovery_cadence_weeks"] == "profile"
    assert plan["settings"]["recovery_cadence_weeks"] == 2


def test_fitness_plan_takes_the_floor_and_spacing_only(monkeypatch):
    monkeypatch.setattr(service, "_read_history", lambda: ([], {}))
    _class(monkeypatch, 2)
    r = api.post("/season-plan/preview", json={"mode": "fitness", "focus": "run"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["settings"]["recovery_cadence_weeks"] == 2
    assert plan["settings_source"]["recovery_cadence_weeks"] == "profile"
    assert plan["settings"]["ramp"] is None
    assert plan["settings_source"]["ramp"] == "default"
    assert plan["weeks"][0]["target_tss"] == 120


def test_a_cleared_setting_goes_back_to_the_class(world, monkeypatch):
    _class(monkeypatch, 2)
    r = api.post("/season-plan", json={"mode": "event", "event_id": "e1", "ramp": "ambitious", "peak_ctl": 99})
    assert r.status_code == 201, r.text
    plan = service.update_settings({"ramp": None, "peak_ctl": None}, trigger="instructions")
    assert plan["settings"]["ramp"] == "gentle"
    assert plan["targets"]["peak_ctl"] == round(cfg.PEAK_CTL["70.3"] * 0.85)
    assert plan["settings_source"]["ramp"] == plan["settings_source"]["peak_ctl"] == "profile"


def test_a_later_class_change_leaves_a_saved_plan_alone(world, monkeypatch):
    _class(monkeypatch, 2)
    assert api.post("/season-plan", json={"mode": "event", "event_id": "e1"}).status_code == 201
    _class(monkeypatch, 9)
    plan = api.get("/season-plan").json()
    assert plan["settings"]["ramp"] == "gentle"
    assert plan["settings"]["recovery_cadence_weeks"] == 2
