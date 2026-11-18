"""
The adjust_season_plan coach tool: it ends in the same service calls as the
dashboard's week panel, saves the athlete's request as the pin note, and is
never offered with "Include context" off.

No real Claude: _run_coach_tool is called directly, and coach_reply runs
against a scripted fake client.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.ai import claude
from app.season import service, store
from app.season.builder import build_event_plan

TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())


def _plan(runway: int = 20) -> dict:
    plan = build_event_plan(
        today=MONDAY,
        event={"event_id": "e1", "name": "Race", "type": "olympic",
               "race_date": (MONDAY + timedelta(days=7 * (runway - 1) + 5)).isoformat()},
        baseline={
            "ctl": 40.0, "atl": 42.0, "tsb": -2.0, "avg_weekly_tss_4w": 300,
            "avg_weekly_hours_4w": 5.5, "tss_per_hour": 55, "captured_at": MONDAY.isoformat(),
            "limited_history": False,
        },
        now_iso=datetime.combine(MONDAY, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
    )
    store.save_plan(plan)
    return plan


def _ws(i: int) -> str:
    return (MONDAY + timedelta(days=7 * i)).isoformat()


def _tool():
    return next(t for t in claude.COACH_TOOLS if t["name"] == "adjust_season_plan")


def test_the_tool_requires_the_athletes_request():
    schema = _tool()["input_schema"]
    assert "athlete_request" in schema["required"]
    assert set(schema["properties"]["operation"]["enum"]) == {
        "set_week_target", "make_recovery", "pause", "set_hours_cap", "set_ramp", "set_split"
    }


def test_set_week_target_pins_with_the_request_as_the_note():
    _plan()
    text, is_error = claude._run_coach_tool(
        "adjust_season_plan",
        {"operation": "set_week_target", "week_start": _ws(2), "tss": 280, "athlete_request": "Work trip that week"},
        fallback_note="yes",
    )
    assert is_error is False
    result = json.loads(text)
    assert "Takes effect in the 7-day plan" in result["note"]
    w = store.load_plan()["weeks"][2]
    assert w["target_tss"] == 280 and w["pinned"] and w["pin_note"] == "Work trip that week"
    rev = next(r for r in store.load_plan()["revisions"] if r["week_start"] == _ws(2))
    assert rev["trigger"] == "coach"
    assert rev["reason_text"].startswith("The coach, at your request,")


def test_the_athletes_message_is_the_fallback_note():
    _plan()
    claude._run_coach_tool(
        "adjust_season_plan", {"operation": "make_recovery", "week_start": _ws(2)}, fallback_note="I'm wrecked"
    )
    assert store.load_plan()["weeks"][2]["pin_note"] == "I'm wrecked"


def test_pause_hours_and_ramp_through_the_tool():
    _plan()
    for tool_input in (
        {"operation": "set_hours_cap", "hours": 6},
        {"operation": "set_ramp", "ramp": "gentle"},
        {"operation": "pause", "days": 9},
    ):
        text, is_error = claude._run_coach_tool("adjust_season_plan", {**tool_input, "athlete_request": "x"})
        assert is_error is False, text
    saved = store.load_plan()
    assert saved["settings"]["weekly_hours_cap"] == 6
    assert saved["settings"]["ramp"] == "gentle"
    assert any(w["kind"] == "return" for w in saved["weeks"])


def test_errors_come_back_as_tool_errors():
    text, is_error = claude._run_coach_tool(
        "adjust_season_plan", {"operation": "make_recovery", "week_start": _ws(2), "athlete_request": "x"}
    )
    assert is_error is True and "No active season plan" in text

    _plan()
    for bad in (
        {"operation": "make_recovery", "week_start": _ws(19)},  # the race week
        {"operation": "fly", "week_start": _ws(2)},
        {"operation": "pause", "days": "soon"},
        {"operation": "set_week_target"},
    ):
        text, is_error = claude._run_coach_tool("adjust_season_plan", {**bad, "athlete_request": "x"})
        assert is_error is True, bad


def test_the_tool_is_not_offered_with_context_off(monkeypatch):
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])

    monkeypatch.setattr(claude, "_client", lambda: SimpleNamespace(messages=Messages()))
    claude.coach_reply("make next week easy", [], "ctx", tools=False)
    assert "tools" not in calls[0]

    claude.coach_reply("make next week easy", [], "ctx", tools=True)
    names = [t["name"] for t in calls[1]["tools"]]
    assert "adjust_season_plan" in names


def test_the_coach_is_told_about_the_tool_only_with_a_plan():
    assert service.coach_line(TODAY) is None
    _plan()
    assert service.coach_line(TODAY).startswith("Season plan toward Race")
