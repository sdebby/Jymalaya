"""
Nightly sleep: Garmin's one-night payload is parsed defensively, pulled one
request per night without ever raising, stored as a `sleep_sec` field on the
midnight-UTC `recovery` points, backfilled SLEEP_BACKFILL_DAYS on the first
sync and incrementally after that -- and kept out of every Claude payload.
Fakes only -- no Garmin, no InfluxDB, no network.

The payload is trimmed from a live response on 2026-11-13
(/wellness-service/wellness/dailySleep?date=; the sleep-service range endpoint
answered null).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import app.influx.client as influx_client
from app import sync_service
from app.garmin import personal_api
from app.garmin.personal_api import parse_sleep, pull_sleep

from tests.test_recovery import _FakeInfluxDBClient, recovery_everywhere  # noqa: F401

NIGHT = {
    "calendarDate": "2026-11-13",
    "sleepTimeSeconds": 26640,
    "napTimeSeconds": 1920,
    "deepSleepSeconds": 7140,
}


# --- parse_sleep -------------------------------------------------------------
def test_the_night_is_sleep_time_without_naps():
    assert parse_sleep(NIGHT) == {"date": "2026-11-13", "sleep_sec": 26640}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"calendarDate": "2026-11-13", "sleepTimeSeconds": None},
        {"calendarDate": "2026-11-13", "sleepTimeSeconds": 0},
        {"calendarDate": "2026-11-13", "sleepTimeSeconds": 600},  # under an hour
        {"calendarDate": "2026-11-13", "sleepTimeSeconds": 20 * 3600},
        {"calendarDate": "not-a-date", "sleepTimeSeconds": 26640},
    ],
)
def test_missing_or_implausible_nights_are_skipped(payload):
    assert parse_sleep(payload) is None


# --- pull_sleep --------------------------------------------------------------
@pytest.fixture
def fake_api(monkeypatch):
    calls: list[str] = []

    def fake(path, params=None):
        assert path == "/wellness-service/wellness/dailySleep"
        calls.append(params["date"])
        if params["date"] == "2026-11-11":
            raise personal_api.PersonalApiError("garmin_error", "500", personal_api.ACTION_RETRY)
        if params["date"] == "2026-11-12":
            return None  # watch off
        return {**NIGHT, "calendarDate": params["date"]}

    monkeypatch.setattr(personal_api, "_json", fake)
    monkeypatch.setattr(personal_api, "_token", lambda force=False: "tok")
    monkeypatch.setenv(personal_api.KEY_ENV, "test-key-not-real")
    return calls


def test_one_request_per_night_and_failures_are_skipped(fake_api):
    rows = pull_sleep(date(2026, 11, 10), date(2026, 11, 13))
    assert sorted(fake_api) == ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
    assert [r["date"] for r in rows] == ["2026-11-10", "2026-11-13"]


def test_a_start_after_the_end_requests_nothing(fake_api):
    assert pull_sleep(date(2026, 11, 14), date(2026, 11, 13)) == []
    assert fake_api == []


def test_pull_skips_entirely_without_a_key(monkeypatch):
    monkeypatch.delenv(personal_api.KEY_ENV, raising=False)
    monkeypatch.setattr(personal_api, "_json", lambda *a, **k: pytest.fail("must not request"))
    assert pull_sleep(date(2026, 11, 1)) == []


# --- storage -------------------------------------------------------------------
def test_a_sleep_row_writes_only_its_own_field(monkeypatch):
    monkeypatch.setattr(influx_client, "InfluxDBClient", _FakeInfluxDBClient)
    repo = influx_client.InfluxRepo()
    assert repo.write_recovery([{"date": "2026-11-13", "sleep_sec": 26640}]) == 1
    (line,) = [p.to_line_protocol() for p in repo._write.records]
    midnight = int(datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp())
    # No hrv / resting_hr field, so the day's stored readings are kept.
    assert line == f"recovery sleep_sec=26640 {midnight}"


# --- the sync window -----------------------------------------------------------
class SleepRepo:
    def __init__(self, stored: bool):
        self.stored = stored

    def query(self, flux):
        assert '_field == "sleep_sec"' in flux
        return [{"time": datetime.now(timezone.utc), "_value": 26640.0}] if self.stored else []


def test_first_sleep_pull_backfills_the_chart_window():
    start = sync_service._sleep_start(SleepRepo(False), days=4)
    assert start == date.today() - timedelta(days=sync_service.SLEEP_BACKFILL_DAYS - 1)


def test_later_sleep_pulls_use_the_incremental_window():
    assert sync_service._sleep_start(SleepRepo(True), days=4) == date.today() - timedelta(days=3)


def test_hrv_backfill_check_ignores_stored_sleep():
    from app.influx.queries import latest_recovery_point

    assert '_field != "sleep_sec"' in latest_recovery_point()


# --- not shared with the coach ---------------------------------------------------
def test_no_sleep_data_reaches_claude(recovery_everywhere):  # noqa: F811
    pb = recovery_everywhere
    texts = [json.dumps(pb.build_plan_payload(), default=str), pb.build_coach_context()]
    for text in texts:
        assert "sleep_sec" not in text.lower()
