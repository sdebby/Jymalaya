"""
The day before the primary race never carries a session, whatever the distance
or disciplines, and even when the athlete's custom coach instructions ask for
one. Prompt rule 31 says so; _clear_pre_race_day enforces it after generation.
Fakes only.
"""

from __future__ import annotations

from pathlib import Path

from app.recommend_service import _clear_pre_race_day

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "recommendation_system.txt"


def _session(discipline="run", title="Shakeout run"):
    return {"discipline": discipline, "title": title, "duration_min": 20}


def _plan():
    return {
        "days": [
            {"date": "2026-11-12", "is_rest_day": False, "sessions": [_session(title="Easy run")]},
            {"date": "2026-11-13", "is_rest_day": False, "sessions": [_session(), _session("swim", "Openers")]},
            {"date": "2026-11-14", "is_rest_day": False, "sessions": [_session(title="Race")]},
        ]
    }


def _payload(race_date="2026-11-14", type_="10K"):
    return {"primary_goal": {"type": type_, "race_date": race_date}}


def test_session_on_the_eve_of_the_race_is_cleared():
    plan = _plan()
    assert _clear_pre_race_day(plan, _payload()) == ["2026-11-13"]
    eve = plan["days"][1]
    assert eve["sessions"] == []
    # Not a schedule rest day: it just has nothing on it.
    assert eve["is_rest_day"] is False
    # The day before it and race day are untouched.
    assert len(plan["days"][0]["sessions"]) == 1
    assert plan["days"][2]["sessions"][0]["title"] == "Race"


def test_any_distance():
    for type_ in ("5K", "Marathon", "Olympic", "Full_IM", "custom"):
        plan = _plan()
        assert _clear_pre_race_day(plan, _payload(type_=type_)) == ["2026-11-13"]


def test_empty_eve_reports_nothing():
    plan = _plan()
    plan["days"][1]["sessions"] = []
    assert _clear_pre_race_day(plan, _payload()) == []


def test_no_goal_or_bad_date_is_a_no_op():
    for payload in ({}, {"primary_goal": None}, _payload(race_date=""), _payload(race_date="soon")):
        plan = _plan()
        assert _clear_pre_race_day(plan, payload) == []
        assert len(plan["days"][1]["sessions"]) == 2


def test_race_beyond_the_window_touches_nothing():
    plan = _plan()
    assert _clear_pre_race_day(plan, _payload(race_date="2026-12-01")) == []


def test_athlete_requested_eve_is_kept():
    plan = _plan()
    assert _clear_pre_race_day(plan, _payload(), keep={"2026-11-13"}) == []
    assert len(plan["days"][1]["sessions"]) == 2


def test_prompt_rule_outranks_custom_instructions():
    text = PROMPT.read_text(encoding="utf-8")
    assert "31. The day before primary_goal.race_date is ALWAYS an empty day" in text
    assert "even when athlete_custom_instructions asks" in text
    assert "or with rule 31 (no session the day" in text
