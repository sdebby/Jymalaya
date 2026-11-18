"""
MFA handoff flow: /sync/status exposes the live pending flag while _prompt_mfa is
waiting, and POST /sync/mfa delivers the code to it. No real Garmin involved --
_prompt_mfa is exercised directly in a thread with MFA_CODE_FILE pointed at tmp.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.garmin import client as garmin_client
from main import app

# base_url carries the /api prefix the routers are mounted under, so the paths
# below stay the bare route paths.
client = TestClient(app, base_url="http://testserver/api")


@pytest.fixture
def mfa_file(tmp_path, monkeypatch):
    path = tmp_path / "mfa_code"
    monkeypatch.setattr(garmin_client, "MFA_CODE_FILE", path)
    # A real poll interval would make the submit test wait it out.
    monkeypatch.setattr(garmin_client, "MFA_POLL_SECONDS", 0.01)
    return path


def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_mfa_submit_completes_prompt(mfa_file):
    result: dict = {}

    def run_prompt():
        result["code"] = garmin_client._prompt_mfa()

    t = threading.Thread(target=run_prompt, daemon=True)
    t.start()
    try:
        # The prompt flags itself as pending, and the API surfaces it live.
        assert _wait_for(lambda: garmin_client.MFA_STATE["pending"])
        assert client.get("/sync/status").json()["mfa_pending"] is True

        res = client.post("/sync/mfa", json={"code": " 123456 "})
        assert res.status_code == 200
        assert res.json() == {"submitted": True}

        # _prompt_mfa polls for the file -- give it time to consume it.
        t.join(timeout=10)
        assert not t.is_alive()
        assert result["code"] == "123456"
        assert not mfa_file.exists()  # consumed, not left behind
        assert garmin_client.MFA_STATE["pending"] is False
        assert client.get("/sync/status").json()["mfa_pending"] is False
    finally:
        # Never leave a stuck prompt thread holding pending state for other tests.
        if t.is_alive():
            mfa_file.write_text("999999", encoding="utf-8")
            t.join(timeout=10)


def test_mfa_submit_without_pending_challenge_is_409(mfa_file):
    assert garmin_client.MFA_STATE["pending"] is False
    res = client.post("/sync/mfa", json={"code": "123456"})
    assert res.status_code == 409
    assert not mfa_file.exists()


def test_mfa_submit_rejects_bad_code_format(mfa_file):
    for bad in ("", "abc123", "123", "123456789"):
        res = client.post("/sync/mfa", json={"code": bad})
        assert res.status_code == 400, bad
    assert not mfa_file.exists()
