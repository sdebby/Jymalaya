"""
Incremental sync: the pull window shrinks to the days since the last successful
sync, FIT samples are fetched only for workouts without stored samples, daily load is
recomputed from the full stored history, and a failed attempt preserves the
previous last_sync. Fakes only -- no Garmin/InfluxDB/network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import sync_service
from app.garmin.client import GarminPullError


def _iso_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# --- _window_days ----------------------------------------------------------
def test_window_full_when_never_synced():
    assert sync_service._window_days({"last_sync": None}, 28) == 28
    assert sync_service._window_days({}, 28) == 28


def test_window_days_since_last_sync_plus_overlap():
    st = {"last_sync": _iso_days_ago(10)}
    assert sync_service._window_days(st, 28) == 10 + sync_service.OVERLAP_DAYS


def test_window_floor_when_synced_today():
    st = {"last_sync": _iso_days_ago(0)}
    assert sync_service._window_days(st, 28) == sync_service.OVERLAP_DAYS


def test_window_capped_at_requested():
    st = {"last_sync": _iso_days_ago(40)}
    assert sync_service._window_days(st, 28) == 28


def test_window_full_on_corrupt_timestamp():
    assert sync_service._window_days({"last_sync": "not-a-date"}, 28) == 28


# --- _stored_base_ids ------------------------------------------------------
class _IdRepo:
    def __init__(self, rows):
        self.rows = rows

    def query(self, flux):
        return self.rows


def test_stored_base_ids_strips_brick_suffixes():
    repo = _IdRepo(
        [
            {"activity_id": "111"},
            {"activity_id": "222_bike"},
            {"activity_id": "222_run"},
            {"_value": "333"},
        ]
    )
    assert sync_service._stored_base_ids(repo, 28) == {"111", "222", "333"}


def test_stored_base_ids_empty_on_query_failure():
    class Boom:
        def query(self, flux):
            raise RuntimeError("influx down")

    assert sync_service._stored_base_ids(Boom(), 28) == set()


# --- _vo2max_days ----------------------------------------------------------
def test_vo2max_follows_the_incremental_window_when_readings_are_stored():
    repo = _IdRepo([{"discipline": "run", "vo2max": 46.2}])
    assert sync_service._vo2max_days(repo, 3) == 3


def test_vo2max_pulls_the_full_span_when_none_are_stored():
    assert sync_service._vo2max_days(_IdRepo([]), 3) == sync_service.VO2MAX_WINDOW_DAYS


def test_vo2max_follows_a_wider_first_sync_when_none_are_stored():
    first = sync_service.FIRST_SYNC_DAYS
    assert sync_service._vo2max_days(_IdRepo([]), first) == first


def test_vo2max_falls_back_to_the_window_on_query_failure():
    class Boom:
        def query(self, flux):
            raise RuntimeError("influx down")

    assert sync_service._vo2max_days(Boom(), 3) == 3


def test_sync_pulls_vo2max_over_the_incremental_window(monkeypatch, tmp_path):
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    sync_service._save_status({"last_sync": _iso_days_ago(1), "status": "ok", "error": None})
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: True)
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: [])
    pulled = {}
    monkeypatch.setattr(
        sync_service, "pull_vo2max", lambda **kw: pulled.update(kw) or []
    )

    # Workouts are stored, so the window follows last_sync.
    monkeypatch.setattr(sync_service, "_history_blank", lambda repo: False)

    class FakeRepo:
        def query(self, flux):
            return [{"discipline": "run", "vo2max": 46.2}] if '"vo2max"' in flux else []

        def write_workouts(self, w):
            return 0

        def write_daily_load(self, rows):
            pass

        def write_vo2max(self, rows):
            return 0

        def write_recovery(self, rows):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)

    sync_service.run_sync()
    assert pulled == {"days": 1 + sync_service.OVERLAP_DAYS}


# --- first sync into an empty store ----------------------------------------
def test_history_blank_reads_the_oldest_workout():
    assert sync_service._history_blank(_IdRepo([])) is True
    assert sync_service._history_blank(_IdRepo([{"_value": False}])) is False


def test_history_not_blank_when_the_store_is_unreadable():
    class Boom:
        def query(self, flux):
            raise RuntimeError("influx down")

    assert sync_service._history_blank(Boom()) is False


def _pulled_days(monkeypatch, tmp_path, last_sync, stored_rows):
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    if last_sync:
        sync_service._save_status({"last_sync": last_sync, "status": "ok", "error": None})
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: True)
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])
    pulled = {}
    monkeypatch.setattr(
        sync_service, "pull_activities", lambda **kw: pulled.update(kw) or []
    )

    class FakeRepo:
        def query(self, flux):
            return stored_rows if "first()" in flux and "workouts" in flux else []

        def write_workouts(self, w):
            return 0

        def write_daily_load(self, rows):
            pass

        def write_vo2max(self, rows):
            return 0

        def write_recovery(self, rows):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)
    summary = sync_service.run_sync()
    assert summary["window_days"] == pulled["days"]
    return pulled["days"]


def test_first_sync_pulls_sixty_days(monkeypatch, tmp_path):
    assert _pulled_days(monkeypatch, tmp_path, None, []) == 60


def test_empty_store_pulls_sixty_days_even_with_a_last_sync(monkeypatch, tmp_path):
    # A full data reset wipes InfluxDB but leaves the sync status behind.
    assert _pulled_days(monkeypatch, tmp_path, _iso_days_ago(1), []) == 60


def test_stored_history_keeps_the_incremental_window(monkeypatch, tmp_path):
    days = _pulled_days(monkeypatch, tmp_path, _iso_days_ago(1), [{"_value": False}])
    assert days == 1 + sync_service.OVERLAP_DAYS


# --- the sync hands the stored-sample ids to the pull ----------------------
def test_sampled_ids_read_from_the_store():
    class Repo:
        def query(self, flux):
            assert "workout_samples" in flux
            return [{"activity_id": "111"}, {"_value": "999_run"}]

    assert sync_service._sampled_ids(Repo(), 5) == {"111", "999_run"}


def test_sampled_ids_fall_back_to_empty_on_a_store_failure():
    class Repo:
        def query(self, flux):
            raise RuntimeError("influx down")

    assert sync_service._sampled_ids(Repo(), 5) == set()


# --- full pipeline behaviors ------------------------------------------------
def test_failed_sync_preserves_last_sync(monkeypatch, tmp_path):
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    prev_sync = _iso_days_ago(2)
    sync_service._save_status(
        {"last_sync": prev_sync, "activity_count": 12, "status": "ok", "error": None}
    )

    class FakeRepo:
        def query(self, flux):
            return []

        def write_vo2max(self, rows):
            return 0


        def write_recovery(self, rows):

            return 0

        def close(self):
            pass

    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)

    def boom(**kw):
        raise GarminPullError("login failed")

    monkeypatch.setattr(sync_service, "pull_activities", boom)
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])

    with pytest.raises(GarminPullError):
        sync_service.run_sync()

    st = sync_service.get_status()
    assert st["status"] == "error"
    assert st["last_sync"] == prev_sync
    assert st["activity_count"] == 12


def test_daily_load_recomputed_from_full_history(monkeypatch, tmp_path):
    """The daily_load series must span all stored workouts, not the pull window."""
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})

    history_rows = [
        {
            "time": datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc),
            "tss": 80.0,
            "discipline": "bike",
            "activity_id": "1",
        },
        {
            "time": datetime(2026, 7, 16, 7, 0, tzinfo=timezone.utc),
            "tss": 60.0,
            "discipline": "run",
            "activity_id": "2",
        },
    ]
    written = {}

    class FakeRepo:
        # The history read pivots fields to columns; the id read does not.
        def query(self, flux):
            return history_rows if "pivot" in flux else []

        def write_workouts(self, w):
            return 0

        def write_daily_load(self, rows):
            written["rows"] = rows

        def write_vo2max(self, rows):
            return 0


        def write_recovery(self, rows):

            return 0

        def close(self):
            pass

    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: [])
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: False)

    import app.recommend_service as reco

    monkeypatch.setattr(reco, "run_recommendation", lambda trigger, **kw: None)

    sync_service.run_sync()

    all_rows = [r for r in written["rows"] if r["discipline"] == "all"]
    assert all_rows[0]["date"] == "2026-05-01"
    # The series runs to today, not to the last workout: the trailing rest days
    # have to decay, or ATL stays frozen at the last training day's peak.
    assert all_rows[-1]["date"] == date.today().isoformat()
    assert any(r["date"] == "2026-07-16" and r["tss"] == 60.0 for r in all_rows)
    # CTL carries across the gap instead of restarting at zero.
    assert all_rows[-1]["ctl"] > 0


# --- plan regeneration skip -------------------------------------------------
def _run_faked_sync(monkeypatch, tmp_path, raw, plan_fresh, **sync_kwargs):
    """Run the pipeline with fakes; returns (summary, recommendation calls)."""
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: plan_fresh)
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: raw)
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])

    class FakeRepo:
        def query(self, flux):
            # Activity 111 is already stored; no workout history.
            return [{"activity_id": "111"}] if "distinct" in flux else []

        def write_workouts(self, w):
            return 0

        def write_daily_load(self, rows):
            pass

        def write_vo2max(self, rows):
            return 0


        def write_recovery(self, rows):

            return 0

        def close(self):
            pass

    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)

    calls: list[str] = []
    import app.recommend_service as reco

    monkeypatch.setattr(reco, "run_recommendation", lambda trigger, **kw: calls.append(trigger))
    return sync_service.run_sync(**sync_kwargs), calls


_KNOWN = {"activityId": 111, "startTimeLocal": "2026-07-16 07:00:00"}
_NEW = {"activityId": 999, "startTimeLocal": "2026-07-17 07:00:00"}


def test_plan_skipped_when_nothing_new_and_plan_current(monkeypatch, tmp_path):
    summary, calls = _run_faked_sync(monkeypatch, tmp_path, [_KNOWN], plan_fresh=True)
    assert summary["new_activities"] == 0
    assert summary["recommendation"] == "skipped (no new activities)"
    assert calls == []


def test_plan_regenerated_for_new_activities(monkeypatch, tmp_path):
    summary, calls = _run_faked_sync(
        monkeypatch, tmp_path, [_KNOWN, _NEW], plan_fresh=True
    )
    assert summary["new_activities"] == 1
    assert summary["recommendation"] == "generated"
    assert calls == ["auto_sync"]


def test_plan_regenerated_when_window_stale(monkeypatch, tmp_path):
    # No new activities, but the cached plan no longer starts today.
    summary, calls = _run_faked_sync(monkeypatch, tmp_path, [_KNOWN], plan_fresh=False)
    assert summary["recommendation"] == "generated"
    assert calls == ["auto_sync"]


def test_overwriting_plan_changes_always_regenerates(monkeypatch, tmp_path):
    # Nothing new and the plan is current -- a skip, except that the athlete
    # just asked to overwrite their plan changes with a new plan.
    summary, calls = _run_faked_sync(
        monkeypatch, tmp_path, [_KNOWN], plan_fresh=True, keep_requests=False
    )
    assert summary["recommendation"] == "generated"
    assert calls == ["auto_sync"]


def test_keeping_plan_changes_still_skips_a_current_plan(monkeypatch, tmp_path):
    # The current plan already holds the requested days.
    summary, calls = _run_faked_sync(
        monkeypatch, tmp_path, [_KNOWN], plan_fresh=True, keep_requests=True
    )
    assert summary["recommendation"] == "skipped (no new activities)"
    assert calls == []
