"""Weather endpoint for an event's race location/date (PRD section 7.5)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import events_service
from app.models import Weather
from app.weather.openmeteo import cached_weather_for_event

router = APIRouter(tags=["weather"])


@router.get("/weather/{event_id}", response_model=Weather)
def event_weather(event_id: str):
    event = events_service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return cached_weather_for_event(event)
