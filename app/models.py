"""Pydantic request/response models for the API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Optional, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

from app.config import (
    DEFAULT_ACTIVITY_CLASS,
    DEFAULT_VO2MAX_DECLINE_THRESHOLD,
    MAX_AGE_YEARS,
    age_on,
)

EventType = Literal[
    "sprint", "olympic", "70.3", "140.6",
    "marathon", "half-marathon","15K", "10K", "5K",
    "custom",
]
Discipline = Literal["swim", "bike", "run", "strength"]

# Which disciplines a race of each EventType is actually made of. The GOAL picks
# the disciplines, the schedule picks only the days (rule 4 of the plan prompt):
# a marathon is a run race even on a day whose preferred_disciplines lists swim.
# None means unknown -- no restriction, the schedule governs on its own.
# "strength" is deliberately in no entry: it is not a race discipline, it is
# support work, and rule 4 lets a day that asks for it keep it whatever the race.
RACE_DISCIPLINES: dict[str, tuple[Discipline, ...] | None] = {
    "sprint": ("swim", "bike", "run"),
    "olympic": ("swim", "bike", "run"),
    "70.3": ("swim", "bike", "run"),
    "140.6": ("swim", "bike", "run"),
    "marathon": ("run",),
    "half-marathon": ("run",),
    "15K": ("run",),
    "10K": ("run",),
    "5K": ("run",),
    "custom": None,
}


def race_disciplines(goal_type: str | None) -> list[str] | None:
    """The disciplines this goal's race contains; None when unknown."""
    disciplines = RACE_DISCIPLINES.get(goal_type or "")
    return list(disciplines) if disciplines else None


class EventIn(BaseModel):
    """Event create/update payload."""

    name: str
    type: EventType
    race_date: str = Field(description="ISO date, YYYY-MM-DD")
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    location_name: Optional[str] = Field(default=None, description="City, for geocoding")
    target_finish_time_sec: Optional[int] = None
    sub_goals: list[str] = Field(default_factory=list)
    icon: Optional[str] = Field(default=None, description="Emoji shown in the dashboard")
    website: Optional[str] = Field(default=None, description="Race/event website URL")
    notes: Optional[str] = None
    participating: bool = Field(
        default=True,
        description="False keeps the event on the calendar but the coach ignores it as a goal",
    )


class Event(EventIn):
    event_id: str


class AthleteProfile(BaseModel):
    """
    Athlete thresholds (config/athlete.yaml keys — ftp_w drives bike TSS).

    The birthday is what's stored; `age` is derived from it on every read, so it
    stays right without the athlete ever editing it again.
    """

    ftp_w: float = Field(gt=0, description="Functional Threshold Power, watts")
    run_threshold_pace_sec_km: float = Field(gt=0)
    css_sec_100m: float = Field(gt=0, description="Critical Swim Speed, sec/100m")
    lthr_bpm: float = Field(gt=0)
    weight_kg: float = Field(gt=0)
    activity_class: int = Field(
        default=DEFAULT_ACTIVITY_CLASS,
        ge=0,
        le=10,
        description="Habitual activity level, 0 (inactive) to 10 (competitive)",
    )
    # Capped at 5 so a typo like 50 cannot silently disable the plan's VO2max
    # rule for good; a 5-point drop in four weeks does not happen, so the cap
    # still leaves room to opt out of the rule deliberately.
    vo2max_decline_threshold: float = Field(
        default=DEFAULT_VO2MAX_DECLINE_THRESHOLD,
        gt=0,
        le=5,
        description="4-week VO2max drop (ml/kg/min) that asks for a VO2max session",
    )
    birthday: date = Field(description="ISO date, YYYY-MM-DD")

    @field_validator("birthday")
    @classmethod
    def _birthday_is_plausible(cls, v: date) -> date:
        years = age_on(v)
        if years < 0:
            raise ValueError("birthday must be in the past")
        if years > MAX_AGE_YEARS:
            raise ValueError(f"birthday cannot be more than {MAX_AGE_YEARS} years ago")
        return v

    @computed_field(description="Completed years since the birthday (derived)")
    @property
    def age(self) -> int:
        return age_on(self.birthday)


class MfaCode(BaseModel):
    """Garmin MFA code submitted from the dashboard during a pending sync."""

    code: str


class GarminApiKey(BaseModel):
    """A new Garmin Personal API key, pasted into the dashboard's error modal."""

    key: str


class CoachMessage(BaseModel):
    role: str  # "user" | "coach"
    text: str


class CoachRequest(BaseModel):
    message: str
    history: list[CoachMessage] = Field(default_factory=list)
    # "Include context" in the chat composer. False means the turn is answered
    # from general triathlon knowledge only: no athlete data in the system
    # prompt and no plan-editing tools (see app/routers/coach.py).
    include_context: bool = True
    # Completed workouts attached in the conversation (newest first): the route
    # adds each one's full recorded data to the system context. The pattern is
    # the analysis route's activity_id guard.
    workout_ids: list[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)]] = Field(
        default_factory=list, max_length=3
    )


class CoachInstructions(BaseModel):
    """Athlete's standing instructions for the AI coach; empty text clears them."""

    text: str = Field(max_length=4000)


Weekday = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]


SessionType = Literal["any", "endurance", "quality", "long", "brick"]


class DaySchedule(BaseModel):
    """One weekday of the athlete's training week."""

    available: bool
    rest: bool = False
    preferred_disciplines: list[Discipline] = Field(default_factory=list)
    max_sessions: int = Field(default=1, ge=1, le=4)
    session_type: SessionType = "any"

    @model_validator(mode="after")
    def _brick_needs_two_disciplines(self) -> "DaySchedule":
        # A brick is two disciplines back-to-back, so the day must say which
        # two — and it can never fit in a single session.
        if self.session_type == "brick" and not self.rest:
            if len(self.preferred_disciplines) < 2:
                raise ValueError(
                    "a brick day needs at least two preferred disciplines"
                )
            if self.max_sessions < 2:
                self.max_sessions = 2
        return self


class ScheduleConstraints(BaseModel):
    """Week-level plan constraints, computed from the days (read-only)."""

    max_training_days_per_week: int = Field(ge=0, le=7)
    max_consecutive_training_days: int = Field(ge=0, le=7)
    long_ride_day: Optional[Weekday] = None
    long_run_day: Optional[Weekday] = None
    brick_allowed_days: list[Weekday] = Field(default_factory=list)


class WeeklyScheduleIn(BaseModel):
    """PUT /schedule body: just the days — the constraints are computed."""

    schedule: dict[Weekday, DaySchedule]

    @field_validator("schedule")
    @classmethod
    def _all_weekdays(cls, week: dict) -> dict:
        missing = sorted(set(get_args(Weekday)) - set(week))
        if missing:
            raise ValueError(f"missing weekdays: {', '.join(missing)}")
        return week


class WeeklySchedule(WeeklyScheduleIn):
    """The full schedule as served: the days plus the derived constraints."""

    constraints: ScheduleConstraints


class StructureSegment(BaseModel):
    """One segment of a planned session's structure (warmup/main/cooldown...)."""

    model_config = ConfigDict(extra="allow")

    segment: Optional[str] = None
    duration_min: Optional[float] = None
    zone: Optional[str] = None
    target_power_w: Optional[str] = Field(default=None, description='Range like "112-149"')
    notes: Optional[str] = None


class PlanSessionEdit(BaseModel):
    """A planned session as supplied by a coach plan-editing tool call.

    Validates structure only — coaching guardrails (80/20 etc.) stay in the
    system prompt so the athlete can consciously override them.
    """

    model_config = ConfigDict(extra="allow")

    discipline: Discipline
    title: Optional[str] = None
    type: Optional[str] = None
    duration_min: float = Field(gt=0)
    estimated_tss: float = Field(ge=0)
    zones_target: Optional[dict] = None
    structure: Optional[list[StructureSegment]] = None
    notes: Optional[str] = None


class DeviceSendIn(BaseModel):
    """One planned session to send to the athlete's watch (POST /device/send),
    addressed like the coach tools address it: (date, session_index)."""

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    session_index: int = Field(ge=0)


class SessionMoveIn(BaseModel):
    """A planned session dragged to another day (POST /recommend/move-session)."""

    from_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    session_index: int = Field(ge=0)
    to_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class Weather(BaseModel):
    event_id: str
    race_date: str
    source: Literal["forecast", "historical_climate", "unavailable"]
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    location_name: Optional[str] = None
    days_until_race: Optional[int] = None
    temp_c: Optional[float] = None
    apparent_temp_c: Optional[float] = None
    wind_kmh: Optional[float] = None
    wind_dir_deg: Optional[float] = None
    precipitation_probability_pct: Optional[float] = None
    humidity_pct: Optional[float] = None
    uv_index: Optional[float] = None
    note: Optional[str] = None
