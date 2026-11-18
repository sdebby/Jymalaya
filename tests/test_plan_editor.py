"""Tests for app.plan_editor — coach plan-editing tool backend."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app import plan_editor


def _sample_plan() -> dict:
    return {
        "plan_week_start": "2026-07-10",
        "summary": "test week",
        "weekly_tss_target": 300,
        "periodization_phase": "build",
        "days": [
            {
                "date": "2026-07-10",
                "day_name": "Friday",
                "is_rest_day": False,
                "sessions": [
                    {
                        "discipline": "run",
                        "title": "Easy run",
                        "type": "endurance",
                        "duration_min": 45,
                        "estimated_tss": 40,
                    },
                    {
                        "discipline": "strength",
                        "title": "Core",
                        "duration_min": 20,
                        "estimated_tss": 10,
                    },
                ],
                "notes": "",
            },
            {
                "date": "2026-07-11",
                "day_name": "Saturday",
                "is_rest_day": True,
                "sessions": [],
                "notes": "",
            },
        ],
        "coach_notes": "",
        "warning_flags": [],
        "_meta": {"plan_id": "abc", "generated_at": "2026-07-10T00:00:00+00:00"},
    }


@pytest.fixture()
def plan_file(tmp_path, monkeypatch):
    f = tmp_path / "latest_recommendation.json"
    f.write_text(json.dumps(_sample_plan()), encoding="utf-8")
    # Patch the name plan_editor actually reads, not recommend_service's.
    monkeypatch.setattr(plan_editor, "LATEST_FILE", f)
    return f


def _read(plan_file) -> dict:
    return json.loads(plan_file.read_text(encoding="utf-8"))


def test_update_session(plan_file):
    res = plan_editor.update_session(
        "2026-07-10",
        0,
        {"discipline": "run", "title": "Short run", "duration_min": 30, "estimated_tss": 25},
    )
    assert res["ok"] is True
    assert res["session_index"] == 0
    plan = _read(plan_file)
    s = plan["days"][0]["sessions"][0]
    assert s["title"] == "Short run"
    assert s["duration_min"] == 30
    assert plan["_meta"]["modified_by"] == "coach"
    assert "modified_at" in plan["_meta"]


def test_remove_last_session_flips_rest_day(plan_file):
    plan_editor.remove_session("2026-07-10", 1)
    res = plan_editor.remove_session("2026-07-10", 0)
    assert res["day_sessions"] == 0
    assert res["day_is_rest_day"] is True
    assert _read(plan_file)["days"][0]["is_rest_day"] is True


def test_add_session_clears_rest_day(plan_file):
    res = plan_editor.add_session(
        "2026-07-11",
        {"discipline": "swim", "title": "Technique", "duration_min": 40, "estimated_tss": 30},
    )
    assert res["day_is_rest_day"] is False
    assert res["session_index"] == 0
    day = _read(plan_file)["days"][1]
    assert day["is_rest_day"] is False
    assert day["sessions"][0]["discipline"] == "swim"


def test_move_session(plan_file):
    res = plan_editor.move_session("2026-07-10", 0, "2026-07-11")
    assert res["moved_from"] == "2026-07-10"
    assert res["date"] == "2026-07-11"
    plan = _read(plan_file)
    assert len(plan["days"][0]["sessions"]) == 1
    assert plan["days"][1]["sessions"][0]["title"] == "Easy run"
    assert plan["days"][1]["is_rest_day"] is False


def test_unknown_date_lists_valid_dates(plan_file):
    with pytest.raises(plan_editor.PlanEditError) as exc:
        plan_editor.remove_session("2026-07-20", 0)
    assert "2026-07-10" in str(exc.value)
    assert "2026-07-11" in str(exc.value)


def test_bad_session_index(plan_file):
    with pytest.raises(plan_editor.PlanEditError, match="out of range"):
        plan_editor.remove_session("2026-07-10", 5)


def test_missing_plan_file(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_editor, "LATEST_FILE", tmp_path / "nope.json")
    with pytest.raises(plan_editor.PlanEditError, match="No generated plan"):
        plan_editor.remove_session("2026-07-10", 0)


def test_invalid_session_payload(plan_file):
    with pytest.raises(ValidationError):
        plan_editor.add_session(
            "2026-07-11",
            {"discipline": "yoga", "duration_min": 30, "estimated_tss": 20},
        )
    with pytest.raises(ValidationError):
        plan_editor.add_session(
            "2026-07-11",
            {"discipline": "run", "duration_min": -5, "estimated_tss": 20},
        )
    # Failed validation must not touch the file.
    assert _read(plan_file)["days"][1]["sessions"] == []


def _names(structure) -> list[str]:
    return [s["segment"] for s in structure]


def _seg(name: str, minutes: float = 5) -> dict:
    return {"segment": name, "duration_min": minutes}


def _bike(structure) -> dict:
    return {"discipline": "bike", "duration_min": 20, "estimated_tss": 20, "structure": structure}


def test_segment_appended_after_cooldown_moves_before_it(plan_file):
    plan_editor.update_session(
        "2026-07-10",
        0,
        _bike([_seg("Warmup"), _seg("Interval 5"), _seg("Cooldown"), _seg("Recovery 5")]),
    )
    s = _read(plan_file)["days"][0]["sessions"][0]
    assert _names(s["structure"]) == ["Warmup", "Interval 5", "Recovery 5", "Cooldown"]


def test_warmup_and_cooldown_fixed_at_both_ends(plan_file):
    plan_editor.add_session(
        "2026-07-11",
        _bike([_seg("Cool-down"), _seg("Main set"), _seg("Tempo"), _seg("Warm up")]),
    )
    s = _read(plan_file)["days"][1]["sessions"][0]
    assert _names(s["structure"]) == ["Warm up", "Main set", "Tempo", "Cool-down"]


def test_multi_leg_session_with_two_cooldowns_left_as_given():
    structure = [
        _seg("Bike warmup"), _seg("Bike main"), _seg("Bike cooldown"),
        _seg("Run main"), _seg("Run cooldown"),
    ]
    assert plan_editor.order_segments(structure) == structure


def test_session_without_structure_still_validates(plan_file):
    plan_editor.update_session(
        "2026-07-10", 0, {"discipline": "run", "duration_min": 30, "estimated_tss": 25}
    )
    assert "structure" not in _read(plan_file)["days"][0]["sessions"][0]


def test_generated_plan_segments_are_ordered():
    from app.recommend_service import _order_all_segments

    plan = {"days": [{"sessions": [_bike([_seg("Cooldown"), _seg("Interval")])]}, {"sessions": []}]}
    _order_all_segments(plan)
    assert _names(plan["days"][0]["sessions"][0]["structure"]) == ["Interval", "Cooldown"]
