"""Unit tests for the Garmin activity parser + brick splitter (no network)."""

from app.garmin.parser import parse_activities


def test_standalone_run_normalized():
    raw = [
        {
            "activityType": {"typeKey": "running"},
            "activityId": 5,
            "activityName": "Morning Run",
            "duration": 1800,
            "distance": 5000,
            "averageSpeed": 2.78,  # m/s -> ~6:00/km
            "averageHR": 150,
            "startTimeLocal": "2026-06-21 07:00:00",
        }
    ]
    [w] = parse_activities(raw)
    assert w["discipline"] == "run"
    assert w["is_brick_split"] is False
    assert w["duration_sec"] == 1800
    assert w["avg_pace_sec_per_km"] == 359.7  # 1000 / 2.78
    assert w["avg_pace_sec_per_100m"] is None
    assert w["date"] == "2026-06-21"
    assert w["tss"] is None  # metrics fills this later


def test_local_and_utc_start_times_are_both_kept():
    # `start_time`/`date` stay local (the athlete's calendar day); the point
    # timestamp needs the true instant, which only startTimeGMT carries.
    raw = [
        {
            "activityType": {"typeKey": "running"},
            "activityId": 9,
            "duration": 1800,
            "startTimeLocal": "2026-07-26 19:15:44",
            "startTimeGMT": "2026-07-26 16:15:44",
        }
    ]
    [w] = parse_activities(raw)
    assert w["start_time"] == "2026-07-26 19:15:44"
    assert w["start_time_utc"] == "2026-07-26 16:15:44"
    assert w["date"] == "2026-07-26"


def test_missing_gmt_start_leaves_utc_empty():
    # Falls back to converting the local wall clock at write time rather than
    # inventing an instant here.
    raw = [
        {
            "activityType": {"typeKey": "running"},
            "activityId": 10,
            "duration": 1800,
            "startTimeLocal": "2026-07-26 19:15:44",
        }
    ]
    [w] = parse_activities(raw)
    assert w["start_time_utc"] == ""
    assert w["start_time"] == "2026-07-26 19:15:44"


def test_extra_recorded_metrics_are_parsed():
    raw = [
        {
            "activityType": {"typeKey": "running"},
            "activityId": 8,
            "duration": 3000,
            "distance": 8000,
            "elevationGain": 120.4,
            "elevationLoss": 118.2,
            "calories": 540,
            "maxPower": 412,
            "averageRunningCadenceInStepsPerMinute": 172.5,
            "startTimeLocal": "2026-06-22 07:00:00",
        }
    ]
    [w] = parse_activities(raw)
    assert w["elevation_gain_m"] == 120.4
    assert w["elevation_loss_m"] == 118.2
    assert w["calories"] == 540
    assert w["max_power"] == 412
    assert w["avg_cadence"] == 172.5


def test_cadence_prefers_the_sport_specific_key():
    raw = [
        {
            "activityType": {"typeKey": "cycling"},
            "activityId": 9,
            "duration": 3600,
            "averageBikingCadenceInRevPerMinute": 88,
            "startTimeLocal": "2026-06-22 09:00:00",
        }
    ]
    [w] = parse_activities(raw)
    assert w["avg_cadence"] == 88


def test_swim_pace_is_over_moving_time():
    # sTSS is defined over moving time, so pace excludes rest between sets:
    # Garmin's averageSpeed for a pool swim is measured over active lengths.
    raw = [
        {
            "activityType": {"typeKey": "lap_swimming"},
            "activityId": 6,
            "duration": 1800,  # timer, rest included
            "distance": 2000,
            "averageSpeed": 1.25,  # active-only -> 1600 s moving, 80 s/100m
            "startTimeLocal": "2026-06-21 18:00:00",
        }
    ]
    [w] = parse_activities(raw)
    assert w["discipline"] == "swim"
    assert w["moving_duration_sec"] == 1600.0
    assert w["avg_pace_sec_per_100m"] == 80.0
    assert w["avg_pace_sec_per_km"] is None


def test_swim_moving_time_prefers_the_rest_excluding_field():
    # movingDuration and averageSpeed both claim to exclude rest; when one just
    # echoes the timer, the other one is the real moving time.
    raw = [
        {
            "activityType": {"typeKey": "lap_swimming"},
            "activityId": 7,
            "duration": 1800,
            "movingDuration": 1800,  # echoes the timer -> carries no rest info
            "distance": 2000,
            "averageSpeed": 1.25,
            "startTimeLocal": "2026-06-22 18:00:00",
        }
    ]
    [w] = parse_activities(raw)
    assert w["moving_duration_sec"] == 1600.0


def test_swim_without_moving_data_falls_back_to_duration():
    raw = [
        {
            "activityType": {"typeKey": "open_water_swimming"},
            "activityId": 8,
            "duration": 1800,
            "distance": 2000,
            "startTimeLocal": "2026-06-23 18:00:00",
        }
    ]
    [w] = parse_activities(raw)
    assert w["moving_duration_sec"] == 1800.0
    assert w["avg_pace_sec_per_100m"] == 90.0


def test_moving_time_is_swim_only():
    raw = [
        {
            "activityType": {"typeKey": "running"},
            "activityId": 9,
            "duration": 1800,
            "distance": 5000,
            "averageSpeed": 2.78,
            "startTimeLocal": "2026-06-24 07:00:00",
        }
    ]
    [w] = parse_activities(raw)
    assert w["moving_duration_sec"] is None


def test_brick_splits_into_bike_and_run():
    raw = [
        {
            "activityType": {"typeKey": "multi_sport"},
            "activityId": 999,
            "_legs": [
                {
                    "activityType": {"typeKey": "cycling"},
                    "activityId": 1,
                    "duration": 3600,
                    "distance": 30000,
                    "avgPower": 200,
                    "startTimeLocal": "2026-06-20 08:00:00",
                },
                {
                    "activityType": {"typeKey": "running"},
                    "activityId": 2,
                    "duration": 1800,
                    "distance": 5000,
                    "averageSpeed": 3.0,
                    "startTimeLocal": "2026-06-20 09:05:00",
                },
            ],
        }
    ]
    entries = parse_activities(raw)
    assert len(entries) == 2
    bike, run = entries  # sorted by start time
    assert bike["discipline"] == "bike" and run["discipline"] == "run"
    assert bike["activity_id"] == "999_bike"
    assert run["activity_id"] == "999_run"
    # both legs share one session_id
    assert bike["session_id"] == run["session_id"]
    assert bike["session_id"] is not None
    assert bike["is_brick_split"] and run["is_brick_split"]


def test_brick_session_id_is_deterministic():
    # Re-parsing the same brick must yield the same session_id, so a re-sync
    # overwrites the same InfluxDB points instead of duplicating them.
    raw = [
        {
            "activityType": {"typeKey": "multi_sport"},
            "activityId": 999,
            "_legs": [
                {"activityType": {"typeKey": "cycling"}, "activityId": 1, "duration": 3600,
                 "startTimeLocal": "2026-06-20 08:00:00"},
                {"activityType": {"typeKey": "running"}, "activityId": 2, "duration": 1800,
                 "startTimeLocal": "2026-06-20 09:05:00"},
            ],
        }
    ]
    first = parse_activities([dict(raw[0])])
    second = parse_activities([dict(raw[0])])
    assert first[0]["session_id"] == second[0]["session_id"]


def test_multisport_without_legs_passes_through_flagged():
    raw = [{"activityType": {"typeKey": "multi_sport"}, "activityId": 42, "duration": 100}]
    [w] = parse_activities(raw)
    assert w["is_brick_split"] is True
    assert w["session_id"] is not None
    assert w["activity_id"] == "42"


def test_unknown_type_maps_to_other():
    raw = [{"activityType": {"typeKey": "rock_climbing"}, "activityId": 7, "duration": 100}]
    [w] = parse_activities(raw)
    assert w["discipline"] == "other"
