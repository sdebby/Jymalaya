"""
Normalized Graded Pace (NGP) for run rTSS, from the stored 5-second samples.

assets/tss_rtss_calculation_spec.md section 2 is the authority:

    NGP_speed = mean( rolling_30s(grade_adjusted_speed) ** 4 ) ** (1/4)
    IF        = NGP_speed / threshold_speed

Average pace undercounts any run that is not steady: the easy jog between
surges pulls the average down, though the surges cost more than they add to it.
On the 2026-11-04 fartlek average pace gave rTSS 56, TrainingPeaks 67.

The grade adjustment is computed here because the FIT file carries no
grade-adjusted speed (Garmin derives its own on the server, and the Personal
API does not expose it). It uses the Minetti et al. (2002) energy cost of
running on a slope, normalised to flat ground. Measured on the same fartlek:

    no grade adjustment         rTSS 60.6
    Garmin's own GAP stream     rTSS 62.0
    Minetti, 30 s grade window  rTSS 66.9   (TrainingPeaks: 67)

Things easy to re-break:
- **The grade window is ~30 s, centred.** Barometric altitude is noisy and the
  cost curve is steep, so a 5 s grade turns noise into pace. 60 s smoothed the
  hills away (65.2 on the fartlek).
- **The grade is clamped to +/-30%** (the edge of Minetti's measured range)
  and needs at least MIN_GRADE_DISTANCE_M of travel in its window, so a
  stationary sample with altitude drift is never a 100% climb.
- **Pauses were already removed** by the sampler (fit.to_samples), so the
  rolling window never averages across a stop.
"""

from __future__ import annotations

BUCKET_SEC = 5
ROLLING_SEC = 30
GRADE_WINDOW_SEC = 30
GRADE_CLAMP = 0.30
MIN_GRADE_DISTANCE_M = 5.0
# Fewer samples than this (2.5 minutes) is not a run worth normalising; the
# caller falls back to average pace.
MIN_SAMPLES = 30

_FLAT_COST = 3.6  # J/kg/m on level ground (Minetti's polynomial at g = 0)


def minetti_cost(grade: float) -> float:
    """Energy cost of running (J/kg/m) at `grade` (rise/run, e.g. 0.05)."""
    g = grade
    return 155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + _FLAT_COST


def grade_factor(grade: float) -> float:
    """How much harder than flat a metre at `grade` is (1.0 on the flat)."""
    g = max(-GRADE_CLAMP, min(GRADE_CLAMP, grade))
    return minetti_cost(g) / _FLAT_COST


def _grades(samples: list[dict], half: int) -> list[float]:
    out = []
    n = len(samples)
    for i in range(n):
        a, b = samples[max(0, i - half)], samples[min(n - 1, i + half)]
        da, db = a.get("distance_m"), b.get("distance_m")
        ha, hb = a.get("altitude_m"), b.get("altitude_m")
        if None in (da, db, ha, hb) or (db - da) < MIN_GRADE_DISTANCE_M:
            out.append(0.0)
            continue
        out.append((hb - ha) / (db - da))
    return out


def ngp_speed(samples: list[dict], bucket_sec: int = BUCKET_SEC) -> float | None:
    """
    NGP as a speed in m/s from samples carrying speed_mps (and, for the grade,
    distance_m + altitude_m). None when there are too few samples or no speed.
    A run with no altitude is normalised without grade -- still the 4th-power
    mean, which is most of the difference on a flat interval session.
    """
    rows = [s for s in samples if s.get("speed_mps") is not None]
    if len(rows) < MIN_SAMPLES:
        return None
    half = max(1, round(GRADE_WINDOW_SEC / bucket_sec / 2))
    grades = _grades(rows, half)
    adjusted = [s["speed_mps"] * grade_factor(g) for s, g in zip(rows, grades)]

    window = max(1, round(ROLLING_SEC / bucket_sec))
    rolling: list[float] = []
    total = 0.0
    for i, v in enumerate(adjusted):
        total += v
        if i >= window:
            total -= adjusted[i - window]
        if i >= window - 1:
            rolling.append(total / window)
    if not rolling:
        return None
    mean4 = sum(max(v, 0.0) ** 4 for v in rolling) / len(rolling)
    speed = mean4**0.25
    return speed if speed > 0 else None


def ngp_sec_per_km(samples: list[dict], bucket_sec: int = BUCKET_SEC) -> float | None:
    """NGP as a pace in seconds per km (the unit the athlete profile stores)."""
    speed = ngp_speed(samples, bucket_sec)
    return round(1000.0 / speed, 1) if speed else None
