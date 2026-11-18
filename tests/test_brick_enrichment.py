"""
Brick legs from the Personal API: the activity list carries each multisport
child as its own row with `parentId`. pull_activities folds them into the
parent's `_legs` (transitions dropped, never stored as standalone workouts),
HR zones come off the rows, an implausible leg's zones are recomputed from its
FIT records, and the parser then splits the brick into bike + run entries.
Fakes only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.garmin import personal_api
from app.garmin.parser import parse_activities, split_bricks

PARENT = {
    "activityId": 999,
    "activityName": "Multisport",
    "activityType": {"typeKey": "multi_sport"},
    "startTimeLocal": "2026-07-18 06:03:58",
    "isParent": True,
}
BIKE = {
    "activityId": 1,
    "parentId": 999,
    "activityName": "Bike leg",
    "activityType": {"typeKey": "road_biking"},
    "startTimeLocal": "2026-07-18 06:03:58",
    "duration": 7330.0,
    "distance": 45763.9,
    "averageHR": 127.0,
    "averageSpeed": 6.24,
    "avgPower": 121.0,
    "hrTimeInZone_1": 3000.0,
    "hrTimeInZone_2": 4000.0,
}
T2 = {
    "activityId": 2,
    "parentId": 999,
    "activityType": {"typeKey": "transition_v2"},
    "startTimeLocal": "2026-07-18 08:06:08",
    "duration": 501.0,
}
RUN = {
    "activityId": 3,
    "parentId": 999,
    "activityName": "Run leg",
    "activityType": {"typeKey": "running"},
    "startTimeLocal": "2026-07-18 08:14:30",
    "duration": 3223.0,
    "distance": 7620.0,
    "averageHR": 143.0,
    "averageSpeed": 2.36,
    # Near-empty: 300 s of zones on a 54-minute leg fails zones_plausible.
    "hrTimeInZone_1": 300.0,
}
SOLO = {
    "activityId": 50,
    "activityType": {"typeKey": "running"},
    "startTimeLocal": "2026-07-19 07:00:00",
    "duration": 1800.0,
    "distance": 5000.0,
    "averageSpeed": 2.78,
    "hrTimeInZone_2": 1700.0,
}


@pytest.fixture
def pull(monkeypatch):
    """pull_activities over a fixed list, with FIT enrichment recorded, not fetched."""
    enriched: list[str] = []

    def fake_enrich(unit, disc, cache):
        enriched.append(str(unit["activityId"]))
        return True

    monkeypatch.setattr(personal_api, "refresh_avatar_from", lambda rows: None)
    monkeypatch.setattr(personal_api, "enrich_with_fit", fake_enrich)

    def run(rows, sampled=None):
        monkeypatch.setattr(personal_api, "list_activities", lambda a, b: [dict(r) for r in rows])
        return personal_api.pull_activities(days=5, sampled_ids=sampled or set()), enriched

    return run


def test_children_fold_into_legs_and_the_parser_splits(pull):
    raw, _ = pull([RUN, PARENT, T2, BIKE, SOLO])
    # Only top-level activities come back: legs never stand alone.
    assert [a["activityId"] for a in raw] == [999, 50]
    legs = raw[0]["_legs"]
    assert [leg["activityType"]["typeKey"] for leg in legs] == ["road_biking", "running"]
    workouts = parse_activities(raw)
    assert [w["activity_id"] for w in workouts] == ["999_bike", "999_run", "50"]
    assert {w["session_id"] for w in workouts[:2]} == {workouts[0]["session_id"]}


def test_zones_come_off_the_list_rows(pull):
    raw, _ = pull([PARENT, BIKE, RUN, SOLO])
    bike, run = raw[0]["_legs"]
    assert bike["_hr_zones"] == {"Z1": 3000.0, "Z2": 4000.0}
    assert raw[1]["_hr_zones"] == {"Z2": 1700.0}
    assert "_hr_zones" not in raw[0]  # the parent is split away


def test_fit_is_fetched_per_leg_and_only_without_stored_samples(pull):
    _, enriched = pull([PARENT, BIKE, RUN, SOLO], sampled={"999_bike"})
    # The bike leg already has samples under its stored id; the run leg and
    # the solo run do not. A brick leg's FIT is its own real child id.
    assert enriched == ["3", "50"]


def test_an_orphan_child_is_dropped(pull):
    raw, _ = pull([RUN, SOLO])  # parent outside the window
    assert [a["activityId"] for a in raw] == [50]


def test_implausible_leg_zones_are_recomputed_from_fit_records(monkeypatch, tmp_path):
    t0 = datetime(2026, 7, 18, 5, 14, 30, tzinfo=timezone.utc)
    records = [{"time": t0 + timedelta(seconds=i), "hr": 150.0 if i < 1800 else 165.0,
                "speed_mps": 2.4, "distance_m": i * 2.4, "altitude_m": 10.0}
               for i in range(3223)]
    fit_path = tmp_path / "3.fit"
    fit_path.write_bytes(b"x")
    monkeypatch.setattr(personal_api, "download_fit", lambda aid, dest_dir=None: fit_path)
    monkeypatch.setattr(personal_api.fit, "decode", lambda p: (records, [], {"rpe": None, "feel": None}))
    monkeypatch.setattr(personal_api, "planned_workout", lambda aid: None)
    monkeypatch.setattr(
        personal_api, "_json",
        lambda path, params=None: [{"sport": "RUNNING", **{f"zone{i}Floor": f for i, f in
                                    enumerate((109, 134, 149, 160, 167), start=1)}}],
    )
    leg = dict(RUN, _hr_zones={"Z1": 300.0})
    assert personal_api.enrich_with_fit(leg, "run", {})
    zones = leg["_hr_zones"]
    assert zones["Z3"] == pytest.approx(1800, abs=2)
    assert zones["Z4"] == pytest.approx(1422, abs=2)
    assert not fit_path.exists()  # deleted straight after decoding
    assert leg["_ngp_sec_per_km"] == pytest.approx(1000 / 2.4, abs=0.5)
    assert len(leg["_detail"]["samples"]) == 645


def test_plausible_zones_are_kept(monkeypatch, tmp_path):
    t0 = datetime(2026, 7, 19, 4, 0, tzinfo=timezone.utc)
    records = [{"time": t0 + timedelta(seconds=i), "hr": 170.0, "speed_mps": 2.78} for i in range(1800)]
    fit_path = tmp_path / "50.fit"
    fit_path.write_bytes(b"x")
    monkeypatch.setattr(personal_api, "download_fit", lambda aid, dest_dir=None: fit_path)
    monkeypatch.setattr(personal_api.fit, "decode", lambda p: (records, [], {"rpe": None, "feel": None}))
    monkeypatch.setattr(personal_api, "planned_workout", lambda aid: None)
    monkeypatch.setattr(personal_api, "_json", lambda *a, **k: pytest.fail("no zone floors needed"))
    solo = dict(SOLO, _hr_zones={"Z2": 1700.0})
    assert personal_api.enrich_with_fit(solo, "run", {})
    assert solo["_hr_zones"] == {"Z2": 1700.0}


def test_a_failed_fit_download_leaves_the_workout_without_detail(monkeypatch):
    def boom(aid, dest_dir=None):
        raise personal_api.PersonalApiError("garmin_error", "500", personal_api.ACTION_RETRY)

    monkeypatch.setattr(personal_api, "download_fit", boom)
    solo = dict(SOLO)
    assert personal_api.enrich_with_fit(solo, "run", {}) is False
    assert "_detail" not in solo and "_ngp_sec_per_km" not in solo


def test_transition_leg_dropped_in_parser():
    activity = {
        "activityType": {"typeKey": "multi_sport"},
        "activityId": 5,
        "_legs": [
            {"activityType": {"typeKey": "cycling"}, "activityId": 1, "duration": 100,
             "startTimeLocal": "2026-07-18 06:00:00"},
            {"activityType": {"typeKey": "transition_v2"}, "activityId": 2, "duration": 60,
             "startTimeLocal": "2026-07-18 06:30:00"},
            {"activityType": {"typeKey": "running"}, "activityId": 3, "duration": 100,
             "startTimeLocal": "2026-07-18 06:31:00"},
        ],
    }
    entries = split_bricks(activity)
    assert [e["discipline"] for e in entries] == ["bike", "run"]
