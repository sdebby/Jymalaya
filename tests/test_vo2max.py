"""
VO2max: Garmin's max-metrics payload is parsed defensively, fetched in chunks
without ever raising, stored as a midnight-UTC daily series tagged by
discipline, and served by GET /metrics/vo2max. Fakes only -- no Garmin, no
InfluxDB, no network.

The REALISTIC payload below is trimmed from a real
`scripts/backfill_vo2max.py --days 7 --debug` dump (2026-08-26): a list of
per-day entries, `generic` for running and `cycling` for the bike, each with
calendarDate plus both a precise and a rounded value. Garmin emits an entry only
for days it has data for and sends `cycling: null` on days with no qualifying
ride -- which is why the parser tolerates both and the dashboard forward-fills.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.influx.client as influx_client
import app.routers.health as health_router
from app import sync_service
from app.garmin import client as garmin_client
from app.garmin.client import fetch_vo2max, parse_max_metrics
from main import app

# base_url carries the /api prefix the routers are mounted under.
client = TestClient(app, base_url="http://testserver/api")


REALISTIC = [
    {
        "userId": 93155902,
        "generic": {
            "calendarDate": "2026-08-22",
            "vo2MaxPreciseValue": 46.9,
            "vo2MaxValue": 47.0,
            "fitnessAge": None,
            "fitnessAgeDescription": None,
            "maxMetCategory": 0,
        },
        "cycling": {
            "calendarDate": "2026-08-22",
            "vo2MaxPreciseValue": 47.9,
            "vo2MaxValue": 48.0,
            "fitnessAge": None,
            "maxMetCategory": 1,
        },
        "heatAltitudeAcclimation": {"calendarDate": "2026-08-22", "heatTrend": "ACCLIMATIZED"},
    },
    # 2026-08-23 is absent entirely -- Garmin skips days with no estimate.
    {
        "userId": 93155902,
        "generic": {
            "calendarDate": "2026-08-24",
            "vo2MaxPreciseValue": 46.8,
            "vo2MaxValue": 47.0,
            "maxMetCategory": 0,
        },
        "cycling": None,
    },
]


# --- parse_max_metrics -----------------------------------------------------
def test_parses_both_sports_oldest_first():
    assert parse_max_metrics(REALISTIC) == [
        {"date": "2026-08-22", "discipline": "bike", "vo2max": 47.9},
        {"date": "2026-08-22", "discipline": "run", "vo2max": 46.9},
        {"date": "2026-08-24", "discipline": "run", "vo2max": 46.8},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        [None],
        ["nonsense"],
        [{"generic": None, "cycling": None}],
        # Garmin's "no estimate yet" -- a zero would chart as a real point.
        [{"generic": {"calendarDate": "2026-08-24", "vo2MaxValue": 0}}],
        # No calendar day anywhere: the row cannot be placed, so it is dropped.
        [{"generic": {"vo2MaxValue": 52.0}}],
        [{"generic": {"calendarDate": "not-a-date", "vo2MaxValue": 52.0}}],
    ],
)
def test_degenerate_payloads_yield_nothing_and_never_raise(payload):
    assert parse_max_metrics(payload) == []


def test_bare_dict_top_level_is_one_entry():
    payload = {"generic": {"calendarDate": "2026-08-24", "vo2MaxValue": 51.0}}
    assert parse_max_metrics(payload) == [
        {"date": "2026-08-24", "discipline": "run", "vo2max": 51.0}
    ]


def test_falls_back_to_the_rounded_value_and_coerces_strings():
    payload = [{"generic": {"calendarDate": "2026-08-24", "vo2MaxValue": "51.5"}}]
    assert parse_max_metrics(payload) == [
        {"date": "2026-08-24", "discipline": "run", "vo2max": 51.5}
    ]


def test_calendar_date_on_the_entry_is_used_when_the_block_has_none():
    payload = [{"calendarDate": "2026-08-24", "generic": {"vo2MaxPreciseValue": 52.3}}]
    assert parse_max_metrics(payload) == [
        {"date": "2026-08-24", "discipline": "run", "vo2max": 52.3}
    ]


def test_a_repeated_day_cannot_duplicate():
    payload = [
        {"generic": {"calendarDate": "2026-08-24", "vo2MaxValue": 51.0}},
        {"generic": {"calendarDate": "2026-08-24", "vo2MaxValue": 52.0}},
    ]
    assert parse_max_metrics(payload) == [
        {"date": "2026-08-24", "discipline": "run", "vo2max": 52.0}
    ]


# --- fetch_vo2max ----------------------------------------------------------
class FakeClient:
    def __init__(self, payload=None, boom=False):
        self.paths: list[str] = []
        self.payload = payload if payload is not None else []
        self.boom = boom

    def connectapi(self, path):
        self.paths.append(path)
        if self.boom:
            raise RuntimeError("garmin 500")
        return self.payload


def _spans(paths: list[str]) -> list[tuple[str, str]]:
    return [tuple(p.rsplit("/", 2)[-2:]) for p in paths]


def test_a_short_span_is_a_single_request():
    c = FakeClient()
    fetch_vo2max(c, days=7)
    assert len(c.paths) == 1
    start, end = _spans(c.paths)[0]
    assert start == (date.today() - timedelta(days=6)).isoformat()
    assert end == date.today().isoformat()


def test_a_long_span_is_chunked_without_gaps_or_overlap():
    c = FakeClient()
    fetch_vo2max(c, days=60)
    spans = _spans(c.paths)
    assert len(spans) == 3
    assert spans[0][0] == (date.today() - timedelta(days=59)).isoformat()
    assert spans[-1][1] == date.today().isoformat()
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        gap = date.fromisoformat(next_start) - date.fromisoformat(prev_end)
        assert gap == timedelta(days=1)


def test_fetch_never_raises_when_garmin_fails():
    assert fetch_vo2max(FakeClient(boom=True), days=28) == []


def test_pull_skips_entirely_without_a_cached_session(monkeypatch, tmp_path):
    # A credential login here could park a best-effort call inside the MFA wait.
    monkeypatch.setattr(garmin_client, "TOKEN_STORE", tmp_path / "nope")
    monkeypatch.setattr(
        garmin_client, "_connect", lambda: pytest.fail("must not connect")
    )
    assert garmin_client.pull_vo2max() == []


# --- write_vo2max ----------------------------------------------------------
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


def test_write_vo2max_tags_discipline_and_skips_unusable_rows(monkeypatch):
    monkeypatch.setattr(influx_client, "InfluxDBClient", _FakeInfluxDBClient)
    repo = influx_client.InfluxRepo()
    n = repo.write_vo2max(
        [
            {"date": "2026-08-24", "discipline": "run", "vo2max": 52.3},
            {"date": "2026-08-24", "discipline": "bike", "vo2max": 48.2},
            {"date": "2026-08-25", "discipline": "run", "vo2max": None},
            {"date": "", "discipline": "run", "vo2max": 51.0},
        ]
    )
    assert n == 2
    lines = [p.to_line_protocol() for p in repo._write.records]
    assert any(line.startswith("vo2max,discipline=run vo2max=52.3") for line in lines)
    assert any(line.startswith("vo2max,discipline=bike vo2max=48.2") for line in lines)


def test_vo2max_points_are_stamped_midnight_utc():
    # The daily-series convention (write_daily_load), not the workout one: the
    # point's UTC date IS the calendar day, so reads must not shift it locally.
    assert influx_client._parse_date("2026-08-24") == datetime(
        2026, 8, 24, tzinfo=timezone.utc
    )


# --- GET /metrics/vo2max ---------------------------------------------------
def test_endpoint_returns_both_series(monkeypatch):
    seen = {}

    class FakeRepo:
        def query(self, flux):
            seen["flux"] = flux
            return [
                {
                    "time": datetime(2026, 8, 24, tzinfo=timezone.utc),
                    "discipline": "run",
                    "vo2max": 52.3,
                },
                {
                    "time": datetime(2026, 8, 24, tzinfo=timezone.utc),
                    "discipline": "bike",
                    "vo2max": 48.2,
                },
            ]

        def close(self):
            pass

    monkeypatch.setattr(health_router, "InfluxRepo", FakeRepo)
    resp = client.get("/metrics/vo2max?days=28")
    assert resp.status_code == 200
    # The substring any new test fake must discriminate on, before "pivot".
    assert "vo2max" in seen["flux"]
    assert [r["discipline"] for r in resp.json()] == ["run", "bike"]
    assert resp.json()[0]["time"].startswith("2026-08-24T00:00:00")


def test_endpoint_is_empty_not_an_error_without_data(monkeypatch):
    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    monkeypatch.setattr(health_router, "InfluxRepo", FakeRepo)
    resp = client.get("/metrics/vo2max")
    assert resp.status_code == 200
    assert resp.json() == []


# --- best-effort inside the sync -------------------------------------------
def test_a_vo2max_failure_does_not_fail_the_sync(monkeypatch, tmp_path):
    """
    The pull reaches an endpoint nothing types, so it is the likeliest step to
    break. A sync that pulled activities fine must still report ok.
    """

    class FakeRepo:
        def query(self, flux):
            return []

        def write_workouts(self, w):
            return 0

        def write_daily_load(self, rows):
            pass

        def write_vo2max(self, rows):
            return len(rows)


        def write_recovery(self, rows):

            return 0

        def close(self):
            pass

    def boom(**kw):
        raise RuntimeError("maxmet 500")

    import app.recommend_service as reco

    monkeypatch.setattr(reco, "run_recommendation", lambda trigger, **kw: None)
    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: False)
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: [])
    monkeypatch.setattr(sync_service, "pull_vo2max", boom)

    summary = sync_service.run_sync()
    assert summary["status"] == "ok"
    assert summary["error"] is None
    assert summary["vo2max_points"] == 0
