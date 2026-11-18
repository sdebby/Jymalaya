"""
A workout already logged today is never a plan session: the dashboard shows the
logged workout itself, so a session restating it draws it twice and the load
forecast counts it twice. Rule 5 says so in the prompt, and
_drop_completed_sessions enforces it after generation. Fakes only.
"""

from __future__ import annotations

from app.ai import claude
from app.recommend_service import _drop_completed_sessions

TODAY = "2026-09-28"
TOMORROW = "2026-09-29"


def _payload(*workouts):
    return {"today_date": TODAY, "last_7_days_workouts": list(workouts)}


def _logged(discipline="run", minutes=47, day=TODAY):
    return {"date": day, "discipline": discipline, "duration_min": minutes, "tss": 51.7}


def _session(discipline="run", minutes=47, title="Completed earlier today"):
    return {"discipline": discipline, "title": title, "duration_min": minutes}


def _plan(today_sessions, tomorrow_sessions=None):
    return {
        "days": [
            {"date": TODAY, "is_rest_day": False, "sessions": today_sessions},
            {"date": TOMORROW, "is_rest_day": False, "sessions": tomorrow_sessions or []},
        ]
    }


def test_session_restating_todays_workout_is_dropped():
    # The 2026-09-28 plan: a 47-min run logged, echoed back as a 47-min session.
    plan = _plan([_session()], [_session(minutes=47, title="Easy run")])
    dropped = _drop_completed_sessions(plan, _payload(_logged()))

    assert dropped == ["Completed earlier today"]
    assert plan["days"][0]["sessions"] == []
    assert plan["days"][0]["is_rest_day"] is False
    # The same run tomorrow is real planned work.
    assert len(plan["days"][1]["sessions"]) == 1


def test_rounded_echo_is_still_caught():
    plan = _plan([_session(minutes=45)])
    assert _drop_completed_sessions(plan, _payload(_logged(minutes=47))) == [
        "Completed earlier today"
    ]


def test_other_discipline_still_to_do_is_kept():
    # Run done this morning, swim still to come tonight.
    plan = _plan([_session("swim", 45, "Technique swim")])
    assert _drop_completed_sessions(plan, _payload(_logged())) == []
    assert len(plan["days"][0]["sessions"]) == 1


def test_same_discipline_of_clearly_different_length_is_kept():
    # Race morning: a 15-min shakeout jog is logged, the 55-min race is not done.
    plan = _plan([_session("run", 55, "Kfar Saba 10K")])
    assert _drop_completed_sessions(plan, _payload(_logged(minutes=15))) == []
    assert plan["days"][0]["sessions"][0]["title"] == "Kfar Saba 10K"


def test_athlete_requested_today_is_left_alone():
    plan = _plan([_session()])
    assert _drop_completed_sessions(plan, _payload(_logged()), keep={TODAY}) == []
    assert len(plan["days"][0]["sessions"]) == 1


def test_workout_logged_yesterday_does_not_touch_today():
    plan = _plan([_session(title="Easy run")])
    assert _drop_completed_sessions(plan, _payload(_logged(day="2026-09-27"))) == []
    assert len(plan["days"][0]["sessions"]) == 1


def test_one_logged_workout_cancels_one_session():
    plan = _plan([_session(), _session(title="Second run")])
    assert _drop_completed_sessions(plan, _payload(_logged())) == ["Completed earlier today"]
    assert [s["title"] for s in plan["days"][0]["sessions"]] == ["Second run"]


def test_malformed_input_is_safe():
    assert _drop_completed_sessions({}, _payload(_logged())) == []
    assert _drop_completed_sessions({"days": None}, _payload(_logged())) == []
    assert _drop_completed_sessions(_plan([_session()]), {}) == []
    plan = _plan([_session()])
    assert _drop_completed_sessions(plan, {"today_date": TODAY}) == []
    assert len(plan["days"][0]["sessions"]) == 1


def test_rule_5_never_asks_for_a_session():
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    rule = text[text.index("5. Day 1 of the plan is TODAY") : text.index("\n6. ")]
    # A plan day has no notes field: pointing there is what made the model
    # invent a session to hold the acknowledgement.
    assert "day's notes" not in rule
    assert "NEVER emit it as a session" in rule
    assert '"summary" or "coach_notes"' in rule
