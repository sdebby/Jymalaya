"""Events CRUD endpoints (PRD section 7.4, where they are called goals)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app import events_service
from app.models import Event, EventIn
from app.season import service as season_service

log = logging.getLogger("jymalaya.events")

router = APIRouter(tags=["events"])


def _season_follows(event_id: str) -> None:
    """
    The season plan follows its event: a new date or distance rebuilds it, a
    deletion or "not participating" marks it event_missing. Best effort -- the
    event edit itself must never fail over it; the next sync re-checks anyway.
    """
    try:
        season_service.on_event_changed(event_id)
    except Exception as e:  # noqa: BLE001
        log.warning("Season plan did not follow event %s: %s", event_id, e, exc_info=True)


@router.get("/events", response_model=list[Event])
def list_events():
    return events_service.list_events()


@router.post("/events", response_model=Event, status_code=201)
def create_event(event: EventIn):
    return events_service.create_event(event.model_dump())


@router.get("/events/{event_id}", response_model=Event)
def get_event(event_id: str):
    event = events_service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.put("/events/{event_id}", response_model=Event)
def update_event(event_id: str, event: EventIn):
    updated = events_service.update_event(event_id, event.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Event not found")
    _season_follows(event_id)
    return updated


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: str):
    if not events_service.delete_event(event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    _season_follows(event_id)
