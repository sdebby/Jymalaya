"""
Garmin's VO2max reaches the plan as a 4-week trend, and a falling one asks for a
VO2max session.

The direction is classified in code rather than by Claude because it gates a
prompt rule, so it has to be deterministic -- and against the athlete's own
vo2max_decline_threshold, not a constant, so they can tune what counts as a
decline.

No network: fakes only.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest

import app.schedule_store as store
from app.ai import claude
from app.ai import prompt_builder as pb

TODAY = date.today()
DEFAULT = 0.5

# Every direction _vo2max_trend can emit. The prompt has to name them all, and a
# test below proves each one is reachable.
DIRECTIONS = ("declining", "rising", "stable", "insufficient_data")

PROFILE = {
    "ftp_w": 250.0,
    "run_threshold_pace_sec_km": 270.0,
    "css_sec_100m": 95.0,
    "lthr_bpm": 162.0,
    "weight_kg": 72.0,
    "activity_class": 6,
    "vo2max_decline_threshold": DEFAULT,
}


def _row(days_ago: int, discipline: str, value: float) -> dict:
    """
    A stored vo2max row as InfluxRepo.query returns one: midnight UTC, no `date`
    field. Reading the calendar day off that stamp is the invariant the whole
    measurement rests on -- a local conversion shifts it west of UTC.
    """
    day = TODAY - timedelta(days=days_ago)
    return {
        "time": datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
        "discipline": discipline,
        "vo2max": value,
    }


def _trend(rows, threshold: float = DEFAULT, discipline: str = "run"):
    return pb._vo2max_trend(rows, TODAY, threshold).get(discipline)


# --- bucketing --------------------------------------------------------------
def test_four_weeks_of_readings_bucket_oldest_first():
    rows = [_row(24, "run", 51.0), _row(17, "run", 50.0), _row(10, "run", 49.0), _row(3, "run", 48.0)]
    t = _trend(rows)
    assert t["weekly_avg"] == [51.0, 50.0, 49.0, 48.0]
    assert t["current"] == 48.0
    assert t["change"] == -3.0
    assert t["direction"] == "declining"


def test_a_week_with_several_readings_is_averaged():
    rows = [_row(24, "run", 50.0), _row(3, "run", 48.0), _row(5, "run", 49.0)]
    assert _trend(rows)["weekly_avg"] == [50.0, None, None, 48.5]


def test_an_empty_week_is_null_and_the_ends_still_compare():
    """Garmin emits a day only when it has an estimate, so gaps are normal."""
    t = _trend([_row(24, "run", 52.0), _row(3, "run", 49.0)])
    assert t["weekly_avg"] == [52.0, None, None, 49.0]
    assert t["change"] == -3.0
    assert t["direction"] == "declining"


def test_the_newest_reading_is_current_whatever_order_the_rows_arrive_in():
    rows = [_row(3, "run", 48.0), _row(24, "run", 52.0), _row(17, "run", 50.0)]
    assert _trend(rows)["current"] == 48.0


def test_a_reading_from_today_lands_in_the_newest_bucket():
    """The midnight-UTC stamp IS the calendar day; no local conversion."""
    assert _trend([_row(0, "run", 47.0)])["weekly_avg"] == [None, None, None, 47.0]


def test_readings_outside_the_window_are_ignored():
    assert pb._vo2max_trend([_row(30, "run", 55.0)], TODAY, DEFAULT) == {}


# --- classifying the direction ----------------------------------------------
def test_a_rise_is_named_as_one():
    t = _trend([_row(24, "run", 48.0), _row(3, "run", 51.0)])
    assert t["change"] == 3.0
    assert t["direction"] == "rising"


def test_a_change_inside_the_deadband_is_stable():
    t = _trend([_row(24, "run", 50.0), _row(3, "run", 49.7)])
    assert t["change"] == -0.3
    assert t["direction"] == "stable"  # Garmin's noise, not a trend


def test_a_drop_of_exactly_the_threshold_is_declining():
    """Pins the boundary: the comparison is <=, not <."""
    t = _trend([_row(24, "run", 50.0), _row(3, "run", 50.0 - DEFAULT)])
    assert t["change"] == -DEFAULT
    assert t["direction"] == "declining"


def test_the_athletes_threshold_decides_what_counts_as_a_decline():
    """The whole point of the Settings field: the same drop, two verdicts."""
    rows = [_row(24, "run", 50.0), _row(3, "run", 49.7)]
    assert _trend(rows, threshold=0.5)["direction"] == "stable"
    assert _trend(rows, threshold=0.2)["direction"] == "declining"


def test_one_populated_week_is_insufficient_data():
    t = _trend([_row(3, "run", 50.0), _row(5, "run", 50.0)])
    assert t["direction"] == "insufficient_data"
    # null, not 0.0 -- a zero reads as a measured "no change".
    assert t["change"] is None
    assert t["current"] == 50.0


def test_a_series_that_stops_early_is_insufficient_data():
    """
    The athlete sells the power meter: two weeks of cycling readings, then
    nothing. Three-week-old averages must not schedule Z5 intervals.
    """
    t = _trend([_row(24, "bike", 48.5), _row(17, "bike", 47.8)], discipline="bike")
    assert t["direction"] == "insufficient_data"
    assert t["change"] is None


def test_a_reading_inside_the_freshness_window_is_still_classified():
    t = _trend([_row(24, "run", 50.0), _row(10, "run", 48.0)])
    assert t["direction"] == "declining"


def test_every_direction_is_reachable():
    got = {
        _trend([_row(24, "run", 50.0), _row(3, "run", 48.0)])["direction"],
        _trend([_row(24, "run", 48.0), _row(3, "run", 50.0)])["direction"],
        _trend([_row(24, "run", 50.0), _row(3, "run", 49.9)])["direction"],
        _trend([_row(3, "run", 50.0)])["direction"],
    }
    assert got == set(DIRECTIONS)


# --- disciplines ------------------------------------------------------------
def test_the_disciplines_are_kept_separate():
    """The real query returns both series interleaved in one list."""
    rows = [
        _row(24, "run", 50.0),
        _row(24, "bike", 44.0),
        _row(3, "run", 48.0),
        _row(3, "bike", 46.0),
    ]
    trend = pb._vo2max_trend(rows, TODAY, DEFAULT)
    assert trend["run"]["direction"] == "declining"
    assert trend["bike"]["direction"] == "rising"


def test_a_discipline_with_no_readings_is_absent():
    """Absent means no information -- never a decline to zero."""
    trend = pb._vo2max_trend([_row(24, "bike", 44.0), _row(3, "bike", 43.0)], TODAY, DEFAULT)
    assert "bike" in trend
    assert "run" not in trend


def test_a_discipline_the_app_does_not_know_is_dropped():
    assert pb._vo2max_trend([_row(3, "hiking", 44.0)], TODAY, DEFAULT) == {}


# --- rows that are not readings ---------------------------------------------
def test_no_rows_gives_nothing():
    assert pb._vo2max_trend([], TODAY, DEFAULT) == {}


def test_workout_rows_are_skipped_silently():
    """
    Every other payload test's FakeRepo ignores the flux and answers this read
    with workout rows, which carry a `discipline` too. Skipping them is what
    keeps test_post_race_recovery, test_coach_instructions, test_athlete_profile
    and test_schedule_store green without a new fake.
    """
    rows = [
        {"date": TODAY.isoformat(), "discipline": "run", "tss": 80, "duration_sec": 3600},
        {"date": TODAY.isoformat(), "discipline": "bike", "tss": 120, "duration_sec": 5400},
    ]
    assert pb._vo2max_trend(rows, TODAY, DEFAULT) == {}


@pytest.mark.parametrize("value", [None, "n/a", ""])
def test_rows_with_no_usable_value_are_skipped(value):
    row = _row(3, "run", 50.0)
    row["vo2max"] = value
    assert pb._vo2max_trend([row], TODAY, DEFAULT) == {}


# --- the payload ------------------------------------------------------------
def _payload(monkeypatch, *, vo2max=(), workouts=(), threshold=DEFAULT, legacy_fake=False):
    seen = []

    class FakeRepo:
        """
        Answers per read, unlike the other payload fakes in the suite. The
        measurement name is the discriminator, before the pivot.
        """

        def query(self, flux):
            seen.append(flux)
            if not legacy_fake and '_measurement == "vo2max"' in flux:
                return list(vo2max)
            return list(workouts)

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb, "drop_split_brick_parents", lambda rows: rows)
    monkeypatch.setattr(
        pb, "get_athlete_profile", lambda: {**PROFILE, "vo2max_decline_threshold": threshold}
    )
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    # get_schedule reads (and on a fresh clone writes) the real
    # data/schedule.json; the factory week keeps the payload deterministic.
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_plan_payload(), seen


def test_payload_carries_the_trend(monkeypatch):
    rows = [_row(24, "run", 51.0), _row(3, "run", 48.0)]
    payload, _ = _payload(monkeypatch, vo2max=rows)
    assert payload["vo2max_trend"]["run"]["direction"] == "declining"


def test_payload_omits_the_key_without_readings(monkeypatch):
    payload, _ = _payload(monkeypatch)
    assert "vo2max_trend" not in payload


def test_only_the_trend_reaches_claude(monkeypatch):
    """
    The athlete's ask: the trend, not the 28 daily points. Pinned so nobody
    slips the daily series into the payload later.
    """
    rows = [_row(d, "run", 50.0 - d / 10) for d in range(28)]
    payload, _ = _payload(monkeypatch, vo2max=rows)
    block = payload["vo2max_trend"]["run"]
    assert set(block) == {"weekly_avg", "current", "change", "direction"}
    assert len(block["weekly_avg"]) == pb.VO2MAX_TREND_WEEKS


def test_payload_honours_the_profile_threshold(monkeypatch):
    rows = [_row(24, "run", 50.0), _row(3, "run", 49.7)]
    stable, _ = _payload(monkeypatch, vo2max=rows, threshold=0.5)
    declining, _ = _payload(monkeypatch, vo2max=rows, threshold=0.2)
    assert stable["vo2max_trend"]["run"]["direction"] == "stable"
    assert declining["vo2max_trend"]["run"]["direction"] == "declining"


def test_the_trend_uses_the_existing_query(monkeypatch):
    """Nobody hand-rolls a second Flux for this."""
    _, seen = _payload(monkeypatch, vo2max=[_row(3, "run", 50.0)])
    assert pb.vo2max_last_n_days(pb.VO2MAX_TREND_DAYS) in seen


def test_the_window_matches_the_buckets():
    assert pb.VO2MAX_TREND_DAYS == pb.VO2MAX_TREND_WEEKS * 7
    assert pb.VO2MAX_TREND_FRESH_WEEKS < pb.VO2MAX_TREND_WEEKS


def test_a_fake_that_ignores_the_flux_leaves_the_key_out(monkeypatch):
    """The regression guard for the suite's four other payload test files."""
    workouts = [{"date": TODAY.isoformat(), "discipline": "run", "tss": 80, "duration_sec": 3600}]
    payload, _ = _payload(monkeypatch, workouts=workouts, legacy_fake=True)
    assert "vo2max_trend" not in payload


# --- the prompt -------------------------------------------------------------
def _prompt() -> str:
    return claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _flat() -> str:
    """Re-wrapping a paragraph must not break a prose assertion."""
    return " ".join(_prompt().split())


def test_the_section_sits_after_rule_20_and_before_the_json_instruction():
    text = _prompt()
    heading = text.index("VO2max trend (vo2max_trend):")
    assert text.index("\n20. ") < heading < text.index("Respond ONLY in the JSON")


@pytest.mark.parametrize("rule", ["21", "22", "23"])
def test_each_vo2max_rule_appears_once(rule):
    assert len(re.findall(rf"^{rule}\. ", _prompt(), re.MULTILINE)) == 1


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_every_direction_the_code_emits_is_named_in_the_prompt(direction):
    assert f'"{direction}"' in _prompt()


def test_the_rule_converts_a_session_instead_of_adding_one():
    """Adding one would fight max_sessions, the week limits and 80/20."""
    flat = _flat()
    assert "never add a session for it" in flat
    assert "rules 2, 3 and 6 and still bind" in flat


def test_the_goal_still_picks_the_sport():
    assert "Rule 4 still picks the sport" in _flat()


def test_the_rule_yields_to_recovery_and_fatigue():
    flat = _flat()
    assert "rules 17-20" in flat
    assert "current_load.tsb is below -25" in flat


def test_the_prompt_points_at_the_athletes_own_threshold():
    assert "athlete_profile.vo2max_decline_threshold" in _flat()


def test_the_section_uses_the_athlete_facing_unit():
    # The pace-unit ban over this whole file lives in test_training_zones_prompt.
    assert "ml/kg/min" in _prompt()


def test_the_vo2max_rules_reach_the_built_prompt():
    system = claude._build_plan_system({"primary_goal": {"type": "marathon"}})
    assert "VO2max trend (vo2max_trend):" in system
