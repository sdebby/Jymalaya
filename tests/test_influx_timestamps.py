"""
The workout point timestamp must be a true instant.

Garmin gives a local wall clock (`startTimeLocal`) alongside the real UTC one
(`startTimeGMT`). Stamping the local string as UTC pushes recent points into the
future, where every `range(start: -Nd)` read -- whose implicit stop is now() --
skips them, and pushes evening workouts onto the next calendar day.

These assert behaviour that holds in any machine timezone rather than pinning a
fixed offset, so they are meaningful on the Windows dev box too (TZ switching
via tzset is POSIX-only).
"""

from datetime import date, datetime, timezone

from app.influx.client import _parse_ts


def test_utc_start_time_is_authoritative():
    # startTimeGMT wins over the local wall clock, whatever the machine's zone.
    ts = _parse_ts(
        {
            "start_time": "2026-07-26 19:15:44",
            "start_time_utc": "2026-07-26 16:15:44",
            "date": "2026-07-26",
        }
    )
    assert ts == datetime(2026, 7, 26, 16, 15, 44, tzinfo=timezone.utc)


def test_local_start_time_round_trips_to_the_same_wall_clock():
    # No startTimeGMT (e.g. a brick leg summary without it): the local wall
    # clock is converted, so reading the instant back in local time recovers it.
    wall = datetime(2026, 7, 26, 19, 15, 44)
    ts = _parse_ts({"start_time": "2026-07-26 19:15:44"})
    assert ts.tzinfo is not None
    assert ts.astimezone().replace(tzinfo=None) == wall


def test_local_start_time_is_converted_not_relabelled():
    # The actual bug: the naive wall clock was given tzinfo=utc as-is. Off a
    # UTC machine the corrected instant must differ from that relabelling.
    # The offset is the one in force on the workout's own date, not today's:
    # a July workout keeps its summer offset after DST ends.
    ts = _parse_ts({"start_time": "2026-07-26 19:15:44"})
    relabelled = datetime(2026, 7, 26, 19, 15, 44, tzinfo=timezone.utc)
    offset = datetime(2026, 7, 26, 19, 15, 44).astimezone().utcoffset()
    if offset:
        assert ts != relabelled
        assert ts == relabelled - offset
    else:
        assert ts == relabelled


def test_workout_finished_now_does_not_land_in_the_future():
    # The regression that hid a just-synced activity from every read.
    ts = _parse_ts({"start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    assert ts <= datetime.now(timezone.utc)


def test_date_only_fallback_keeps_its_local_calendar_day():
    # Midnight local, so the UTC instant can fall on the previous day -- but
    # converted back to local it is still the athlete's date.
    ts = _parse_ts({"date": "2026-07-26"})
    assert ts.astimezone().date() == date(2026, 7, 26)


def test_unparseable_falls_back_to_now():
    ts = _parse_ts({"start_time": "not a timestamp"})
    assert abs((datetime.now(timezone.utc) - ts).total_seconds()) < 5
