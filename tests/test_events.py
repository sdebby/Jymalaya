"""Unit tests for event (de)serialization helpers (no network/InfluxDB)."""

from app.events_service import _row_to_event


def test_row_to_event_deserializes_types():
    row = {
        "goal_id": "abc123",
        "name": "Ironman Austria",
        "type": "140.6",
        "race_date": "2026-09-06",
        "location_lat": 46.62,
        "location_lon": 14.31,
        "location_name": "Klagenfurt",
        "target_finish_time_sec": 37800.0,
        "sub_goals": '["sub-10:30 finish", "hold 230W on bike"]',
        "notes": "A race",
    }
    g = _row_to_event(row)
    assert g["event_id"] == "abc123"  # from the stored goal_id tag
    assert g["type"] == "140.6"
    assert g["target_finish_time_sec"] == 37800
    assert g["sub_goals"] == ["sub-10:30 finish", "hold 230W on bike"]
    assert g["location_lat"] == 46.62


def test_row_to_event_handles_empty_and_missing():
    g = _row_to_event({"goal_id": "x", "name": "Race", "type": "sprint", "race_date": "2026-07-01"})
    assert g["sub_goals"] == []
    assert g["target_finish_time_sec"] is None
    assert g["location_lat"] is None
    assert g["notes"] is None


def test_row_to_event_bad_subgoals_json_degrades():
    g = _row_to_event({"goal_id": "x", "sub_goals": "not json"})
    assert g["sub_goals"] == []
