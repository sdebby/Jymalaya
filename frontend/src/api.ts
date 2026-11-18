// Thin REST client. All calls go through the Vite proxy at /api -> :8080.

import type { AthleteProfile, AthleteProfileIn, DeviceSendResult, RaceEvent, RaceEventIn, LoadRow, Plan, PlanRequest, RecoveryRow, SeasonChoice, SeasonPlan, SeasonPlanIn, SeasonSettingsIn, SeasonSplit, SeasonSplitAction, SeasonWeekAction, SyncStatus, Vo2maxRow, Weather, WeeklySchedule, WorkoutAnalysis, WorkoutRow, WorkoutStreams } from "./types";

const BASE = "/api";

// The athlete's answer to "keep your plan changes?", as a query string. Left
// off when there was no question, so the backend default (keep) applies.
function keepQuery(keepRequests?: boolean): string {
  return keepRequests === undefined ? "" : `?keep_requests=${keepRequests}`;
}

// True when a fetch rejected because its AbortSignal fired (the chat's Stop
// button), rather than because the request actually failed. Callers that pass a
// signal must tell the two apart: a stop is not an error to report.
export function isAbortError(e: unknown): boolean {
  return (e as { name?: string } | null)?.name === "AbortError";
}

// A sync that failed on Garmin's Personal API: the backend's {code, message,
// action}, which GarminErrorModal turns into the suggested next step.
// code: no_key | key_rejected | garmin_error | network
export interface GarminFailure {
  code: string;
  message: string;
  action: string;
}

export class GarminSyncError extends Error {
  failure: GarminFailure;
  constructor(failure: GarminFailure) {
    super(failure.message);
    this.name = "GarminSyncError";
    this.failure = failure;
  }
}

function isGarminFailure(d: unknown): d is GarminFailure {
  const o = d as Partial<GarminFailure> | null;
  return !!o && typeof o.code === "string" && typeof o.message === "string" && typeof o.action === "string";
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

async function sendJSON<T>(method: string, path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {
      /* keep status */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => getJSON<{ status: string; influxdb_connected: boolean }>(`/health`),
  load: (days = 28, discipline = "all") =>
    getJSON<LoadRow[]>(`/metrics/load?days=${days}&discipline=${discipline}`),
  // Daily running/cycling VO2max from Garmin. Optional panel data -- the
  // athlete's watch may report neither, so callers must tolerate an empty list.
  vo2max: (days = 28) => getJSON<Vo2maxRow[]>(`/metrics/vo2max?days=${days}`),
  // Daily HRV, resting HR and sleep from Garmin, for the recovery-trend cards
  // (28 days) and the weekly sleep chart (8 weeks). Same
  // contract as vo2max: an empty list is a normal answer.
  recovery: (days = 28) => getJSON<RecoveryRow[]>(`/metrics/recovery?days=${days}`),
  workouts: (days = 28) => getJSON<WorkoutRow[]>(`/metrics/workouts?days=${days}`),
  events: () => getJSON<RaceEvent[]>(`/events`),
  createEvent: (event: RaceEventIn) => sendJSON<RaceEvent>("POST", `/events`, event),
  updateEvent: (eventId: string, event: RaceEventIn) => sendJSON<RaceEvent>("PUT", `/events/${eventId}`, event),
  deleteEvent: async (eventId: string): Promise<void> => {
    const res = await fetch(BASE + `/events/${eventId}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`delete event -> HTTP ${res.status}`);
  },
  weather: (eventId: string) => getJSON<Weather>(`/weather/${eventId}`),
  athlete: () => getJSON<AthleteProfile>(`/athlete`),
  updateAthlete: (a: AthleteProfileIn) => sendJSON<AthleteProfile>("PUT", `/athlete`, a),

  // Recompute stored TSS/IF for the last `days` days from the saved athlete
  // profile (Settings "Re-calculate" after a threshold edit). No Garmin pull.
  recomputeMetrics: (days = 7) =>
    sendJSON<{ stored_workouts: number; considered: number; rewritten: number; load_days: number }>(
      "POST",
      `/metrics/recompute?days=${days}`,
      {},
    ),

  // Athlete's standing instructions for the AI coach (empty text = none).
  coachInstructions: () => getJSON<{ text: string }>(`/coach/instructions`),
  // An active season plan reads the new text and says what it changed.
  updateCoachInstructions: (text: string) =>
    sendJSON<{ text: string; season_plan_note?: string | null }>("PUT", `/coach/instructions`, { text }),

  // The athlete's training week: per-day availability, preferred disciplines,
  // session count and type, plus the week-level constraints.
  schedule: () => getJSON<WeeklySchedule>(`/schedule`),
  updateSchedule: (s: WeeklySchedule) => sendJSON<WeeklySchedule>("PUT", `/schedule`, s),

  // Full reset: wipes the InfluxDB bucket + cached plan on the backend.
  deleteAllData: async (): Promise<void> => {
    const res = await fetch(BASE + `/data`, { method: "DELETE" });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = (await res.json())?.detail ?? detail;
      } catch {
        /* keep status */
      }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
  },
  // The season plan, or null when there is none. Optional panel data, so a
  // failure reads as no plan rather than blanking the dashboard.
  seasonPlan: async (): Promise<SeasonPlan | null> => {
    try {
      return await getJSON<SeasonPlan | null>(`/season-plan`);
    } catch {
      return null;
    }
  },
  // A full plan from the create form, not saved.
  previewSeasonPlan: (body: SeasonPlanIn) => sendJSON<SeasonPlan>("POST", `/season-plan/preview`, body),
  // Build and save a plan, replacing any plan already there.
  createSeasonPlan: (body: SeasonPlanIn) => sendJSON<SeasonPlan>("POST", `/season-plan`, body),
  // Set one week by hand and pin it, or unpin it. The response carries
  // `warnings` for any guardrail the edit breaks (allowed, but said).
  editSeasonWeek: (weekStart: string, action: SeasonWeekAction, value?: number | null, note?: string | null) =>
    sendJSON<SeasonPlan>("PATCH", `/season-plan/weeks/${weekStart}`, { action, value: value ?? null, note: note || null }),
  // A week's swim/bike/run split by hand, or back to the plan's table.
  editSeasonSplit: (weekStart: string, action: SeasonSplitAction, split?: SeasonSplit) =>
    sendJSON<SeasonPlan>("PATCH", `/season-plan/weeks/${weekStart}`, { action, split: split ?? null }),
  // Only the keys sent change; weekly_hours_cap: null removes the limit.
  updateSeasonSettings: (changes: SeasonSettingsIn) => sendJSON<SeasonPlan>("PATCH", `/season-plan/settings`, changes),
  // Answer the fitness check-in for `weekStart` (next week on the last day of
  // this one, or this week to change its choice).
  answerCheckin: (weekStart: string, choice: SeasonChoice) =>
    sendJSON<SeasonPlan>("POST", `/season-plan/checkin`, { week_start: weekStart, choice }),
  // The mid-week fatigue question: yes halves the rest of this week.
  // Sub events: earlier races the plan eases off around.
  addSeasonSubEvent: (eventId: string) => sendJSON<SeasonPlan>("POST", `/season-plan/sub-events`, { event_id: eventId }),
  removeSeasonSubEvent: (eventId: string) =>
    sendJSON<SeasonPlan>("DELETE", `/season-plan/sub-events/${encodeURIComponent(eventId)}`, undefined),
  weekEasy: (accept: boolean) => sendJSON<SeasonPlan>("POST", `/season-plan/week-easy`, { accept }),
  cancelSeasonPlan: async (): Promise<void> => {
    const res = await fetch(BASE + `/season-plan`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`cancel season plan -> HTTP ${res.status}`);
  },

  syncStatus: () => getJSON<SyncStatus>(`/sync/status`),

  // Hand the emailed Garmin MFA code to the sync currently waiting on it.
  submitMfa: (code: string) => sendJSON<{ submitted: boolean }>("POST", `/sync/mfa`, { code }),

  // Latest generated plan; null until the recommendation engine has run (404).
  recommendation: async (signal?: AbortSignal): Promise<Plan | null> => {
    try {
      const res = await fetch(BASE + `/recommend/latest`, { signal });
      if (!res.ok) return null;
      return res.json();
    } catch (e) {
      if (isAbortError(e)) throw e;
      return null;
    }
  },

  // The cached analysis for one completed workout, or null when it has not been
  // analyzed yet (the backend 404s). Same shape as recommendation() above: a
  // missing analysis is an ordinary state, not an error to surface.
  workoutAnalysis: async (activityId: string, signal?: AbortSignal): Promise<WorkoutAnalysis | null> => {
    try {
      const res = await fetch(BASE + `/workouts/${encodeURIComponent(activityId)}/analysis`, { signal });
      if (!res.ok) return null;
      return res.json();
    } catch (e) {
      if (isAbortError(e)) throw e;
      return null;
    }
  },

  // Run the analysis: the backend pulls this activity's detailed Garmin data,
  // derives the coaching metrics and asks Claude to review it. Slow (a Garmin
  // fetch plus a Claude call), and `force` re-runs it over a cached result.
  // The workout's recorded streams for the modal's graphs; null when no
  // samples are stored (synced before FIT samples were kept) or on any error.
  workoutStreams: async (activityId: string, signal?: AbortSignal): Promise<WorkoutStreams | null> => {
    try {
      const res = await fetch(BASE + `/workouts/${encodeURIComponent(activityId)}/streams`, { signal });
      if (!res.ok) return null;
      return res.json();
    } catch (e) {
      if (isAbortError(e)) throw e;
      return null;
    }
  },

  analyzeWorkout: (activityId: string, force = false) =>
    sendJSON<WorkoutAnalysis>(
      "POST",
      `/workouts/${encodeURIComponent(activityId)}/analyze?force=${force}`,
      {},
    ),

  // Send to device: put the plan session at (date, sessionIndex) on the
  // athlete's intervals.icu calendar, which passes it on to the watch. Sending
  // the same session again updates that calendar entry.
  sendToDevice: (date: string, sessionIndex: number) =>
    sendJSON<DeviceSendResult>("POST", `/device/send`, { date, session_index: sessionIndex }),

  // A planned session dragged to another day. Returns the updated plan.
  moveSession: (fromDate: string, sessionIndex: number, toDate: string) =>
    sendJSON<Plan>("POST", `/recommend/move-session`, { from_date: fromDate, session_index: sessionIndex, to_date: toDate }),

  // Ask the AI coach to regenerate the 7-day plan from current data, without
  // a Garmin sync. Returns the fresh plan.
  regeneratePlan: (keepRequests?: boolean) =>
    sendJSON<Plan>("POST", `/recommend/trigger${keepQuery(keepRequests)}`, {}),

  // Plan days the athlete asked the coach to change that are still in force.
  // Read before a sync or re-plan to decide whether to ask about them; a
  // failure reads as none, so this check can never block a sync.
  planRequests: async (): Promise<PlanRequest[]> => {
    try {
      const body = await getJSON<{ requests: PlanRequest[] }>(`/recommend/requests`);
      return Array.isArray(body?.requests) ? body.requests : [];
    } catch {
      return [];
    }
  },

  sync: async (keepRequests?: boolean): Promise<SyncStatus> => {
    const res = await fetch(BASE + `/sync${keepQuery(keepRequests)}`, { method: "POST" });
    if (!res.ok) {
      // Surface the backend's user-facing reason (auth/MFA-timeout/rate-limit).
      let detail: unknown = `sync -> HTTP ${res.status}`;
      try {
        detail = (await res.json())?.detail ?? detail;
      } catch {
        /* keep status */
      }
      if (isGarminFailure(detail)) throw new GarminSyncError(detail);
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  },

  // A new Garmin Personal API key. The backend checks it with Garmin and saves
  // it to .env only when it is accepted; it is never sent back.
  saveGarminKey: async (key: string): Promise<void> => {
    const res = await fetch(BASE + `/garmin/api-key`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    if (!res.ok) {
      let detail: unknown = `HTTP ${res.status}`;
      try {
        detail = (await res.json())?.detail ?? detail;
      } catch {
        /* keep status */
      }
      throw new Error(isGarminFailure(detail) ? detail.message : typeof detail === "string" ? detail : JSON.stringify(detail));
    }
  },

  // Coach chat (POST /coach). plan_updated is true when the coach edited the
  // stored 7-day plan via its tools, settings_updated when it saved an approved
  // settings change (thresholds, weight, instructions); null only on hard
  // network failure.
  // Pass a signal to let the chat's Stop button abandon a slow turn -- that
  // rejects with an AbortError instead of returning null.
  // includeContext=false answers from general triathlon knowledge: the backend
  // sends no athlete data and offers the coach no plan-editing tools.
  coach: async (
    message: string,
    history: { role: string; text: string }[],
    includeContext: boolean,
    signal?: AbortSignal,
  ): Promise<{ reply: string; plan_updated: boolean; settings_updated?: boolean; suggestions?: string[]; error?: boolean } | null> => {
    try {
      const res = await fetch(BASE + `/coach`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history, include_context: includeContext }),
        signal,
      });
      if (!res.ok) {
        // Surface the backend's reason (e.g. missing key, billing) instead of a
        // generic message, so the chat tells the user what to fix.
        try {
          const detail = (await res.json())?.detail;
          return detail
            ? { reply: `Coach unavailable — ${detail}`, plan_updated: false, error: true }
            : null;
        } catch {
          return null;
        }
      }
      const data = await res.json();
      if (!data.reply) return null;
      return {
        reply: data.reply,
        plan_updated: !!data.plan_updated,
        settings_updated: !!data.settings_updated,
        // The follow-up questions for the chat chips; none from an older backend.
        suggestions: Array.isArray(data.suggestions) ? data.suggestions.filter((q: unknown) => typeof q === "string") : [],
      };
    } catch (e) {
      if (isAbortError(e)) throw e;
      return null;
    }
  },
};
