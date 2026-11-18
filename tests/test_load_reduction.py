"""
Load-monotony check (prompt rule 32): six complete weeks of TSS that were flat
(each week within 25 of the one before) or only climbing (at most one step
level or down) make the next 7 days 15% lighter than the last complete week.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import recommend_service as reco
from app import schedule_store as store
from app import sync_service
from app.ai import prompt_builder as pb
from app.metrics import load_trend as lt

# A Wednesday, so the current week is partial and must be left out.
TODAY = date(2026, 11, 11)
THIS_MONDAY = TODAY - timedelta(days=TODAY.weekday())


def _week_rows(weekly, today=TODAY, discipline="run"):
    """One workout per week, on that week's Tuesday, oldest week first."""
    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(weeks=len(weekly))
    return [
        {"date": (first + timedelta(weeks=i, days=1)).isoformat(), "discipline": discipline, "tss": t}
        for i, t in enumerate(weekly)
    ]


# --- detection --------------------------------------------------------------
@pytest.mark.parametrize(
    "weekly, expected",
    [
        ([300, 310, 295, 305, 300, 310], "plateau"),
        # A slow drift is still flat: each step is within 25 of the last.
        ([300, 320, 340, 360, 380, 400], "plateau"),
        ([300, 330, 300, 330, 300, 330], None),  # 30-point swings, two of them down
        ([300, 340, 380, 420, 460, 500], "rising"),
        ([300, 340, 320, 380, 420, 460], "rising"),  # one dip
        ([300, 340, 340, 380, 420, 460], "rising"),  # one level step
        ([300, 340, 200, 380, 300, 460], None),  # two dips
        ([400, 360, 320, 280, 240, 200], None),  # falling
    ],
)
def test_detect(weekly, expected):
    assert lt.detect(weekly) == expected


def test_a_week_with_no_tss_means_no_check():
    assert lt.weekly_tss(_week_rows([300, 310, 0, 305, 300, 310]), TODAY) is None
    assert lt.reduction_block(_week_rows([300, 310, 0, 305, 300, 310]), TODAY) is None


def test_less_than_six_weeks_of_history_means_no_check():
    assert lt.reduction_block(_week_rows([300, 310, 305, 300]), TODAY) is None


def test_current_partial_week_is_left_out():
    rows = _week_rows([300, 310, 305, 300, 310, 305])
    rows.append({"date": TODAY.isoformat(), "discipline": "run", "tss": 900})
    assert lt.weekly_tss(rows, TODAY) == [300, 310, 305, 300, 310, 305]


def test_weeks_follow_the_stored_date_not_the_utc_instant():
    from datetime import datetime, timezone

    rows = _week_rows([300, 310, 305, 300, 310, 305])
    # A Sunday-evening workout whose UTC instant is already Monday: the stored
    # date keeps it in the last complete week.
    rows.append(
        {
            "date": (THIS_MONDAY - timedelta(days=1)).isoformat(),
            "time": datetime.combine(THIS_MONDAY, datetime.min.time(), tzinfo=timezone.utc),
            "discipline": "run",
            "tss": 20,
        }
    )
    assert lt.weekly_tss(rows, TODAY)[-1] == 325


def test_only_the_four_disciplines_count():
    rows = _week_rows([300, 310, 305, 300, 310, 305])
    rows += _week_rows([500, 0, 500, 0, 500, 0], discipline="other")
    assert lt.weekly_tss(rows, TODAY) == [300, 310, 305, 300, 310, 305]


def test_block_targets_85_percent_of_last_week():
    block = lt.reduction_block(_week_rows([300, 340, 380, 420, 460, 500]), TODAY)
    assert block["pattern"] == "rising"
    assert block["last_week_tss"] == 500
    assert block["target_tss"] == 425
    assert block["reduction_pct"] == 15
    assert "15% lighter" in block["message"] and "425 TSS" in block["message"]


def test_plateau_message_names_the_level():
    block = lt.reduction_block(_week_rows([300, 310, 295, 305, 300, 310]), TODAY)
    assert block["pattern"] == "plateau"
    assert "around 303 TSS" in block["message"]


# --- payload ----------------------------------------------------------------
def _payload(monkeypatch, workouts):
    class FakeRepo:
        def query(self, flux):
            return list(workouts)

        def close(self):
            pass

    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb, "drop_split_brick_parents", lambda rows: rows)
    monkeypatch.setattr(pb, "get_athlete_profile", lambda: {"ftp_w": 250})
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")
    return pb.build_plan_payload()


def test_payload_carries_the_block_when_triggered(monkeypatch):
    rows = _week_rows([300, 340, 380, 420, 460, 500], today=date.today())
    payload = _payload(monkeypatch, rows)
    assert payload["load_reduction"]["target_tss"] == 425


def test_payload_omits_the_key_otherwise(monkeypatch):
    rows = _week_rows([300, 340, 200, 380, 300, 460], today=date.today())
    assert "load_reduction" not in _payload(monkeypatch, rows)


def test_rule_32_is_in_the_prompt():
    from pathlib import Path

    root = Path(pb.__file__).resolve().parents[2]
    text = (root / "prompts" / "recommendation_system.txt").read_text(encoding="utf-8")
    assert "32. load_reduction" in text


# --- post-check -------------------------------------------------------------
BLOCK = {"pattern": "rising", "target_tss": 400, "reduction_pct": 15, "message": "lighter"}


def _plan(*tss):
    return {"days": [{"date": f"2026-11-1{i}", "sessions": [{"estimated_tss": t}]} for i, t in enumerate(tss)]}


def test_over_target_is_flagged_not_rewritten():
    plan = _plan(200, 260)
    flag = reco._check_load_reduction(plan, {"load_reduction": BLOCK})
    assert flag and "460 TSS" in flag
    assert plan["warning_flags"] == [flag]
    assert [d["sessions"][0]["estimated_tss"] for d in plan["days"]] == [200, 260]
    assert plan["load_reduction"]["planned_tss"] == 460


def test_within_tolerance_or_under_is_fine():
    for plan in (_plan(200, 230), _plan(100, 100)):
        assert reco._check_load_reduction(plan, {"load_reduction": BLOCK}) is None
        assert "warning_flags" not in plan
        assert plan["load_reduction"]["message"] == "lighter"


def test_no_block_no_stamp():
    plan = _plan(500)
    assert reco._check_load_reduction(plan, {}) is None
    assert "load_reduction" not in plan


def test_season_budget_is_a_ceiling_during_a_lighter_week():
    week = {
        "is_current_week": True,
        "target_tss": 500,
        "phase": "build",
        "budget_tss": 500,
        "plan_dates": ["2026-11-10", "2026-11-11"],
    }
    payload = {"season_plan": {"weeks": [week]}, "current_load": {"tsb": 0}}
    assert reco._check_season_target(_plan(100, 100), payload) is not None
    payload["load_reduction"] = BLOCK
    assert reco._check_season_target(_plan(100, 100), payload) is None


# --- sync -------------------------------------------------------------------
def test_sync_summary_carries_the_notice(monkeypatch, tmp_path):
    class FakeRepo:
        def query(self, flux):
            return []

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

    monkeypatch.setattr(
        reco, "run_recommendation", lambda trigger, **kw: {"load_reduction": {"message": "lighter"}}
    )
    monkeypatch.setattr(sync_service, "_plan_is_current", lambda: False)
    monkeypatch.setattr(sync_service, "get_athlete_profile", lambda: {})
    monkeypatch.setattr(sync_service, "pull_activities", lambda **kw: [])
    monkeypatch.setattr(sync_service, "pull_vo2max", lambda **kw: [])
    monkeypatch.setattr(sync_service, "parse_activities", lambda raw: [])
    monkeypatch.setattr(sync_service, "apply_tss", lambda w, p: [])
    monkeypatch.setattr(sync_service, "compute_load", lambda d, discipline, through=None: [])
    monkeypatch.setattr(sync_service, "daily_tss", lambda w, d=None: {})
    monkeypatch.setattr(sync_service, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(sync_service, "STATUS_FILE", tmp_path / "sync_status.json")
    assert sync_service.run_sync()["load_reduction_notice"] == "lighter"
