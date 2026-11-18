"""
The athlete's own RPE and feel (2026-11-14): read from the FIT file's session
message, carried onto the workout by the parser, written by storage, and sent
to no Claude payload (display only).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.ai.prompt_builder import _recent_workouts, _workout_block
from app.garmin import fit, personal_api
from app.garmin.parser import parse_activities
from app.influx import client as influx_client


def _frame(**fields):
    numbers = {"workout_feel": 192, "workout_rpe": 193, "total_timer_time": 7}
    return SimpleNamespace(
        fields=[SimpleNamespace(name=n, value=v, def_num=numbers.get(n, 0)) for n, v in fields.items()]
    )


def _row(**extra):
    base = {
        "activityId": 101,
        "activityName": "Tempo run",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": "2026-11-14 07:00:00",
        "startTimeGMT": "2026-11-14 05:00:00",
        "duration": 3300.0,
        "distance": 9800.0,
        "averageSpeed": 2.97,
    }
    base.update(extra)
    return base


def test_rating_scales():
    # Garmin stores RPE in tenths of the 1-10 scale and feel as 0-100.
    assert fit._rating(_frame(workout_rpe=90, workout_feel=75)) == {"rpe": 9.0, "feel": 75.0}


def test_feel_zero_is_very_weak_not_missing():
    assert fit._rating(_frame(workout_rpe=20, workout_feel=0)) == {"rpe": 2.0, "feel": 0.0}


def test_one_without_the_other_and_none():
    assert fit._rating(_frame(workout_rpe=None, workout_feel=50)) == {"rpe": None, "feel": 50.0}
    assert fit._rating(_frame(total_timer_time=3300.0)) == {"rpe": None, "feel": None}


def test_out_of_range_is_dropped():
    assert fit._rating(_frame(workout_rpe=0, workout_feel=255)) == {"rpe": None, "feel": None}


def test_unnamed_fields_are_read_by_number():
    frame = SimpleNamespace(
        fields=[
            SimpleNamespace(name="unknown_192", value=25, def_num=192),
            SimpleNamespace(name="unknown_193", value=80, def_num=193),
        ]
    )
    assert fit._rating(frame) == {"rpe": 8.0, "feel": 25.0}


def test_enrich_attaches_the_rating(monkeypatch, tmp_path):
    t0 = datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc)
    records = [
        {"time": t0.replace(second=s), "hr": 140.0, "speed_mps": 3.0, "distance_m": 3.0 * s, "altitude_m": 10.0}
        for s in range(0, 60)
    ]
    fake = tmp_path / "101.fit"
    fake.write_bytes(b"x")
    monkeypatch.setattr(personal_api, "download_fit", lambda aid: fake)
    monkeypatch.setattr(personal_api.fit, "decode", lambda p: (records, [], {"rpe": 6.0, "feel": 0.0}))
    monkeypatch.setattr(personal_api, "planned_workout", lambda aid: None)
    row = _row(_hr_zones={"Z2": 3300.0})
    assert personal_api.enrich_with_fit(row, "run", {})
    assert (row["_rpe"], row["_feel"]) == (6.0, 0.0)
    (w,) = parse_activities([row])
    assert (w["rpe"], w["feel"]) == (6.0, 0.0)


def test_no_rating_is_none_on_the_workout():
    (w,) = parse_activities([_row()])
    assert w["rpe"] is None and w["feel"] is None


def test_storage_writes_a_zero_feel_and_skips_a_missing_rating():
    written = []

    class FakeWrite:
        def write(self, bucket, org, record):
            written.extend(record)

    repo = influx_client.InfluxRepo.__new__(influx_client.InfluxRepo)
    repo._write, repo._bucket, repo._org = FakeWrite(), "b", "o"
    rated, plain = parse_activities([_row(_rpe=2.0, _feel=0.0), _row(activityId=102)])
    repo.write_workouts([rated, plain])
    first, second = (p.to_line_protocol() for p in written)
    assert "rpe=2" in first and "feel=0" in first
    # Not written at all, so a re-sync never blanks a stored rating.
    assert "rpe=" not in second and "feel=" not in second


def test_not_sent_to_claude():
    row = {
        "time": datetime.now(timezone.utc),
        "date": "2026-11-14",
        "activity_id": "101",
        "discipline": "run",
        "duration_sec": 3300.0,
        "rpe": 9.0,
        "feel": 75.0,
    }
    for block in (_recent_workouts([row])[0], _workout_block(row)):
        assert not {"rpe", "feel"} & set(block)
