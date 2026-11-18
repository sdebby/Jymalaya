"""
Garmin's Training Effect on stored workouts (2026-11-09): the parser reads the
two scores and the primary benefit from the activity list row, storage writes
them, and no Claude payload carries them (display only).
"""

from datetime import datetime, timezone

from app.ai.prompt_builder import _recent_workouts, _workout_block
from app.garmin.parser import _te, _te_label, parse_activities
from app.influx import client as influx_client


def _row(**extra):
    base = {
        "activityId": 101,
        "activityName": "Tempo run",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": "2026-11-04 07:00:00",
        "startTimeGMT": "2026-11-04 05:00:00",
        "duration": 3300.0,
        "distance": 9800.0,
        "averageSpeed": 2.97,
    }
    base.update(extra)
    return base


def test_scores_and_label_are_parsed():
    (w,) = parse_activities(
        [_row(aerobicTrainingEffect=3.04, anaerobicTrainingEffect=0.0, trainingEffectLabel="tempo")]
    )
    assert w["aerobic_te"] == 3.0
    # 0.0 is Garmin's real "No Benefit", never dropped as empty.
    assert w["anaerobic_te"] == 0.0
    assert w["te_label"] == "TEMPO"


def test_missing_or_bad_values_are_none():
    (w,) = parse_activities([_row()])
    assert w["aerobic_te"] is None and w["anaerobic_te"] is None and w["te_label"] is None
    assert _te("x") is None
    assert _te(5.6) is None and _te(-0.1) is None
    assert _te(5.0) == 5.0
    assert _te_label("") is None and _te_label(None) is None


def test_brick_legs_keep_their_own_scores():
    parent = {
        "activityId": 900,
        "activityType": {"typeKey": "multi_sport"},
        "startTimeLocal": "2026-11-01 08:00:00",
        "startTimeGMT": "2026-11-01 06:00:00",
        "_legs": [
            _row(activityId=901, activityType={"typeKey": "cycling"}, startTimeLocal="2026-11-01 08:00:00",
                 aerobicTrainingEffect=3.6, anaerobicTrainingEffect=1.2, trainingEffectLabel="TEMPO"),
            _row(activityId=902, activityType={"typeKey": "running"}, startTimeLocal="2026-11-01 09:30:00",
                 aerobicTrainingEffect=2.4, anaerobicTrainingEffect=0.4, trainingEffectLabel="AEROBIC_BASE"),
        ],
    }
    bike, run = parse_activities([parent])
    assert (bike["aerobic_te"], bike["te_label"]) == (3.6, "TEMPO")
    assert (run["aerobic_te"], run["anaerobic_te"], run["te_label"]) == (2.4, 0.4, "AEROBIC_BASE")


def test_storage_writes_the_fields():
    assert "aerobic_te" in influx_client._WORKOUT_NUM_FIELDS
    assert "anaerobic_te" in influx_client._WORKOUT_NUM_FIELDS
    assert "te_label" in influx_client._WORKOUT_STR_FIELDS


def test_zero_anaerobic_is_written(monkeypatch):
    written = []

    class FakeWrite:
        def write(self, bucket, org, record):
            written.extend(record)

    repo = influx_client.InfluxRepo.__new__(influx_client.InfluxRepo)
    repo._write, repo._bucket, repo._org = FakeWrite(), "b", "o"
    (w,) = parse_activities([_row(aerobicTrainingEffect=2.1, anaerobicTrainingEffect=0.0, trainingEffectLabel="BASE")])
    repo.write_workouts([w])
    line = written[0].to_line_protocol()
    assert "anaerobic_te=0" in line and "aerobic_te=2.1" in line and 'te_label="BASE"' in line


def test_not_sent_to_claude():
    row = {
        "time": datetime.now(timezone.utc),
        "date": "2026-11-09",
        "activity_id": "101",
        "discipline": "run",
        "duration_sec": 3300.0,
        "aerobic_te": 3.0,
        "anaerobic_te": 0.0,
        "te_label": "TEMPO",
    }
    for block in (_recent_workouts([row])[0], _workout_block(row)):
        assert not {"aerobic_te", "anaerobic_te", "te_label"} & set(block)
