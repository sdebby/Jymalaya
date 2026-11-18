"""
Athlete avatar: refresh_avatar_from caches the Garmin profile image (its URL
rides on every Personal API activity row) under data/, skips the download while
the cached copy is fresh, and GET /athlete/avatar serves it (404 before the
first sync). Fakes only.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.garmin.client as garmin_client
import app.garmin.personal_api as personal_api
import app.routers.athlete as athlete_router
from main import app

# base_url carries the /api prefix the routers are mounted under, so the paths
# below stay the bare route paths.
client = TestClient(app, base_url="http://testserver/api")

PNG_BYTES = b"\x89PNG fake image bytes"


class FakeResponse:
    content = PNG_BYTES
    headers = {"Content-Type": "image/png"}

    def raise_for_status(self):
        pass


def _fake_get(calls):
    def get(url, **kw):
        calls.append(url)
        return FakeResponse()
    return get


def test_refresh_caches_image_and_skips_while_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(garmin_client, "AVATAR_DIR", tmp_path)
    monkeypatch.setattr(personal_api, "AVATAR_DIR", tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(personal_api.httpx, "get", _fake_get(calls))
    rows = [{"activityId": 1}, {"activityId": 2, "ownerProfileImageUrlLarge": "https://example.invalid/a"}]

    personal_api.refresh_avatar_from(rows)
    cached = garmin_client.avatar_file()
    assert cached is not None
    assert cached.suffix == ".png"
    assert cached.read_bytes() == PNG_BYTES
    assert calls == ["https://example.invalid/a"]

    # A fresh cached copy short-circuits: no second download.
    personal_api.refresh_avatar_from(rows)
    assert len(calls) == 1


def test_refresh_never_raises_without_profile_image(tmp_path, monkeypatch):
    monkeypatch.setattr(garmin_client, "AVATAR_DIR", tmp_path)
    monkeypatch.setattr(personal_api, "AVATAR_DIR", tmp_path)
    personal_api.refresh_avatar_from([{"activityId": 1}])
    assert garmin_client.avatar_file() is None


def test_avatar_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(athlete_router, "avatar_file", lambda: None)
    assert client.get("/athlete/avatar").status_code == 404

    img = tmp_path / "athlete_avatar.png"
    img.write_bytes(PNG_BYTES)
    monkeypatch.setattr(athlete_router, "avatar_file", lambda: img)
    resp = client.get("/athlete/avatar")
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES
    assert resp.headers["content-type"] == "image/png"
