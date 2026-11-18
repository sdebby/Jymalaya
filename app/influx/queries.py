"""Flux query templates for Jymalaya reads."""

from __future__ import annotations

from app.config import get_settings


def _bucket() -> str:
    return get_settings().influx_bucket


def workouts_last_n_days(days: int = 28) -> str:
    """Pivoted `workouts` rows over the last `days` days (one row per activity)."""
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "workouts")
  |> pivot(rowKey: ["_time", "activity_id"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''


def daily_load_last_n_days(days: int = 28, discipline: str = "all") -> str:
    """CTL/ATL/TSB/TSS daily series for one discipline (default combined)."""
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "daily_load" and r.discipline == "{discipline}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''


def daily_load_all_last_n_days(days: int = 28) -> str:
    """
    Every discipline's daily series in one read (combined `all` included), one
    row per day per discipline, oldest first. The plan payload needs all five;
    one query here costs about what a single-discipline read does, where five
    separate reads cost five times that.

    group() before sort() for the reason vo2max_last_n_days documents: the
    `discipline` tag stays in the group key after pivot, so without it the sort
    only orders within each discipline's own table.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "daily_load")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"])
'''


def vo2max_last_n_days(days: int = 28) -> str:
    """
    Daily running/cycling VO2max over the last `days` days, one row per day per
    discipline: {time, discipline, vo2max}.

    Modelled on daily_load_last_n_days but deliberately not filtered to one
    discipline -- the panel draws both series and there is only one field, so a
    single read returns everything. Points are upserted at midnight UTC, so a
    (day, discipline) pair holds exactly one value and no last() pass is needed.

    group() before sort() is load-bearing: keep() leaves `discipline` in the
    group key, so without it the sort only orders within each discipline's own
    table and the caller gets two interleaved runs instead of one series.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "vo2max")
  |> pivot(rowKey: ["_time", "discipline"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "discipline", "vo2max"])
  |> group()
  |> sort(columns: ["_time"])
'''


def recovery_last_n_days(days: int = 28) -> str:
    """
    Daily HRV, resting HR and sleep over the last `days` days, one row per day:
    {time, hrv?, resting_hr?, sleep_sec?}. A day with only one reading has no column for
    the other (pivot leaves it out), which callers read as a gap.

    Points sit at midnight UTC (write_recovery), so a day holds one value per
    field and no last() pass is needed.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "recovery")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"])
'''


def latest_recovery_point() -> str:
    """
    The newest stored recovery point, or no rows when none is stored. The sync
    uses "none stored" to decide that the next pull is the full backfill to the
    first activity rather than the incremental last-sync window. Sleep has its
    own window (latest_sleep_point), so it does not count here.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d)
  |> filter(fn: (r) => r._measurement == "recovery" and r._field != "sleep_sec")
  |> keep(columns: ["_time", "_value"])
  |> group()
  |> last()
'''


def latest_sleep_point() -> str:
    """
    The newest stored night of sleep, or no rows when none is stored: the sync
    then backfills SLEEP_BACKFILL_DAYS instead of the incremental window.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d)
  |> filter(fn: (r) => r._measurement == "recovery" and r._field == "sleep_sec")
  |> keep(columns: ["_time", "_value"])
  |> group()
  |> last()
'''


def oldest_workout_point() -> str:
    """
    The oldest stored workout's point (one field per workout, as in
    workout_activity_ids_last_n_days), or no rows. Its `_time` is the true UTC
    instant, not the local calendar day; callers that need a day allow for it.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d)
  |> filter(fn: (r) => r._measurement == "workouts" and r._field == "is_brick_split")
  |> keep(columns: ["_time", "_value"])
  |> group()
  |> first()
'''


def all_events() -> str:
    """Latest state of every event, one row per goal_id (fields pivoted to columns).

    Storage keeps the pre-rename `goals` measurement and `goal_id` tag.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d)
  |> filter(fn: (r) => r._measurement == "goals")
  |> group(columns: ["goal_id", "_field"])
  |> last()
  |> group()
  |> map(fn: (r) => ({{ r with _value: string(v: r._value) }}))
  |> pivot(rowKey: ["goal_id"], columnKey: ["_field"], valueColumn: "_value")
'''


def event_by_id(event_id: str) -> str:
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d)
  |> filter(fn: (r) => r._measurement == "goals" and r.goal_id == "{event_id}")
  |> group(columns: ["goal_id", "_field"])
  |> last()
  |> group()
  |> map(fn: (r) => ({{ r with _value: string(v: r._value) }}))
  |> pivot(rowKey: ["goal_id"], columnKey: ["_field"], valueColumn: "_value")
'''


def workout_activity_ids_last_n_days(days: int = 28) -> str:
    """Distinct stored activity_id tags over the window (for incremental sync)."""
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "workouts" and r._field == "is_brick_split")
  |> keep(columns: ["activity_id"])
  |> group()
  |> distinct(column: "activity_id")
'''


def workout_by_activity_id(activity_id: str) -> str:
    """
    One stored workout by its `activity_id` tag.

    Every other workout read here is relative ("last N days"); analysis addresses
    a single activity, which may also be one synced minutes ago. The explicit
    future `stop` keeps a same-day point visible instead of falling off the
    implicit now() boundary.
    """
    escaped = str(activity_id).replace("\\", "\\\\").replace('"', '\\"')
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d, stop: 3650d)
  |> filter(fn: (r) => r._measurement == "workouts" and r.activity_id == "{escaped}")
  |> pivot(rowKey: ["_time", "activity_id"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''


def sampled_activity_ids(days: int = 28) -> str:
    """
    Distinct activity_id tags with FIT samples stored, over the window (a day
    wider than asked: a sample's UTC instant can sit a day off the workout's
    local date). The sync fetches a FIT file only for workouts not in here.
    """
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -{days + 1}d, stop: 1d)
  |> filter(fn: (r) => r._measurement == "workout_samples" and r._field == "t")
  |> keep(columns: ["activity_id"])
  |> group()
  |> distinct(column: "activity_id")
'''


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def workout_samples_for(activity_id: str) -> str:
    """One workout's 5 s samples, oldest first: {time, t, hr?, speed_mps?, ...}."""
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d, stop: 3650d)
  |> filter(fn: (r) => r._measurement == "workout_samples" and r.activity_id == "{_escape(activity_id)}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"])
'''


def workout_laps_for(activity_id: str) -> str:
    """One workout's laps, oldest first: {time, lap, duration_sec?, ...}."""
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d, stop: 3650d)
  |> filter(fn: (r) => r._measurement == "workout_laps" and r.activity_id == "{_escape(activity_id)}")
  |> pivot(rowKey: ["_time", "lap"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"])
'''


def workout_planned_for(activity_id: str) -> str:
    """The structured workout an activity was run against: {time, _value: json}."""
    return f'''
from(bucket: "{_bucket()}")
  |> range(start: -3650d, stop: 3650d)
  |> filter(fn: (r) => r._measurement == "workout_planned" and r.activity_id == "{_escape(activity_id)}" and r._field == "json")
  |> last()
'''
