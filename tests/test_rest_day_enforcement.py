"""
Rest days are the one guardrail the athlete cannot be talked out of, so they
are enforced in code after generation rather than trusted to the prompt.
Fakes only.
"""

from __future__ import annotations

from app.recommend_service import _enforce_rest_days

PAYLOAD = {
    "plan_days": [
        {"date": "2026-07-27", "day_name": "Monday", "is_rest_day": False},
        {"date": "2026-07-28", "day_name": "Tuesday", "is_rest_day": True},
        {"date": "2026-07-29", "day_name": "Wednesday", "is_rest_day": False},
    ]
}


def _session(title="Easy spin"):
    return {"discipline": "bike", "title": title, "duration_min": 45}


def test_session_on_a_rest_day_is_cleared():
    plan = {
        "days": [
            {"date": "2026-07-27", "is_rest_day": False, "sessions": [_session()]},
            # Claude slipped a "light" session onto the rest day.
            {"date": "2026-07-28", "is_rest_day": False, "sessions": [_session("Recovery jog")]},
            {"date": "2026-07-29", "is_rest_day": False, "sessions": [_session()]},
        ]
    }
    cleared = _enforce_rest_days(plan, PAYLOAD)

    assert cleared == ["2026-07-28"]
    rest_day = plan["days"][1]
    assert rest_day["sessions"] == []
    assert rest_day["is_rest_day"] is True
    # Training days are left exactly as generated.
    assert len(plan["days"][0]["sessions"]) == 1
    assert plan["days"][2]["is_rest_day"] is False


def test_correctly_planned_rest_day_is_untouched():
    plan = {
        "days": [
            {"date": "2026-07-27", "is_rest_day": False, "sessions": [_session()]},
            {"date": "2026-07-28", "is_rest_day": True, "sessions": []},
        ]
    }
    assert _enforce_rest_days(plan, PAYLOAD) == []
    assert plan["days"][1]["is_rest_day"] is True


def test_rest_day_flag_is_repaired_even_with_no_sessions():
    # No session to drop, but the day was mislabelled a training day.
    plan = {"days": [{"date": "2026-07-28", "is_rest_day": False, "sessions": []}]}
    assert _enforce_rest_days(plan, PAYLOAD) == []
    assert plan["days"][0]["is_rest_day"] is True


def test_no_rest_days_and_malformed_plans_are_safe():
    no_rest = {"plan_days": [{"date": "2026-07-27", "is_rest_day": False}]}
    plan = {"days": [{"date": "2026-07-27", "is_rest_day": False, "sessions": [_session()]}]}
    assert _enforce_rest_days(plan, no_rest) == []
    assert len(plan["days"][0]["sessions"]) == 1

    assert _enforce_rest_days({}, PAYLOAD) == []
    assert _enforce_rest_days({"days": None}, PAYLOAD) == []
    # An unknown date is not in the rest set, so it passes through.
    assert _enforce_rest_days({"days": [{"date": "1999-01-01"}]}, PAYLOAD) == []
