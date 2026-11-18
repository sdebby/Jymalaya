"""
A workout's stored detail: the FIT sampler (app/garmin/fit.py) that turns
records into the 5 s samples and laps the sync stores, and the readers
(app/workout_detail.py) that hand them to the analysis in the shape it has
always read. No FIT file, Garmin or InfluxDB involved.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.garmin import fit
from app.garmin.personal_api import planned_workout
from app import workout_detail

T0 = datetime(2026, 11, 4, 4, 0, 50, tzinfo=timezone.utc)


def _records(n, **fields):
    return [{"time": T0 + timedelta(seconds=i), **fields} for i in range(n)]


# --- fit.to_samples ----------------------------------------------------------
def test_samples_bucket_by_five_seconds_and_average():
    recs = _records(10, hr=140.0, speed_mps=3.0)
    recs[5]["hr"] = 150.0
    out = fit.to_samples(recs, "bike")
    assert [s["t"] for s in out] == [0, 5]
    assert out[0]["hr"] == 140.0 and out[1]["hr"] == 142.0
    assert out[0]["time"] == T0


def test_samples_keep_a_genuine_zero():
    out = fit.to_samples(_records(5, power_w=0.0, speed_mps=0.0), "bike")
    assert out[0]["power_w"] == 0.0 and out[0]["speed_mps"] == 0.0


def test_samples_skip_recording_pauses():
    recs = _records(10, hr=140.0)
    later = [{"time": T0 + timedelta(seconds=600 + i), "hr": 150.0} for i in range(10)]
    out = fit.to_samples(recs + later, "run")
    # The 10-minute stop is not ten minutes of buckets.
    assert [s["t"] for s in out] == [0, 5, 10, 15]


def test_run_cadence_is_steps_per_minute():
    recs = _records(5, cadence=89.0, fractional_cadence=0.5)
    assert fit.to_samples(recs, "run")[0]["cadence"] == 179.0
    assert fit.to_samples(recs, "bike")[0]["cadence"] == 89.5


def test_samples_never_carry_coordinates():
    # decode() never reads position fields; a record dict that somehow had them
    # still cannot pass them on, since only named fields are copied.
    recs = _records(5, hr=140.0, position_lat=1, position_long=2)
    keys = {k for s in fit.to_samples(recs, "run") for k in s}
    assert not any("position" in k or "lat" in k for k in keys)


def test_laps_are_numbered_in_order_with_intensity():
    laps = [
        {"start_time": T0 + timedelta(minutes=15), "total_timer_time": 120.0, "intensity": "active",
         "avg_running_cadence": 88, "avg_fractional_cadence": 0.5},
        {"start_time": T0, "total_timer_time": 900.0, "total_distance": 2356.9, "intensity": "warmup",
         "start_position_lat": 5},
    ]
    out = fit.to_laps(laps, "run")
    assert [lap["lap"] for lap in out] == [1, 2]
    assert out[0]["duration_sec"] == 900.0 and out[0]["intensity"] == "warmup"
    assert out[1]["avg_cadence"] == 177.0
    assert all("start_position_lat" not in lap for lap in out)


def test_hr_zones_from_records():
    recs = _records(601, hr=150.0)
    zones = fit.hr_zones_from_records(recs, [109, 134, 149, 160, 167])
    assert zones["Z3"] == 600.0 and zones["Z1"] == 0.0


# --- workout_detail ----------------------------------------------------------
def test_stream_rebuckets_to_thirty_seconds_with_the_old_keys():
    rows = [{"t": t, "hr": 140.0 + t / 5, "altitude_m": 10.0, "speed_mps": 3.0} for t in range(0, 60, 5)]
    out = workout_detail.bucket_stream(rows, 60)
    assert out["bucket_sec"] == 30 and out["sample_count"] == 2
    first = out["samples"][0]
    assert first["t"] == 0 and first["hr"] == 142.5
    assert first["elevation_m"] == 10.0 and "altitude_m" not in first


def test_bucket_widens_for_a_long_session():
    assert workout_detail.bucket_seconds(5 * 3600) == 90
    assert workout_detail.bucket_seconds(3600) == 30
    assert workout_detail.bucket_seconds(None) == 30


def test_stream_is_none_without_samples():
    assert workout_detail.bucket_stream([], 3600) is None


class FakeRepo:
    def __init__(self, rows):
        self.rows = rows

    def query(self, flux):
        return self.rows


def test_laps_read_back_sorted_by_number():
    repo = FakeRepo([{"lap": "2", "duration_sec": 120.0}, {"lap": "1", "duration_sec": 900.0, "intensity": "warmup"}])
    assert workout_detail.stored_laps(repo, "1") == [
        {"lap": 1, "duration_sec": 900.0, "intensity": "warmup"},
        {"lap": 2, "duration_sec": 120.0},
    ]


def test_planned_workout_read_back_from_json():
    planned = {"name": "VO2max intervals", "steps": [{"step": "active", "duration_value": 180.0}]}
    assert workout_detail.stored_planned(FakeRepo([{"_value": json.dumps(planned)}]), "1") == planned
    assert workout_detail.stored_planned(FakeRepo([{"_value": "not json"}]), "1") is None
    assert workout_detail.stored_planned(FakeRepo([]), "1") is None


# --- planned workout from the Personal API -----------------------------------
def test_planned_workout_is_flattened(monkeypatch):
    from app.garmin import personal_api

    payload = [{
        "workoutName": "VO2max intervals 5 x 3 min",
        "steps": [
            {"intensity": "ACTIVE", "durationType": "TIME", "durationValue": 900.0,
             "targetType": "HEART_RATE", "targetValueLow": 108.0, "targetValueHigh": 148.0, "notes": "Warmup"},
            {"intensity": None, "durationType": "REPEAT_UNTIL_STEPS_CMPLT", "durationValue": 1.0,
             "targetType": None, "targetValue": 5.0},
        ],
    }]
    monkeypatch.setattr(personal_api, "_json", lambda path, params=None: payload)
    out = planned_workout(123)
    assert out["name"] == "VO2max intervals 5 x 3 min"
    assert out["steps"][0] == {
        "step": "active", "duration_type": "TIME", "duration_value": 900.0, "target_type": "HEART_RATE",
        "target_low": 108.0, "target_high": 148.0, "description": "Warmup",
    }
    assert out["steps"][1]["repeat"] == 5.0


def test_no_planned_workout_is_none(monkeypatch):
    from app.garmin import personal_api

    monkeypatch.setattr(personal_api, "_json", lambda path, params=None: None)
    assert planned_workout(123) is None
