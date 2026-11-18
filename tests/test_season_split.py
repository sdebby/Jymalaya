"""
The swim / bike / run split of a triathlon season plan (app/season/split.py):
the table, the weeks that carry it, the athlete's own splits, the actuals per
discipline, the payload budgets, the code-side flag and rule 29.

No network: plans come from the builders, events are faked, the store is the
temp file tests/conftest.py gives every test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import events_service
from app.recommend_service import _check_season_target
from app.season import config as cfg
from app.season import service, split, store
from app.season.builder import build_event_plan
from app.season.checkin import build_fitness_plan
from main import app

api = TestClient(app, base_url="http://testserver/api")

TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())
BASELINE = {
    "ctl": 40.0, "atl": 42.0, "tsb": -2.0, "avg_weekly_tss_4w": 300,
    "avg_weekly_hours_4w": 5.5, "tss_per_hour": 55, "captured_at": MONDAY.isoformat(),
    "limited_history": False,
}
NOW = datetime.combine(MONDAY, datetime.min.time(), tzinfo=timezone.utc).isoformat()


def _race(runway: int) -> str:
    return (MONDAY + timedelta(days=7 * (runway - 1) + 5)).isoformat()


def _plan(type_: str = "olympic", runway: int = 20, save: bool = True) -> dict:
    plan = build_event_plan(
        today=MONDAY,
        event={"event_id": "e1", "name": "Race", "type": type_, "race_date": _race(runway)},
        baseline=dict(BASELINE),
        now_iso=NOW,
        plan_id="p1",
    )
    if save:
        store.save_plan(service._stored(plan))
    return plan


def _fitness(focus: str | None) -> dict:
    return build_fitness_plan(today=MONDAY, baseline=dict(BASELINE), focus=focus, now_iso=NOW, first_week=MONDAY)


def _ws(i: int) -> str:
    return (MONDAY + timedelta(days=7 * i)).isoformat()


def _weeks():
    return store.load_plan()["weeks"]


# --- the table ----------------------------------------------------------------------
def test_every_table_row_adds_up_to_100():
    rows = [r for t in cfg.DISCIPLINE_SPLIT.values() for r in t.values()] + [cfg.FITNESS_TRI_SPLIT]
    for row in rows:
        assert set(row) == set(cfg.SPLIT_DISCIPLINES)
        assert sum(row.values()) == 100
    assert set(cfg.DISCIPLINE_SPLIT) == set(cfg.TRI_TYPES)
    for table in cfg.DISCIPLINE_SPLIT.values():
        assert set(table) == set(cfg.SPLIT_PHASES)


@pytest.mark.parametrize("type_", ["olympic", "140.6"])
def test_each_week_carries_its_phase_split(type_):
    plan = _plan(type_, runway=30, save=False)
    for w in plan["weeks"]:
        phase = "taper" if w["phase"] == "race" else w["phase"]
        assert w["split"] == cfg.DISCIPLINE_SPLIT[type_][phase]
        assert w["split_source"] == "table"
        assert sum(w["discipline_tss"].values()) == round(w["target_tss"])
        assert set(w["discipline_hours"]) == {"swim", "bike", "run"}


def test_a_run_race_and_a_run_fitness_plan_have_no_split():
    assert all("split" not in w for w in _plan("10K", save=False)["weeks"])
    assert all("split" not in w for w in _fitness("run")["weeks"])


def test_a_triathlon_fitness_plan_has_one_split():
    plan = service.view(_fitness(None), MONDAY)
    assert all(w["split"] == cfg.FITNESS_TRI_SPLIT for w in plan["weeks"])


@pytest.mark.parametrize("target", [0, 1, 99, 301, 457, 1000])
def test_discipline_tss_adds_up_exactly(target):
    for row in [cfg.FITNESS_TRI_SPLIT, *cfg.DISCIPLINE_SPLIT["70.3"].values()]:
        assert sum(split.discipline_tss(target, row).values()) == target


def test_hours_use_each_disciplines_own_rate():
    plan = {"baseline": {"tss_per_hour": 50, "tss_per_hour_by_discipline": {"swim": 40}},
            "weeks": [{"target_tss": 400, "split": {"swim": 20, "bike": 50, "run": 30}}]}
    w = split.add_view(plan)["weeks"][0]
    assert w["discipline_tss"] == {"swim": 80, "bike": 200, "run": 120}
    assert w["discipline_hours"] == {"swim": 2.0, "bike": 4.0, "run": 2.4}


def test_the_view_fields_are_never_stored():
    _plan()
    assert all("discipline_tss" not in w and "split" in w for w in _weeks())


def test_an_older_plan_gets_its_split_on_read():
    plan = _plan(save=False)
    for w in plan["weeks"]:
        for k in ("split", "split_source", "discipline_tss", "discipline_hours"):
            w.pop(k)
    store.save_plan(plan)
    assert service.get_plan(MONDAY)["weeks"][0]["split"] == cfg.DISCIPLINE_SPLIT["olympic"]["base"]


# --- the athlete's own split ---------------------------------------------------------
def test_set_split_is_kept_and_separate_from_the_pin():
    _plan()
    out = service.edit_split(_ws(2), "set_split", {"swim": 10, "bike": 60, "run": 30}, today=MONDAY)
    w = _weeks()[2]
    assert w["split"] == {"swim": 10, "bike": 60, "run": 30} and w["split_source"] == "user"
    assert w["pinned"] is False
    assert out["revisions"][-1]["reason_text"] == "You set this week's split to swim 10 / bike 60 / run 30"
    # The week's TSS still moves; the split stays.
    service.edit_week(_ws(1), "lighter", today=MONDAY)
    assert _weeks()[2]["split"] == {"swim": 10, "bike": 60, "run": 30}


def test_reset_split_goes_back_to_the_table():
    _plan()
    service.edit_split(_ws(2), "set_split", {"swim": 10, "bike": 60, "run": 30}, today=MONDAY)
    service.edit_split(_ws(2), "reset_split", today=MONDAY)
    w = _weeks()[2]
    assert w["split"] == cfg.DISCIPLINE_SPLIT["olympic"]["base"] and w["split_source"] == "table"


@pytest.mark.parametrize(
    "bad",
    [{"swim": 20, "bike": 40, "run": 39}, {"swim": 20, "bike": 40, "run": 40, "strength": 0},
     {"swim": 20, "bike": 40}, {"swim": 20.5, "bike": 40, "run": 39.5}, {"swim": -5, "bike": 65, "run": 40}],
)
def test_a_bad_split_is_refused_through_the_route(bad):
    _plan()
    r = api.patch(f"/season-plan/weeks/{_ws(2)}", json={"action": "set_split", "split": bad})
    assert r.status_code == 422


def test_split_route_and_refusals():
    _plan()
    r = api.patch(f"/season-plan/weeks/{_ws(3)}", json={"action": "set_split", "split": {"swim": 15, "bike": 50, "run": 35}})
    assert r.status_code == 200
    assert r.json()["weeks"][3]["discipline_tss"]["bike"] == round(r.json()["weeks"][3]["target_tss"] * 0.5)
    past = (MONDAY - timedelta(days=7)).isoformat()
    store.save_plan({**store.load_plan(), "weeks": [{**_weeks()[0], "week_start": past}] + _weeks()})
    assert api.patch(f"/season-plan/weeks/{past}", json={"action": "reset_split"}).status_code == 422


def test_a_run_plan_takes_no_split():
    _plan("10K")
    r = api.patch(f"/season-plan/weeks/{_ws(1)}", json={"action": "set_split", "split": {"swim": 0, "bike": 0, "run": 100}})
    assert r.status_code == 422


def test_the_split_table_setting_resplits_table_weeks_only():
    _plan()
    service.edit_split(_ws(2), "set_split", {"swim": 10, "bike": 60, "run": 30}, today=MONDAY)
    r = api.patch("/season-plan/settings", json={"split_by_phase": {"base": {"swim": 25, "bike": 40, "run": 35}}})
    assert r.status_code == 200
    weeks = _weeks()
    assert weeks[0]["split"] == {"swim": 25, "bike": 40, "run": 35}
    assert weeks[2]["split"] == {"swim": 10, "bike": 60, "run": 30}
    build = next(w for w in weeks if w["phase"] == "build")
    assert build["split"] == cfg.DISCIPLINE_SPLIT["olympic"]["build"]
    # Null goes back to the code table.
    api.patch("/season-plan/settings", json={"split_by_phase": None})
    assert _weeks()[0]["split"] == cfg.DISCIPLINE_SPLIT["olympic"]["base"]
    assert api.patch("/season-plan/settings", json={"split_by_phase": {"race": {"swim": 0, "bike": 0, "run": 100}}}).status_code == 422


def test_the_create_form_sends_its_own_table(monkeypatch):
    monkeypatch.setattr(service, "_read_history", lambda: ([], {}))
    monkeypatch.setattr(
        service, "_target_event",
        lambda eid: {"event_id": "e1", "name": "Race", "type": "70.3", "race_date": _race(30)},
    )
    body = {"mode": "event", "event_id": "e1", "split_by_phase": {"peak": {"swim": 10, "bike": 60, "run": 30}}}
    plan = api.post("/season-plan", json=body).json()
    peak = next(w for w in plan["weeks"] if w["phase"] == "peak")
    assert peak["split"] == {"swim": 10, "bike": 60, "run": 30}
    assert plan["settings"]["split_by_phase"] == {"peak": {"swim": 10, "bike": 60, "run": 30}}
    bad = {**body, "split_by_phase": {"peak": {"swim": 10, "bike": 60, "run": 31}}}
    assert api.post("/season-plan/preview", json=bad).status_code == 422


def test_a_new_race_date_keeps_the_athletes_split_and_table(monkeypatch):
    _plan(runway=20)
    service.update_settings({"split_by_phase": {"base": {"swim": 25, "bike": 40, "run": 35}}}, today=MONDAY)
    service.edit_split(_ws(2), "set_split", {"swim": 10, "bike": 60, "run": 30}, today=MONDAY)
    event = {"event_id": "e1", "name": "Race", "type": "olympic", "race_date": _race(24), "participating": True}
    monkeypatch.setattr(events_service, "get_event", lambda eid: event)
    service.on_event_changed("e1", today=MONDAY)
    weeks = _weeks()
    assert len(weeks) == 24
    assert weeks[2]["split"] == {"swim": 10, "bike": 60, "run": 30}
    assert weeks[0]["split"] == {"swim": 25, "bike": 40, "run": 35}


def test_a_new_distance_off_triathlon_drops_the_split(monkeypatch):
    _plan(runway=20)
    event = {"event_id": "e1", "name": "Race", "type": "marathon", "race_date": _race(20), "participating": True}
    monkeypatch.setattr(events_service, "get_event", lambda eid: event)
    service.on_event_changed("e1", today=MONDAY)
    assert all("split" not in w for w in _weeks())


# --- actuals --------------------------------------------------------------------------
def _workout(day: date, discipline: str, tss: float, hours: float) -> dict:
    return {"date": day.isoformat(), "time": datetime(day.year, day.month, day.day, 7, tzinfo=timezone.utc),
            "discipline": discipline, "tss": tss, "duration_sec": hours * 3600}


def test_actuals_per_discipline_including_brick_legs_and_strength():
    workouts = [
        _workout(MONDAY, "swim", 40, 1),
        _workout(MONDAY, "bike", 80, 1.5),  # a brick's legs arrive split
        _workout(MONDAY, "run", 30, 0.5),
        _workout(MONDAY, "strength", 20, 0.75),
    ]
    totals = service.weekly_totals(workouts, "monday")
    w = {"week_start": MONDAY.isoformat()}
    split.set_actual(w, totals[MONDAY.isoformat()])
    assert w["actual_tss"] == 170
    assert w["actual_by_discipline"] == {
        "swim": {"tss": 40, "hours": 1.0}, "bike": {"tss": 80, "hours": 1.5},
        "run": {"tss": 30, "hours": 0.5}, "strength": {"tss": 20, "hours": 0.8},
    }


def test_baseline_rates_per_discipline():
    workouts = [_workout(MONDAY - timedelta(days=7 * i + 1), "swim", 50, 1) for i in range(4)]
    workouts += [_workout(MONDAY - timedelta(days=3), "run", 10, 0.2)]
    base = service.compute_baseline(workouts, {}, MONDAY, "monday", MONDAY)
    assert base["tss_per_hour_by_discipline"] == {"swim": 50.0}  # under an hour of run: no rate


# --- the 7-day plan --------------------------------------------------------------------
def test_payload_budgets_per_discipline():
    plan = _plan(save=False)
    plan["weeks"][0]["actual_tss"] = 100
    plan["weeks"][0]["actual_by_discipline"] = {"bike": {"tss": 100, "hours": 2}}
    store.save_plan(service._stored(plan))
    dates = [(MONDAY + timedelta(days=d)).isoformat() for d in range(3, 10)]
    block = service.payload_block(MONDAY + timedelta(days=3), dates)
    cur, nxt = block["weeks"]
    parts = split.discipline_tss(cur["target_tss"], cur["split_pct"])
    assert cur["discipline_budget_tss"] == {"swim": parts["swim"], "bike": max(parts["bike"] - 100, 0), "run": parts["run"]}
    nparts = split.discipline_tss(nxt["target_tss"], nxt["split_pct"])
    assert nxt["discipline_budget_tss"] == {d: round(v * 3 / 7) for d, v in nparts.items()}


def test_discipline_budgets_never_exceed_what_the_week_has_left():
    plan = _plan(save=False)
    w = plan["weeks"][0]
    parts = split.discipline_tss(w["target_tss"], w["split"])
    # Run far over its share: the week has 60 TSS left, owed to swim and bike.
    done_run = parts["run"] + parts["swim"] + parts["bike"] - 60
    w["actual_tss"] = done_run
    w["actual_by_discipline"] = {"run": {"tss": done_run, "hours": 5}}
    store.save_plan(service._stored(plan))
    cur = service.payload_block(MONDAY, [MONDAY.isoformat()])["weeks"][0]
    assert cur["budget_tss"] == 60
    budgets = cur["discipline_budget_tss"]
    assert sum(budgets.values()) == 60 and budgets["run"] == 0
    assert budgets["bike"] > budgets["swim"] > 0


def test_a_run_plan_payload_has_no_split():
    _plan("10K")
    block = service.payload_block(MONDAY, [MONDAY.isoformat()])
    assert "discipline_budget_tss" not in block["weeks"][0]


def test_a_split_edit_this_week_makes_the_7_day_plan_stale():
    plan = _plan()
    service.edit_split(_ws(0), "set_split", {"swim": 10, "bike": 60, "run": 30}, today=MONDAY)
    assert store.load_plan()["payload_changed_at"] != plan["payload_changed_at"]
    before = store.load_plan()["payload_changed_at"]
    service.edit_split(_ws(8), "set_split", {"swim": 10, "bike": 60, "run": 30}, today=MONDAY)
    assert store.load_plan()["payload_changed_at"] == before


def _plan_with(sessions: list[tuple[str, float]], day: str) -> dict:
    return {"days": [{"date": day, "sessions": [{"discipline": d, "estimated_tss": t} for d, t in sessions]}]}


def _payload(budgets: dict, tsb: float = 0.0, budget: int = 300) -> dict:
    return {
        "current_load": {"tsb": tsb},
        "season_plan": {"weeks": [{
            "is_current_week": True, "target_tss": budget, "phase": "build", "plan_dates": ["2026-11-02"],
            "budget_tss": budget, "discipline_budget_tss": budgets,
        }]},
    }


def test_the_split_flag_names_the_disciplines_that_are_off():
    budgets = {"swim": 60, "bike": 140, "run": 100}
    plan = _plan_with([("swim", 20), ("bike", 180), ("run", 100)], "2026-11-02")
    flag = _check_season_target(plan, _payload(budgets))
    assert "swim 20 TSS against 60" in flag and "bike 180 TSS against 140" in flag and "run" not in flag.split(":")[1]
    assert len(plan["warning_flags"]) == 1  # the week total is on budget


def test_the_split_flag_respects_the_floor_and_the_ceiling_cases():
    budgets = {"swim": 30, "bike": 170, "run": 100}
    # Swim 9 under a 30 budget: under SPLIT_MIN_TSS, not a miss.
    plan = _plan_with([("swim", 21), ("bike", 170), ("run", 100), ("strength", 9)], "2026-11-02")
    assert _check_season_target(plan, _payload(budgets)) is None
    # Under budget at TSB below -30 is a ceiling, not a miss.
    plan = _plan_with([("swim", 10), ("bike", 100), ("run", 60)], "2026-11-02")
    assert _check_season_target(plan, _payload(budgets, tsb=-35)) is None


def test_the_coach_can_set_a_weeks_split():
    _plan()
    out = service.coach_adjust({"operation": "set_split", "week_start": _ws(1), "swim_pct": 15, "bike_pct": 55, "run_pct": 30}, "more bike")
    assert _weeks()[1]["split"] == {"swim": 15, "bike": 55, "run": 30}
    assert "The coach, at your request, set this week's split" in out["changed"][0]


def test_the_coach_line_names_the_split():
    _plan()
    assert "split swim 20 / bike 43 / run 37 percent" in service.coach_line(MONDAY)


def test_rule_29_is_appended_after_rule_28():
    text = (Path(__file__).resolve().parents[1] / "prompts" / "recommendation_system.txt").read_text(encoding="utf-8")
    assert text.index("\n28. ") < text.index("\n29. A season week with discipline_budget_tss")
    assert "within 15%" in text and "Strength sessions count toward the\n    week's total only" in text
