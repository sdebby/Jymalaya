"""
Workout analysis orchestration and caching (app/analysis_service.py) plus the
routes over it (app/routers/analysis.py).

No real Claude, Garmin or InfluxDB: _client() is monkeypatched to a FakeClient
with scripted responses (style follows tests/test_coach_tools.py), the stored
detail readers are stubbed, and the cache directory is redirected to tmp_path.
The analysis makes no Garmin call at all: its detail is read from InfluxDB.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import analysis_service
from app.ai import claude
from app.ai.claude import ClaudeError
from app.routers import analysis as analysis_router
from main import app

# The prefix lives in base_url so the route paths below stay bare (main.py mounts
# every router under /api).
api = TestClient(app, base_url="http://testserver/api")

WORKOUT = {
    "activity_id": "12345",
    "discipline": "run",
    "title": "Morning Run",
    "date": "2026-08-26",
    "duration_sec": 3600,
    "distance_m": 12000,
    "tss": 85,
}

ANALYSIS_JSON = json.dumps(
    {
        "form_context": "Solid aerobic hour.",
        "what_went_well": ["Even pacing"],
        "what_to_improve": [],
        "flags": [],
    }
)


def _resp(text):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A fully stubbed analysis pipeline with the cache in tmp_path."""
    monkeypatch.setattr(analysis_service, "ANALYSIS_DIR", tmp_path / "analyses")

    fake = FakeClient([_resp(ANALYSIS_JSON)])
    monkeypatch.setattr(claude, "_client", lambda: fake)

    monkeypatch.setattr(analysis_service, "InfluxRepo", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(analysis_service, "load_stored_workout", lambda repo, aid: dict(WORKOUT))
    monkeypatch.setattr(analysis_service, "get_athlete_profile", lambda: {"ftp_w": 250})
    monkeypatch.setattr(
        analysis_service,
        "stored_samples",
        lambda repo, aid: [{"t": 0, "hr": 140}, {"t": 5, "hr": 141}, {"t": 30, "hr": 142}],
    )
    monkeypatch.setattr(analysis_service, "stored_laps", lambda repo, aid: [])
    monkeypatch.setattr(analysis_service, "stored_planned", lambda repo, aid: None)
    monkeypatch.setattr(
        analysis_service, "build_analysis_payload", lambda w, **kw: {"workout": w, **kw}
    )
    return fake


def test_analysis_is_generated_and_cached(env):
    out = analysis_service.analyze_workout("12345")

    assert out["form_context"] == "Solid aerobic hour."
    assert len(env.messages.calls) == 1
    # Up-front thinking is off on every Claude call in this repo.
    assert env.messages.calls[0]["thinking"] == claude._thinking_off(claude.MODEL)

    meta = out["_meta"]
    assert meta["activity_id"] == "12345"
    assert meta["trigger"] == "manual"
    assert meta["analysis_id"] and meta["generated_at"]

    assert json.loads((analysis_service.ANALYSIS_DIR / "12345.json").read_text())["form_context"]


def test_cached_analysis_does_not_call_claude(env):
    analysis_service.analyze_workout("12345")
    assert len(env.messages.calls) == 1

    again = analysis_service.analyze_workout("12345")
    assert again["form_context"] == "Solid aerobic hour."
    assert len(env.messages.calls) == 1  # served from the file


def test_force_re_runs_the_analysis(env):
    analysis_service.analyze_workout("12345")
    env.messages.script.append(_resp(json.dumps({"form_context": "Second look."})))

    again = analysis_service.analyze_workout("12345", force=True)
    assert again["form_context"] == "Second look."
    assert len(env.messages.calls) == 2


def test_unknown_workout_raises(env, monkeypatch):
    monkeypatch.setattr(analysis_service, "load_stored_workout", lambda repo, aid: None)
    with pytest.raises(analysis_service.AnalysisError):
        analysis_service.analyze_workout("nope")


def test_a_workout_without_stored_samples_is_refused(env, monkeypatch):
    """Synced before samples were kept: the backfill is the way, never Garmin."""
    monkeypatch.setattr(analysis_service, "stored_samples", lambda repo, aid: [])
    with pytest.raises(analysis_service.DetailNotStored, match="backfill_fit.py"):
        analysis_service.analyze_workout("12345")
    assert env.messages.calls == []


def test_stored_samples_reach_the_payload_bucketed(env, monkeypatch):
    seen = {}

    def payload(w, **kw):
        seen.update(kw)
        return {"workout": w, **kw}

    monkeypatch.setattr(analysis_service, "build_analysis_payload", payload)
    analysis_service.analyze_workout("12345")
    assert seen["stream"]["bucket_sec"] == 30
    assert [s["t"] for s in seen["stream"]["samples"]] == [0, 30]
    assert seen["stream"]["samples"][0]["hr"] == 140.5


def test_clear_analyses_empties_the_cache(env):
    analysis_service.analyze_workout("12345")
    assert analysis_service.clear_analyses() == "deleted (1)"
    assert analysis_service.cached_analysis("12345") is None


# --- routes ------------------------------------------------------------------


def test_get_analysis_404_when_uncached(monkeypatch):
    monkeypatch.setattr(analysis_router, "cached_analysis", lambda aid: None)
    assert api.get("/workouts/12345/analysis").status_code == 404


def test_get_analysis_returns_the_cached_one(monkeypatch):
    monkeypatch.setattr(analysis_router, "cached_analysis", lambda aid: {"form_context": "hi"})
    res = api.get("/workouts/12345/analysis")
    assert res.status_code == 200
    assert res.json()["form_context"] == "hi"


def test_analyze_passes_force_through(monkeypatch):
    seen = {}

    def fake(activity_id, *, force=False):
        seen.update(activity_id=activity_id, force=force)
        return {"form_context": "ok"}

    monkeypatch.setattr(analysis_router, "analyze_workout", fake)
    assert api.post("/workouts/12345/analyze?force=true").status_code == 200
    assert seen == {"activity_id": "12345", "force": True}


@pytest.mark.parametrize(
    "error, status",
    [
        (analysis_service.DetailNotStored("run the backfill"), 409),
        (analysis_service.AnalysisError("not stored"), 404),
        (ClaudeError("no key"), 503),
    ],
)
def test_analyze_error_mapping(monkeypatch, error, status):
    def boom(activity_id, *, force=False):
        raise error

    monkeypatch.setattr(analysis_router, "analyze_workout", boom)
    assert api.post("/workouts/12345/analyze").status_code == status


@pytest.mark.parametrize("bad", ["..%2Fescape", "a$b", "with space"])
def test_traversal_and_junk_ids_are_rejected(bad):
    """activity_id becomes a cache filename, so the route constrains it."""
    res = api.post(f"/workouts/{bad}/analyze")
    assert res.status_code in (404, 422)  # never 200, never a write outside the dir
