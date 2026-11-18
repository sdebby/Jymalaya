"""
Derived per-workout metrics (app/metrics/analysis.py).

Pure functions over a bucketed sample stream -- no fakes, no network. These
numbers are computed in code precisely so they are checkable, so this file is
where the arithmetic is pinned.
"""

from __future__ import annotations

from app.metrics import analysis

HOUR = 3600


def _samples(n: int, *, hr, power=None, speed=None, cadence=None, bucket=30):
    """n buckets, each metric given as a value or a callable of the index."""

    def at(spec, i):
        return spec(i) if callable(spec) else spec

    out = []
    for i in range(n):
        s = {"t": i * bucket, "hr": at(hr, i)}
        if power is not None:
            s["power_w"] = at(power, i)
        if speed is not None:
            s["speed_mps"] = at(speed, i)
        if cadence is not None:
            s["cadence"] = at(cadence, i)
        out.append(s)
    return out


# --- aerobic decoupling ------------------------------------------------------


def test_decoupling_zero_when_output_and_hr_hold():
    samples = _samples(120, hr=140, power=200)
    out = analysis.aerobic_decoupling(samples, HOUR)
    assert out["status"] == "ok"
    assert out["metric"] == "Pw:HR"
    assert out["decoupling_pct"] == 0.0


def test_decoupling_positive_when_hr_drifts_up():
    """Same watts, higher heart rate in the second half -- classic cardiac drift."""
    samples = _samples(120, hr=lambda i: 140 if i < 60 else 154, power=200)
    out = analysis.aerobic_decoupling(samples, HOUR)
    # 200/140 = 1.4286, 200/154 = 1.2987 -> ~9.1% decoupling.
    assert 9.0 < out["decoupling_pct"] < 9.2


def test_decoupling_uses_speed_without_power():
    samples = _samples(120, hr=150, speed=3.3)
    out = analysis.aerobic_decoupling(samples, HOUR)
    assert out["metric"] == "Pa:HR"


def test_decoupling_gated_on_short_sessions():
    """Over 20 minutes the number describes the warmup, not durability."""
    out = analysis.aerobic_decoupling(_samples(40, hr=140, power=200), 20 * 60)
    assert out["status"] == "not_applicable"
    assert "shorter than" in out["reason"]


def test_decoupling_skipped_without_output():
    out = analysis.aerobic_decoupling(_samples(120, hr=140), HOUR)
    assert out["status"] == "not_applicable"


# --- half splits -------------------------------------------------------------


def test_half_splits_report_a_fade():
    samples = _samples(100, hr=150, speed=lambda i: 3.5 if i < 50 else 3.15)
    out = analysis.half_splits(samples, "run")
    assert out["status"] == "ok"
    assert out["speed_mps"]["change_pct"] == -10.0
    # Runners read pace, so the split is mirrored into sec/km.
    assert out["pace_sec_per_km"]["first_half"] == round(1000 / 3.5, 1)


def test_half_splits_need_enough_samples():
    assert analysis.half_splits(_samples(2, hr=150), "run")["status"] == "not_applicable"


# --- power zones -------------------------------------------------------------


def test_power_zones_split_by_percent_ftp():
    # 30 buckets at 100 W (Z1 at FTP 250 = 40%) and 30 at 200 W (80% = Z3).
    samples = _samples(60, hr=140, power=lambda i: 100 if i < 30 else 200)
    out = analysis.power_zone_split(samples, 250, 30)
    assert out["status"] == "ok"
    assert out["seconds"]["Z1"] == 900
    assert out["seconds"]["Z3"] == 900
    assert out["percent"]["Z2"] == 0.0


def test_power_zones_need_an_ftp():
    samples = _samples(10, hr=140, power=200)
    assert analysis.power_zone_split(samples, None, 30)["status"] == "not_applicable"


def test_power_zones_need_power():
    assert analysis.power_zone_split(_samples(10, hr=140), 250, 30)["status"] == "not_applicable"


# --- normalized power --------------------------------------------------------


def test_stream_np_equals_average_on_steady_power():
    samples = _samples(20, hr=140, power=200)
    assert analysis.stream_normalized_power(samples, 30) == 200.0


def test_stream_np_exceeds_average_on_variable_power():
    """NP punishes surges: the fourth power weights the hard buckets."""
    samples = _samples(40, hr=140, power=lambda i: 100 if i % 2 else 300)
    np_w = analysis.stream_normalized_power(samples, 30)
    average = 200.0
    assert np_w > average


# --- cadence -----------------------------------------------------------------


def test_cadence_stability_flat_series():
    out = analysis.cadence_stability(_samples(10, hr=140, cadence=90))
    assert out["status"] == "ok"
    assert out["mean"] == 90.0
    assert out["std_dev"] == 0.0


def test_cadence_stability_needs_cadence():
    assert analysis.cadence_stability(_samples(10, hr=140))["status"] == "not_applicable"


# --- the assembled block -----------------------------------------------------


def test_derived_metrics_unavailable_without_a_stream():
    out = analysis.build_derived_metrics({"discipline": "run"}, None, {"ftp_w": 250})
    assert out["status"] == "unavailable"


def test_derived_metrics_include_power_zones_only_for_bikes():
    stream = {"bucket_sec": 30, "samples": _samples(120, hr=140, power=200, cadence=90)}
    profile = {"ftp_w": 250}

    bike = analysis.build_derived_metrics(
        {"discipline": "bike", "duration_sec": HOUR}, stream, profile
    )
    assert "power_zones" in bike
    assert bike["normalized_power_from_stream_w"] == 200.0

    run = analysis.build_derived_metrics(
        {"discipline": "run", "duration_sec": HOUR}, stream, profile
    )
    assert "power_zones" not in run
    assert run["aerobic_decoupling"]["status"] == "ok"
