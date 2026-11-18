"""
Normalized Graded Pace (app/metrics/ngp.py) and the run rTSS that uses it
(app/metrics/calculator.py). Pure numbers, no FIT file.

The live check that chose the model is in the module docstring: on the
2026-11-04 fartlek the FIT samples give rTSS 66.9 against TrainingPeaks' 67.
"""

from __future__ import annotations

import pytest

from app.metrics.calculator import estimate_tss_method
from app.metrics.ngp import grade_factor, ngp_sec_per_km, ngp_speed

PROFILE = {"run_threshold_pace_sec_km": 321.0, "lthr_bpm": 167}


def _samples(speeds, grade=0.0, step=5):
    out, dist, alt = [], 0.0, 0.0
    for i, v in enumerate(speeds):
        out.append({"t": i * step, "speed_mps": v, "distance_m": dist, "altitude_m": alt})
        dist += v * step
        alt += v * step * grade
    return out


def test_a_steady_flat_run_has_ngp_equal_to_its_pace():
    assert ngp_speed(_samples([3.0] * 120)) == pytest.approx(3.0)


def test_surges_raise_ngp_above_the_average():
    speeds = ([2.4] * 24 + [3.6] * 24) * 5  # 2 min easy, 2 min hard, same average 3.0
    assert ngp_speed(_samples(speeds)) > 3.05


def test_uphill_raises_ngp_and_downhill_lowers_it():
    flat = ngp_speed(_samples([3.0] * 120))
    assert ngp_speed(_samples([3.0] * 120, grade=0.05)) > flat * 1.15
    assert ngp_speed(_samples([3.0] * 120, grade=-0.05)) < flat


def test_grade_factor_is_one_on_the_flat_and_clamped():
    assert grade_factor(0.0) == pytest.approx(1.0)
    assert grade_factor(0.9) == grade_factor(0.30)


def test_a_stop_with_altitude_drift_is_not_a_climb():
    rows = _samples([3.0] * 60)
    for r in rows[30:40]:  # standing still while the barometer drifts up 5 m
        r["distance_m"] = rows[30]["distance_m"]
        r["altitude_m"] = rows[30]["altitude_m"] + 5
        r["speed_mps"] = 0.0
    assert ngp_speed(rows) < 3.1


def test_too_few_samples_is_none():
    assert ngp_speed(_samples([3.0] * 10)) is None
    assert ngp_sec_per_km([]) is None


def test_run_tss_uses_ngp_when_present_then_pace_then_hr():
    run = {"discipline": "run", "duration_sec": 3025.0, "avg_pace_sec_per_km": 394.2, "avg_hr": 150}
    assert estimate_tss_method(run, PROFILE)[2] == "pace"
    assert estimate_tss_method(run, PROFILE)[0] == pytest.approx(55.7, abs=0.1)
    run["ngp_sec_per_km"] = 359.8
    tss, intensity, method = estimate_tss_method(run, PROFILE)
    assert method == "ngp"
    assert tss == pytest.approx(66.9, abs=0.1)
    assert estimate_tss_method({"discipline": "run", "duration_sec": 3600, "avg_hr": 150}, PROFILE)[2] == "hr"
