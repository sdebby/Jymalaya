"""Simulated athlete with a known recovery time: the fit must recover it. No network, no data files."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import supercomp as sc  # noqa: E402

# Scaled like real run efficiency (about 1.55 W/bpm, a few percent of swing from load).
TRUE = {"tau1": 42.0, "tau2": 6.0, "p0": 1.524, "k1": 0.00004, "k2": 0.00012}


def simulate(seed: int, n_days: int = 100, noise: float = 0.02) -> tuple[dict, int]:
    rng = np.random.default_rng(seed)
    tss = np.zeros(n_days)
    for i in range(n_days):
        r = rng.random()
        tss[i] = 0 if r < 0.30 else rng.uniform(25, 60) if r < 0.75 else rng.uniform(85, 130)
    model = dict(TRUE)
    p = sc.perf_curve(model, tss)
    start = date(2026, 7, 1)
    daily = []
    ctl = atl = 40.0
    for i, t in enumerate(tss):
        ctl += (t - ctl) / 42
        atl += (t - atl) / 7
        daily.append({"time": f"{(start + timedelta(i)).isoformat()}T00:00:00+00:00",
                      "discipline": "all", "tss": float(t), "ctl": ctl, "atl": atl, "tsb": ctl - atl})
    workouts = []
    for i, t in enumerate(tss):
        if t >= 25:
            eff = p[i] + rng.normal(0, noise)
            workouts.append({"date": (start + timedelta(i)).isoformat(), "discipline": "run",
                             "duration_sec": 3000, "avg_hr": 145.0, "avg_power": eff * 145.0})
    truth = sc.forecast_peak(model, tss)["peak_day_offset"]
    return {"daily_load": daily, "workouts": workouts}, truth


def test_fit_recovers_peak_day():
    errors, truths = [], set()
    for seed in range(12):
        export, truth = simulate(seed)
        truths.add(truth)
        rep = sc.build_report(export)
        got = (date.fromisoformat(rep["supercompensation"]["ready_day"])
               - date.fromisoformat(rep["generated_for"])).days
        errors.append(abs(got - truth))
    assert len(truths) > 1 and True, truths   # the truth must vary, or the test proves nothing
    assert np.mean(errors) <= 1.0, errors
    assert max(errors) <= 2, errors


def test_few_runs_falls_back_and_says_so():
    export, _ = simulate(1)
    export["workouts"] = export["workouts"][:5]
    rep = sc.build_report(export)
    assert rep["method"] == "empirical_lag"
    assert rep["confidence"] == "low"
    assert rep["next_workout"]["when"] >= rep["generated_for"]


def test_target_tss_scales_with_fitness():
    export, _ = simulate(2)
    rep = sc.build_report(export)
    ctl = rep["now"]["ctl"]
    lo, hi = rep["next_workout"]["target_tss"]
    assert lo == round(1.3 * ctl) and hi == round(1.7 * ctl)


if __name__ == "__main__":
    test_fit_recovers_peak_day()
    test_few_runs_falls_back_and_says_so()
    test_target_tss_scales_with_fitness()
    print("ok")
