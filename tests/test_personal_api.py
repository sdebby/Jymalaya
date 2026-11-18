"""
Garmin Personal API client (app/garmin/personal_api.py): the key-for-token
exchange and its cache, error codes the dashboard maps to an action, list
chunking and paging, the FIT download, and the routes that surface it. A fake
httpx stands in for Garmin; no key is real.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.garmin import personal_api
from app.routers import sync as sync_router
from main import app

api = TestClient(app, base_url="http://testserver/api")
KEY = "GCDC_test-key-that-is-long-enough.not.real"
# The real request functions, captured before conftest's offline guard replaces
# them for each test; the `garmin` fixture puts them back over a fake httpx.
REAL_REQUEST = personal_api._request
REAL_EXCHANGE = personal_api.exchange_key


class Resp:
    def __init__(self, status=200, text="", json_body=None, content=b"", ctype="text/plain"):
        self.status_code = status
        self.text = text
        self._json = json_body
        self.content = content or text.encode()
        self.headers = {"Content-Type": ctype}

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


@pytest.fixture
def garmin(monkeypatch):
    """A scriptable fake Garmin: token answers and GET answers per path."""
    state = {"tokens": [], "gets": [], "token_status": 200, "get": {}}

    def post(url, headers=None, timeout=None):
        state["tokens"].append(headers["Authorization"])
        return Resp(state["token_status"], text=f"tok{len(state['tokens'])}")

    def get(url, params=None, headers=None, timeout=None):
        path = url.replace(personal_api.BASE, "")
        state["gets"].append((path, params, headers))
        answer = state["get"].get(path.split("?")[0], Resp(404))
        return answer(params, headers) if callable(answer) else answer

    monkeypatch.setattr(personal_api.httpx, "post", post)
    monkeypatch.setattr(personal_api.httpx, "get", get)
    monkeypatch.setattr(personal_api, "_request", REAL_REQUEST)
    monkeypatch.setattr(personal_api, "exchange_key", REAL_EXCHANGE)
    monkeypatch.setenv(personal_api.KEY_ENV, KEY)
    personal_api.reset_token()
    yield state
    personal_api.reset_token()


# --- token -------------------------------------------------------------------
def test_the_token_is_cached_and_sent_as_bearer(garmin):
    garmin["get"]["/x"] = Resp(200, json_body={"ok": 1})
    assert personal_api._json("/x") == {"ok": 1}
    assert personal_api._json("/x") == {"ok": 1}
    assert garmin["tokens"] == [f"ApiKey {KEY}"]
    assert all(h["Authorization"] == "Bearer tok1" for _, _, h in garmin["gets"])


def test_a_401_requests_a_new_token_once(garmin):
    answers = iter([Resp(401), Resp(200, json_body=[1])])
    garmin["get"]["/x"] = lambda p, h: next(answers)
    assert personal_api._json("/x") == [1]
    assert len(garmin["tokens"]) == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_refused_key_is_key_rejected(garmin, status):
    garmin["token_status"] = status
    with pytest.raises(personal_api.PersonalApiError) as e:
        personal_api._json("/x")
    assert e.value.code == "key_rejected"
    assert "Personal API Key page" in e.value.action


def test_no_key_is_no_key(garmin, monkeypatch):
    monkeypatch.delenv(personal_api.KEY_ENV)
    with pytest.raises(personal_api.PersonalApiError) as e:
        personal_api._json("/x")
    assert e.value.code == "no_key"


def test_a_server_error_is_garmin_error(garmin):
    garmin["get"]["/x"] = Resp(500)
    with pytest.raises(personal_api.PersonalApiError) as e:
        personal_api._json("/x")
    assert e.value.code == "garmin_error"


def test_a_network_failure_is_network(garmin, monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(personal_api.httpx, "post", down)
    with pytest.raises(personal_api.PersonalApiError) as e:
        personal_api._json("/x")
    assert e.value.code == "network"


def test_the_key_never_reaches_an_error_message(garmin):
    garmin["token_status"] = 401
    with pytest.raises(personal_api.PersonalApiError) as e:
        personal_api._json("/x")
    assert KEY not in str(e.value.detail())


# --- activity list -----------------------------------------------------------
def test_the_list_is_chunked_and_paged(garmin, monkeypatch):
    monkeypatch.setattr(personal_api, "PAGE_SIZE", 2)
    pages = {0: [{"activityId": 1}, {"activityId": 2}], 2: [{"activityId": 3}]}

    def answer(params, headers):
        return Resp(200, json_body=pages.get(params["start"], []) if params["startDate"] == "2026-01-01" else [])

    garmin["get"]["/activitylist-service/activities/search/activities"] = answer
    rows = personal_api.list_activities(date(2026, 1, 1), date(2026, 6, 1))
    assert [r["activityId"] for r in rows] == [1, 2, 3]
    spans = {(p["startDate"], p["endDate"]) for _, p, _ in garmin["gets"]}
    assert ("2026-01-01", "2026-03-31") in spans  # 90 days
    assert all(p["excludeChildren"] == "false" for _, p, _ in garmin["gets"])


# --- FIT download ------------------------------------------------------------
FIT = b"\x0e\x10\xdd\x52\xb2\x14\x08\x00.FITrest-of-file"


def test_a_zipped_fit_is_unpacked_and_asked_for_as_a_file(garmin, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("123_ACTIVITY.fit", FIT)
    garmin["get"]["/download-service/files/activity/123"] = Resp(200, content=buf.getvalue())
    path = personal_api.download_fit(123, tmp_path)
    assert path.read_bytes() == FIT
    # Accept: application/json answers 406 on a download.
    assert garmin["gets"][-1][2]["Accept"] == "*/*"


def test_a_non_fit_download_is_ignored(garmin, tmp_path):
    garmin["get"]["/download-service/files/activity/123"] = Resp(200, content=b"<html>nope</html>")
    assert personal_api.download_fit(123, tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_purge_removes_leftover_fit_files(tmp_path):
    (tmp_path / "1.fit").write_bytes(b"x")
    (tmp_path / "keep.txt").write_text("x")
    assert personal_api.purge_fit_tmp(tmp_path) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["keep.txt"]


# --- routes ------------------------------------------------------------------
def test_sync_failure_carries_code_and_action(monkeypatch):
    def boom(**kw):
        raise personal_api.PersonalApiError("key_rejected", "expired", personal_api.ACTION_KEY)

    monkeypatch.setattr(sync_router, "run_sync", boom)
    res = api.post("/sync")
    assert res.status_code == 502
    assert res.json()["detail"] == {"code": "key_rejected", "message": "expired", "action": personal_api.ACTION_KEY}


def test_a_new_key_is_checked_then_saved(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n")
    monkeypatch.setattr(sync_router, "ROOT", tmp_path)
    monkeypatch.setattr(personal_api, "exchange_key", lambda key: "tok")
    monkeypatch.delenv(personal_api.KEY_ENV, raising=False)
    res = api.put("/garmin/api-key", json={"key": KEY})
    assert res.status_code == 200 and res.json() == {"saved": True}
    assert KEY not in res.text
    assert f"{personal_api.KEY_ENV}={KEY}" in env.read_text()
    assert "OTHER=1" in env.read_text()
    monkeypatch.delenv(personal_api.KEY_ENV, raising=False)


def test_a_rejected_key_is_not_saved(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n")
    monkeypatch.setattr(sync_router, "ROOT", tmp_path)

    def refuse(key):
        raise personal_api.PersonalApiError("key_rejected", "expired", personal_api.ACTION_KEY)

    monkeypatch.setattr(personal_api, "exchange_key", refuse)
    res = api.put("/garmin/api-key", json={"key": KEY})
    assert res.status_code == 400
    assert personal_api.KEY_ENV not in env.read_text()


def test_no_env_file_means_edit_it_by_hand(monkeypatch, tmp_path):
    monkeypatch.setattr(sync_router, "ROOT", tmp_path)
    monkeypatch.setattr(personal_api, "exchange_key", lambda key: pytest.fail("must not call Garmin"))
    assert api.put("/garmin/api-key", json={"key": KEY}).status_code == 409


def test_a_malformed_key_is_refused_before_garmin(monkeypatch):
    monkeypatch.setattr(personal_api, "exchange_key", lambda key: pytest.fail("must not call Garmin"))
    assert api.put("/garmin/api-key", json={"key": "short"}).status_code == 400
