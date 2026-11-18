// Pure aggregation of API rows into the dashboard view-model. No React here.

import { DISCIPLINE_COLOR, HR_ZONE_COLOR, C } from "./theme";
import { hm, km, monthDay, pace, round } from "./format";
import type { LoadRow, Plan, PlanSession, RaceEvent, RecoveryRow, SeasonPhase, SeasonPlan, StructureSegment, Vo2maxRow, WorkoutRow } from "./types";

const DAY = 86400000;
const ORDER = ["swim", "bike", "run", "strength"] as const;

function daysAgo(iso: string): number {
  const t = new Date(iso).getTime();
  return (Date.now() - t) / DAY;
}
const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN);
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

// Local "YYYY-MM-DD" (workout timestamps and plan dates are compared in local
// time). Shared with the week panels and the Training Days grid.
export function localISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// How long a finished event stays in the events row (faded) before it drops out.
// Display only: the event stays stored, because post-race recovery reads 21 days
// back (RECENT_RACE_DAYS) and the Training Days grid frames race days 60 back.
export const PAST_EVENT_VISIBLE_DAYS = 14;
// Until then it is drawn faded, in the events row and the Manage events list.
export const PAST_EVENT_OPACITY = 0.45;
// An upcoming event the athlete is not racing is drawn faded the same way.
export const NOT_RACING_OPACITY = 0.5;
// The trend charts' window (2026-11-08, the athlete's call; it was 4 weeks):
// Training Load, Fatigue Zones, Weekly Volume, TSS by Week, Run Pace Trend
// and VO2max. The workouts fetch (60 days) covers 8 calendar weeks.
export const TREND_WEEKS = 8;
export const TREND_DAYS = TREND_WEEKS * 7;

// False only when the athlete turned Participating off. A missing field counts
// as participating, like the backend's default for events saved before it existed.
export function isParticipating(e: Pick<RaceEvent, "participating">): boolean {
  return e.participating !== false;
}

// A well-formed "YYYY-MM-DD", or null. An unset or malformed date is not a date
// at all -- a plain string comparison would treat "" as before every cutoff.
function eventDate(raceDate: string): string | null {
  const [y, m, d] = (raceDate || "").split("-").map(Number);
  return y && m && d ? localISO(new Date(y, m - 1, d)) : null;
}

// Race day itself is not past, matching active_event in the backend.
export function isPastEvent(e: Pick<RaceEvent, "race_date">, today = new Date()): boolean {
  const day = eventDate(e.race_date);
  return day != null && day < localISO(today);
}

// The events the events row lists: everything except those finished more than
// PAST_EVENT_VISIBLE_DAYS ago. Date-less events stay, so they can be fixed.
export function visibleEvents(events: RaceEvent[], today = new Date()): RaceEvent[] {
  const cutoff = localISO(new Date(today.getFullYear(), today.getMonth(), today.getDate() - PAST_EVENT_VISIBLE_DAYS));
  return events.filter((e) => {
    const day = eventDate(e.race_date);
    return day == null || day >= cutoff;
  });
}

// Buckets completed workouts by local calendar date, dropping a stale unsplit
// multisport parent when its later split legs (_bike/_run/_swim/_leg) are also
// stored (see split_bricks in app/garmin/parser.py).
//
// Keys on the stored `date` field, never on time.date(): the point's time is the
// true UTC instant, so an evening workout's UTC date is the wrong calendar day.
export function groupWorkoutsByDate(workouts: WorkoutRow[]): Map<string, WorkoutRow[]> {
  const ids = new Set(workouts.map((w) => w.activity_id));
  const hasSplitLegs = (id: string) =>
    ["bike", "run", "swim", "leg"].some((s) => ids.has(`${id}_${s}`));
  const map = new Map<string, WorkoutRow[]>();
  for (const w of workouts) {
    if (hasSplitLegs(w.activity_id)) continue;
    const date = w.date || localISO(new Date(w.time));
    (map.get(date) ?? map.set(date, []).get(date)!).push(w);
  }
  for (const rows of map.values()) rows.sort((a, b) => a.time.localeCompare(b.time));
  return map;
}

// A session moved to another plan day, as move_session in app/plan_editor.py does
// it (end of that day, both days marked as requested). For showing a dragged card
// in its new day before the backend has answered.
export function movePlanSession(plan: Plan, fromDate: string, index: number, toDate: string): Plan {
  const moved = plan.days.find((d) => d.date === fromDate)?.sessions?.[index];
  if (!moved) return plan;
  return {
    ...plan,
    days: plan.days.map((d) => {
      if (d.date === fromDate) {
        const sessions = d.sessions.filter((_, i) => i !== index);
        return { ...d, sessions, is_rest_day: sessions.length === 0, athlete_requested: true };
      }
      if (d.date === toDate) {
        return { ...d, sessions: [...(d.sessions ?? []), moved], is_rest_day: false, athlete_requested: true };
      }
      return d;
    }),
  };
}

// The eight KPI tiles. Numbers stay numbers so the tiles can draw them on a
// scale; the subs are the ready-made comparison lines under each one.
export interface KpiData {
  volumeH: number;
  volumeSub: string;
  ctl: number;
  ctlSub: string;
  atl: number;
  atlSub: string;
  tsb: number;
  tsbSub: string;
  tss: number;
  tssAvg: number;
  tssSub: string;
  /** sum of `days`, so the number and the day boxes always agree */
  sessions: number;
  /** last 7 calendar days, oldest first, today last: sessions logged that day */
  days: number[];
  /** weekday initials for those 7 days, same order */
  dayLetters: string[];
  /** NaN when nothing in the last 7 days has an IF */
  intensity: number;
  intensitySub: string;
  distanceKm: number;
  distanceSub: string;
}
export interface DisciplineCard {
  name: string;
  color: string;
  distLabel: string;
  timeLabel: string;
  tss: string;
  m1l: string;
  m1v: string;
  m2l: string;
  m2v: string;
}

export interface ZoneBar {
  label: string;
  pct: number;
  color: string;
}

export interface ViewModel {
  hasLoad: boolean;
  loadChart: { ctl: number[]; atl: number[]; tsb: number[]; dates: string[]; xLabels: { i: number; label: string }[] };
  current: { ctl: number; atl: number; tsb: number };
  kpis: KpiData;
  weeklyVolume: { cats: string[]; series: { name: string; color: string; values: number[] }[] };
  weeklyTss: { cats: string[]; series: { name: string; color: string; values: number[] }[] };
  hrZones: ZoneBar[] | null;
  paceTrend: { values: number[]; weekTips: string[]; xLabels: { i: number; label: string }[] } | null;
  vo2max: Vo2maxTrend | null;
  recovery: RecoveryTrend;
  weeklySleep: WeeklySleep;
}

// Adds a workout's time in each HR zone to the running totals. zones_time_sec is
// a JSON string; a row without it, or with a malformed one, adds nothing.
function addZoneSeconds(totals: Record<string, number>, raw: WorkoutRow["zones_time_sec"]) {
  if (!raw) return;
  let zones: Record<string, unknown>;
  try {
    zones = typeof raw === "string" ? JSON.parse(raw) : (raw as Record<string, unknown>);
  } catch {
    return;
  }
  for (const [z, sec] of Object.entries(zones)) {
    totals[z] = (totals[z] || 0) + (Number(sec) || 0);
  }
}

function hrZones(workouts: WorkoutRow[], withinDays = 7): ZoneBar[] | null {
  const totals: Record<string, number> = {};
  for (const w of workouts) {
    if (daysAgo(w.time) > withinDays) continue;
    addZoneSeconds(totals, w.zones_time_sec);
  }
  const grand = sum(Object.values(totals));
  if (grand <= 0) return null;
  return Object.keys(totals)
    .sort()
    .map((z) => ({
      label: z,
      pct: Math.round((totals[z] / grand) * 100),
      color: HR_ZONE_COLOR[z] ?? C.muted,
    }));
}

function loadChart(load: LoadRow[]) {
  const rows = [...load].sort((a, b) => a.time.localeCompare(b.time));
  const ctl = rows.map((r) => r.ctl ?? 0);
  const atl = rows.map((r) => r.atl ?? 0);
  const tsb = rows.map((r) => r.tsb ?? 0);
  const n = rows.length;
  const xLabels =
    n > 1
      ? [
          { i: 0, label: `${Math.round(n / 7)} wks ago` },
          { i: Math.floor(n / 3), label: "" },
          { i: Math.floor((2 * n) / 3), label: "" },
          { i: n - 1, label: "now" },
        ]
      : [];
  const last = rows[n - 1] ?? {};
  const dates = rows.map((r) => monthDay(r.time));
  // Trend of the load curves themselves, for the CTL/ATL tiles. Short windows
  // clamp to the oldest row available rather than reporting a bogus jump.
  const ctlThen = rows[Math.max(0, n - 29)]?.ctl ?? 0;
  const atlThen = rows[Math.max(0, n - 8)]?.atl ?? 0;
  return {
    ctl,
    atl,
    tsb,
    dates,
    xLabels,
    current: { ctl: last.ctl ?? 0, atl: last.atl ?? 0, tsb: last.tsb ?? 0 },
    deltas: { ctl28: (last.ctl ?? 0) - ctlThen, atl7: (last.atl ?? 0) - atlThen },
  };
}

interface Agg {
  count: number;
  distance: number;
  duration: number;
  tss: number;
  power: number[];
  ifs: number[];
  paceKm: number[];
  pace100: number[];
  hr: number[];
}

function aggregateByDiscipline(workouts: WorkoutRow[], withinDays: number): Map<string, Agg> {
  const m = new Map<string, Agg>();
  for (const w of workouts) {
    if (daysAgo(w.time) > withinDays) continue;
    const k = w.discipline || "other";
    const a =
      m.get(k) ??
      ({ count: 0, distance: 0, duration: 0, tss: 0, power: [], ifs: [], paceKm: [], pace100: [], hr: [] } as Agg);
    a.count += 1;
    a.distance += w.distance_m || 0;
    a.duration += w.duration_sec || 0;
    a.tss += w.tss || 0;
    if (w.normalized_power || w.avg_power) a.power.push((w.normalized_power || w.avg_power)!);
    if (w.intensity_factor) a.ifs.push(w.intensity_factor);
    if (w.avg_pace_sec_per_km) a.paceKm.push(w.avg_pace_sec_per_km);
    if (w.avg_pace_sec_per_100m) a.pace100.push(w.avg_pace_sec_per_100m);
    if (w.avg_hr) a.hr.push(w.avg_hr);
    m.set(k, a);
  }
  return m;
}

export function disciplineCards(workouts: WorkoutRow[], withinDays = 28): DisciplineCard[] {
  const agg = aggregateByDiscipline(workouts, withinDays);
  return ORDER.map((name) => {
    const a = agg.get(name);
    const color = DISCIPLINE_COLOR[name];
    if (!a) {
      return { name, color, distLabel: "—", timeLabel: "0:00", tss: "0", m1l: "—", m1v: "—", m2l: "—", m2v: "—" };
    }
    const base = { name, color, timeLabel: hm(a.duration), tss: round(a.tss) };
    if (name === "bike")
      return {
        ...base,
        distLabel: `${km(a.distance)} km`,
        m1l: "Norm. Power",
        m1v: isNaN(avg(a.power)) ? "—" : `${Math.round(avg(a.power))} W`,
        m2l: "Int. Factor",
        m2v: isNaN(avg(a.ifs)) ? "—" : avg(a.ifs).toFixed(2),
      };
    if (name === "run")
      return {
        ...base,
        distLabel: `${km(a.distance)} km`,
        m1l: "Avg Pace",
        m1v: isNaN(avg(a.paceKm)) ? "—" : `${pace(avg(a.paceKm))}`,
        m2l: "Sessions",
        m2v: String(a.count),
      };
    if (name === "swim")
      return {
        ...base,
        distLabel: `${km(a.distance)} km`,
        m1l: "Avg /100m",
        m1v: isNaN(avg(a.pace100)) ? "—" : pace(avg(a.pace100)),
        m2l: "Sessions",
        m2v: String(a.count),
      };
    // strength
    return {
      ...base,
      distLabel: `${a.count} sess`,
      m1l: "Sessions",
      m1v: String(a.count),
      m2l: "Avg HR",
      m2v: isNaN(avg(a.hr)) ? "—" : `${Math.round(avg(a.hr))}`,
    };
  });
}

// ISO-8601 week number (1-53) of a date, for chart axis labels.
function isoWeek(d: Date): number {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7; // Mon=1 .. Sun=7
  date.setUTCDate(date.getUTCDate() + 4 - day); // nearest Thursday decides the week
  const yearStart = Date.UTC(date.getUTCFullYear(), 0, 1);
  return Math.ceil(((date.getTime() - yearStart) / DAY + 1) / 7);
}

// Calendar-week bucketing for the weekly charts, Monday-aligned to match the
// ISO W## axis labels (index 0 = the current week, 1 = last week, ...).
function weekIndexer(now = new Date()) {
  const dowMon = (now.getDay() + 6) % 7; // days since this week's Monday
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return {
    index: (iso: string) => {
      const d = new Date(iso);
      const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
      const wholeDaysAgo = Math.round((startToday - dayStart) / DAY);
      return Math.floor((wholeDaysAgo - dowMon + 6) / 7);
    },
    // Week number of bucket `wi`, via that week's Monday.
    weekNo: (wi: number) => isoWeek(new Date(startToday - (dowMon + wi * 7) * DAY)),
  };
}

// Per-discipline weekly totals of `valueOf(workout)` (hours, TSS, ...).
function weekBuckets(workouts: WorkoutRow[], weeks: number, valueOf: (w: WorkoutRow) => number) {
  // pos 0 = oldest of the window, pos weeks-1 = current week
  const wk = weekIndexer();
  const byPos: Record<string, number[]> = {};
  for (const d of ORDER) byPos[d] = new Array(weeks).fill(0);
  for (const w of workouts) {
    const wi = wk.index(w.time);
    if (wi < 0 || wi >= weeks) continue;
    const pos = weeks - 1 - wi;
    const d = w.discipline || "other";
    if (byPos[d]) byPos[d][pos] += valueOf(w);
  }
  const cats = Array.from({ length: weeks }, (_, i) => `W${wk.weekNo(weeks - 1 - i)}`);
  const series = ORDER.map((name) => ({
    name: name[0].toUpperCase() + name.slice(1),
    color: DISCIPLINE_COLOR[name],
    values: byPos[name].map((v) => +v.toFixed(2)),
  }));
  return { cats, series };
}

function paceTrend(workouts: WorkoutRow[], weeks = TREND_WEEKS) {
  const wk = weekIndexer();
  const byPos: number[][] = Array.from({ length: weeks }, () => []);
  for (const w of workouts) {
    if (w.discipline !== "run" || !w.avg_pace_sec_per_km) continue;
    const wi = wk.index(w.time);
    if (wi < 0 || wi >= weeks) continue;
    byPos[weeks - 1 - wi].push(w.avg_pace_sec_per_km);
  }
  const values = byPos.map((xs) => (xs.length ? Math.round(avg(xs)) : NaN));
  if (values.every((v) => isNaN(v))) return null;
  // fill gaps by carrying neighbours so the line stays continuous
  for (let i = 0; i < values.length; i++)
    if (isNaN(values[i])) values[i] = values.find((v) => !isNaN(v)) ?? 0;
  const weekNo = (i: number) => wk.weekNo(weeks - 1 - i);
  const xLabels = values.map((_, i) => ({ i, label: `W${weekNo(i)}` }));
  const weekTips = values.map((_, i) => (i === values.length - 1 ? "This week" : `Week ${weekNo(i)}`));
  return { values, weekTips, xLabels };
}

const VO2MAX_ORDER = ["run", "bike"] as const;
const VO2MAX_LABEL: Record<string, string> = { run: "Running", bike: "Cycling" };

export interface Vo2maxSeries {
  discipline: string;
  label: string;
  color: string;
  values: number[]; // dense: one entry per day of the window
}
export interface Vo2maxTrend {
  series: Vo2maxSeries[];
  dayTips: string[];
  xLabels: { i: number; label: string }[];
}

// Daily VO2max as one dense series per sport, one point per day of the window.
//
// Garmin only revises VO2max after a qualifying activity, so most days carry no
// reading. Each series is FORWARD-FILLED (the estimate stands until Garmin
// changes it -- that is what the number means) and the leading gap is
// BACK-FILLED from the first reading, the same "carry a neighbour so the line
// stays continuous" idiom paceTrend uses. The arrays must be dense and numeric:
// LineChart auto-scales its y-axis with Math.min/Math.max over
// series.flatMap(s => s.values), so a single NaN poisons the axis for every
// series, not just its own.
//
// A sport with ZERO readings is omitted entirely rather than zero-filled:
// cycling VO2max needs a power meter and is legitimately absent, and a flat zero
// line would crush the real series into the top of the chart.
function vo2maxTrend(rows: Vo2maxRow[], days = TREND_DAYS): Vo2maxTrend | null {
  const now = new Date();
  const dayDates: Date[] = [];
  for (let i = days - 1; i >= 0; i--) {
    dayDates.push(new Date(now.getFullYear(), now.getMonth(), now.getDate() - i));
  }
  const idxOf = new Map(dayDates.map((d, i) => [localISO(d), i]));

  const series: Vo2maxSeries[] = [];
  for (const disc of VO2MAX_ORDER) {
    const values: number[] = new Array(days).fill(NaN);
    let any = false;
    for (const r of rows) {
      if (r.discipline !== disc || typeof r.vo2max !== "number") continue;
      // Midnight-UTC point: the ISO date prefix IS the calendar day. Parsing it
      // as a Date would shift the day in any timezone west of UTC.
      const i = idxOf.get(r.time.slice(0, 10));
      if (i === undefined) continue;
      values[i] = r.vo2max;
      any = true;
    }
    if (!any) continue;
    for (let i = 1; i < days; i++) if (isNaN(values[i])) values[i] = values[i - 1];
    const first = values.find((v) => !isNaN(v)) as number;
    for (let i = 0; i < days; i++) if (isNaN(values[i])) values[i] = first;
    series.push({
      discipline: disc,
      label: VO2MAX_LABEL[disc],
      color: DISCIPLINE_COLOR[disc],
      values: values.map((v) => +v.toFixed(1)),
    });
  }
  if (!series.length) return null;

  // A label roughly every 7 days, with the right edge always reading "today".
  const xLabels: { i: number; label: string }[] = [];
  for (let i = 0; i < days - 1; i += 7) xLabels.push({ i, label: monthDay(dayDates[i]) });
  xLabels.push({ i: days - 1, label: "today" });

  return { series, dayTips: dayDates.map((d) => monthDay(d)), xLabels };
}

export interface RecoverySeries {
  values: (number | null)[]; // one entry per day; null = no reading that day
  avg: number; // rounded mean of the days with a reading
  latest: number; // the most recent reading
}
export interface RecoveryTrend {
  hrv: RecoverySeries | null;
  restingHr: RecoverySeries | null;
  dayTips: string[];
  xLabels: { i: number; label: string }[];
}

// Daily HRV and resting HR over the window, for the recovery-trend cards.
//
// Deliberately the OPPOSITE of vo2maxTrend: gaps stay null and are drawn as
// gaps. A VO2max estimate stands until Garmin revises it, so carrying it
// forward is honest; an HRV or resting HR reading belongs to one night, and a
// night the watch was not worn has no value. The average ignores the gaps.
// A series with no readings at all is null, and its card shows the empty state.
function recoveryTrend(rows: RecoveryRow[], days = 28): RecoveryTrend {
  const now = new Date();
  const dayDates: Date[] = [];
  for (let i = days - 1; i >= 0; i--) {
    dayDates.push(new Date(now.getFullYear(), now.getMonth(), now.getDate() - i));
  }
  const idxOf = new Map(dayDates.map((d, i) => [localISO(d), i]));

  const build = (pick: (r: RecoveryRow) => number | null | undefined): RecoverySeries | null => {
    const values: (number | null)[] = new Array(days).fill(null);
    for (const r of rows) {
      const v = pick(r);
      if (typeof v !== "number" || !Number.isFinite(v)) continue;
      // Midnight-UTC point: the ISO date prefix IS the calendar day.
      const i = idxOf.get(r.time.slice(0, 10));
      if (i !== undefined) values[i] = v;
    }
    const xs = values.filter((v): v is number => v != null);
    if (!xs.length) return null;
    return { values, avg: Math.round(avg(xs)), latest: xs[xs.length - 1] };
  };

  // A date at the first day, every seventh day after it, and the last day.
  const xLabels: { i: number; label: string }[] = [];
  for (let i = 0; i < days - 1; i += 7) xLabels.push({ i, label: monthDay(dayDates[i]) });
  xLabels.push({ i: days - 1, label: monthDay(dayDates[days - 1]) });

  return {
    hrv: build((r) => r.hrv),
    restingHr: build((r) => r.resting_hr),
    dayTips: dayDates.map((d) => monthDay(d)),
    xLabels,
  };
}

export interface WeeklySleep {
  cats: string[]; // W## labels, oldest first, the current week last
  hours: number[]; // average sleep per night with a reading; 0 = none that week
  nights: number[]; // nights with a reading in each week
  hasData: boolean;
}

// Average sleep per night for each Monday-start week of the trend window, the
// same weeks and W## labels as the other weekly charts. Nights without a
// reading are left out of the average (a night the watch was off is not a
// night of no sleep), and the current week averages only its nights so far.
function weeklySleep(rows: RecoveryRow[], weeks = TREND_WEEKS): WeeklySleep {
  const wk = weekIndexer();
  const sums = new Array(weeks).fill(0);
  const nights = new Array(weeks).fill(0);
  for (const r of rows) {
    const v = r.sleep_sec;
    if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) continue;
    // Midnight-UTC point: read the calendar day off the string and parse it as
    // local midnight, or new Date() would shift it a day west of UTC.
    const wi = wk.index(`${r.time.slice(0, 10)}T00:00:00`);
    if (wi < 0 || wi >= weeks) continue;
    const pos = weeks - 1 - wi;
    sums[pos] += v / 3600;
    nights[pos] += 1;
  }
  const cats = Array.from({ length: weeks }, (_, i) => `W${wk.weekNo(weeks - 1 - i)}`);
  const hours = sums.map((s, i) => (nights[i] ? +(s / nights[i]).toFixed(2) : 0));
  return { cats, hours, nights, hasData: nights.some((n) => n > 0) };
}

// Rolling 7-day blocks across the fetched window: block 0 is the last 7 days,
// block 3 the oldest. The tiles' "vs 4-wk avg" deltas compare block 0 against
// the mean of all four.
function weekBlocks(workouts: WorkoutRow[], valueOf: (w: WorkoutRow) => number, blocks = 4): number[] {
  const out: number[] = new Array(blocks).fill(0);
  for (const w of workouts) {
    const b = Math.floor(daysAgo(w.time) / 7);
    if (b >= 0 && b < blocks) out[b] += valueOf(w);
  }
  return out;
}

// Mean intensity factor per 7-day block. IF is an average, not a total, and a
// block with no rated workout is NaN rather than 0 so it can be left out.
function ifBlocks(workouts: WorkoutRow[], blocks = 4): number[] {
  const buckets: number[][] = Array.from({ length: blocks }, () => []);
  for (const w of workouts) {
    if (!w.intensity_factor) continue;
    const b = Math.floor(daysAgo(w.time) / 7);
    if (b >= 0 && b < blocks) buckets[b].push(w.intensity_factor);
  }
  return buckets.map((xs) => (xs.length ? avg(xs) : NaN));
}

const signed = (v: number, digits = 0) => (v >= 0 ? "+" : "-") + Math.abs(v).toFixed(digits);

function buildKpis(
  workouts: WorkoutRow[],
  current: { ctl: number; atl: number; tsb: number },
  deltas: { ctl28: number; atl7: number },
): KpiData {
  const vol = weekBlocks(workouts, (w) => (w.duration_sec || 0) / 3600);
  const tss = weekBlocks(workouts, (w) => w.tss || 0);
  const dist = weekBlocks(workouts, (w) => (w.distance_m || 0) / 1000);
  const ifs = ifBlocks(workouts);
  const rated = ifs.filter((v) => !isNaN(v));

  // The Sessions tile: the 7 calendar days on the stored `date` field, so an
  // evening workout lands on the athlete's day, and the count is taken from the
  // same days as the boxes. A rolling 168-hour window (weekBlocks) also caught
  // the evening of the day before the first box, and counted stale unsplit
  // brick parents that groupWorkoutsByDate drops. A brick's bike and run legs
  // still count as two sessions.
  const byDate = groupWorkoutsByDate(workouts);
  const days: number[] = [];
  const dayLetters: string[] = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    days.push(byDate.get(localISO(d))?.length ?? 0);
    dayLetters.push("SMTWTFS"[d.getDay()]);
  }

  return {
    volumeH: +vol[0].toFixed(1),
    volumeSub: `${signed(vol[0] - avg(vol), 1)}h vs 4-wk avg`,
    ctl: Math.round(current.ctl),
    ctlSub: `${signed(deltas.ctl28)} / 28d`,
    atl: Math.round(current.atl),
    atlSub: `${signed(deltas.atl7)} / 7d`,
    tsb: Math.round(current.tsb),
    tsbSub: current.tsb < 0 ? "loading" : "fresh",
    tss: Math.round(tss[0]),
    tssAvg: Math.round(avg(tss)),
    tssSub: `${signed(tss[0] - avg(tss))} vs avg`,
    sessions: sum(days),
    days,
    dayLetters,
    intensity: ifs[0],
    intensitySub: isNaN(ifs[0]) ? "no rated workouts" : `${signed(ifs[0] - avg(rated), 2)} vs 4-wk avg`,
    distanceKm: +dist[0].toFixed(1),
    distanceSub: `${signed(dist[0] - avg(dist), 1)} km vs 4-wk avg`,
  };
}

export function buildViewModel(
  load: LoadRow[],
  workouts: WorkoutRow[],
  vo2max: Vo2maxRow[] = [],
  recovery: RecoveryRow[] = [],
): ViewModel {
  const lc = loadChart(load);
  return {
    hasLoad: load.length > 0,
    loadChart: { ctl: lc.ctl, atl: lc.atl, tsb: lc.tsb, dates: lc.dates, xLabels: lc.xLabels },
    current: lc.current,
    kpis: buildKpis(workouts, lc.current, lc.deltas),
    weeklyVolume: weekBuckets(workouts, TREND_WEEKS, (w) => (w.duration_sec || 0) / 3600),
    weeklyTss: weekBuckets(workouts, TREND_WEEKS, (w) => w.tss || 0),
    hrZones: hrZones(workouts),
    paceTrend: paceTrend(workouts),
    vo2max: vo2maxTrend(vo2max),
    recovery: recoveryTrend(recovery),
    weeklySleep: weeklySleep(recovery),
  };
}

// ---- Sync summary -----------------------------------------------------------
// The modal after a sync: the last 7 days over the next 7, computed from the
// workouts and the plan's sessions, never from Claude's prose.

const SUMMARY_DAYS = 7;
// The tallest bar across both rows, in px. Near the design's 0.72 px per TSS when
// the largest day is about 116 TSS.
const BAR_MAX_PX = 84;
const ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5"];

export interface SummarySlice {
  label: string;
  value: string;
  pct: number; // raw share, 0-100
  color: string;
}
export interface SummaryDay {
  iso: string;
  weekday: string;
  dayOfMonth: number;
  isToday: boolean;
  open: boolean; // the plan does not reach this day
  hard: boolean;
  sessions: number;
  tss: number;
  barPx: number;
  parts: { discipline: string; tss: number; color: string }[]; // swim first
}
// One band under the bars. phase null: days outside the season plan.
export interface SummaryBand {
  span: number;
  label: string;
  phase: SeasonPhase | null;
}
export interface SummaryRow {
  title: string;
  range: string;
  status: string | null;
  tss: number;
  tssNote: string;
  facts: { k: string; v: string }[];
  days: SummaryDay[];
  bands: SummaryBand[];
  load: SummarySlice[];
  zones: SummarySlice[];
  easyPct: number;
}
export interface SyncSummary {
  last: SummaryRow;
  next: SummaryRow;
  note: string;
}

const isoOffset = (today: Date, days: number) =>
  localISO(new Date(today.getFullYear(), today.getMonth(), today.getDate() + days));

// A calendar day as a local Date. Only for "YYYY-MM-DD" strings: new Date() on
// one would read it as midnight UTC and shift it west of UTC.
function localDay(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

// "Sat 24 Oct"
function dayLabel(iso: string): string {
  const d = localDay(iso);
  return `${d.toLocaleDateString("en-US", { weekday: "short" })} ${d.getDate()} ${d.toLocaleDateString("en-US", { month: "short" })}`;
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// The highest zone a label names ("Z1-Z2" is Z2), or null. Anchored like
// zoneColor: a label like "65-75% FTP" must not read as zone 5.
function highestZone(label: string | null | undefined): string | null {
  if (!label) return null;
  const digits: number[] = [];
  for (const m of label.matchAll(/z(?:one)?\s*-?\s*([1-5])(?:\s*-\s*(?:z(?:one)?\s*)?([1-5])(?![\d%x]))?/gi)) {
    digits.push(Number(m[1]));
    if (m[2]) digits.push(Number(m[2]));
  }
  const bare = label.match(/^\s*([1-5])\s*$/)?.[1];
  if (bare) digits.push(Number(bare));
  return digits.length ? `Z${Math.max(...digits)}` : null;
}

// Planned minutes per zone: the segments, or the session's zones_target when no
// segment names a zone. Segment minutes can differ from duration_min; only the
// shares are drawn.
function addPlannedZones(totals: Record<string, number>, s: PlanSession) {
  let found = false;
  for (const seg of s.structure ?? []) {
    const z = highestZone(seg.zone);
    if (!z || !seg.duration_min) continue;
    totals[z] = (totals[z] || 0) + seg.duration_min;
    found = true;
  }
  if (found) return;
  for (const [label, share] of Object.entries(s.zones_target ?? {})) {
    const z = highestZone(label);
    const pct = parseFloat(String(share));
    if (z && pct > 0) totals[z] = (totals[z] || 0) + (pct / 100) * (s.duration_min || 0);
  }
}

const isHard = (s: PlanSession) =>
  (s.structure ?? []).some((seg) => {
    const z = highestZone(seg.zone);
    return z === "Z4" || z === "Z5";
  });

function zoneSlices(totals: Record<string, number>): { zones: SummarySlice[]; easyPct: number } {
  const grand = sum(ZONES.map((z) => totals[z] || 0));
  if (grand <= 0) return { zones: [], easyPct: 0 };
  const zones = ZONES.filter((z) => totals[z] > 0).map((z) => {
    const pct = (totals[z] / grand) * 100;
    return { label: z, value: `${Math.round(pct)}%`, pct, color: HR_ZONE_COLOR[z] };
  });
  // From the raw shares: adding the rounded ones can be a point off.
  return { zones, easyPct: Math.round((((totals.Z1 || 0) + (totals.Z2 || 0)) / grand) * 100) };
}

function loadSlices(bySport: Record<string, number>): SummarySlice[] {
  const grand = sum(Object.values(bySport));
  if (grand <= 0) return [];
  return ORDER.filter((d) => bySport[d] > 0)
    .sort((a, b) => bySport[b] - bySport[a])
    .map((d) => ({
      label: capitalize(d),
      value: String(Math.round(bySport[d])),
      pct: (bySport[d] / grand) * 100,
      color: DISCIPLINE_COLOR[d],
    }));
}

function summaryDay(iso: string, todayIso: string, fields: Partial<SummaryDay>): SummaryDay {
  const d = localDay(iso);
  return {
    iso,
    weekday: d.toLocaleDateString("en-US", { weekday: "short" }),
    dayOfMonth: d.getDate(),
    isToday: iso === todayIso,
    open: false,
    hard: false,
    sessions: 0,
    tss: 0,
    barPx: 0,
    parts: [],
    ...fields,
  };
}

// One part per sport, swim first, so a two-sport day stacks in a fixed order.
function dayParts(bySport: Record<string, number>): SummaryDay["parts"] {
  return ORDER.filter((d) => bySport[d] > 0).map((d) => ({ discipline: d, tss: bySport[d], color: DISCIPLINE_COLOR[d] }));
}

// One band per season week, spanning its columns and never merged across weeks.
function phaseBands(isos: string[], season: SeasonPlan | null): SummaryBand[] {
  if (!season || season.status === "cancelled" || !season.weeks.length) {
    return [{ span: isos.length, label: "No season plan yet.", phase: null }];
  }
  const { weeks } = season;
  const weekOf = (iso: string) =>
    weeks.findIndex((w) => w.week_start <= iso && iso <= isoOffset(localDay(w.week_start), 6));
  const groups: { index: number; span: number }[] = [];
  for (const iso of isos) {
    const index = weekOf(iso);
    const prev = groups[groups.length - 1];
    if (prev && prev.index === index) prev.span += 1;
    else groups.push({ index, span: 1 });
  }
  if (groups.length === 1 && groups[0].index < 0) {
    const start = weeks[0].week_start;
    const label =
      isos[isos.length - 1] < start
        ? `No season plan for these days. It starts ${dayLabel(start)}.`
        : "No season plan for these days.";
    return [{ span: isos.length, label, phase: null }];
  }
  return groups.map(({ index, span }) => {
    if (index < 0) return { span, label: "No plan", phase: null };
    const w = weeks[index];
    const phase = capitalize(w.phase);
    const label =
      span < 3 ? phase : `${phase} · week ${index + 1} of ${weeks.length} · ${w.kind} · target ${Math.round(w.target_tss)} TSS`;
    return { span, label, phase: w.phase };
  });
}

// "15 days to the 15K", or null with no upcoming event the athlete is racing.
function eventCountdown(event: RaceEvent | null, today: Date): string | null {
  if (!event || !isParticipating(event) || isPastEvent(event, today)) return null;
  const day = eventDate(event.race_date);
  if (!day) return null;
  const n = Math.round((localDay(day).getTime() - localDay(localISO(today)).getTime()) / DAY);
  if (n === 0) return `Race day: the ${event.name}`;
  return `${n} ${n === 1 ? "day" : "days"} to the ${event.name}`;
}

// The plan's short note. A plan cached before headline existed falls back to the
// first two sentences of coach_notes; a period inside "5.5 km" does not end one.
function summaryNote(plan: Plan): string {
  const headline = plan.headline?.trim();
  if (headline) return headline;
  const notes = (plan.coach_notes ?? "").replace(/[*_`#>]/g, "").replace(/\s+/g, " ").trim();
  return notes.split(/(?<=[.!?])\s+/).slice(0, 2).join(" ");
}

function lastRow(workouts: WorkoutRow[], season: SeasonPlan | null, tsb: number, today: Date, todayIso: string): SummaryRow {
  const counted = new Set<string>(ORDER);
  const byDate = groupWorkoutsByDate(workouts);
  const isos = Array.from({ length: SUMMARY_DAYS }, (_, i) => isoOffset(today, i - (SUMMARY_DAYS - 1)));
  const load: Record<string, number> = {};
  const zoneSec: Record<string, number> = {};
  let seconds = 0;
  const days = isos.map((iso) => {
    const rows = (byDate.get(iso) ?? []).filter((w) => counted.has(w.discipline));
    const bySport: Record<string, number> = {};
    for (const w of rows) {
      bySport[w.discipline] = (bySport[w.discipline] || 0) + (w.tss || 0);
      load[w.discipline] = (load[w.discipline] || 0) + (w.tss || 0);
      seconds += w.duration_sec || 0;
      addZoneSeconds(zoneSec, w.zones_time_sec);
    }
    return summaryDay(iso, todayIso, { sessions: rows.length, tss: sum(rows.map((w) => w.tss || 0)), parts: dayParts(bySport) });
  });
  return {
    title: "Last 7 Days",
    range: `${dayLabel(isos[0])} to ${dayLabel(isos[isos.length - 1])}`,
    status: "Completed",
    tss: Math.round(sum(days.map((d) => d.tss))),
    tssNote: "logged",
    facts: [
      { k: "Time", v: `${(seconds / 3600).toFixed(1)} h` },
      { k: "Sessions", v: String(sum(days.map((d) => d.sessions))) },
      { k: "Form now", v: tsb.toFixed(1) },
    ],
    days,
    bands: phaseBands(isos, season),
    load: loadSlices(load),
    ...zoneSlices(zoneSec),
  };
}

function nextRow(plan: Plan, season: SeasonPlan | null, event: RaceEvent | null, today: Date, todayIso: string): SummaryRow {
  const isos = Array.from({ length: SUMMARY_DAYS }, (_, i) => isoOffset(today, i + 1));
  const byDate = new Map((plan.days ?? []).map((d) => [d.date, d]));
  const load: Record<string, number> = {};
  const zoneMin: Record<string, number> = {};
  let minutes = 0;
  let restDays = 0;
  const days = isos.map((iso) => {
    const planned = byDate.get(iso);
    if (!planned) return summaryDay(iso, todayIso, { open: true });
    if (planned.is_rest_day) restDays += 1;
    const sessions = planned.sessions ?? [];
    const bySport: Record<string, number> = {};
    for (const s of sessions) {
      const tss = s.estimated_tss || 0;
      bySport[s.discipline] = (bySport[s.discipline] || 0) + tss;
      load[s.discipline] = (load[s.discipline] || 0) + tss;
      minutes += s.duration_min || 0;
      addPlannedZones(zoneMin, s);
    }
    return summaryDay(iso, todayIso, {
      sessions: sessions.length,
      hard: sessions.some(isHard),
      tss: sum(sessions.map((s) => s.estimated_tss || 0)),
      parts: dayParts(bySport),
    });
  });
  const budget = plan.window_budget_tss;
  return {
    title: "Next 7 Days",
    range: `${dayLabel(isos[0])} to ${dayLabel(isos[isos.length - 1])}`,
    status: eventCountdown(event, today),
    tss: Math.round(sum(days.map((d) => d.tss))),
    tssNote: budget != null ? `planned, of ${Math.round(budget)} budget` : "planned",
    facts: [
      { k: "Time", v: `${(minutes / 60).toFixed(1)} h` },
      { k: "Sessions", v: String(sum(days.map((d) => d.sessions))) },
      { k: "Rest days", v: String(restDays) },
    ],
    days,
    bands: phaseBands(isos, season),
    load: loadSlices(load),
    ...zoneSlices(zoneMin),
  };
}

export function syncSummary(
  workouts: WorkoutRow[],
  plan: Plan,
  season: SeasonPlan | null,
  event: RaceEvent | null,
  tsb: number,
  today = new Date(),
): SyncSummary {
  const todayIso = localISO(today);
  const last = lastRow(workouts, season, tsb, today, todayIso);
  const next = nextRow(plan, season, event, today, todayIso);
  // One y scale across all 14 days, so a column compares straight down.
  const max = Math.max(...[...last.days, ...next.days].map((d) => d.tss));
  for (const d of [...last.days, ...next.days]) {
    d.barPx = max > 0 ? Math.round((BAR_MAX_PX * d.tss) / max) : 0;
  }
  return { last, next, note: summaryNote(plan) };
}

// A planned session's structure as the modal draws it. Claude writes interval
// sets unrolled ("Interval 1", "Recovery 1", "Interval 2", ...); runs of the
// same step pattern fold into one repeat. Display only: the plan JSON, the
// plan editor and the coach-chat attach text keep every segment.
export type StructureItem =
  | { kind: "single"; seg: StructureSegment }
  | {
      kind: "repeat";
      count: number;
      steps: StructureSegment[]; // one rep, rep numbers stripped from the names
      lastRepShort: boolean; // the last rep stops before its final step (no closing recovery)
      totalMin: number;
    };

const MAX_PATTERN = 3;

function repName(name: string | undefined): string {
  const stripped = (name ?? "").replace(/\s*#?\d+$/, "").trim();
  return stripped || (name ?? "");
}

// Notes are left out of the key: Claude writes them on the first rep only.
function stepKey(s: StructureSegment): string {
  return [repName(s.segment).toLowerCase(), s.duration_min ?? "", s.zone ?? "", s.target_power_w ?? ""].join("|");
}

// The repeat of `len` steps starting at `start`, or null when there is none.
// `floor` is the first step not already folded into an earlier repeat.
// A later rep may repeat the first rep's notes or leave them out, but a note of
// its own ("rep 3: sprint the last 30 s") keeps the set unrolled -- folding
// would drop it or pin it on every rep.
function repeatAt(segs: StructureSegment[], keys: string[], start: number, len: number, floor: number) {
  const same = (at: number, n: number) => {
    if (at + n > segs.length) return false;
    for (let k = 0; k < n; k++) if (keys[at + k] !== keys[start + k]) return false;
    return true;
  };
  // A run starts where it really starts: never fold its tail after the whole
  // run was refused, which would re-anchor the pattern on a later rep -- or
  // part-way into one. When the unfolded step before `start` matches this
  // pattern's last step, the run began there: "Interval 1" (notes of its own),
  // Recovery, Interval, ... once folded as Interval 1 then 4 x (Recovery,
  // Interval). Steps already in a repeat are not looked at, so a second set
  // may start with the same recovery the first one ended on.
  if (start > floor && keys[start - 1] === keys[start + len - 1]) return null;
  let count = 1;
  let end = start + len;
  while (same(end, len)) {
    count += 1;
    end += len;
  }
  let lastRepShort = false;
  if (len > 1 && same(end, len - 1)) {
    count += 1;
    end += len - 1;
    lastRepShort = true;
  }
  if (count < 2) return null;

  const steps: StructureSegment[] = [];
  for (let k = 0; k < len; k++) {
    const first = segs[start + k].notes?.trim() ?? "";
    for (let at = start + k + len; at < end; at += len) {
      const n = segs[at].notes?.trim();
      if (n && n !== first) return null;
    }
    steps.push({ ...segs[start + k], segment: repName(segs[start + k].segment), notes: first || undefined });
  }
  const totalMin = Math.round(sum(segs.slice(start, end).map((s) => s.duration_min ?? 0)) * 10) / 10;
  return { count, end, steps, lastRepShort, totalMin };
}

export function groupStructure(segs: StructureSegment[]): StructureItem[] {
  const keys = segs.map(stepKey);
  const items: StructureItem[] = [];
  let i = 0;
  let floor = 0;
  while (i < segs.length) {
    // Widest coverage wins; on a tie the shorter pattern (4 x A, not 2 x (A, A)).
    let best: ReturnType<typeof repeatAt> = null;
    for (let len = 1; len <= MAX_PATTERN; len++) {
      const r = repeatAt(segs, keys, i, len, floor);
      if (r && (!best || r.end > best.end)) best = r;
    }
    if (best) {
      const { count, steps, lastRepShort, totalMin } = best;
      items.push({ kind: "repeat", count, steps, lastRepShort, totalMin });
      i = best.end;
      floor = i;
    } else {
      items.push({ kind: "single", seg: segs[i] });
      i += 1;
    }
  }
  return items;
}

// --- Coach chat suggestions ---------------------------------------------------
// The three chips above the chat composer while the conversation is clean (no
// question asked yet): one about the latest synced workout, one about the next
// planned day, one about load and form. Once the coach has answered, its own
// follow-up questions replace them (CoachChat). Each slot falls back to a fixed
// question when the sync gave it nothing to name.
const SUGGESTION_WORKOUT_DAYS = 3;
const SUGGESTION_DISCIPLINES = ["swim", "bike", "run", "strength"];
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// With "Include context" off no athlete data is sent, so nothing here may name it.
export const GENERAL_SUGGESTIONS = [
  "How should I fuel a long ride?",
  "How do I pace an olympic triathlon?",
  "What does a good taper week look like?",
];

export function syncSuggestions(
  workouts: WorkoutRow[],
  plan: Plan | null,
  tsb: number | null | undefined,
  today: Date = new Date(),
): string[] {
  const todayISO = localISO(today);
  const dayOf = (w: WorkoutRow) => w.date ?? localISO(new Date(w.time));
  const shiftISO = (days: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() + days);
    return localISO(d);
  };

  // 1. The latest workout, when it is recent enough to still be the topic.
  const since = shiftISO(-(SUGGESTION_WORKOUT_DAYS - 1));
  const latest = workouts
    .filter((w) => SUGGESTION_DISCIPLINES.includes(w.discipline) && dayOf(w) >= since && dayOf(w) <= todayISO)
    .sort((a, b) => b.time.localeCompare(a.time))[0];
  let first = "What should I do tomorrow?";
  if (latest) {
    const day = dayOf(latest);
    const when = day === todayISO ? "today" : day === shiftISO(-1) ? "yesterday" : `on ${WEEKDAYS[new Date(`${day}T00:00`).getDay()]}`;
    first = `How did my ${latest.discipline} ${when} go?`;
  }

  // 2. Tomorrow in the plan: its first session, or the empty day.
  const tomorrow = plan?.days?.find((d) => d.date === shiftISO(1));
  let second = "I'm feeling tired, adjust this week?";
  if (tomorrow) {
    const s = tomorrow.sessions?.[0];
    if (!s) second = tomorrow.is_rest_day ? "What should I do on tomorrow's rest day?" : "Why is tomorrow empty?";
    else {
      const name = (s.title || s.discipline).trim();
      second = name.length <= 40 ? `What is the goal of tomorrow's ${name}?` : `What is the goal of tomorrow's ${s.discipline}?`;
    }
  }

  // 3. Load and form. The limits are the Friel zones of the Fatigue Zones chart.
  let third = "Am I on track for my next race?";
  if (plan?.load_reduction) third = "Why is this week lighter?";
  else if (tsb != null && tsb < -30) third = `My form is ${Math.round(tsb)}, should I ease off?`;
  else if (tsb != null && tsb < -10) third = `My form is ${Math.round(tsb)}, is that OK?`;
  else if (tsb != null && tsb > 5) third = "I'm fresh, can I push harder this week?";

  return [first, second, third];
}
