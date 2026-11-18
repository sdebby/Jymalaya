"""
The modal's graph data (GET /workouts/{id}/streams over
analysis_service.workout_streams): stored samples bucketed, power zones for a
ride only, and a 404 when nothing is stored. No InfluxDB, Garmin or Claude.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import analysis_service
from main import app

api = TestClient(app, base_url="http://testserver/api")


@pytest.fixture
def stub(monkeypatch):
    state = {"workout": {"activity_id": "1", "discipline": "bike", "duration_sec": 120}, "rows": []}
    monkeypatch.setattr(analysis_service, "InfluxRepo", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(analysis_service, "load_stored_workout", lambda repo, aid: state["workout"])
    monkeypatch.setattr(analysis_service, "stored_samples", lambda repo, aid: state["rows"])
    monkeypatch.setattr(analysis_service, "get_athlete_profile", lambda: {"ftp_w": 200})
    return state


def test_ride_returns_buckets_and_power_zones(stub):
    stub["rows"] = [{"t": t, "hr": 130, "power_w": 150, "altitude_m": 40} for t in range(0, 120, 5)]
    res = api.get("/workouts/1/streams")
    assert res.status_code == 200
    body = res.json()
    assert body["bucket_sec"] == 30
    assert [s["t"] for s in body["samples"]] == [0, 30, 60, 90]
    assert body["samples"][0]["elevation_m"] == 40
    # 150 W on a 200 W FTP is 75%: Z2.
    assert body["power_zones"]["Z2"] == 120
    assert body["power_zones"]["Z4"] == 0


def test_run_has_no_power_zones(stub):
    stub["workout"] = {"activity_id": "1", "discipline": "run", "duration_sec": 60}
    stub["rows"] = [{"t": 0, "hr": 130, "power_w": 240}]
    assert api.get("/workouts/1/streams").json()["power_zones"] is None


def test_no_samples_is_404(stub):
    assert api.get("/workouts/1/streams").status_code == 404


def test_unknown_workout_is_404(stub):
    stub["workout"] = None
    assert api.get("/workouts/1/streams").status_code == 404


def test_bad_id_is_rejected(stub):
    assert api.get("/workouts/..%2Fx/streams").status_code in (404, 422)
