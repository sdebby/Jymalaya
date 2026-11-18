"""
current_load in the plan payload comes from ONE daily_load read covering every
discipline, not one read per discipline. The payload is built on every plan and
every coach-chat turn, and the per-discipline reads were five serial Influx
round trips. Fakes only.
"""

from __future__ import annotations

from datetime import date, timedelta

import app.schedule_store as store
from app.ai import prompt_builder as pb


def _load_row(discipline: str, day: date, ctl: float, atl: float) -> dict:
    return {
        "discipline": discipline,
        "date": day.isoformat(),
        "tss": 0.0,
        "ctl": ctl,
        "atl": atl,
        "tsb": round(ctl - atl, 1),
    }


class FakeRepo:
    """Answers the daily_load read with `rows`, filtered like Flux would."""

    def __init__(self, rows):
        self.rows = rows
        self.seen: list[str] = []

    def query(self, flux):
        self.seen.append(flux)
        if '_measurement == "daily_load"' not in flux:
            return []
        for disc in ("all", "swim", "bike", "run", "strength"):
            if f'r.discipline == "{disc}"' in flux:
                return [r for r in self.rows if r["discipline"] == disc]
        return list(self.rows)

    def close(self):
        pass


def test_plan_payload_reads_daily_load_once(monkeypatch):
    today = date.today()
    rows = [
        _load_row("all", today, 40.5, 21.9),
        _load_row("swim", today, 5.1, 2.0),
        _load_row("bike", today, 17.1, 9.0),
        _load_row("run", today, 18.4, 10.9),
    ]
    repo = FakeRepo(rows)
    monkeypatch.setattr(pb, "InfluxRepo", lambda: repo)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))
    monkeypatch.setattr(pb, "get_instructions", lambda: "")

    load = pb.build_plan_payload()["current_load"]

    assert sum('"daily_load"' in f for f in repo.seen) == 1
    assert (load["ctl"], load["atl"], load["tsb"]) == (40.5, 21.9, 18.6)
    # No strength series stored: None, exactly as the per-discipline read gave.
    assert load["ctl_per_discipline"] == {
        "swim": 5.1,
        "bike": 17.1,
        "run": 18.4,
        "strength": None,
    }


def test_latest_loads_matches_the_per_discipline_read():
    """Stale series are padded to today the same way _latest_load pads them."""
    today = date.today()
    rows = [
        _load_row("all", today - timedelta(days=4), 40.0, 30.0),
        _load_row("all", today - timedelta(days=3), 41.0, 32.0),
        _load_row("run", today - timedelta(days=5), 18.0, 12.0),
        _load_row("bike", today, 17.0, 9.0),
    ]
    repo = FakeRepo(rows)

    loads = pb._latest_loads(repo)

    assert set(loads) == {"all", "run", "bike"}
    for disc in ("all", "run", "bike"):
        assert loads[disc] == pb._latest_load(repo, disc)
        assert loads[disc]["date"] == today.isoformat()
    # Three rest days decay fatigue faster than fitness, so form rises.
    assert loads["all"]["tsb"] > 41.0 - 32.0
