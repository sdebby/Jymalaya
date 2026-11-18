"""
Guards against the double-counted brick: an un-split multisport parent stored
alongside the split legs of the same session (both carry the full duration).
"""

from app.influx.client import drop_split_brick_parents
from app.sync_service import _drop_known_unsplit_bricks


def test_parent_with_stored_legs_is_dropped():
    rows = [
        {"activity_id": "999", "discipline": "other", "duration_sec": 11000.0},
        {"activity_id": "999_bike", "discipline": "bike", "duration_sec": 8300.0},
        {"activity_id": "999_run", "discipline": "run", "duration_sec": 2600.0},
    ]
    kept = drop_split_brick_parents(rows)
    assert [r["activity_id"] for r in kept] == ["999_bike", "999_run"]


def test_parent_without_stored_legs_is_kept():
    # Leg enrichment never succeeded for this brick, so the parent is the only
    # record of the session -- dropping it would lose the workout entirely.
    rows = [
        {"activity_id": "999", "discipline": "other", "duration_sec": 11000.0},
        {"activity_id": "123", "discipline": "run", "duration_sec": 2600.0},
    ]
    assert drop_split_brick_parents(rows) == rows


def test_standalone_workouts_are_untouched():
    rows = [
        {"activity_id": "1", "discipline": "run"},
        {"activity_id": "2", "discipline": "swim"},
    ]
    assert drop_split_brick_parents(rows) == rows


def test_unsplit_brick_is_not_rewritten_when_already_stored():
    # known_ids holds base ids, so a brick already split into {id}_bike/_run
    # registers as known -- its leg-less parent must not be written.
    workouts = [
        {"activity_id": "999", "is_brick_split": True, "discipline": "other"},
        {"activity_id": "555", "is_brick_split": False, "discipline": "run"},
    ]
    kept = _drop_known_unsplit_bricks(workouts, {"999"})
    assert [w["activity_id"] for w in kept] == ["555"]


def test_unsplit_brick_is_written_when_the_activity_is_new():
    workouts = [{"activity_id": "999", "is_brick_split": True, "discipline": "other"}]
    assert _drop_known_unsplit_bricks(workouts, set()) == workouts


def test_split_legs_are_always_written():
    # Legs carry the suffix, so they must survive even though their base id is
    # in known_ids (a re-sync overwrites their points in place).
    workouts = [
        {"activity_id": "999_bike", "is_brick_split": True, "discipline": "bike"},
        {"activity_id": "999_run", "is_brick_split": True, "discipline": "run"},
    ]
    assert _drop_known_unsplit_bricks(workouts, {"999"}) == workouts
