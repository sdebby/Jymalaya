"""
The season plan reaching the 7-day plan: on_sync (actuals, adaptation, the
notice), staleness through payload_changed_at, the season_plan payload block,
the coach line, the code-side target check and the appended prompt rules.

No network: the repo, events and schedule are faked; the store is the temp
file tests/conftest.py gives every test.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import app.ai.prompt_builder as pb
import app.recommend_service as reco
from app import instructions, schedule_store, sync_service
from app.ai import claude
from app.recommend_service import _check_season_target
from app.season import service, store
from app.season.builder import build_event_plan

TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())


def _plan(weeks_ago: int = 1, runway: int = 16) -> dict:
    """A plan made `weeks_ago` weeks ago, its first week starting that Monday."""
    made = MONDAY - timedelta(days=7 * weeks_ago)
    race = made + timedelta(days=7 * (runway - 1) + 5)
    return build_event_plan(
        today=made,
        event={"event_id": "e1", "name": "Spring Olympic", "type": "olympic", "race_date": race.isoformat()},
        baseline={
            "ctl": 40.0, "atl": 42.0, "tsb": -2.0, "avg_weekly_tss_4w": 300,
            "avg_weekly_hours_4w": 5.5, "tss_per_hour": 55, "captured_at": made.isoformat(),
            "limited_history": False,
        },
        now_iso=datetime.combine(made, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
        plan_id="p1",
    )


def _workout(day: date, tss: float) -> dict:
    return {
        "date": day.isoformat(),
        "time": datetime(day.year, day.month, day.day, 7, tzinfo=timezone.utc),
        "discipline": "run",
        "tss": tss,
        "duration_sec": tss / 55 * 3600,
    }


def _last_week_and_today() -> list[dict]:
    """
    45 TSS every day of last week, plus a short session today: without it a
    Sunday run reads last Sunday's workout as 7 days off, which is a layoff.
    """
    return [_workout(MONDAY - timedelta(days=d), 45) for d in range(1, 8)] + [_workout(TODAY, 20)]


class FakeRepo:
    """Workouts for workout reads, one load row dated today for load reads."""

    def __init__(self, workouts, tsb=-5.0):
        self.workouts = workouts
        self.load = {"date": TODAY.isoformat(), "discipline": "all", "ctl": 45.0, "atl": 45.0 - tsb, "tsb": tsb, "tss": 0}

    def query(self, flux):
        return [dict(self.load)] if "daily_load" in flux else [dict(w) for w in self.workouts]

    def close(self):
        pass


class ExplodingRepo:
    def query(self, flux):
        raise AssertionError("no read without a plan")

    def close(self):
        pass


@pytest.fixture(autouse=True)
def plan_event(monkeypatch):
    """
    on_sync re-checks the plan's event; answer from the stored plan, unchanged,
    so these tests never reach the events store in InfluxDB.
    """

    def get_event(event_id):
        event = (store.load_plan() or {}).get("event") or {}
        return {**event, "participating": True} if event.get("event_id") == event_id else None

    monkeypatch.setattr(service.events_service, "get_event", get_event)


# --- on_sync -----------------------------------------------------------------
def test_on_sync_without_a_plan_reads_nothing():
    assert service.on_sync(ExplodingRepo()) is None
    assert not store.SEASON_PLAN_FILE.exists()


def test_on_sync_stores_actuals_and_form_and_says_nothing_when_on_track():
    plan = _plan(weeks_ago=1)
    store.save_plan(plan)
    workouts = _last_week_and_today()
    assert service.on_sync(FakeRepo(workouts)) is None
    saved = store.load_plan()
    assert saved["weeks"][0]["actual_tss"] == 315
    assert saved["last_form"]["tsb"] == -5.0
    # Nothing the payload reads moved, so the 7-day plan is not made stale.
    assert saved["payload_changed_at"] == plan["payload_changed_at"]


def test_on_sync_adapts_with_a_notice_and_a_second_sync_is_quiet():
    store.save_plan(_plan(weeks_ago=1))
    workouts = _last_week_and_today()
    notice = service.on_sync(FakeRepo(workouts, tsb=-35))
    assert notice == "Plan adjusted: week 3 lowered 20% (TSB -35)."
    first = store.load_plan()
    assert first["payload_changed_at"] != _plan(weeks_ago=1)["payload_changed_at"]

    assert service.on_sync(FakeRepo(workouts, tsb=-35)) is None
    second = store.load_plan()
    assert second["revisions"] == first["revisions"]
    assert second["payload_changed_at"] == first["payload_changed_at"]


def test_on_sync_completes_a_plan_whose_race_has_passed():
    plan = _plan(weeks_ago=3, runway=2)
    store.save_plan(plan)
    service.on_sync(FakeRepo([]))
    assert store.load_plan()["status"] == "completed"
    assert service.payload_block(TODAY, [TODAY.isoformat()]) is None


def test_a_failing_season_update_never_fails_the_sync(monkeypatch, tmp_path):
    class Repo(FakeRepo):
        def write_workouts(self, w):
            return 0

        def write_daily_load(self, rows):
            pass

        def write_vo2max(self, rows):
            return 0

        def write_recovery(self, rows):
            return 0

    monkeypatch.setattr(reco, "run_recommendation", lambda trigger, **kw: None)
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: False)
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: [])
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])
    monkeypatch.setattr(sync_service, "parse_activities", lambda raw: [])
    monkeypatch.setattr(sync_service, "apply_tss", lambda w, p: [])
    monkeypatch.setattr(sync_service, "compute_load", lambda d, discipline, through=None: [])
    monkeypatch.setattr(sync_service, "daily_tss", lambda w, d=None: {})
    monkeypatch.setattr(sync_service, "InfluxRepo", lambda: Repo([]))
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")

    assert sync_service.run_sync()["season_plan_notice"] is None

    def boom(repo):
        raise RuntimeError("season store unreadable")

    monkeypatch.setattr(sync_service.season_service, "on_sync", boom)
    summary = sync_service.run_sync()
    assert summary["status"] == "ok"
    assert summary["season_plan_notice"] is None

    # A failed Garmin pull (MFA pending, bad login) never reaches the season
    # plan: it moves only on a data refresh.
    calls = []
    monkeypatch.setattr(sync_service.season_service, "on_sync", lambda repo: calls.append(1))

    def no_garmin(**kw):
        raise sync_service.GarminPullError("MFA code needed")

    monkeypatch.setattr(sync_service, "pull_activities", no_garmin)
    with pytest.raises(sync_service.GarminPullError):
        sync_service.run_sync()
    assert calls == []


# --- staleness -------------------------------------------------------------------
@pytest.fixture()
def cached_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "ci.txt")
    monkeypatch.setattr(schedule_store, "SCHEDULE_FILE", tmp_path / "schedule.json")

    def set_generated(at: datetime):
        monkeypatch.setattr(reco, "latest_recommendation", lambda: {"_meta": {"generated_at": at.isoformat()}})

    return set_generated


def test_a_season_change_after_the_plan_makes_it_stale(cached_plan):
    now = datetime.now(timezone.utc)
    cached_plan(now - timedelta(minutes=5))
    plan = _plan()
    plan["payload_changed_at"] = now.isoformat()
    store.save_plan(plan)
    assert sync_service._plan_is_current() is False


def test_a_season_change_before_the_plan_keeps_it_current(cached_plan):
    now = datetime.now(timezone.utc)
    cached_plan(now)
    plan = _plan()
    plan["payload_changed_at"] = (now - timedelta(minutes=5)).isoformat()
    store.save_plan(plan)
    assert sync_service._plan_is_current() is True


def test_no_season_plan_leaves_staleness_as_it_was(cached_plan):
    cached_plan(datetime.now(timezone.utc))
    assert sync_service._plan_is_current() is True


def test_cancelling_makes_the_plan_stale(cached_plan):
    cached_plan(datetime.now(timezone.utc) - timedelta(seconds=1))
    store.save_plan(_plan())
    assert service.cancel() is True
    assert sync_service._plan_is_current() is False
    assert service.get_plan() is None
    assert service.cancel() is False


# --- the payload block ---------------------------------------------------------
def _window(start: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


def test_block_splits_the_window_between_two_season_weeks():
    plan = _plan(weeks_ago=0)
    plan["weeks"][0]["actual_tss"] = 100
    store.save_plan(plan)
    wednesday = MONDAY + timedelta(days=2)
    block = service.payload_block(wednesday, _window(wednesday))
    cur, nxt = block["weeks"]
    assert cur["is_current_week"] and not nxt["is_current_week"]
    assert cur["plan_dates"] == _window(wednesday)[:5]
    assert nxt["plan_dates"] == _window(wednesday)[5:]
    assert cur["remaining_tss"] == cur["budget_tss"] == cur["target_tss"] - 100
    assert nxt["done_tss"] == 0
    assert nxt["budget_tss"] == round(nxt["target_tss"] * 2 / 7)
    assert block["event"]["name"] == "Spring Olympic"
    assert block["form_band"] == "ok"
    assert block["weeks_in_plan"] == 16


def test_no_block_without_a_plan():
    assert service.payload_block(TODAY, _window(TODAY)) is None


def _payload(monkeypatch):
    monkeypatch.setattr(pb, "InfluxRepo", lambda: FakeRepo([]))
    monkeypatch.setattr(pb, "drop_split_brick_parents", lambda rows: rows)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    monkeypatch.setattr(pb, "get_schedule", lambda: schedule_store._normalize(schedule_store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_plan_payload()


def test_payload_has_no_season_key_without_a_plan(monkeypatch):
    assert "season_plan" not in _payload(monkeypatch)


def test_payload_carries_the_season_block(monkeypatch):
    store.save_plan(_plan(weeks_ago=0))
    p = _payload(monkeypatch)
    weeks = p["season_plan"]["weeks"]
    assert weeks[0]["is_current_week"]
    assert sum(len(w["plan_dates"]) for w in weeks) == 7


def test_coach_line_says_where_the_athlete_stands():
    store.save_plan(_plan(weeks_ago=1))
    line = service.coach_line(TODAY)
    assert line.startswith("Season plan toward Spring Olympic")
    assert "week 2 of 16" in line


# --- the target check -----------------------------------------------------------
def _sessions_plan(dates_tss: dict[str, list[float]]) -> dict:
    return {
        "weekly_tss_target": 999,
        "periodization_phase": "peak",
        "days": [
            {"date": d, "sessions": [{"discipline": "run", "estimated_tss": t} for t in tss]}
            for d, tss in dates_tss.items()
        ],
    }


def _season_payload(budget=200, phase="build", tsb=-5.0, recent_race=None):
    payload = {
        "current_load": {"tsb": tsb},
        "season_plan": {
            "weeks": [
                {"is_current_week": True, "phase": phase, "target_tss": 300, "budget_tss": budget,
                 "plan_dates": ["2026-11-04", "2026-11-05"]},
                {"is_current_week": False, "phase": phase, "target_tss": 320, "budget_tss": 90,
                 "plan_dates": ["2026-11-09"]},
            ]
        },
    }
    if recent_race:
        payload["recent_race"] = recent_race
    return payload


def test_target_check_passes_within_ten_percent_and_stamps_the_plan():
    plan = _sessions_plan({"2026-11-04": [100], "2026-11-05": [90], "2026-11-09": [400]})
    assert _check_season_target(plan, _season_payload()) is None
    assert plan["weekly_tss_target"] == 300
    assert plan["periodization_phase"] == "build"
    assert "warning_flags" not in plan


@pytest.mark.parametrize("tss", [[200, 60], [80, 50]])
def test_target_check_flags_over_and_under(tss):
    plan = _sessions_plan({"2026-11-04": tss[:1], "2026-11-05": tss[1:]})
    flag = _check_season_target(plan, _season_payload())
    assert flag and "200 TSS" in flag
    assert plan["warning_flags"] == [flag]


@pytest.mark.parametrize("kw", [{"tsb": -35}, {"recent_race": {"name": "x"}}])
def test_under_the_budget_is_fine_when_it_is_a_ceiling(kw):
    plan = _sessions_plan({"2026-11-04": [50]})
    assert _check_season_target(plan, _season_payload(**kw)) is None


def test_race_week_reads_as_taper():
    plan = _sessions_plan({"2026-11-04": [100], "2026-11-05": [100]})
    _check_season_target(plan, _season_payload(phase="race"))
    assert plan["periodization_phase"] == "taper"


def test_no_season_plan_leaves_the_plan_alone():
    plan = _sessions_plan({"2026-11-04": [500]})
    assert _check_season_target(plan, {"current_load": {}}) is None
    assert plan["weekly_tss_target"] == 999
    assert "window_budget_tss" not in plan


def test_window_budget_sums_every_week_in_the_window():
    plan = _sessions_plan({"2026-11-04": [100], "2026-11-05": [90]})
    _check_season_target(plan, _season_payload())
    assert plan["window_budget_tss"] == 290


def test_window_budget_is_stamped_before_the_plan_has_a_current_week():
    # A season plan starting inside the window: no week is current yet, so the
    # target stays the model's, but those days still have a budget.
    plan = _sessions_plan({"2026-11-02": [100]})
    payload = {
        "current_load": {},
        "season_plan": {
            "weeks": [
                {"is_current_week": False, "phase": "build", "target_tss": 317, "budget_tss": 226,
                 "plan_dates": ["2026-11-01", "2026-11-02"]},
            ]
        },
    }
    assert _check_season_target(plan, payload) is None
    assert plan["window_budget_tss"] == 226
    assert plan["weekly_tss_target"] == 999


# --- the prompt -------------------------------------------------------------------
def test_headline_is_in_the_format_after_the_sessions_with_its_rule():
    template = claude.SYSTEM_PROMPT_PATH.with_name("recommendation_plan_format.json").read_text(encoding="utf-8")
    keys = list(json.loads(template))
    # The model fills the template top to bottom: the note follows the sessions.
    assert keys.index("headline") > keys.index("days")
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    assert text.count("\n30. ") == 1
    assert text.index("\n30. ") > text.index("\n29. ")
    rule = text[text.index("\n30. "):]
    assert '"headline"' in rule and "two sentences" in rule and "paces in seconds" in rule


def test_rules_25_to_28_are_appended_after_rule_24():
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    positions = [text.index(f"\n{n}. ") for n in range(24, 29)]
    assert positions == sorted(positions)
    assert text.index("Season plan (season_plan):") > positions[0]
    for n in range(25, 29):
        assert text.count(f"\n{n}. ") == 1
    assert "INSTEAD of\n    choosing a phase from weeks_until_race" in text
