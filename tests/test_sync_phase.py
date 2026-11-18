"""
Live sync-phase flag: run_sync stamps SYNC_PHASE at each pipeline boundary,
/sync/status exposes it, and it always resets to None afterwards (success or
failure). No real Garmin/InfluxDB involved -- the pipeline steps are faked.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import sync_service
from app.garmin.client import GarminPullError
from main import app

# base_url carries the /api prefix the routers are mounted under, so the paths
# below stay the bare route paths.
client = TestClient(app, base_url="http://testserver/api")


def test_status_reports_no_phase_at_rest():
    assert sync_service.SYNC_PHASE["phase"] is None
    assert client.get("/sync/status").json()["sync_phase"] is None


def test_status_reports_live_phase(monkeypatch):
    monkeypatch.setitem(sync_service.SYNC_PHASE, "phase", "coach")
    assert client.get("/sync/status").json()["sync_phase"] == "coach"


@pytest.fixture()
def fake_pipeline(monkeypatch, tmp_path):
    """Fake every pipeline step; each records the phase visible when it ran."""
    seen: list[str] = []

    class FakeRepo:
        def query(self, flux):
            return []

        def write_workouts(self, w):
            seen.append(sync_service.SYNC_PHASE["phase"])
            return 0

        def write_daily_load(self, rows):
            pass

        def write_vo2max(self, rows):
            return 0


        def write_recovery(self, rows):

            return 0

        def close(self):
            pass

    import app.recommend_service as reco

    monkeypatch.setattr(reco, "run_recommendation", lambda trigger, **kw: None)
    # Isolate from the real cached plan: treat it as stale so the coach phase runs.
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: False)
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(
        sync_service,
        "pull_activities",
        lambda **kw: seen.append(sync_service.SYNC_PHASE["phase"]) or [],
    )
    # Not optional: pull_vo2max resumes a cached Garmin session when one exists,
    # so an unpatched fixture makes a real network call on a developer machine.
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])
    monkeypatch.setattr(
        sync_service,
        "parse_activities",
        lambda raw: seen.append(sync_service.SYNC_PHASE["phase"]) or [],
    )
    monkeypatch.setattr(sync_service, "apply_tss", lambda w, p: [])
    monkeypatch.setattr(sync_service, "compute_load", lambda d, discipline, through=None: [])
    monkeypatch.setattr(sync_service, "daily_tss", lambda w, d=None: {})
    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    return seen


def test_run_sync_walks_the_phases_and_resets(fake_pipeline):
    summary = sync_service.run_sync()
    # Even an empty incremental pull regenerates the plan (the fixture's benign
    # recommendation fake records no phase).
    assert fake_pipeline == ["garmin", "parsing", "metrics"]
    assert summary["recommendation"] == "generated"
    assert sync_service.SYNC_PHASE["phase"] is None


def test_phase_resets_on_garmin_failure(fake_pipeline, monkeypatch):
    def boom(**kw):
        raise GarminPullError("login failed")

    monkeypatch.setattr(sync_service, "pull_activities", boom)
    with pytest.raises(GarminPullError):
        sync_service.run_sync()
    assert sync_service.SYNC_PHASE["phase"] is None


def test_coach_phase_set_when_activities_synced(fake_pipeline, monkeypatch):
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: [{"id": 1}])

    def fake_reco(trigger, **kw):
        fake_pipeline.append(sync_service.SYNC_PHASE["phase"])

    import app.recommend_service as reco

    monkeypatch.setattr(reco, "run_recommendation", fake_reco)
    summary = sync_service.run_sync()
    # The pull fake was replaced (no "garmin" record); parse, repo-write, and the
    # recommendation hook record the remaining phases.
    assert fake_pipeline == ["parsing", "metrics", "coach"]
    assert summary["recommendation"] == "generated"
    assert sync_service.SYNC_PHASE["phase"] is None
