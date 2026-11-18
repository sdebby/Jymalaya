"""
The coach-chat system context must anchor "today" explicitly. Without it the
model only sees ISO dates (plan days, rest days) and guesses which one is the
current day -- it once answered a Wednesday question as if it were Friday.

It must also carry the athlete profile, the same one the plan and analysis
payloads get, and under it the shared zone bands and pace rules -- without them
the coach improvised zones and quoted Z2 faster than threshold pace.

The second half covers the composer's "Include context" toggle: with it off the
turn must carry no athlete data and no plan-editing tools.
Fakes only.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest

import app.schedule_store as store
from app.ai.claude import COACH_SUGGESTIONS_RULE

PROFILE = {
    "ftp_w": 250,
    "run_threshold_pace_sec_km": 270,
    "css_sec_100m": 95,
    "lthr_bpm": 162,
    "weight_kg": 72,
    "activity_class": 6,
    "vo2max_decline_threshold": 0.5,
    "birthday": "1988-06-15",
    "age": 38,
}


@pytest.fixture
def context(monkeypatch):
    from app.ai import prompt_builder as pb

    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb, "get_athlete_profile", lambda: dict(PROFILE))
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    # last_race reaches events_service.InfluxRepo directly, which patching
    # pb.InfluxRepo does not cover -- without this the test needs a live DB.
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    # get_schedule reads -- and on a fresh clone writes -- the real
    # data/schedule.json, so the rest days asserted below would be whatever the
    # athlete happens to have saved. The factory week fixes them at wed + sun.
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_coach_context()


def _context_with_race(monkeypatch, race: dict | None, measured: dict | None):
    """The coach context with a finished race in the payload."""
    from app.ai import prompt_builder as pb

    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb, "get_athlete_profile", lambda: dict(PROFILE))
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: race)
    monkeypatch.setattr(pb, "_race_day_activity", lambda rows, day: measured)
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_coach_context()


def test_context_names_a_recent_race(monkeypatch):
    """
    The day after a race the goal is gone and TSB has not caught up, so without
    this the coach reads "no race goal set" and brings quality work forward.
    """
    raced = (date.today() - timedelta(days=6)).isoformat()
    context = _context_with_race(
        monkeypatch,
        {"name": "Tel Aviv Marathon", "type": "marathon", "race_date": raced},
        {"tss": 305, "duration_min": 212},
    )
    assert "Raced 6 days ago: Tel Aviv Marathon (marathon)" in context
    assert "measured 305 TSS over 212 min" in context
    assert "post-race recovery" in context


def test_context_says_when_nothing_was_logged_on_race_day(monkeypatch):
    raced = (date.today() - timedelta(days=2)).isoformat()
    context = _context_with_race(
        monkeypatch,
        {"name": "Local 10K", "type": "10K", "race_date": raced},
        None,
    )
    assert "nothing was logged on race day" in context


def test_context_omits_the_race_line_without_one(context):
    assert "Raced" not in context


def _profile_in(context: str) -> dict:
    line = next(l for l in context.splitlines() if l.startswith("Athlete profile"))
    return json.loads(line[line.index("{"):])


def test_context_carries_the_athlete_profile(context):
    profile = _profile_in(context)
    for key in (
        "ftp_w",
        "lthr_bpm",
        "run_threshold_pace_sec_km",
        "css_sec_100m",
        "weight_kg",
        "age",
        "activity_class",
        "vo2max_decline_threshold",
    ):
        assert profile[key] == PROFILE[key]


def test_context_carries_the_zone_rules_under_the_profile(context):
    """The bands sit right under the thresholds they are applied to."""
    from app.ai.claude import training_zones_text

    zones = training_zones_text()
    assert zones in context
    assert (
        context.index("Athlete profile")
        < context.index(zones)
        < context.index("Current form")
    )


def test_context_tells_the_coach_to_recompute_a_questioned_number(context):
    assert "If the athlete questions a number, recompute it" in context


def test_edit_rules_keep_the_cooldown_last_and_reps_whole(context):
    """Plan rule 10, restated for the coach's plan-editing tools."""
    assert "one cooldown last with nothing after it" in context
    assert "N x (work, recovery)" in context
    assert "the last rep keeping its recovery" in context


def test_context_keeps_the_birthday_local(context):
    """Age is what coaching reasons about; the birthday stays on the machine."""
    assert "birthday" not in _profile_in(context)
    assert PROFILE["birthday"] not in context


def test_context_states_today_with_weekday(context):
    today = date.today()
    assert f"Today is {today.strftime('%A')}, {today.isoformat()}." in context


def test_today_line_precedes_the_plan_json(context):
    """The anchor comes before the dated blocks it has to override."""
    assert context.index("Today is ") < context.index("Rest days in this window")


def test_rest_days_carry_their_weekday(context):
    # The factory week always rests wed + sun, so the line is always present --
    # it used to be skipped over whenever the real schedule had no rest day.
    assert "Rest days in this window" in context
    line = context.split("Rest days in this window (never plan work on these): ")[1]
    line = line.split(".\n")[0]
    for entry in line.split(", "):
        day_name, iso = entry.split(" ")
        assert date.fromisoformat(iso).strftime("%A") == day_name


# --- the VO2max trend -------------------------------------------------------
# A plan rule can turn one of the week's hard sessions into Z5 intervals off this
# trend. Without it in the context the coach cannot say why that session is
# there, and its plan-editing tools would soften it as an ordinary hard day.


def _context_with_trend(monkeypatch, trend: dict):
    from app.ai import prompt_builder as pb

    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb, "get_athlete_profile", lambda: dict(PROFILE))
    monkeypatch.setattr(pb, "_vo2max_trend", lambda rows, today, threshold: trend)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_coach_context()


def _trend_lines(context: str) -> list[str]:
    """
    The trend gets a line of its own, and that line is what these tests read.
    Searching the whole context is not safe: the cached plan is embedded
    verbatim, and once a plan reacts to the trend its notes discuss it in the
    same words -- which is how an earlier "not in context" assertion passed
    until the athlete's next plan was generated.
    """
    return [
        l for l in context.splitlines() if re.match(r"^(Swim|Bike|Run|Strength) VO2max ", l)
    ]


def test_context_names_a_declining_vo2max_trend(monkeypatch):
    context = _context_with_trend(
        monkeypatch,
        {"run": {"weekly_avg": [51.0, 50.5, None, 49.2], "current": 49.2,
                 "change": -1.8, "direction": "declining"}},
    )
    (line,) = _trend_lines(context)
    assert line.startswith("Run VO2max declining over 4 weeks")
    assert "now 49.2" in line
    assert "do not remove or soften one without saying why" in line


def test_a_stable_trend_carries_the_numbers_but_no_warning(monkeypatch):
    """Nothing is riding on a stable trend, so there is no session to defend."""
    context = _context_with_trend(
        monkeypatch,
        {"run": {"weekly_avg": [46.7, 46.5, 46.4, 46.5], "current": 46.5,
                 "change": -0.2, "direction": "stable"}},
    )
    (line,) = _trend_lines(context)
    assert line.startswith("Run VO2max stable over 4 weeks")
    assert "do not remove or soften" not in line


def test_context_omits_an_unclassified_trend(monkeypatch):
    """"insufficient_data" is noise in a chat line -- it says nothing."""
    context = _context_with_trend(
        monkeypatch,
        {"bike": {"weekly_avg": [48.5, 47.8, None, None], "current": 47.8,
                  "change": None, "direction": "insufficient_data"}},
    )
    assert _trend_lines(context) == []


def test_context_omits_the_trend_line_without_readings(context):
    assert _trend_lines(context) == []


# --- "Include context" off: the general-question context ---------------------
# The athlete unticks the box to ask a general question (nutrition, transitions),
# so the turn must carry none of their data and none of the plan-editing tools --
# a coach that cannot see the plan must not be able to rewrite it.


@pytest.fixture
def general(monkeypatch):
    """The no-data context, built with every athlete data source booby-trapped."""
    from app.ai import prompt_builder as pb

    class Exploding:
        def __init__(self, *a, **kw):
            raise AssertionError("the general coach context must read no athlete data")

    monkeypatch.setattr(pb, "InfluxRepo", Exploding)
    monkeypatch.setattr(pb, "get_athlete_profile", Exploding)
    monkeypatch.setattr(pb, "get_schedule", Exploding)
    monkeypatch.setattr(pb, "get_instructions", Exploding)
    return pb.build_general_coach_context()


def test_general_context_still_anchors_today(general):
    """A date is not training data, and the model needs it for "before my race"."""
    today = date.today()
    assert f"Today is {today.strftime('%A')}, {today.isoformat()}." in general


def test_general_context_carries_no_athlete_data(general):
    for marker in (
        "Fitness/CTL",
        "Rest days in this window",
        "Plan days JSON",
        "Goal:",
        "Raced",
        "Athlete profile",
        "ftp_w",
        "Training zones",
        "over 4 weeks",
    ):
        assert marker not in general


def test_general_context_says_it_has_no_data(general):
    assert "Include context" in general


def test_general_context_states_the_pace_direction(general):
    """Not athlete data -- and the pace inversion needs no data to happen."""
    assert "a larger min:sec is slower" in general
    assert "never in seconds" in general
    assert "km/h" in general


# --- POST /coach honours the flag -------------------------------------------
def _capture(monkeypatch):
    """Record what the route hands to coach_reply, without calling Claude."""
    from app.routers import coach as route

    seen = {}

    def fake_reply(message, history, context, *, tools=True):
        seen.update(message=message, context=context, tools=tools)
        return "ok", False, False

    monkeypatch.setattr(route, "coach_reply", fake_reply)
    monkeypatch.setattr(route, "build_coach_context", lambda: "FULL CONTEXT")
    monkeypatch.setattr(route, "build_general_coach_context", lambda: "GENERAL CONTEXT")
    return seen


def test_coach_without_context_drops_the_data_and_the_tools(monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    seen = _capture(monkeypatch)
    client = TestClient(app, base_url="http://testserver/api")
    r = client.post("/coach", json={"message": "how do I speed up T1?", "include_context": False})
    assert r.status_code == 200
    # Only the follow-up suggestions rule is added, and it names no athlete data.
    assert seen["context"] == f"GENERAL CONTEXT\n\n{COACH_SUGGESTIONS_RULE}"
    assert seen["tools"] is False


def test_coach_defaults_to_the_full_context(monkeypatch):
    """An older client (or a curl) that omits the flag keeps today's behaviour."""
    from fastapi.testclient import TestClient

    from main import app

    seen = _capture(monkeypatch)
    client = TestClient(app, base_url="http://testserver/api")
    assert client.post("/coach", json={"message": "what should I do tomorrow?"}).status_code == 200
    assert seen["context"] == f"FULL CONTEXT\n\n{COACH_SUGGESTIONS_RULE}"
    assert seen["tools"] is True


def test_coach_context_asks_for_bold_approval_before_a_settings_change(context):
    from app.ai.claude import SETTINGS_APPROVAL_MARKER

    assert "update_athlete_settings" in context
    assert SETTINGS_APPROVAL_MARKER in context
    assert "Never save in the turn they ask" in context
    assert "birthday, activity class and training schedule are changed in" in context
