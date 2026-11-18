"""
POST /recommend/move-session: a planned session dragged to another day on the
dashboard. The plan file and schedule are temp/fake; nothing reaches data/.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import plan_editor, plan_requests
from app.routers import recommend
from app.schedule_store import WEEKDAY_NAMES
from main import app

api = TestClient(app, base_url="http://testserver/api")


def _iso(delta: int) -> str:
    return (date.today() + timedelta(days=delta)).isoformat()


YESTERDAY, TODAY, TOMORROW, DAY_2, REST_DAY = (_iso(d) for d in (-1, 0, 1, 2, 3))


def _plan() -> dict:
    run = {"discipline": "run", "title": "Easy run", "duration_min": 40, "estimated_tss": 35}
    return {
        "days": [
            {"date": d, "day_name": d, "is_rest_day": False, "sessions": [dict(run)]}
            for d in (YESTERDAY, TODAY, TOMORROW)
        ]
        + [
            {"date": d, "day_name": d, "is_rest_day": True, "sessions": []}
            for d in (DAY_2, REST_DAY)
        ]
    }


@pytest.fixture()
def plan_file(tmp_path, monkeypatch):
    f = tmp_path / "latest_recommendation.json"
    f.write_text(json.dumps(_plan()), encoding="utf-8")
    monkeypatch.setattr(plan_editor, "LATEST_FILE", f)
    monkeypatch.setattr("app.recommend_service.LATEST_FILE", f)
    rest_weekday = WEEKDAY_NAMES[date.fromisoformat(REST_DAY).weekday()]
    monkeypatch.setattr(
        recommend,
        "get_schedule",
        lambda: {"schedule": {w: {"rest": w == rest_weekday} for w in WEEKDAY_NAMES}},
    )
    return f


def _move(from_date: str, index: int, to_date: str):
    return api.post(
        "/recommend/move-session",
        json={"from_date": from_date, "session_index": index, "to_date": to_date},
    )


def test_move_returns_plan_and_saves_both_days_as_requests(plan_file):
    res = _move(TOMORROW, 0, DAY_2)
    assert res.status_code == 200
    days = {d["date"]: d for d in res.json()["days"]}
    assert days[TOMORROW]["sessions"] == []
    assert days[TOMORROW]["is_rest_day"] is True
    assert days[DAY_2]["sessions"][0]["title"] == "Easy run"
    assert days[DAY_2]["is_rest_day"] is False
    assert days[TOMORROW]["athlete_requested"] and days[DAY_2]["athlete_requested"]
    assert res.json()["_meta"]["modified_by"] == "athlete"

    saved = {r["date"]: r for r in plan_requests.active_requests()}
    assert set(saved) == {TOMORROW, DAY_2}
    assert saved[DAY_2]["notes"] == [f"Moved Easy run from {TOMORROW} to {DAY_2} on the dashboard"]


def test_today_is_not_a_past_day(plan_file):
    assert _move(TODAY, 0, DAY_2).status_code == 200


@pytest.mark.parametrize(
    "from_date, index, to_date, status",
    [
        (TOMORROW, 0, REST_DAY, 409),  # a rest day in the schedule
        (YESTERDAY, 0, DAY_2, 409),  # the source has passed
        (TOMORROW, 0, YESTERDAY, 409),  # the target has passed
        (TOMORROW, 0, TOMORROW, 422),  # same day
        (TOMORROW, 5, DAY_2, 404),  # no such session
        (TOMORROW, 0, _iso(30), 404),  # not a plan day
        (TOMORROW, 0, "2026-13-45", 422),  # not a date
    ],
)
def test_refused_move_changes_nothing(plan_file, from_date, index, to_date, status):
    before = plan_file.read_text(encoding="utf-8")
    assert _move(from_date, index, to_date).status_code == status
    assert plan_file.read_text(encoding="utf-8") == before
    assert plan_requests.active_requests() == []


def test_no_plan_is_404(plan_file):
    plan_file.unlink()
    assert _move(TOMORROW, 0, DAY_2).status_code == 404
