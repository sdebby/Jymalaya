"""Post-workout analysis endpoints (PRD section 7.9)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from app.ai.claude import ClaudeError
from app.analysis_service import (
    AnalysisError,
    DetailNotStored,
    analyze_workout,
    cached_analysis,
    workout_streams,
)

router = APIRouter(tags=["analysis"])

# activity_id becomes a cache filename, so it is constrained here rather than
# sanitized later: this rejects path traversal before the value is ever joined to
# ANALYSIS_DIR. Real ids are digits, with a _bike/_run suffix on brick legs.
ActivityId = Path(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)


@router.get("/workouts/{activity_id}/analysis")
def get_workout_analysis(activity_id: str = ActivityId):
    """The cached analysis for this workout; 404 when it has not been analyzed."""
    analysis = cached_analysis(activity_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="This workout has not been analyzed yet.")
    return analysis


@router.get("/workouts/{activity_id}/streams")
def get_workout_streams(activity_id: str = ActivityId):
    """
    The workout's stored samples (time, HR, power, speed, elevation, cadence)
    for the modal's graphs, plus power-zone seconds for a ride. 404 when the
    workout is unknown or no samples were stored for it.
    """
    try:
        return workout_streams(activity_id)
    except AnalysisError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/workouts/{activity_id}/analyze")
def post_workout_analysis(
    activity_id: str = ActivityId,
    force: bool = Query(False, description="Re-analyze even when a cached analysis exists"),
):
    """
    Analyze a completed workout: read its stored samples, laps and planned
    workout, derive the coaching metrics, and have Claude review it. Returns the cached analysis
    unless `force`.
    """
    try:
        return analyze_workout(activity_id, force=force)
    except DetailNotStored as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except AnalysisError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ClaudeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
