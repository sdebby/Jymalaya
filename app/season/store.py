"""
The season plan's one file, data/season_plan.json -- the pattern of
app/plan_requests.py and app/schedule_store.py, with an atomic write: the plan
is the athlete's whole season, and a crash mid-write must not leave half of it.

No InfluxDB writes. tests/conftest.py points SEASON_PLAN_FILE at a temp file
for every test, so no test writes into the athlete's live data/.
"""

from __future__ import annotations

import json
import logging
import os

from app.config import ROOT

log = logging.getLogger("jymalaya.season")

SEASON_PLAN_FILE = ROOT / "data" / "season_plan.json"


def load_plan() -> dict | None:
    """The stored plan, or None when absent or unreadable."""
    try:
        data = json.loads(SEASON_PLAN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_plan(plan: dict) -> None:
    """Write the plan atomically: a temp file next to it, then a replace."""
    SEASON_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEASON_PLAN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    os.replace(tmp, SEASON_PLAN_FILE)


def clear_plan() -> bool:
    """Delete the plan. True when there was a file to delete."""
    try:
        SEASON_PLAN_FILE.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        log.warning("Could not delete %s: %s", SEASON_PLAN_FILE, e)
        return False


def payload_changed_at() -> str | None:
    """
    When the plan last changed something the 7-day plan payload reads, or None
    with no plan. _plan_is_current compares this, not the file's mtime: the
    file is rewritten on every sync to store the week's actuals.
    """
    plan = load_plan()
    return (plan or {}).get("payload_changed_at")
