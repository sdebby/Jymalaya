"""
Thin InfluxDB 2.x wrapper for Jymalaya AI Coach.

Idempotency: a workout point uses `activity_id` as a tag and the activity's start
time as the point timestamp, so re-syncing the same activity overwrites the same
point in place -- no separate dedup pass needed (InfluxDB upserts on
measurement + tag set + timestamp).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from app.config import get_settings

WORKOUTS = "workouts"
DAILY_LOAD = "daily_load"
VO2MAX = "vo2max"
RECOVERY = "recovery"  # daily HRV (ms) + resting HR (bpm) + sleep (s)
ATHLETE_PROFILE = "athlete_profile"
# Events keep their pre-rename storage names -- the `goals` measurement and
# `goal_id` tag -- so renaming them never orphans the athlete's saved events.
EVENTS = "goals"

# Event fields are split by storage type. Text fields are written as strings;
# the genuinely numeric fields are written as floats so they match InfluxDB's
# per-measurement field type (a string write to a float field is rejected 422).
# all_events()/event_by_id() cast every _value to string before pivoting, so reads
# stay schema-agnostic regardless of which type a field is stored as.
_EVENT_STR_FIELDS = ("name", "type", "race_date", "location_name", "icon", "website", "notes")
_EVENT_NUM_FIELDS = ("location_lat", "location_lon", "target_finish_time_sec")

# Numeric workout fields written to InfluxDB when present (non-None).
_WORKOUT_NUM_FIELDS = (
    "tss",
    "duration_sec",
    # Swims only: rest-excluded seconds, the basis of sTSS (see the metrics
    # calculator). Absent on other disciplines and on pre-2026-07-27 rows.
    "moving_duration_sec",
    "distance_m",
    "avg_hr",
    "max_hr",
    "avg_power",
    "normalized_power",
    "avg_pace_sec_per_km",
    "avg_pace_sec_per_100m",
    "intensity_factor",
    "elevation_gain_m",
    "elevation_loss_m",
    "calories",
    "max_power",
    "avg_cadence",
    # Runs with FIT samples: Normalized Graded Pace, the rTSS basis.
    "ngp_sec_per_km",
    # Garmin's Training Effect, 0.0-5.0 (0.0 is a real value and is written).
    "aerobic_te",
    "anaerobic_te",
    # The athlete's rating from the FIT file: RPE 1-10, feel 0-100 (0 = Very
    # Weak, a real value and written).
    "rpe",
    "feel",
)
# `date` is the athlete's LOCAL calendar date and is stored rather than re-derived
# from the point timestamp: converting an instant back to a calendar day needs the
# timezone the activity happened in, which the point alone does not carry.
# `tss_method` names the TSS basis (power / ngp / pace / css / hr).
# `te_label` is Garmin's primary training benefit (TEMPO, VO2MAX, ...).
_WORKOUT_STR_FIELDS = ("title", "zones_time_sec", "garmin_type", "date", "tss_method", "te_label")

# A workout's FIT detail (app/garmin/fit.py), one measurement each. Kept off the
# workout point on purpose: every workouts read pivots all of its fields, and a
# planned-workout JSON or hundreds of samples there would ride along into every
# dashboard and Claude payload read.
WORKOUT_SAMPLES = "workout_samples"
WORKOUT_LAPS = "workout_laps"
WORKOUT_PLANNED = "workout_planned"
_SAMPLE_FIELDS = ("t", "hr", "speed_mps", "altitude_m", "distance_m", "power_w", "cadence")
_LAP_NUM_FIELDS = (
    "duration_sec", "elapsed_sec", "distance_m", "avg_hr", "max_hr", "avg_speed_mps",
    "avg_power_w", "norm_power_w", "max_power_w", "elevation_gain_m", "avg_cadence",
)
# Points per write call: a 5-hour ride is ~3600 samples.
_WRITE_BATCH = 5000


_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def _naive(raw) -> datetime | None:
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _parse_ts(workout: dict) -> datetime:
    """
    The workout's true UTC instant, for use as the point timestamp.

    `start_time_utc` (Garmin's startTimeGMT) is authoritative. `start_time` and
    `date` are LOCAL wall clock, so they are converted rather than relabelled --
    stamping local time as UTC pushes recent points into the future, where every
    `range(start: -Nd)` read (which stops at now()) silently skips them.
    """
    utc = _naive(workout.get("start_time_utc") or "")
    if utc is not None:
        return utc.replace(tzinfo=timezone.utc)
    local = _naive(workout.get("start_time") or workout.get("date") or "")
    if local is not None:
        # A naive datetime is interpreted in the system timezone, which applies
        # the DST rules in force on that date.
        return local.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


# Suffixes split_bricks gives a brick's per-discipline legs (app/garmin/parser.py).
_LEG_SUFFIXES = ("bike", "run", "swim", "leg")


def drop_split_brick_parents(rows: list[dict]) -> list[dict]:
    """
    Drop stored multisport parents whose split legs are also stored.

    Bricks are always split into per-discipline entries ({parent}_bike/_run).
    A parent that reached InfluxDB un-split -- written by a sync whose leg
    enrichment came back empty -- keeps its own point, and that point carries
    the WHOLE session's duration and distance. Any reader that sees it next to
    the legs counts the brick twice, so every consumer of stored workouts
    filters through here.
    """
    ids = {str(r.get("activity_id")) for r in rows}
    return [
        r
        for r in rows
        if not any(f"{r.get('activity_id')}_{s}" in ids for s in _LEG_SUFFIXES)
    ]


class InfluxRepo:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.influx_bucket
        self._org = s.influx_org
        self._client = InfluxDBClient(url=s.influx_url, token=s.influx_token, org=s.influx_org)
        self._write = self._client.write_api(write_options=SYNCHRONOUS)
        self._query = self._client.query_api()

    # -- health -------------------------------------------------------------
    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    # -- writes -------------------------------------------------------------
    def write_workouts(self, workouts: list[dict]) -> int:
        points = []
        for w in workouts:
            if not w.get("activity_id"):
                continue
            p = (
                Point(WORKOUTS)
                .tag("activity_id", str(w["activity_id"]))
                .tag("discipline", w.get("discipline") or "other")
                .field("is_brick_split", bool(w.get("is_brick_split")))
                .time(_parse_ts(w), WritePrecision.S)
            )
            if w.get("session_id"):
                p.tag("session_id", str(w["session_id"]))
            for f in _WORKOUT_NUM_FIELDS:
                if w.get(f) is not None:
                    p.field(f, float(w[f]))
            for f in _WORKOUT_STR_FIELDS:
                if w.get(f):
                    p.field(f, str(w[f]))
            points.append(p)
        if points:
            self._write.write(bucket=self._bucket, org=self._org, record=points)
        return len(points)

    def write_workout_detail(self, workouts: list[dict]) -> int:
        """
        Store the `_detail` (samples, laps, planned workout) the sync attached
        to each workout. Samples sit at their own UTC instant and laps at their
        start, both tagged with the stored activity_id (a brick leg's synthetic
        id), so a re-write lands on the same points. Returns samples written.
        """
        points: list[Point] = []
        samples_written = 0
        for w in workouts:
            detail = w.get("_detail") or {}
            aid = str(w.get("activity_id") or "")
            if not aid or not detail.get("samples"):
                continue
            disc = w.get("discipline") or "other"
            for s in detail["samples"]:
                p = (
                    Point(WORKOUT_SAMPLES)
                    .tag("activity_id", aid)
                    .tag("discipline", disc)
                    .time(s["time"], WritePrecision.S)
                )
                for f in _SAMPLE_FIELDS:
                    if s.get(f) is not None:
                        p.field(f, float(s[f]))
                points.append(p)
                samples_written += 1
            for lap in detail.get("laps") or []:
                p = (
                    Point(WORKOUT_LAPS)
                    .tag("activity_id", aid)
                    .tag("lap", str(lap["lap"]))
                    .time(lap["start"], WritePrecision.S)
                )
                for f in _LAP_NUM_FIELDS:
                    if lap.get(f) is not None:
                        p.field(f, float(lap[f]))
                if lap.get("intensity"):
                    p.field("intensity", str(lap["intensity"]))
                points.append(p)
            if detail.get("planned"):
                points.append(
                    Point(WORKOUT_PLANNED)
                    .tag("activity_id", aid)
                    .field("json", json.dumps(detail["planned"]))
                    .time(_parse_ts(w), WritePrecision.S)
                )
        for i in range(0, len(points), _WRITE_BATCH):
            self._write.write(bucket=self._bucket, org=self._org, record=points[i : i + _WRITE_BATCH])
        return samples_written

    def write_daily_load(self, rows: list[dict]) -> int:
        """rows: {date: 'YYYY-MM-DD', discipline: str, tss, ctl, atl, tsb}."""
        points = []
        for r in rows:
            p = (
                Point(DAILY_LOAD)
                .tag("discipline", r.get("discipline") or "all")
                .time(_parse_date(r["date"]), WritePrecision.S)
            )
            for f in ("tss", "ctl", "atl", "tsb"):
                if r.get(f) is not None:
                    p.field(f, float(r[f]))
            points.append(p)
        if points:
            self._write.write(bucket=self._bucket, org=self._org, record=points)
        return len(points)

    def write_vo2max(self, rows: list[dict]) -> int:
        """
        rows: {date: 'YYYY-MM-DD', discipline: 'run'|'bike', vo2max: float}.

        A daily series, so the point is stamped at MIDNIGHT UTC from the calendar
        date (_parse_date) exactly like write_daily_load -- not at an instant.
        _row_date in app/metrics/calculator.py documents why reading the UTC date
        of such a point back as the calendar day is safe, and why the workout
        rule (true UTC instant plus a separate `date` field) does not apply here.

        InfluxDB keys the point on measurement + discipline + timestamp, so a
        re-sync of the same span and the one-off year backfill both overwrite
        each day in place. That idempotency is what lets the sync re-pull a fixed
        window every time with no dedup pass.
        """
        points = []
        for r in rows:
            if r.get("vo2max") is None or not r.get("date"):
                continue
            points.append(
                Point(VO2MAX)
                .tag("discipline", r.get("discipline") or "run")
                .field("vo2max", float(r["vo2max"]))
                .time(_parse_date(r["date"]), WritePrecision.S)
            )
        if points:
            self._write.write(bucket=self._bucket, org=self._org, record=points)
        return len(points)

    def write_recovery(self, rows: list[dict]) -> int:
        """
        rows: {date: 'YYYY-MM-DD', hrv?, resting_hr?, sleep_sec?} (each float | None).

        Midnight UTC from the calendar date, the write_vo2max convention. Each
        field is written only when present: InfluxDB merges fields on the same
        measurement + timestamp, so a day re-pulled with only one reading keeps
        the other one already stored. Re-pulls overwrite in place.
        """
        points = []
        for r in rows:
            if not r.get("date"):
                continue
            p = Point(RECOVERY).time(_parse_date(r["date"]), WritePrecision.S)
            wrote = False
            for f in ("hrv", "resting_hr", "sleep_sec"):
                if r.get(f) is not None:
                    p.field(f, float(r[f]))
                    wrote = True
            if wrote:
                points.append(p)
        if points:
            self._write.write(bucket=self._bucket, org=self._org, record=points)
        return len(points)

    def write_athlete_profile(self, profile: dict) -> None:
        p = Point(ATHLETE_PROFILE).time(datetime.now(timezone.utc), WritePrecision.S)
        wrote = False
        for k, v in (profile or {}).items():
            if isinstance(v, (int, float)):
                p.field(k, float(v))
                wrote = True
        if wrote:
            self._write.write(bucket=self._bucket, org=self._org, record=p)

    def write_event(self, event: dict) -> None:
        """
        Upsert an event. Each save writes a fresh point (timestamp = now) tagged by
        goal_id (its event_id); reads take the last point per goal_id, so the newest save wins.
        Text fields are written every time (cleared -> empty string) so PUT has
        replace semantics. Numeric fields are written only when set: an unset
        numeric field can't be represented as a float, and reads default it to
        None anyway.
        """
        import json

        p = Point(EVENTS).tag("goal_id", str(event["event_id"])).time(
            datetime.now(timezone.utc), WritePrecision.S
        )
        for f in _EVENT_STR_FIELDS:
            val = event.get(f)
            p.field(f, "" if val is None else str(val))
        for f in _EVENT_NUM_FIELDS:
            val = event.get(f)
            if val not in (None, ""):
                try:
                    p.field(f, float(val))
                except (TypeError, ValueError):
                    pass
        p.field("sub_goals", json.dumps(event.get("sub_goals") or []))
        p.field("participating", event.get("participating", True) is not False)
        self._write.write(bucket=self._bucket, org=self._org, record=p)

    def delete_all_data(self) -> None:
        """Full reset: delete every point in the bucket (all measurements)."""
        self._client.delete_api().delete(
            "1970-01-01T00:00:00Z",
            "2100-01-01T00:00:00Z",
            "",  # empty predicate matches everything in the time range
            bucket=self._bucket,
            org=self._org,
        )

    def delete_event(self, event_id: str) -> None:
        self._client.delete_api().delete(
            "1970-01-01T00:00:00Z",
            "2100-01-01T00:00:00Z",
            f'_measurement="{EVENTS}" AND goal_id="{event_id}"',
            bucket=self._bucket,
            org=self._org,
        )

    # -- reads --------------------------------------------------------------
    # Internal Flux columns dropped from flattened read results.
    _DROP_COLS = ("result", "table", "_start", "_stop", "_measurement")

    def query(self, flux: str) -> list[dict]:
        """
        Run a Flux query and flatten each record to a plain dict. Works for both
        pivoted results (fields as columns) and raw long-format results (_field/
        _value), since it reads from rec.values rather than assuming a schema.
        """
        out: list[dict] = []
        for table in self._query.query(flux, org=self._org):
            for rec in table.records:
                row = {
                    k: v for k, v in rec.values.items() if k not in self._DROP_COLS
                }
                # Normalize the timestamp key to "time" for callers.
                if "_time" in row:
                    row["time"] = row.pop("_time")
                out.append(row)
        return out

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
