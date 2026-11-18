"""Schedule endpoints: the athlete's weekly training schedule, edited in the
dashboard Settings and stored in data/schedule.json (app/schedule_store.py).
One resource, one shape — this is everything the plan payload's
training_schedule and schedule_constraints are built from.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.models import WeeklySchedule, WeeklyScheduleIn
from app.schedule_store import get_schedule, save_schedule

router = APIRouter(tags=["schedule"])


@router.get("/schedule", response_model=WeeklySchedule)
def read_schedule():
    """The training week as Claude will see it."""
    return get_schedule()


@router.put("/schedule", response_model=WeeklySchedule)
def write_schedule(body: WeeklyScheduleIn):
    """Replace the training week's days; the constraints come back computed."""
    return save_schedule(body.model_dump())
