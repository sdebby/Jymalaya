"""
POST /device/send: a cached plan session sent to intervals.icu. The client is
faked (no network); the 503 test uses the real one, which stops at the missing
key before any request is made.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import plan_editor
from app.intervals import client as intervals_client
from app.intervals.client import IntervalsError
from app.routers import device
from main import app

api = TestClient(app, base_url="http://testserver/api")

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _plan() -> dict:
    run = {
        "discipline": "run",
        "title": "Easy run",
        "duration_min": 40,
        "estimated_tss": 35,
        "structure": [{"segment": "Main", "duration_min": 40, "zone": "Z2"}],
    }
    return {
        "days": [
            {"date": YESTERDAY, "day_name": "Yesterday", "is_rest_day": False, "sessions": [dict(run)]},
            {"date": TOMORROW, "day_name": "Tomorrow", "is_rest_day": False, "sessions": [dict(run)]},
        ]
    }


@pytest.fixture()
def plan_file(tmp_path, monkeypatch):
    f = tmp_path / "latest_recommendation.json"
    f.write_text(json.dumps(_plan()), encoding="utf-8")
    monkeypatch.setattr(plan_editor, "LATEST_FILE", f)
    return f


@pytest.fixture()
def sent(monkeypatch):
    """Record every event the route hands to intervals.icu."""
    events = []

    def fake_upsert(event):
        events.append(event)
        return {"id": 4242, "name": event["name"], "moving_time": 2400}

    monkeypatch.setattr(device, "upsert_workout", fake_upsert)
    return events


def test_sends_the_session(plan_file, sent):
    r = api.post("/device/send", json={"date": TOMORROW, "session_index": 0})
    assert r.status_code == 200
    assert r.json() == {
        "event_id": 4242,
        "name": "Easy run",
        "date": TOMORROW,
        "external_id": f"jymalaya-{TOMORROW}-0",
        "moving_time_sec": 2400,
    }
    assert len(sent) == 1
    assert sent[0]["type"] == "Run"
    assert "- Main 40m 80-89% LTHR" in sent[0]["description"]


def test_send_does_not_touch_the_plan(plan_file, sent):
    before = plan_file.read_text(encoding="utf-8")
    api.post("/device/send", json={"date": TOMORROW, "session_index": 0})
    assert plan_file.read_text(encoding="utf-8") == before


def test_no_plan_is_404(tmp_path, monkeypatch, sent):
    monkeypatch.setattr(plan_editor, "LATEST_FILE", tmp_path / "missing.json")
    r = api.post("/device/send", json={"date": TOMORROW, "session_index": 0})
    assert r.status_code == 404
    assert sent == []


@pytest.mark.parametrize(
    "body",
    [{"date": "2099-01-01", "session_index": 0}, {"date": TOMORROW, "session_index": 3}],
)
def test_unknown_day_or_session_is_404(plan_file, sent, body):
    assert api.post("/device/send", json=body).status_code == 404
    assert sent == []


@pytest.mark.parametrize(
    "body",
    [{"date": "tomorrow", "session_index": 0}, {"date": TOMORROW, "session_index": -1}],
)
def test_malformed_body_is_422(plan_file, sent, body):
    assert api.post("/device/send", json=body).status_code == 422


def test_a_passed_day_is_refused(plan_file, sent):
    r = api.post("/device/send", json={"date": YESTERDAY, "session_index": 0})
    assert r.status_code == 409
    assert sent == []


def test_missing_key_is_503(plan_file, monkeypatch):
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("no request may be made without a key")

    monkeypatch.setattr(intervals_client.httpx, "post", no_network)
    r = api.post("/device/send", json={"date": TOMORROW, "session_index": 0})
    assert r.status_code == 503
    assert "INTERVALS_API_KEY" in r.json()["detail"]


def test_intervals_failure_is_502(plan_file, monkeypatch):
    def failing(event):
        raise IntervalsError("intervals.icu refused the workout (HTTP 400).")

    monkeypatch.setattr(device, "upsert_workout", failing)
    r = api.post("/device/send", json={"date": TOMORROW, "session_index": 0})
    assert r.status_code == 502
    assert "HTTP 400" in r.json()["detail"]
