"""Send to device: a planned session to the athlete's watch, via intervals.icu."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from app.config import get_athlete_profile
from app.intervals.client import IntervalsError, IntervalsNotConfigured, upsert_workout
from app.intervals.workout import session_to_event
from app.models import DeviceSendIn
from app.plan_editor import PlanEditError, find_session

router = APIRouter(tags=["device"])


@router.post("/device/send")
def send_to_device(body: DeviceSendIn):
    """
    Put the plan session at (date, session_index) on the athlete's intervals.icu
    calendar as a structured workout; intervals.icu passes it on to Garmin
    Connect. Sending the same session again updates that calendar entry.
    """
    try:
        session = find_session(body.date, body.session_index)
    except PlanEditError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if body.date < date.today().isoformat():
        raise HTTPException(status_code=409, detail="This day has passed.")
    try:
        event = session_to_event(body.date, body.session_index, session, get_athlete_profile())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        stored = upsert_workout(event)
    except IntervalsNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except IntervalsError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "event_id": stored.get("id"),
        "name": stored.get("name") or event["name"],
        "date": body.date,
        "external_id": event["external_id"],
        "moving_time_sec": stored.get("moving_time"),
    }
