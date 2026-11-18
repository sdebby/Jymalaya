"""
Custom coach instructions: file storage roundtrip, the GET/PUT endpoints, the
conditional injection into the Claude plan payload, and the plan-staleness
check that forces a regeneration after the instructions change. Fakes only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.instructions as instructions
import app.schedule_store as store
from app import sync_service
from main import app

# base_url carries the /api prefix the routers are mounted under, so the paths
# below stay the bare route paths.
client = TestClient(app, base_url="http://testserver/api")


# --- storage ----------------------------------------------------------------
def test_roundtrip_and_clear(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    assert instructions.get_instructions() == ""  # missing file
    instructions.save_instructions("  bricks only on Saturday \n")
    assert instructions.get_instructions() == "bricks only on Saturday"
    instructions.save_instructions("")
    assert instructions.get_instructions() == ""


# --- API --------------------------------------------------------------------
def test_endpoints_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    assert client.get("/coach/instructions").json() == {"text": ""}

    r = client.put("/coach/instructions", json={"text": "  swim on Tuesdays  "})
    assert r.status_code == 200
    # No season plan, so nothing reads the text for one.
    assert r.json() == {"text": "swim on Tuesdays", "season_plan_note": None}
    assert client.get("/coach/instructions").json() == {"text": "swim on Tuesdays"}

    assert client.put("/coach/instructions", json={"text": ""}).json() == {"text": "", "season_plan_note": None}
    assert client.get("/coach/instructions").json() == {"text": ""}


def test_endpoint_rejects_oversized_text(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    assert client.put("/coach/instructions", json={"text": "x" * 4001}).status_code == 422


# --- plan payload -----------------------------------------------------------
def _payload(monkeypatch, text):
    from app.ai import prompt_builder as pb

    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    # last_race reaches events_service.InfluxRepo directly, which patching
    # pb.InfluxRepo does not cover -- without this the test needs a live DB.
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    # Likewise get_schedule, which reads (and on a fresh clone writes) the real
    # data/schedule.json.
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: text)
    return pb.build_plan_payload()


def test_payload_key_absent_without_instructions(monkeypatch):
    assert "athlete_custom_instructions" not in _payload(monkeypatch, "")


def test_payload_includes_instructions_when_set(monkeypatch):
    p = _payload(monkeypatch, "bricks only on Saturday")
    assert p["athlete_custom_instructions"] == "bricks only on Saturday"


# --- plan staleness after an instructions edit ------------------------------
def _fake_plan(monkeypatch, generated_at: datetime):
    import app.recommend_service as reco

    plan = {"_meta": {"generated_at": generated_at.isoformat()}}
    monkeypatch.setattr(reco, "latest_recommendation", lambda: plan)


def test_plan_current_without_instructions_file(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    _fake_plan(monkeypatch, datetime.now(timezone.utc))
    assert sync_service._plan_is_current() is True


def test_plan_stale_after_instructions_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    # Plan generated an hour ago (still today), instructions saved just now.
    _fake_plan(monkeypatch, datetime.now(timezone.utc) - timedelta(hours=1))
    instructions.save_instructions("swim on Tuesdays")
    assert sync_service._plan_is_current() is False


def test_plan_current_when_generated_after_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    instructions.save_instructions("swim on Tuesdays")
    _fake_plan(monkeypatch, datetime.now(timezone.utc) + timedelta(minutes=1))
    assert sync_service._plan_is_current() is True


def test_plan_stale_when_generated_yesterday(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    _fake_plan(monkeypatch, datetime.now(timezone.utc) - timedelta(days=1))
    assert sync_service._plan_is_current() is False
