"""
Pydantic models of the season plan -- the contract in tests/fixtures/season/.

The stored plan is a plain dict (data/season_plan.json); these models validate
it at the API boundary and the request bodies coming in, the same split as the
rest of the app (app/models.py).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models import EventType

Mode = Literal["event", "fitness"]
PlanStatus = Literal["active", "completed", "cancelled", "event_missing"]
WeekStartDay = Literal["monday", "sunday"]
Ramp = Literal["gentle", "standard", "ambitious"]
Focus = Literal["triathlon", "run", "bike", "swim"]
Phase = Literal["base", "build", "peak", "taper", "race", "fitness"]
Kind = Literal["loading", "recovery", "return", "taper", "race", "sub_taper", "sub_race"]
WeekStatus = Literal["past", "current", "next", "pending_checkin", "projected"]
WeekSource = Literal[
    "builder", "adapt", "user", "checkin_user", "checkin_auto", "projection"
]
Choice = Literal["increase", "stay", "decrease", "recovery"]
Band = Literal["fresh", "ok", "tired", "high"]
# Swim / bike / run percents of a week's TSS target (app/season/split.py).
Split = dict[str, int]
SplitSource = Literal["table", "user", "kept"]
ReasonCode = Literal[
    "FATIGUE_GATE",
    "UNDER_DELIVERY",
    "OVER_DELIVERY_HOLD",
    "FRESH_BONUS",
    "LAYOFF_RETURN",
    "USER_PIN",
    "EVENT_CHANGED",
    "PEAK_LOWERED",
    "COMPRESSED",
    "HOURS_CAP",
    "CHECKIN",
    "SETTINGS",
    "INSTRUCTIONS",
    "SUB_EVENT",
]
# Where a plan setting came from: the create form or the Settings row ("user"),
# the coach instructions' read, or the default. A later change to the
# instructions touches only what they set, never what the athlete set.
SettingSource = Literal["user", "instructions", "profile", "default"]


class PlanEvent(BaseModel):
    event_id: str
    name: str
    type: EventType
    race_date: str


class PlanSettings(BaseModel):
    weekly_hours_cap: Optional[float] = None
    ramp: Optional[Ramp] = None
    growth_pct_per_week: Optional[float] = None
    recovery_cadence_weeks: int = 3
    recovery_factor: float = 0.70
    step_increase_pct: Optional[float] = None
    step_decrease_pct: Optional[float] = None
    focus: Optional[Focus] = None
    # A triathlon plan's own split table, per phase (or "fitness"), over the
    # code table in config.DISCIPLINE_SPLIT.
    split_by_phase: Optional[dict[str, Split]] = None


class SubEvent(BaseModel):
    """A race before the main event that the plan eases off around."""

    event_id: str
    name: str
    type: EventType
    race_date: str


class WeekSubEvent(SubEvent):
    # pre: the week before a long sub event; race: the week it falls in;
    # post: the recovery week after a long one.
    role: Literal["pre", "race", "post"]


class Baseline(BaseModel):
    ctl: Optional[float] = None
    atl: Optional[float] = None
    tsb: Optional[float] = None
    avg_weekly_tss_4w: float
    avg_weekly_hours_4w: float
    tss_per_hour: float
    tss_per_hour_by_discipline: dict[str, float] = {}
    captured_at: str
    limited_history: bool = False


class Targets(BaseModel):
    peak_ctl: float
    peak_weekly_tss: float
    compressed: bool = False


class Summary(BaseModel):
    line1: str
    line2: str


class Week(BaseModel):
    week_start: str
    index: int
    phase: Phase
    kind: Kind
    target_tss: float
    target_hours: float
    actual_tss: Optional[float] = None
    actual_hours: Optional[float] = None
    ctl_end: Optional[float] = None
    status: WeekStatus
    pinned: bool = False
    pin_note: Optional[str] = None
    source: WeekSource
    focus: Optional[str] = None
    # Triathlon plans only: the week's split, where it came from, and what was
    # done per discipline ({tss, hours}, strength included).
    split: Optional[Split] = None
    split_source: Optional[SplitSource] = None
    actual_by_discipline: Optional[dict[str, dict[str, float]]] = None
    # Worked out on read from split and target_tss; never stored.
    discipline_tss: Optional[dict[str, int]] = None
    discipline_hours: Optional[dict[str, float]] = None
    # A sub event's week: which race and its role, and the share of the last
    # loading week it is sized at (reflow re-sizes it).
    sub_event: Optional[WeekSubEvent] = None
    sub_factor: Optional[float] = None


class ClosingWeek(BaseModel):
    week_start: str
    target_tss: float
    actual_tss_to_date: float
    planned_remaining_tss: float
    projected_tss: float
    completion_pct: float


class Form(BaseModel):
    ctl: Optional[float] = None
    atl: Optional[float] = None
    tsb: Optional[float] = None
    band: Band


class CheckinOption(BaseModel):
    choice: Choice
    target_tss: float
    target_hours: float
    delta_pct_vs_last: float
    enabled: bool
    disabled_reason: Optional[str] = None


class Checkin(BaseModel):
    week_start: str
    status: Literal["pending", "answered", "auto_applied"]
    opened_at: str
    source: Optional[Literal["user", "auto"]] = None
    choice: Optional[Choice] = None
    chosen_at: Optional[str] = None
    closing_week: Optional[ClosingWeek] = None
    form: Form
    loading_weeks_since_recovery: int
    recommended: Choice
    reason: str
    options: list[CheckinOption]
    # A layoff: the recovery option is a return week at 60%.
    return_week: bool = False
    # An answered check-in is resolved again from the closing week's final
    # total once the week it decides starts; then it is frozen.
    resolved: Optional[bool] = None


class Revision(BaseModel):
    model_config = {"populate_by_name": True}

    at: str
    trigger: Literal["sync", "user", "coach", "event_change", "instructions"]
    week_start: Optional[str] = None
    field: str
    from_: Optional[float] = Field(default=None, alias="from")
    to: Optional[float] = None
    reason_code: ReasonCode
    reason_text: str


class SeasonPlan(BaseModel):
    schema_version: int = 1
    as_of: Optional[str] = None
    plan_id: str
    mode: Mode
    status: PlanStatus
    created_at: str
    updated_at: str
    # Bumped only when something the 7-day plan payload reads changes; compared
    # with the cached plan's generated_at by _plan_is_current. Not the file
    # mtime: the file is rewritten on every sync (actuals).
    payload_changed_at: Optional[str] = None
    week_start_day: WeekStartDay
    event: Optional[PlanEvent] = None
    # Event mode: races before the main event, by date.
    sub_events: list[SubEvent] = []
    settings: PlanSettings
    baseline: Baseline
    targets: Optional[Targets] = None
    summary: Summary
    weeks: list[Week]
    checkins: list[Checkin] = []
    revisions: list[Revision] = []
    # Coaching guardrails an edit broke -- allowed, but said (a PATCH response).
    warnings: list[str] = []
    # The mid-week "make the rest of this week easy?" question, when open:
    # {week_start, tsb}. Worked out on read.
    fatigue_banner: Optional[dict] = None
    # The coach instructions as the plan read them (app/season/instructions.py):
    # {text_sha, read_at, applied, opted_out, read}; and where each setting
    # came from.
    instructions: Optional[dict] = None
    settings_source: dict[str, SettingSource] = {}
    # Worked out on read: {summary, applied, left_to_weekly_plan, error, used}.
    instructions_view: Optional[dict] = None


class SeasonPlanIn(BaseModel):
    """Body of POST /season-plan and /season-plan/preview."""

    mode: Mode
    event_id: Optional[str] = None
    weekly_hours_cap: Optional[float] = Field(default=None, gt=0, le=40)
    ramp: Optional[Ramp] = None
    peak_ctl: Optional[float] = Field(default=None, gt=0, le=200)
    focus: Optional[Focus] = None
    week_start_day: WeekStartDay = "monday"
    start: Optional[Literal["this", "next"]] = None
    # Triathlon only: the split table, per phase, prefilled from the defaults.
    split_by_phase: Optional[dict[str, dict[str, float]]] = None
    # Fitness only: carry the current plan's finished weeks over (an event
    # plan whose event was removed becomes a fitness plan).
    convert: bool = False
    # Read the athlete's coach instructions into the plan (sports and load).
    use_instructions: bool = True
    # Event only: earlier events the plan eases off around (sub events).
    sub_event_ids: list[str] = []


WeekAction = Literal[
    "set_target", "make_recovery", "lighter", "heavier", "unpin", "skip", "set_split", "reset_split"
]


class WeekEditIn(BaseModel):
    """Body of PATCH /season-plan/weeks/{week_start}."""

    action: WeekAction
    # The TSS for set_target.
    value: Optional[float] = Field(default=None, ge=0, le=3000)
    # Why: travel, illness, a busy week. Saved with the pin.
    note: Optional[str] = Field(default=None, max_length=300)
    # The swim/bike/run percents for set_split.
    split: Optional[dict[str, float]] = None


class SettingsIn(BaseModel):
    """
    Body of PATCH /season-plan/settings. Only the fields sent change; an
    explicit null weekly_hours_cap removes the limit.
    """

    weekly_hours_cap: Optional[float] = Field(default=None, gt=0, le=40)
    ramp: Optional[Ramp] = None
    focus: Optional[Focus] = None
    # The triathlon split table, per phase; null goes back to the defaults.
    split_by_phase: Optional[dict[str, dict[str, float]]] = None


class SubEventIn(BaseModel):
    """Body of POST /season-plan/sub-events."""

    event_id: str


class CheckinIn(BaseModel):
    """Body of POST /season-plan/checkin."""

    week_start: str
    choice: Choice


class WeekEasyIn(BaseModel):
    """Body of POST /season-plan/week-easy: the answer to the mid-week question."""

    accept: bool
