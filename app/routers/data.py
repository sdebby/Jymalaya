"""Data administration: full reset triggered from the dashboard Settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.analysis_service import clear_analyses
from app.influx.client import InfluxRepo
from app.plan_requests import clear_requests
from app.recommend_service import LATEST_FILE
from app.season.store import clear_plan as clear_season_plan

router = APIRouter(tags=["data"])


@router.delete("/data")
def delete_all_data():
    """
    Wipe every point in the InfluxDB bucket (workouts and their FIT detail,
    daily_load, vo2max, recovery, goals, athlete_profile mirror), the cached
    plan file, the plan
    changes the athlete asked the coach for, the season plan, and every cached
    workout analysis.
    The athlete profile and the training schedule are NOT touched.
    """
    repo = InfluxRepo()
    try:
        repo.delete_all_data()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"InfluxDB delete failed: {e}") from e
    finally:
        repo.close()

    plan_cache = "absent"
    try:
        if LATEST_FILE.exists():
            LATEST_FILE.unlink()
            plan_cache = "deleted"
    except OSError as e:
        plan_cache = f"failed ({e})"

    # The requests are edits to the plan just deleted; nothing is left to keep.
    requests = "deleted" if clear_requests() else "absent"
    # The season plan is built on the history just wiped.
    season = "deleted" if clear_season_plan() else "absent"
    return {
        "influx": "deleted",
        "plan_cache": plan_cache,
        "plan_requests": requests,
        "season_plan": season,
        "analyses": clear_analyses(),
    }
