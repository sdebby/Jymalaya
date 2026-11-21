// API shapes (mirror the FastAPI/InfluxDB read models).

export interface WorkoutRow {
  time: string;
  // Local calendar date ("YYYY-MM-DD") stored with the workout. Absent on rows
  // written before the backend stored it; fall back to converting `time`.
  date?: string;
  activity_id: string;
  discipline: string;
  title?: string;
  duration_sec?: number;
  distance_m?: number;
  tss?: number;
  avg_hr?: number;
  avg_power?: number;
  max_power?: number;
  normalized_power?: number;
  avg_cadence?: number;
  elevation_gain_m?: number;
  elevation_loss_m?: number;
  calories?: number;
  avg_pace_sec_per_km?: number;
  avg_pace_sec_per_100m?: number;
  intensity_factor?: number;
  zones_time_sec?: string; // JSON: { "Z1": seconds, ... } time-in-HR-zone
  // Garmin's Training Effect, 0.0-5.0 (0.0 is a real "No Benefit"), and its
  // primary-benefit key (TEMPO, LACTATE_THRESHOLD, ...). Absent before 2026-11-09.
  aerobic_te?: number;
  anaerobic_te?: number;
  te_label?: string;
  // The athlete's own rating from the FIT file: RPE 1-10, feel 0-100 in steps
  // of 25 (0 is a real "Very Weak"). Either can be absent.
  rpe?: number;
  feel?: number;
  is_brick_split?: boolean;
}

// One daily Garmin VO2max reading (GET /api/metrics/vo2max).
//
// `time` is stamped MIDNIGHT UTC by the backend (write_vo2max in
// app/influx/client.py), so `time.slice(0, 10)` IS the calendar day. Never put
// it through `new Date()` to get that day: west of UTC it lands on the previous
// date, silently shifting the whole series by one.
export interface Vo2maxRow {
  time: string;
  discipline: string; // "run" | "bike"
  vo2max: number;
}

// One day of Garmin recovery readings (GET /api/metrics/recovery): the
// overnight HRV average in ms, the resting heart rate in bpm and the night's
// sleep in seconds (naps excluded; Garmin dates a night by the morning after).
// Any may be absent on a day Garmin had no reading for. `time` is midnight UTC, read the
// day off it exactly as for Vo2maxRow.
export interface RecoveryRow {
  time: string;
  hrv?: number | null;
  resting_hr?: number | null;
  sleep_sec?: number | null;
}

export interface LoadRow {
  time: string;
  ctl?: number;
  atl?: number;
  tsb?: number;
  tss?: number;
}

export interface RaceEvent {
  event_id: string;
  name: string;
  type: string;
  race_date: string;
  location_lat?: number | null;
  location_lon?: number | null;
  location_name?: string | null;
  target_finish_time_sec?: number | null;
  sub_goals: string[];
  icon?: string | null;
  website?: string | null;
  notes?: string | null;
  // false = calendar only: the coach never treats it as a goal.
  participating: boolean;
}

// Create/update payload — same as RaceEvent without the server-assigned id.
export type RaceEventIn = Omit<RaceEvent, "event_id">;

export const EVENT_TYPES = [
  { value: "sprint", label: "Sprint" },
  { value: "olympic", label: "Olympic" },
  { value: "70.3", label: "Half (70.3)" },
  { value: "140.6", label: "Full (140.6)" },
  { value: "marathon", label: "Marathon" },
  { value: "half-marathon", label: "Half marathon" },
  { value: "15K", label: "15K" },
  { value: "10K", label: "10K" },
  { value: "5K", label: "5K" },
  { value: "custom", label: "Custom" },
] as const;

export interface Weather {
  event_id: string;
  race_date: string;
  source: "forecast" | "historical_climate" | "unavailable";
  location_name?: string | null;
  days_until_race?: number | null;
  temp_c?: number | null;
  apparent_temp_c?: number | null;
  wind_kmh?: number | null;
  precipitation_probability_pct?: number | null;
  humidity_pct?: number | null;
  uv_index?: number | null;
  note?: string | null;
}

export interface StructureSegment {
  segment?: string;
  duration_min?: number;
  zone?: string;
  target_power_w?: string; // watt range for bike segments, e.g. "140-188"
  notes?: string;
}

export interface PlanSession {
  discipline: string;
  title?: string;
  type?: string;
  duration_min?: number;
  estimated_tss?: number;
  zones_target?: Record<string, string>;
  structure?: StructureSegment[];
  notes?: string;
}

export interface PlanDay {
  date: string;
  day_name: string;
  is_rest_day: boolean;
  sessions: PlanSession[];
  // The athlete asked the coach to change this day; kept when the plan is
  // rebuilt, if they choose to keep their changes.
  athlete_requested?: boolean;
}

// A plan day the athlete asked the coach to change (GET /recommend/requests),
// as it stands after the edit. Lives until the day has passed.
export interface PlanRequest {
  date: string;
  day_name: string;
  sessions: PlanSession[];
  notes: string[];
  requested_at: string;
}

export interface Plan {
  plan_week_start: string;
  summary?: string;
  weekly_tss_target?: number;
  periodization_phase?: string;
  days: PlanDay[];
  coach_notes?: string;
  // The short note in the sync summary. Absent on plans cached before it existed.
  headline?: string;
  // The season plan's budget for the plan's window; absent without a season plan.
  window_budget_tss?: number;
  // Six flat or only-rising weeks of TSS made this plan lighter (prompt rule 32).
  load_reduction?: {
    pattern: "plateau" | "rising";
    weekly_tss: number[];
    last_week_tss: number;
    reduction_pct: number;
    target_tss: number;
    planned_tss?: number;
    message: string;
  };
  // Stamped by the backend when the plan is cached (recommend_service).
  _meta?: { plan_id?: string; generated_at?: string; trigger?: string; athlete_requests?: "kept" | "overwritten" };
}

// A planned session put on the athlete's intervals.icu calendar
// (POST /device/send), from where intervals.icu passes it to the watch.
// moving_time_sec is the workout length as intervals.icu read the steps.
export interface DeviceSendResult {
  event_id: number | string | null;
  name: string;
  date: string;
  external_id: string;
  moving_time_sec?: number | null;
}

// Claude's review of a completed workout — mirrors prompts/analysis_format.json.
// Every field is optional: the model can omit any of them, and the modal renders
// whatever came back rather than assuming a complete object.
// GET /workouts/{id}/streams: the stored samples bucketed (30 s, widened for
// long sessions), t in seconds of moving time, plus %FTP power-zone seconds for
// a ride (null otherwise).
export interface StreamSample {
  t: number;
  hr?: number;
  power_w?: number;
  speed_mps?: number;
  elevation_m?: number;
  cadence?: number;
  distance_m?: number;
}

export interface WorkoutStreams {
  activity_id: string;
  discipline: string;
  bucket_sec: number;
  samples: StreamSample[];
  power_zones: Record<string, number> | null;
}

export interface WorkoutAnalysis {
  plan_adherence?: {
    had_structured_workout?: boolean;
    verdict?: string;
    detail?: string;
  };
  what_went_well?: string[];
  what_to_improve?: string[];
  // A list since 2026-11-07; analyses cached before that hold one paragraph.
  form_context?: string[] | string;
  flags?: string[];
  // Stamped by the backend when the analysis is cached (analysis_service).
  _meta?: {
    analysis_id?: string;
    generated_at?: string;
    activity_id?: string;
    garmin_activity_id?: string | null;
    trigger?: string;
  };
}

// Something attached to the coach chat, from a card's "ask coach" button or the
// footer of a detail modal. Two kinds:
//
//   plan    — a planned session. sessionIndex is its zero-based position within
//             its plan day: the address the coach's plan-editing tools use, since
//             the plan JSON has no session IDs.
//   workout — a completed session. The chat names it by activity_id and the
//             backend sends its full recorded data and cached analysis.
export interface PlanAttachment {
  kind: "plan";
  session: PlanSession;
  date: string; // plan day date, YYYY-MM-DD
  dayName: string;
  sessionIndex: number;
}

export interface WorkoutAttachment {
  kind: "workout";
  workout: WorkoutRow;
  date: string;
  dayName: string;
}

//   season_week — one week of the season plan, from its week panel. date is the
//             week's first day, the address the adjust_season_plan tool uses.
export interface SeasonWeekAttachment {
  kind: "season_week";
  week: SeasonWeek;
  weeksInPlan: number;
  date: string;
  dayName: string;
}

export type CoachAttachment = PlanAttachment | WorkoutAttachment | SeasonWeekAttachment;

// Athlete thresholds served by GET/PUT /athlete (config/athlete.yaml keys).
// What's stored and editable — the birthday is the stored form of the age.
export interface AthleteProfileIn {
  ftp_w: number;
  run_threshold_pace_sec_km: number;
  css_sec_100m: number;
  lthr_bpm: number;
  weight_kg: number;
  activity_class: number; // habitual activity level, 0 (inactive) - 10 (competitive)
  vo2max_decline_threshold: number; // 4-week VO2max drop (ml/kg/min) that asks for a VO2max session
  birthday: string; // ISO date, YYYY-MM-DD
}

// What the backend serves back: the stored fields plus the age it derives from
// the birthday. `age` is read-only — sending it changes nothing.
export interface AthleteProfile extends AthleteProfileIn {
  age: number;
}

// The athlete's training week, served by GET/PUT /schedule. Edited in Settings
// and stored as data/schedule.json — the only source for what the AI coach is
// allowed to plan.
export type Weekday =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday";

export type SessionType = "any" | "endurance" | "quality" | "long" | "brick";

export interface DaySchedule {
  available: boolean;
  rest: boolean;
  preferred_disciplines: string[];
  max_sessions: number;
  session_type: SessionType;
}

// Computed by the backend from the days; read-only in the UI.
export interface ScheduleConstraints {
  max_training_days_per_week: number;
  max_consecutive_training_days: number;
  long_ride_day: Weekday | null;
  long_run_day: Weekday | null;
  brick_allowed_days: Weekday[];
}

export interface WeeklySchedule {
  schedule: Record<Weekday, DaySchedule>;
  constraints: ScheduleConstraints;
}

export interface SyncStatus {
  last_sync: string | null;
  activity_count: number;
  status: string;
  error: string | null;
  // True while a sync is blocked waiting for the Garmin MFA email code.
  mfa_pending?: boolean;
  // Pipeline step of the currently running sync ("garmin" | "parsing" |
  // "metrics" | "coach"); null when no sync is running.
  sync_phase?: string | null;
  // On the POST /sync response: "generated" when the AI coach produced a new
  // plan, otherwise "skipped (...)" with the reason.
  recommendation?: string;
  // On the POST /sync response: "Plan adjusted: ..." when the sync moved a
  // season-plan target, else null.
  season_plan_notice?: string | null;
  // Set when six flat or only-rising weeks made the new plan lighter.
  load_reduction_notice?: string | null;
}

// ---- Season plan (GET /season-plan; tests/fixtures/season/ is the contract) --

export type SeasonPhase = "base" | "build" | "peak" | "taper" | "race" | "fitness";
export type SeasonKind = "loading" | "recovery" | "return" | "taper" | "race" | "sub_taper" | "sub_race";
export type SeasonWeekStatus = "past" | "current" | "next" | "pending_checkin" | "projected";
export type SeasonRamp = "gentle" | "standard" | "ambitious";
export type SeasonFocus = "triathlon" | "run" | "bike" | "swim";
export type SeasonChoice = "increase" | "stay" | "decrease" | "recovery";
export type SeasonBand = "fresh" | "ok" | "tired" | "high";

export interface SeasonCheckinOption {
  choice: SeasonChoice;
  target_tss: number;
  target_hours: number;
  delta_pct_vs_last: number;
  enabled: boolean;
  disabled_reason: string | null;
}

// A fitness plan's weekly check-in. "pending" is the one open now (worked out
// on read, last of `checkins`); answered / auto_applied ones are stored.
export interface SeasonCheckin {
  week_start: string;
  status: "pending" | "answered" | "auto_applied";
  opened_at: string;
  source: "user" | "auto" | null;
  choice: SeasonChoice | null;
  chosen_at: string | null;
  closing_week: {
    week_start: string;
    target_tss: number;
    actual_tss_to_date: number;
    planned_remaining_tss: number;
    projected_tss: number;
    completion_pct: number;
  } | null;
  form: { ctl: number | null; atl: number | null; tsb: number | null; band: SeasonBand };
  loading_weeks_since_recovery: number;
  recommended: SeasonChoice;
  reason: string;
  options: SeasonCheckinOption[];
  return_week?: boolean;
  resolved?: boolean | null;
}

export interface SeasonWeek {
  week_start: string;
  index: number;
  phase: SeasonPhase;
  kind: SeasonKind;
  target_tss: number;
  target_hours: number;
  actual_tss: number | null;
  actual_hours: number | null;
  ctl_end: number | null;
  status: SeasonWeekStatus;
  pinned: boolean;
  pin_note: string | null;
  source: string;
  focus: string | null;
  // Triathlon plans only: swim/bike/run percents of the target, where they
  // came from, the target per discipline (TSS and hours, worked out on read)
  // and what was done per discipline, strength included.
  split?: SeasonSplit | null;
  split_source?: "table" | "user" | null;
  discipline_tss?: SeasonSplit | null;
  discipline_hours?: SeasonSplit | null;
  actual_by_discipline?: Record<string, { tss: number; hours: number }> | null;
  // A sub event's week: the race, and whether this is the week before it
  // (pre), its own week (race) or the recovery week after (post).
  sub_event?: (SeasonSubEvent & { role: "pre" | "race" | "post" }) | null;
  sub_factor?: number | null;
}

// A race before the main event that the plan eases off around.
export interface SeasonSubEvent {
  event_id: string;
  name: string;
  type: string;
  race_date: string;
}

export type SplitDiscipline = "swim" | "bike" | "run";

export interface SeasonInstructionsView {
  summary: string;
  // One plain line per change the plan made from them.
  applied: string[];
  // Days and session details: the 7-day plan follows those itself.
  left_to_weekly_plan: string[];
  error?: string | null;
  used: boolean;
  opted_out?: boolean;
}
export type SeasonSplit = Record<SplitDiscipline, number>;

export interface SeasonRevision {
  at: string;
  trigger: "sync" | "user" | "coach" | "event_change" | "instructions";
  week_start: string | null;
  field: string;
  from: number | null;
  to: number | null;
  reason_code: string;
  reason_text: string;
}

export interface SeasonPlan {
  schema_version: number;
  as_of: string | null;
  plan_id: string;
  mode: "event" | "fitness";
  status: "active" | "completed" | "cancelled" | "event_missing";
  created_at: string;
  updated_at: string;
  week_start_day: "monday" | "sunday";
  event: { event_id: string; name: string; type: string; race_date: string } | null;
  // Event plans: earlier races the plan eases off around, by date.
  sub_events?: SeasonSubEvent[];
  settings: {
    weekly_hours_cap: number | null;
    ramp: SeasonRamp | null;
    growth_pct_per_week: number | null;
    focus: SeasonFocus | null;
    split_by_phase?: Record<string, SeasonSplit> | null;
    // Loading weeks between recovery weeks (3 unless the instructions say otherwise).
    recovery_cadence_weeks?: number;
  };
  baseline: {
    ctl: number | null;
    atl: number | null;
    tsb: number | null;
    avg_weekly_tss_4w: number;
    avg_weekly_hours_4w: number;
    tss_per_hour: number;
    captured_at: string;
    limited_history: boolean;
  };
  targets: { peak_ctl: number; peak_weekly_tss: number; compressed: boolean } | null;
  summary: { line1: string; line2: string };
  weeks: SeasonWeek[];
  checkins: SeasonCheckin[];
  revisions: SeasonRevision[];
  // On a PATCH response: coaching guardrails the edit broke.
  warnings?: string[];
  // The open mid-week "make the rest of this week easy?" question.
  fatigue_banner?: { week_start: string; tsb: number } | null;
  // Where each load setting came from: the form or Settings ("user"), the
  // coach instructions, or the default.
  settings_source?: Partial<Record<"weekly_hours_cap" | "ramp" | "recovery_cadence_weeks" | "peak_ctl", "user" | "instructions" | "profile" | "default">>;
  // The coach instructions as the plan read them (worked out on read).
  instructions_view?: SeasonInstructionsView | null;
}

export type SeasonWeekAction = "set_target" | "make_recovery" | "lighter" | "heavier" | "unpin" | "skip";
export type SeasonSplitAction = "set_split" | "reset_split";

// Body of PATCH /season-plan/settings: only the keys present change.
export interface SeasonSettingsIn {
  weekly_hours_cap?: number | null;
  ramp?: SeasonRamp;
  focus?: SeasonFocus;
  // Sent on its own: the triathlon split table per phase; null = the defaults.
  split_by_phase?: Record<string, SeasonSplit> | null;
}

// Body of POST /season-plan and /season-plan/preview.
export interface SeasonPlanIn {
  mode: "event" | "fitness";
  event_id?: string | null;
  weekly_hours_cap?: number | null;
  ramp?: SeasonRamp | null;
  peak_ctl?: number | null;
  focus?: SeasonFocus | null;
  week_start_day: "monday" | "sunday";
  start?: "this" | "next" | null;
  // Fitness only: carry the current plan's finished weeks over.
  convert?: boolean;
  // Triathlon only: the split table rows the athlete changed, per phase.
  split_by_phase?: Record<string, SeasonSplit> | null;
  // Read the coach instructions into the plan (default on).
  use_instructions?: boolean;
  // Event only: earlier events to ease off around (sub events).
  sub_event_ids?: string[];
}
