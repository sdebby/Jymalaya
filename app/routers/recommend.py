"""Recommendation endpoints (PRD 7.7 / 8)."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException

from app.ai.claude import ClaudeError
from app.models import SessionMoveIn
from app.plan_editor import PlanEditError, find_session, move_session
from app.plan_requests import active_requests
from app.recommend_service import latest_recommendation, run_recommendation
from app.schedule_store import WEEKDAY_NAMES, get_schedule
from app.season import service as season_service

log = logging.getLogger("jymalaya.recommend")

router = APIRouter(tags=["recommend"])


@router.post("/recommend/trigger")
def trigger_recommendation(keep_requests: bool = True):
    """
    Generate next week's plan now and return it. keep_requests=false overwrites
    the days the athlete asked the coach to change (see POST /sync).

    The season plan catches up first, as on a sync, so the new plan is built
    against this week's actuals and form. Best effort, like in the sync.
    """
    try:
        season_service.on_sync()
    except Exception as e:  # noqa: BLE001 -- the re-plan must not depend on it
        log.warning("Season plan update before re-plan failed: %s", e, exc_info=True)
    try:
        return run_recommendation(trigger="manual", keep_requests=keep_requests)
    except ClaudeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/recommend/latest")
def get_latest():
    plan = latest_recommendation()
    if plan is None:
        raise HTTPException(status_code=404, detail="No plan generated yet.")
    return plan


@router.get("/recommend/requests")
def get_requests():
    """
    The plan days the athlete asked the coach to change that are still in force
    (their day has not passed). The dashboard asks before a sync or re-plan
    whether to keep them when this list is not empty.
    """
    return {"requests": active_requests()}


@router.post("/recommend/move-session")
def move_plan_session(body: SessionMoveIn):
    """
    Move a planned session to another day (a card dragged on the dashboard) and
    return the updated plan. Saved as a requested day, like a coach edit.

    The rest-day refusal lives here, not in move_session: the coach may still
    put a session on a rest day when the athlete confirms it in the chat.
    """
    if body.from_date == body.to_date:
        raise HTTPException(status_code=422, detail="The session is already on that day.")
    try:
        target_weekday = WEEKDAY_NAMES[date.fromisoformat(body.to_date).weekday()]
        date.fromisoformat(body.from_date)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    today = date.today().isoformat()
    if body.from_date < today or body.to_date < today:
        raise HTTPException(status_code=409, detail="This day has passed.")
    if get_schedule()["schedule"][target_weekday]["rest"]:
        raise HTTPException(
            status_code=409,
            detail=f"{target_weekday.capitalize()} is a rest day in your training schedule.",
        )
    try:
        session = find_session(body.from_date, body.session_index)
        name = session.get("title") or session.get("discipline") or "Session"
        note = f"Moved {name} from {body.from_date} to {body.to_date} on the dashboard"
        move_session(body.from_date, body.session_index, body.to_date, note=note, by="athlete")
    except PlanEditError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return latest_recommendation()
