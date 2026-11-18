"""
An event the athlete is not participating in stays on the calendar but is never a
goal: the plan, the coach chat and the analysis read the goal only through
active_event / last_race, so those two are where it is left out.

No network: events_service.list_events and the Influx repo are faked.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import app.schedule_store as store
from app import events_service
from app.ai import prompt_builder as pb
from app.events_service import _row_to_event, is_participating
from app.influx.client import InfluxRepo
from app.models import EventIn

TODAY = date.today()


def _iso(days_from_today: int) -> str:
    return (TODAY + timedelta(days=days_from_today)).isoformat()


def _event(event_id: str, race_date: str, **extra) -> dict:
    return {
        "event_id": event_id,
        "name": f"race-{event_id}",
        "type": "10K",
        "race_date": race_date,
        **extra,
    }


def _fake_events(monkeypatch, events: list[dict]) -> None:
    monkeypatch.setattr(events_service, "list_events", lambda: events)


# --- reading -----------------------------------------------------------------
@pytest.mark.parametrize("stored", [None, "", "true", "True"])
def test_row_to_event_defaults_to_participating(stored):
    """Events saved before the field existed have no column: they are racing."""
    row = {"goal_id": "x", "name": "Race", "race_date": "2026-11-01"}
    if stored is not None:
        row["participating"] = stored
    assert _row_to_event(row)["participating"] is True


@pytest.mark.parametrize("stored", ["false", "False", " FALSE "])
def test_row_to_event_reads_an_explicit_opt_out(stored):
    # The query stringifies every value, so a stored bool arrives as text.
    row = {"goal_id": "x", "participating": stored}
    assert _row_to_event(row)["participating"] is False


def test_is_participating_defaults_to_true():
    assert is_participating({}) is True
    assert is_participating({"participating": True}) is True
    assert is_participating({"participating": False}) is False


def test_event_in_defaults_to_participating():
    event = EventIn(name="Race", type="10K", race_date="2026-11-01")
    assert event.participating is True
    assert event.model_dump()["participating"] is True


# --- writing -----------------------------------------------------------------
class _FakeWrite:
    def __init__(self):
        self.records = []

    def write(self, bucket, org, record):
        self.records.append(record)


def _written_line(event: dict) -> str:
    repo = InfluxRepo.__new__(InfluxRepo)  # no connection needed for a write
    repo._bucket, repo._org = "tricoach", "tricoach"
    repo._write = _FakeWrite()
    repo.write_event(event)
    return repo._write.records[0].to_line_protocol()


def test_write_event_stores_the_opt_out():
    assert "participating=false" in _written_line(_event("a", "2026-11-01", participating=False))


def test_write_event_stores_participating_by_default():
    assert "participating=true" in _written_line(_event("a", "2026-11-01"))


# --- active_event ------------------------------------------------------------
def test_active_event_skips_a_calendar_only_event(monkeypatch):
    _fake_events(
        monkeypatch,
        [
            _event("fun", _iso(10), participating=False),
            _event("goal", _iso(40)),
        ],
    )
    assert events_service.active_event()["event_id"] == "goal"


def test_active_event_is_none_when_only_calendar_only_events_remain(monkeypatch):
    _fake_events(monkeypatch, [_event("fun", _iso(10), participating=False)])
    assert events_service.active_event() is None


def test_active_event_still_picks_an_event_with_no_flag(monkeypatch):
    """Dicts without the key (older fixtures, legacy rows) count as racing."""
    _fake_events(monkeypatch, [_event("goal", _iso(10))])
    assert events_service.active_event()["event_id"] == "goal"


# --- last_race ---------------------------------------------------------------
def test_last_race_skips_a_race_the_athlete_was_not_racing(monkeypatch):
    """No recovery window for a finish line they only jogged past."""
    _fake_events(
        monkeypatch,
        [
            _event("skipped", _iso(-2), participating=False),
            _event("raced", _iso(-9)),
        ],
    )
    assert events_service.last_race()["event_id"] == "raced"


def test_last_race_is_none_when_the_only_past_event_was_calendar_only(monkeypatch):
    _fake_events(monkeypatch, [_event("skipped", _iso(-2), participating=False)])
    assert events_service.last_race() is None


# --- what Claude sees --------------------------------------------------------
class _FakeRepo:
    def query(self, flux):
        return []

    def close(self):
        pass


def test_plan_payload_has_no_goal_for_a_calendar_only_event(monkeypatch):
    _fake_events(
        monkeypatch,
        [
            _event("ahead", _iso(14), participating=False),
            _event("behind", _iso(-3), participating=False),
        ],
    )
    monkeypatch.setattr(pb, "InfluxRepo", lambda: _FakeRepo())
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")

    payload = pb.build_plan_payload()

    assert payload["primary_goal"] is None
    assert "recent_race" not in payload
