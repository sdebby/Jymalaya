"""Unit tests for the stored-history metrics recompute (pure part, no Influx)."""

from datetime import datetime, timezone

from app.influx.client import _parse_ts
from app.sync_service import _recompute_rows, _restore_point_time

PROFILE = {
    "ftp_w": 250,
    "run_threshold_pace_sec_km": 270,
    "css_sec_100m": 95,
    "lthr_bpm": 162,
}


def test_swim_row_gets_elapsed_pace_and_cubed_tss():
    # Stored with Garmin's active-only pace (95 -> IF 1.0) and the old squared
    # formula; recompute must switch to elapsed pace and cube the IF.
    row = {
        "discipline": "swim",
        "duration_sec": 1800.0,
        "distance_m": 1000.0,
        "avg_pace_sec_per_100m": 95.0,
        "tss": 50.0,
        "intensity_factor": 1.0,
    }
    changed = _recompute_rows([row], PROFILE)
    assert changed == [row]
    assert row["avg_pace_sec_per_100m"] == 180.0  # 1800 / 1000 * 100
    # IF = 95/180 = 0.528; 0.5h * 0.528^3 * 100 = 7.4
    assert row["intensity_factor"] == 0.528
    assert row["tss"] == 7.4


def test_unchanged_row_is_not_rewritten():
    # A bike row whose stored values already match the current profile must
    # not be flagged for rewrite.
    row = {
        "discipline": "bike",
        "duration_sec": 3600.0,
        "normalized_power": 225.0,
        "tss": 81.0,
        "intensity_factor": 0.9,
        "tss_method": "power",
    }
    assert _recompute_rows([row], PROFILE) == []


def test_row_without_method_is_rewritten_once_to_stamp_it():
    # Rows stored before tss_method existed get it on the first recompute, and
    # are then stable.
    row = {
        "discipline": "bike",
        "duration_sec": 3600.0,
        "normalized_power": 225.0,
        "tss": 81.0,
        "intensity_factor": 0.9,
    }
    assert _recompute_rows([row], PROFILE) == [row]
    assert row["tss_method"] == "power"
    assert _recompute_rows([row], PROFILE) == []


def test_rewrite_lands_on_the_same_instant():
    # A recompute overwrites the point in place, so the time it writes back must
    # be the time it read. Feeding the local `start_time` back instead would
    # re-convert it and drift the point by the UTC offset on every run.
    stored = datetime(2026, 7, 26, 16, 15, 44, tzinfo=timezone.utc)
    row = {"time": stored, "start_time": "2026-07-26 19:15:44", "date": "2026-07-26"}
    assert _parse_ts(_restore_point_time(row)) == stored


def test_rewrite_is_stable_across_repeated_recomputes():
    stored = datetime(2026, 7, 26, 16, 15, 44, tzinfo=timezone.utc)
    row = {"time": stored, "start_time": "2026-07-26 19:15:44"}
    for _ in range(3):
        row["time"] = _parse_ts(_restore_point_time(row))
    assert row["time"] == stored


def test_row_without_inputs_keeps_stored_tss():
    # No power/pace/HR -> estimate_tss yields None; the stored value stays and
    # the row is not rewritten.
    row = {"discipline": "bike", "duration_sec": 3600.0, "tss": 42.0, "intensity_factor": 0.65}
    assert _recompute_rows([row], PROFILE) == []
    assert row["tss"] == 42.0
