"""Season plan endpoints: the persistent week-by-week plan (app/season/)."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.season import service
from app.season.builder import SeasonPlanError
from app.season.models import (
    Checkin,
    CheckinIn,
    Revision,
    SeasonPlan,
    SeasonPlanIn,
    SettingsIn,
    SubEventIn,
    WeekEasyIn,
    WeekEditIn,
)

router = APIRouter(tags=["season"])


def _build(fn, body: SeasonPlanIn) -> dict:
    try:
        return fn(body.model_dump())
    except service.EventGoneError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SeasonPlanError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/season-plan", response_model=Optional[SeasonPlan])
def get_season_plan():
    """The plan with statuses and summary for today, or null when there is none."""
    return service.get_plan()


@router.post("/season-plan/preview", response_model=SeasonPlan)
def preview_season_plan(body: SeasonPlanIn):
    """A full plan from the create form, not saved."""
    return _build(service.preview, body)


@router.post("/season-plan", response_model=SeasonPlan, status_code=201)
def create_season_plan(body: SeasonPlanIn):
    """Build and save a plan, replacing any plan already there."""
    return _build(service.create, body)


def _edit(fn, *args) -> dict:
    try:
        return fn(*args)
    except service.NoPlanError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except service.SeasonEditError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.patch("/season-plan/weeks/{week_start}", response_model=SeasonPlan)
def edit_week(week_start: str, body: WeekEditIn):
    """
    Set one week by hand and pin it (lighter, heavier, make recovery, skip, an
    exact TSS) or unpin it. Later unpinned weeks re-flow; `warnings` names any
    guardrail the edit breaks.
    """
    if body.action in service.SPLIT_ACTIONS:
        return _edit(service.edit_split, week_start, body.action, body.split)
    return _edit(service.edit_week, week_start, body.action, body.value, body.note)


@router.patch("/season-plan/settings", response_model=SeasonPlan)
def edit_settings(body: SettingsIn):
    """
    Change the weekly hours cap and/or build-up pace (future weeks re-flow), or
    on its own the triathlon split table.
    """
    return _edit(service.update_settings, body.model_dump(include=body.model_fields_set))


@router.post("/season-plan/sub-events", response_model=SeasonPlan)
def add_sub_event(body: SubEventIn):
    """
    Make an earlier event a sub event: the plan eases off around it (a
    mini-taper sized by its distance) and the main event stays the goal.
    """
    return _edit(service.add_sub_event, body.event_id)


@router.delete("/season-plan/sub-events/{event_id}", response_model=SeasonPlan)
def remove_sub_event(event_id: str):
    """Take a sub event off the plan; its weeks go back to the build."""
    return _edit(service.remove_sub_event, event_id)


@router.get("/season-plan/checkin", response_model=Optional[Checkin])
def get_checkin():
    """The fitness check-in open now (last day of the week), or null."""
    plan = service.active_plan()
    return service.pending_checkin(plan, date.today()) if plan else None


@router.post("/season-plan/checkin", response_model=SeasonPlan)
def post_checkin(body: CheckinIn):
    """Answer the open check-in, or change this week's choice."""
    return _edit(service.answer_checkin, body.week_start, body.choice)


@router.post("/season-plan/week-easy", response_model=SeasonPlan)
def post_week_easy(body: WeekEasyIn):
    """Answer the mid-week fatigue question: yes halves the rest of this week."""
    return _edit(service.week_easy, body.accept)


@router.delete("/season-plan", status_code=204)
def delete_season_plan():
    if not service.cancel():
        raise HTTPException(status_code=404, detail="No season plan.")


@router.get("/season-plan/revisions", response_model=list[Revision])
def get_revisions():
    """The plan's change log, newest first."""
    return service.revisions()
