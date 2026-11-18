"""
One workout's stored detail -- samples, laps, planned workout -- read back from
InfluxDB for the post-workout analysis (PRD section 7.9).

Since 2026-11-06 the Analyze button makes no Garmin call: the sync stores each
workout's FIT samples (5 s, no GPS), laps and planned workout
(app/garmin/personal_api.py), and this module reshapes them into exactly what
the old detail fetch produced, so app/metrics/analysis.py and the analysis
prompt read the same keys as before:

    stream:  {"bucket_sec", "sample_count", "samples": [{"t", "hr"?, "power_w"?,
              "speed_mps"?, "elevation_m"?, "cadence"?, "distance_m"?}]}
    laps:    [{"lap", "duration_sec"?, "distance_m"?, "avg_hr"?, ...}]
    planned: {"name", "steps": [...]} or None
"""

from __future__ import annotations

import json
import math

from app.influx.client import InfluxRepo
from app.influx.queries import workout_laps_for, workout_planned_for, workout_samples_for

# The analysis resolution: 30 s buckets, widened so at most MAX_BUCKETS reach
# the prompt (a 5-hour ride at 30 s would be 600 rows).
DEFAULT_BUCKET_SEC = 30
MAX_BUCKETS = 240
# stored sample field -> analysis stream key
_STREAM_KEYS = {
    "hr": "hr",
    "power_w": "power_w",
    "speed_mps": "speed_mps",
    "altitude_m": "elevation_m",
    "cadence": "cadence",
    "distance_m": "distance_m",
}
_LAP_KEYS = (
    "duration_sec", "distance_m", "avg_hr", "max_hr", "avg_power_w", "norm_power_w",
    "max_power_w", "avg_speed_mps", "avg_cadence", "elevation_gain_m",
)


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def bucket_seconds(duration_sec, bucket_sec: int = DEFAULT_BUCKET_SEC) -> int:
    """The requested bucket, widened to keep the series under MAX_BUCKETS."""
    duration = _num(duration_sec) or 0.0
    needed = duration / MAX_BUCKETS if duration > 0 else 0
    if needed <= bucket_sec:
        return bucket_sec
    return int(math.ceil(needed / bucket_sec) * bucket_sec)


def bucket_stream(rows: list[dict], duration_sec=None) -> dict | None:
    """Stored 5 s samples averaged into the analysis buckets (pauses already removed)."""
    bucket = bucket_seconds(duration_sec)
    buckets: dict[int, dict[str, list[float]]] = {}
    for r in rows:
        t = _num(r.get("t"))
        if t is None or t < 0:
            continue
        slot = int(t // bucket) * bucket
        target = buckets.setdefault(slot, {})
        for src, key in _STREAM_KEYS.items():
            v = _num(r.get(src))
            if v is not None:
                target.setdefault(key, []).append(v)
    samples = []
    for slot in sorted(buckets):
        sample: dict = {"t": slot}
        for key, values in buckets[slot].items():
            sample[key] = round(sum(values) / len(values), 2)
        if len(sample) > 1:
            samples.append(sample)
    if not samples:
        return None
    return {"bucket_sec": bucket, "sample_count": len(samples), "samples": samples}


def stored_samples(repo: InfluxRepo, activity_id: str) -> list[dict]:
    return repo.query(workout_samples_for(activity_id))


def stored_laps(repo: InfluxRepo, activity_id: str) -> list[dict]:
    out = []
    for r in repo.query(workout_laps_for(activity_id)):
        try:
            row: dict = {"lap": int(r.get("lap"))}
        except (TypeError, ValueError):
            continue
        for key in _LAP_KEYS:
            v = _num(r.get(key))
            if v is not None:
                row[key] = round(v, 2)
        if r.get("intensity"):
            row["intensity"] = r["intensity"]
        out.append(row)
    return sorted(out, key=lambda x: x["lap"])


def stored_planned(repo: InfluxRepo, activity_id: str) -> dict | None:
    for r in repo.query(workout_planned_for(activity_id)):
        try:
            planned = json.loads(r.get("_value") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(planned, dict) and planned.get("steps"):
            return planned
    return None
