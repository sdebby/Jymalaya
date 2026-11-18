"""
Decode a Garmin FIT activity file into the samples and laps Jymalaya stores.

The Personal API has no streams or laps endpoint: the original FIT file is the
only source of second-by-second data. The sync downloads it, this module turns
it into small rows, and the file is deleted straight after (personal_api.py).

Things easy to re-break:
- **No coordinates.** `position_lat` / `position_long` (and the lap start/end
  positions) are never read, the rule `maxpoly=0` kept on the old detail path:
  there is no map, and a GPS trace is the most personal thing in the file.
- **Timestamps are true UTC instants** (FIT stores UTC), so a sample point in
  InfluxDB follows the workout-timestamp invariant in CLAUDE.md.
- **Pauses are skipped, not bucketed**: a gap above MAX_SAMPLE_GAP_SEC between
  records is a stopped timer. Buckets are aligned on the activity clock, so a
  five-minute stop at a traffic light is not five minutes of zero speed.
- **RPE and feel are the athlete's own answers**, from the session message
  (`workout_rpe` 10-100 in tens, `workout_feel` 0/25/50/75/100; confirmed live
  2026-11-14). A feel of 0 is "Very Weak", a real answer, and either one can be
  there without the other. A file without them (an indoor ride saved with no
  rating) gives None, never 0.
- **Run cadence is steps per minute**: FIT stores strides (one foot) plus a
  fractional part, so it is doubled. Bike is rpm and swim strokes/min as stored.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

# A gap larger than this between consecutive records is a recording pause, not
# workout time -- the convention the HR-zone repair and the analysis use too.
MAX_SAMPLE_GAP_SEC = 60
# Resolution samples are stored at (the athlete's call). NGP on the 2026-11-04
# fartlek was identical at 1 s and 5 s.
SAMPLE_BUCKET_SEC = 5

# record field -> stored sample field. First present source wins.
_RECORD_FIELDS = {
    "hr": ("heart_rate",),
    "speed_mps": ("enhanced_speed", "speed"),
    "altitude_m": ("enhanced_altitude", "altitude"),
    "distance_m": ("distance",),
    "power_w": ("power",),
}
# lap field -> stored lap field (the names the analysis payload has always used).
_LAP_FIELDS = {
    "duration_sec": ("total_timer_time",),
    "elapsed_sec": ("total_elapsed_time",),
    "distance_m": ("total_distance",),
    "avg_hr": ("avg_heart_rate",),
    "max_hr": ("max_heart_rate",),
    "avg_speed_mps": ("enhanced_avg_speed", "avg_speed"),
    "avg_power_w": ("avg_power",),
    "norm_power_w": ("normalized_power",),
    "max_power_w": ("max_power",),
    "elevation_gain_m": ("total_ascent",),
}


def _num(value) -> float | None:
    """A finite float, keeping a real zero (0 W coasting, 0 m/s stopped)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# session field -> its FIT field number, for a profile that does not name it.
_RATING_FIELDS = {"workout_feel": 192, "workout_rpe": 193}


def _rating(frame) -> dict:
    """
    {"rpe": 1.0-10.0 | None, "feel": 0-100 | None} from a session message.
    RPE is stored in tenths of the 1-10 scale; feel keeps Garmin's 0-100 scale
    (0 Very Weak, 25 Weak, 50 Normal, 75 Strong, 100 Very Strong).
    """
    raw: dict = {}
    for f in frame.fields:
        for name, num in _RATING_FIELDS.items():
            if f.name == name or f.def_num == num:
                raw[name] = _num(f.value)
    rpe, feel = raw.get("workout_rpe"), raw.get("workout_feel")
    return {
        "rpe": round(rpe / 10.0, 1) if rpe is not None and 10 <= rpe <= 100 else None,
        "feel": float(round(feel)) if feel is not None and 0 <= feel <= 100 else None,
    }


def _first(msg: dict, names: tuple[str, ...]) -> float | None:
    for n in names:
        v = _num(msg.get(n))
        if v is not None:
            return v
    return None


def _cadence(msg: dict, discipline: str, whole: str, frac: str) -> float | None:
    c = _num(msg.get(whole))
    if c is None:
        return None
    c += _num(msg.get(frac)) or 0.0
    return c * 2 if discipline == "run" else c


def decode(path: Path | str) -> tuple[list[dict], list[dict], dict]:
    """
    (records, laps, rating) from a FIT file. `rating` is the athlete's
    {"rpe", "feel"} for the session, each None when not answered. Each record is {"time": aware UTC datetime,
    hr?, speed_mps?, altitude_m?, distance_m?, power_w?, cadence_raw?, frac?};
    each lap is the raw lap message minus positions. Discipline-specific
    shaping (cadence units) happens in to_samples / to_laps.
    """
    import fitdecode  # local import: only the sync and the backfill need it

    records: list[dict] = []
    laps: list[dict] = []
    rating: dict = {"rpe": None, "feel": None}
    with fitdecode.FitReader(str(path)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                raw = {f.name: f.value for f in frame.fields}
                ts = raw.get("timestamp")
                if not isinstance(ts, datetime):
                    continue
                rec: dict = {"time": ts}
                for out, names in _RECORD_FIELDS.items():
                    v = _first(raw, names)
                    if v is not None:
                        rec[out] = v
                if raw.get("cadence") is not None:
                    rec["cadence"] = _num(raw.get("cadence"))
                    rec["fractional_cadence"] = _num(raw.get("fractional_cadence"))
                records.append(rec)
            elif frame.name == "lap":
                laps.append(
                    {
                        f.name: f.value
                        for f in frame.fields
                        if "position" not in f.name and not f.name.startswith("unknown")
                    }
                )
            elif frame.name == "session":
                for key, value in _rating(frame).items():
                    if rating[key] is None:
                        rating[key] = value
    records.sort(key=lambda r: r["time"])
    return records, laps, rating


def to_samples(
    records: list[dict], discipline: str, bucket_sec: int = SAMPLE_BUCKET_SEC
) -> list[dict]:
    """
    Records averaged into `bucket_sec` buckets on the activity clock, pauses
    removed. Each sample: {"time": UTC datetime of the bucket start, "t": active
    seconds since the start, hr?, speed_mps?, altitude_m?, distance_m?, power_w?,
    cadence?}. Distance and altitude are averaged like the rest -- over 5 s the
    mean is the mid-bucket value, which is what a grade needs.
    """
    if not records:
        return []
    origin = records[0]["time"]
    previous = origin
    offset = 0.0
    buckets: dict[int, dict] = {}
    for r in records:
        gap = (r["time"] - previous).total_seconds()
        if gap > MAX_SAMPLE_GAP_SEC:
            offset += gap
        previous = r["time"]
        active = (r["time"] - origin).total_seconds() - offset
        if active < 0:
            continue
        slot = int(active // bucket_sec) * bucket_sec
        b = buckets.setdefault(slot, {"time": r["time"], "values": {}})
        for key in ("hr", "speed_mps", "altitude_m", "distance_m", "power_w"):
            if r.get(key) is not None:
                b["values"].setdefault(key, []).append(r[key])
        cad = _cadence(r, discipline, "cadence", "fractional_cadence")
        if cad is not None:
            b["values"].setdefault("cadence", []).append(cad)

    out = []
    for slot in sorted(buckets):
        b = buckets[slot]
        sample = {"time": b["time"], "t": slot}
        for key, values in b["values"].items():
            sample[key] = round(sum(values) / len(values), 2)
        if len(sample) > 2:
            out.append(sample)
    return out


def to_laps(laps: list[dict], discipline: str) -> list[dict]:
    """Laps as compact rows, oldest first: {"lap": n, "start": UTC datetime, ...}."""
    out = []
    ordered = sorted(
        (lap for lap in laps if isinstance(lap.get("start_time"), datetime)),
        key=lambda lap: lap["start_time"],
    )
    for i, lap in enumerate(ordered, start=1):
        row: dict = {"lap": i, "start": lap["start_time"]}
        for name, names in _LAP_FIELDS.items():
            v = _first(lap, names)
            if v is not None:
                row[name] = round(v, 2)
        whole, frac = (
            ("avg_running_cadence", "avg_fractional_cadence")
            if lap.get("avg_running_cadence") is not None
            else ("avg_cadence", "avg_fractional_cadence")
        )
        cad = _cadence(lap, discipline, whole, frac)
        if cad is not None:
            row["avg_cadence"] = round(cad, 1)
        if isinstance(lap.get("intensity"), str):
            row["intensity"] = lap["intensity"]
        out.append(row)
    return out


def hr_zones_from_records(records: list[dict], floors: list[float]) -> dict | None:
    """
    Time in each HR zone, integrated over the 1-second records against the
    zone floors (Z1..Z5, ascending). The repair path for a leg whose list
    numbers fail zones_plausible -- the same convention the library path used:
    HR below the Z1 floor belongs to no zone, and gaps over a minute are pauses.
    """
    if len(records) < 2 or not floors:
        return None
    zones = {f"Z{i}": 0.0 for i in range(1, len(floors) + 1)}
    for a, b in zip(records, records[1:]):
        hr = a.get("hr")
        dt = (b["time"] - a["time"]).total_seconds()
        if hr is None or dt <= 0 or dt > MAX_SAMPLE_GAP_SEC:
            continue
        zone = None
        for i, low in enumerate(floors, start=1):
            if hr >= low:
                zone = i
        if zone is not None:
            zones[f"Z{zone}"] += dt
    return {z: round(s, 3) for z, s in zones.items()}
