"""
A finished race buys a recovery window, sized from what the athlete actually did.

No network: events_service.list_events and the Influx repo are faked.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest

import app.schedule_store as store
from app import events_service
from app.ai import claude
from app.ai import prompt_builder as pb

TODAY = date.today()


def _iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _goal(goal_id: str, race_date: str, goal_type: str = "marathon") -> dict:
    return {
        "event_id": goal_id,
        "name": f"race-{goal_id}",
        "type": goal_type,
        "race_date": race_date,
    }


def _fake_goals(monkeypatch, goals: list[dict]) -> None:
    monkeypatch.setattr(events_service, "list_events", lambda: goals)


# --- last_race --------------------------------------------------------------
def test_last_race_picks_the_most_recent_past_race(monkeypatch):
    _fake_goals(monkeypatch, [_goal("old", _iso(40)), _goal("recent", _iso(3))])
    assert events_service.last_race()["event_id"] == "recent"


def test_last_race_ignores_today_and_the_future(monkeypatch):
    """Race day is still active_event; rule 5 of the plan prompt owns it."""
    _fake_goals(monkeypatch, [_goal("today", _iso(0)), _goal("soon", _iso(-10))])
    assert events_service.last_race() is None


def test_last_race_ignores_a_goal_with_no_date(monkeypatch):
    """
    The string-comparison trap: _row_to_event defaults race_date to "", and
    "" < today is True, so a plain comparison would call a date-less goal the
    athlete's last race.
    """
    _fake_goals(monkeypatch, [_goal("dateless", "")])
    assert events_service.last_race() is None


def test_last_race_ignores_a_malformed_date(monkeypatch):
    _fake_goals(monkeypatch, [_goal("junk", "not-a-date")])
    assert events_service.last_race() is None


def test_last_race_honours_the_window(monkeypatch):
    _fake_goals(monkeypatch, [_goal("stale", _iso(30))])
    assert events_service.last_race() is not None
    assert events_service.last_race(within_days=21) is None


def test_last_race_is_deterministic_on_a_tie(monkeypatch):
    same = _iso(2)
    _fake_goals(monkeypatch, [_goal("aaa", same), _goal("zzz", same)])
    assert events_service.last_race()["event_id"] == "zzz"


def test_last_race_with_no_goals(monkeypatch):
    _fake_goals(monkeypatch, [])
    assert events_service.last_race() is None


# --- _workout_date ----------------------------------------------------------
def test_workout_date_prefers_the_stored_local_day():
    """
    A 22:00 local session is the NEXT day in UTC. The stored `date` field is the
    athlete's calendar day and must win, or the race-day lookup misses it.
    """
    evening = datetime(2026, 9, 14, 21, 30, tzinfo=timezone.utc)
    assert pb._workout_date({"date": "2026-09-14", "time": evening}) == "2026-09-14"


def test_workout_date_falls_back_to_the_local_date():
    t = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
    assert pb._workout_date({"time": t}) == t.astimezone().date().isoformat()


def test_workout_date_is_none_without_either():
    assert pb._workout_date({}) is None


# --- _race_day_activity -----------------------------------------------------
RACE_DAY = "2026-09-14"


def _leg(discipline: str, duration_sec: int, tss: float, intensity: float | None):
    return {
        "date": RACE_DAY,
        "discipline": discipline,
        "duration_sec": duration_sec,
        "tss": tss,
        "intensity_factor": intensity,
    }


def test_race_day_activity_aggregates_the_legs():
    """
    A triathlon race day is several brick-split rows sharing one date. They come
    back from Influx grouped, not chronological, so the legs are sorted here --
    given out of order, they must still read swim, bike, run.
    """
    noon = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)
    rows = [
        dict(_leg("bike", 9000, 180, 0.80), time=noon + timedelta(minutes=35)),
        dict(_leg("run", 5400, 110, 0.85), time=noon + timedelta(minutes=190)),
        dict(_leg("swim", 1800, 40, 0.90), time=noon),
    ]
    measured = pb._race_day_activity(rows, RACE_DAY)
    assert measured["tss"] == 330
    assert measured["duration_min"] == 270
    assert measured["session_count"] == 3
    assert measured["disciplines"] == ["swim", "bike", "run"]
    # Duration-weighted, so the 30-min swim does not count like the 2.5h ride.
    assert measured["intensity_factor"] == pytest.approx(0.83, abs=0.01)


def test_race_day_activity_ignores_legs_with_no_intensity():
    rows = [_leg("run", 3600, 100, 0.88), _leg("run", 600, 5, None)]
    assert pb._race_day_activity(rows, RACE_DAY)["intensity_factor"] == 0.88


def test_race_day_activity_is_none_when_nothing_was_logged():
    assert pb._race_day_activity([_leg("run", 3600, 100, 0.88)], "2026-09-15") is None
    assert pb._race_day_activity([], RACE_DAY) is None


def test_race_day_activity_matches_an_evening_race_on_the_local_day():
    """The CLAUDE.md invariant: match on `date`, never on the UTC instant."""
    row = dict(
        _leg("run", 3600, 100, 0.88),
        time=datetime(2026, 9, 14, 22, 30, tzinfo=timezone.utc),
    )
    assert pb._race_day_activity([row], RACE_DAY)["tss"] == 100


# --- _recent_race_block -----------------------------------------------------
def test_recent_race_block_reports_days_since_and_disciplines():
    goal = _goal("m", _iso(6))
    block = pb._recent_race_block(goal, TODAY, [])
    assert block["days_since_race"] == 6
    assert block["race_disciplines"] == ["run"]
    assert block["measured"] is None
    # Never weeks_until_race -- that would read as a race still to come.
    assert "weeks_until_race" not in block


def test_recent_race_block_without_a_goal():
    assert pb._recent_race_block(None, TODAY, []) is None


def test_recent_race_block_with_a_malformed_date():
    assert pb._recent_race_block(_goal("x", "nope"), TODAY, []) is None


# --- the payload ------------------------------------------------------------
def _payload(monkeypatch, *, last_race=None, workouts=()):
    class FakeRepo:
        def query(self, flux):
            return list(workouts)

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb, "drop_split_brick_parents", lambda rows: rows)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: last_race)
    # get_schedule reads (and on a fresh clone writes) the real
    # data/schedule.json; the factory week keeps the payload deterministic.
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_plan_payload()


def test_payload_omits_recent_race_without_one(monkeypatch):
    assert "recent_race" not in _payload(monkeypatch)


def test_payload_carries_the_recent_race(monkeypatch):
    race_date = _iso(4)
    row = {
        "date": race_date,
        "discipline": "run",
        "duration_sec": 12600,
        "tss": 305,
        "intensity_factor": 0.89,
    }
    payload = _payload(monkeypatch, last_race=_goal("m", race_date), workouts=[row])
    recent = payload["recent_race"]
    assert recent["days_since_race"] == 4
    assert recent["race_disciplines"] == ["run"]
    assert recent["measured"]["tss"] == 305
    assert recent["measured"]["duration_min"] == 210


def test_the_lookup_window_fits_inside_the_history(monkeypatch):
    """
    The race-day activity is read from the same history list, so a longer
    window would silently report every older race as "nothing logged".
    """
    assert pb.RECENT_RACE_DAYS < pb.PLAN_HISTORY_DAYS


# --- the prompt -------------------------------------------------------------
def test_the_recovery_section_sits_between_rule_16_and_the_json_instruction():
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    heading = text.index("Post-race recovery (recent_race):")
    assert text.index("\n16. ") < heading < text.index("Respond ONLY in the JSON")


@pytest.mark.parametrize("rule", ["17", "18", "19", "20"])
def test_each_recovery_rule_appears_once(rule):
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    assert len(re.findall(rf"^{rule}\. ", text, re.MULTILINE)) == 1


def test_rule_8_hands_off_to_the_recovery_rules():
    """TSB is a lagging average; a single race barely moves it."""
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    assert "when recent_race is present, rules 17-20 set" in text


def test_recovery_rules_reach_the_built_prompt():
    system = claude._build_plan_system({"primary_goal": {"type": "marathon"}})
    assert "Post-race recovery (recent_race):" in system
