"""
HRV and resting HR: Garmin's two range payloads are parsed defensively, fetched
in chunks without ever raising, stored as one midnight-UTC daily `recovery`
series, pulled by the sync back to the first stored activity the first time and
incrementally after that, served by GET /metrics/recovery -- and kept out of
every Claude payload. Fakes only -- no Garmin, no InfluxDB, no network.

The payloads below are trimmed from a live dry run on 2026-10-20
(scripts/backfill_recovery.py): the wellness statistics service filtered to
metric 60, and the nightly HRV summary list.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.influx.client as influx_client
import app.routers.health as health_router
import app.schedule_store as store
from app import sync_service
from app.garmin import personal_api
from app.garmin.client import parse_hrv
from app.garmin.personal_api import parse_daily_rhr, pull_recovery
from main import app

client = TestClient(app, base_url="http://testserver/api")


# /usersummary-service/v2/dailysummaries, trimmed to what is read.
RHR_PAYLOAD = {
    "data": [
        {"calendarDate": "2026-09-23", "heartRate": {"restingValue": 41}},
        {"calendarDate": "2026-09-24", "heartRate": {}},
        {"calendarDate": "2026-09-25", "heartRate": {"restingValue": 43}},
    ]
}

HRV_PAYLOAD = {
    "hrvSummaries": [
        {
            "calendarDate": "2026-09-23",
            "weeklyAvg": 59,
            "lastNightAvg": 57,
            "lastNight5MinHigh": 96,
            "status": "BALANCED",
        },
        {
            "calendarDate": "2026-09-25",
            "weeklyAvg": 59,
            "lastNightAvg": 49,
            "lastNight5MinHigh": 70,
            "status": "BALANCED",
        },
    ]
}


# --- parsers ---------------------------------------------------------------
def test_parse_rhr_reads_the_daily_summaries_and_skips_empty_days():
    assert parse_daily_rhr(RHR_PAYLOAD) == [
        {"date": "2026-09-23", "resting_hr": 41.0},
        {"date": "2026-09-25", "resting_hr": 43.0},
    ]


def test_parse_hrv_takes_the_overnight_average_not_the_weekly_or_peak():
    assert parse_hrv(HRV_PAYLOAD) == [
        {"date": "2026-09-23", "hrv": 57.0},
        {"date": "2026-09-25", "hrv": 49.0},
    ]


def test_parse_hrv_reads_the_single_day_shape_too():
    assert parse_hrv({"hrvSummary": {"calendarDate": "2026-10-20", "lastNightAvg": 80}}) == [
        {"date": "2026-10-20", "hrv": 80.0}
    ]


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, "nope", [None], {"data": None}, {"hrvSummaries": None}],
)
def test_degenerate_payloads_parse_to_nothing(payload):
    assert parse_daily_rhr(payload) == []
    assert parse_hrv(payload) == []


@pytest.mark.parametrize("value", [0, 0.0, -3, "", "abc", 999])
def test_zero_and_implausible_readings_are_gaps(value):
    rhr = {"data": [{"calendarDate": "2026-09-23", "heartRate": {"restingValue": value}}]}
    hrv = {"hrvSummaries": [{"calendarDate": "2026-09-23", "lastNightAvg": value}]}
    assert parse_daily_rhr(rhr) == []
    assert parse_hrv(hrv) == []


def test_bad_dates_are_skipped_and_a_repeated_day_keeps_the_last_value():
    hrv = {"hrvSummaries": [
        {"calendarDate": "not-a-date", "lastNightAvg": 60},
        {"calendarDate": "2026-09-23", "lastNightAvg": 55},
        {"calendarDate": "2026-09-23", "lastNightAvg": "58"},
    ]}
    assert parse_hrv(hrv) == [{"date": "2026-09-23", "hrv": 58.0}]


# --- pull_recovery (Personal API) ------------------------------------------
class FakeApi:
    """Stands in for personal_api._json: records each request, answers fixtures."""

    def __init__(self, boom: str | None = None):
        self.calls: list[tuple[str, dict | None]] = []
        self.boom = boom

    def __call__(self, path, params=None):
        self.calls.append((path, params))
        if self.boom and self.boom in path:
            raise personal_api.PersonalApiError("garmin_error", "500", personal_api.ACTION_RETRY)
        if path.startswith("/usersummary-service"):
            return RHR_PAYLOAD
        return HRV_PAYLOAD


@pytest.fixture
def fake_api(monkeypatch):
    def make(boom=None):
        fake = FakeApi(boom)
        monkeypatch.setattr(personal_api, "_json", fake)
        monkeypatch.setenv(personal_api.KEY_ENV, "test-key-not-real")
        return fake
    return make


def _spans(fake: FakeApi) -> tuple[list, list]:
    rhr = [(p["startDate"], p["endDate"]) for path, p in fake.calls if p]
    hrv = [tuple(path.split("/")[-2:]) for path, p in fake.calls if not p]
    return rhr, hrv


def test_pull_merges_both_series_per_day(fake_api):
    fake_api()
    rows = pull_recovery(date(2026, 9, 23), date(2026, 9, 25))
    assert rows == [
        {"date": "2026-09-23", "hrv": 57.0, "resting_hr": 41.0},
        {"date": "2026-09-25", "hrv": 49.0, "resting_hr": 43.0},
    ]


def test_resting_hr_comes_from_the_daily_summaries(fake_api):
    fake = fake_api()
    pull_recovery(date(2026, 9, 23), date(2026, 9, 25))
    path, params = next(c for c in fake.calls if c[1])
    assert path == "/usersummary-service/v2/dailysummaries"
    assert params == {"startDate": "2026-09-23", "endDate": "2026-09-25"}


def test_a_long_backfill_is_chunked_with_no_gaps_or_overlaps(fake_api):
    fake = fake_api()
    start, end = date(2026, 1, 29), date(2026, 10, 20)
    pull_recovery(start, end)
    for spans in _spans(fake):
        days = [date.fromisoformat(a) for a, _ in spans]
        ends = [date.fromisoformat(b) for _, b in spans]
        assert days[0] == start and ends[-1] == end
        for prev_end, nxt in zip(ends, days[1:]):
            assert nxt == prev_end + timedelta(days=1)
        assert all((b - a).days < personal_api.CHUNK_DAYS for a, b in zip(days, ends))


def test_a_failing_endpoint_never_raises_and_keeps_the_other_series(fake_api):
    fake_api(boom="hrv")
    rows = pull_recovery(date(2026, 9, 23), date(2026, 9, 25))
    assert [r["hrv"] for r in rows] == [None, None]
    assert [r["resting_hr"] for r in rows] == [41.0, 43.0]


def test_a_start_after_the_end_requests_nothing(fake_api):
    fake = fake_api()
    assert pull_recovery(date(2026, 10, 21), date(2026, 10, 20)) == []
    assert fake.calls == []


def test_pull_skips_entirely_without_a_key(monkeypatch):
    monkeypatch.delenv(personal_api.KEY_ENV, raising=False)
    monkeypatch.setattr(personal_api, "_json", lambda *a, **k: pytest.fail("must not request"))
    assert pull_recovery(date(2026, 9, 1)) == []


# --- write_recovery --------------------------------------------------------
class _FakeWriteApi:
    def __init__(self):
        self.records = []

    def write(self, bucket, org, record):
        self.records.extend(record if isinstance(record, list) else [record])


class _FakeInfluxDBClient:
    def __init__(self, url=None, token=None, org=None):
        self.api = _FakeWriteApi()

    def write_api(self, write_options=None):
        return self.api

    def query_api(self):
        return None

    def close(self):
        pass


def test_write_recovery_writes_only_the_fields_present(monkeypatch):
    monkeypatch.setattr(influx_client, "InfluxDBClient", _FakeInfluxDBClient)
    repo = influx_client.InfluxRepo()
    n = repo.write_recovery(
        [
            {"date": "2026-09-23", "hrv": 57.0, "resting_hr": 41.0},
            {"date": "2026-09-24", "hrv": None, "resting_hr": 42.0},
            {"date": "2026-09-25", "hrv": None, "resting_hr": None},
            {"date": "", "hrv": 60.0, "resting_hr": 40.0},
        ]
    )
    assert n == 2
    lines = [p.to_line_protocol() for p in repo._write.records]
    midnight = int(datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp())
    assert lines[0] == f"recovery hrv=57,resting_hr=41 {midnight}"
    # An RHR-only day must not write an hrv field, or it would blank a stored one.
    assert lines[1].startswith("recovery resting_hr=42 ")


# --- GET /metrics/recovery -------------------------------------------------
def test_endpoint_returns_the_stored_rows(monkeypatch):
    seen = {}

    class FakeRepo:
        def query(self, flux):
            seen["flux"] = flux
            return [{"time": datetime(2026, 9, 23, tzinfo=timezone.utc), "hrv": 57.0}]

        def close(self):
            pass

    monkeypatch.setattr(health_router, "InfluxRepo", FakeRepo)
    resp = client.get("/metrics/recovery?days=28")
    assert resp.status_code == 200
    assert '_measurement == "recovery"' in seen["flux"]
    assert "-28d" in seen["flux"]
    assert resp.json()[0]["time"].startswith("2026-09-23T00:00:00")


# --- the sync window ---------------------------------------------------------
class WindowRepo:
    def __init__(self, stored_recovery: bool, oldest: datetime | None):
        self.stored_recovery = stored_recovery
        self.oldest = oldest

    def query(self, flux):
        if '_measurement == "recovery"' in flux:
            return [{"time": datetime.now(timezone.utc), "_value": 50.0}] if self.stored_recovery else []
        if "first()" in flux:
            return [{"time": self.oldest, "_value": False}] if self.oldest else []
        return []


def test_first_pull_reaches_back_to_the_first_stored_activity():
    oldest = datetime(2026, 5, 30, 4, 0, tzinfo=timezone.utc)
    start = sync_service._recovery_start(WindowRepo(False, oldest), [], days=28)
    assert start == date(2026, 5, 29)


def test_first_pull_on_a_fresh_install_uses_the_activities_just_pulled():
    raw = [{"startTimeLocal": "2026-09-01 07:00:00"}, {"startTimeLocal": "garbage"}]
    start = sync_service._recovery_start(WindowRepo(False, None), raw, days=60)
    assert start == min(date(2026, 9, 1), date.today() - timedelta(days=59))


def test_later_pulls_use_the_incremental_last_sync_window():
    oldest = datetime(2026, 5, 30, tzinfo=timezone.utc)
    start = sync_service._recovery_start(WindowRepo(True, oldest), [], days=4)
    assert start == date.today() - timedelta(days=3)


def test_an_unreadable_store_falls_back_to_the_incremental_window():
    class Broken:
        def query(self, flux):
            raise RuntimeError("influx down")

    assert sync_service._recovery_start(Broken(), [], days=4) == date.today() - timedelta(days=3)


def _run_sync(monkeypatch, tmp_path, pull):
    written = {}

    class FakeRepo:
        def query(self, flux):
            return []

        def write_workouts(self, w):
            return 0

        def write_daily_load(self, rows):
            pass

        def write_vo2max(self, rows):
            return 0

        def write_recovery(self, rows):
            written["rows"] = rows
            return len(rows)

        def close(self):
            pass

    import app.recommend_service as reco

    monkeypatch.setattr(reco, "run_recommendation", lambda trigger, **kw: None)
    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: False)
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: [])
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])
    monkeypatch.setattr(sync_service, "pull_recovery", pull)
    return sync_service.run_sync(), written


def test_the_sync_writes_what_it_pulled(monkeypatch, tmp_path):
    rows = [{"date": "2026-09-23", "hrv": 57.0, "resting_hr": 41.0}]
    summary, written = _run_sync(monkeypatch, tmp_path, lambda start, end=None: rows)
    assert written["rows"] == rows
    assert summary["recovery_points"] == 1


def test_a_recovery_failure_does_not_fail_the_sync(monkeypatch, tmp_path):
    def boom(start, end=None):
        raise RuntimeError("hrv 500")

    summary, _ = _run_sync(monkeypatch, tmp_path, boom)
    assert summary["status"] == "ok"
    assert summary["recovery_points"] == 0


# --- not shared with the coach ---------------------------------------------
@pytest.fixture
def recovery_everywhere(monkeypatch):
    """Every Claude payload builder, with recovery rows answering any read."""
    from app.ai import prompt_builder as pb

    row = {
        "time": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
        "hrv": 61.0,
        "resting_hr": 47.0,
        "sleep_sec": 26640.0,  # tests/test_sleep.py pins that this stays out too
    }

    class FakeRepo:
        def query(self, flux):
            return [dict(row)] if '_measurement == "recovery"' in flux else []

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb, "get_athlete_profile", lambda: {"ftp_w": 250, "lthr_bpm": 162})
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb


def test_no_recovery_data_reaches_claude(recovery_everywhere):
    """The athlete's call (2026-10-20): the charts only, not the coach -- yet."""
    pb = recovery_everywhere
    texts = [json.dumps(pb.build_plan_payload(), default=str), pb.build_coach_context()]
    for text in texts:
        lowered = text.lower()
        assert "hrv" not in lowered
        assert "resting_hr" not in lowered
        assert "resting hr" not in lowered
