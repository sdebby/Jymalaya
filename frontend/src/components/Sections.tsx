import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { C, DISCIPLINE_COLOR } from "../theme";
import { api } from "../api";
import { DROPPED, useSessionDrag, type SessionRef } from "../useSessionDrag";
import { Card, CardHead, ChartBox, Empty } from "./primitives";
import { LineChart } from "../charts/LineChart";
import { StackedBars } from "../charts/StackedBars";
import { TssVolumeBars } from "../charts/TssVolumeBars";
import { Donut } from "../charts/Donut";
import { BarsLine } from "../charts/BarsLine";
import { buildForecast } from "../forecast";
import { FORM_ZONES, FORM_ZONE_TICKS, formZoneDomain, formZoneOf, formZoneStreak, signedInt } from "../formZones";
import { monthDay, pace } from "../format";
import { disciplineCards, groupWorkoutsByDate, localISO, type RecoverySeries, type ViewModel } from "../derive";
import { HRV_RANGE, RESTING_HR_RANGE, recoveryDomain, type RecoveryRange } from "../recovery";
import { weekdayOf, type AppSettings } from "../settings";
import { SessionModal } from "./SessionModal";
import { WorkoutModal } from "./WorkoutModal";
import { ChatIcon } from "./ChatIcon";
import { KpiGrid } from "./KpiGrid";
import type { CoachAttachment, RaceEvent, Plan, PlanDay, PlanAttachment, PlanSession, WorkoutRow } from "../types";

const WINDOW_OPTIONS = [
  { label: "Last 7 days", days: 7 },
  { label: "Last 14 days", days: 14 },
  { label: "Last 4 weeks", days: 28 },
] as const;

// X labels for the Fatigue Zones chart: a date a week apart, then "now". The
// Training Load chart above it says "8 wks ago" instead; this one is short
// enough to be read against, so the days can be named.
function zoneXLabels(dates: string[]): { i: number; label: string }[] {
  const n = dates.length;
  if (n < 2) return [];
  const out: { i: number; label: string }[] = [];
  for (let i = 0; i < n - 4; i += 7) out.push({ i, label: dates[i] });
  out.push({ i: n - 1, label: "now" });
  return out;
}

// The load block: Training Load (FFF) with the Fatigue Zones chart under it at
// the same width -- same plot margins and the same 56 days, so each day lines up
// vertically -- and, beside them, the KPI tiles with the Load Forecast under
// those. Everything about fitness, fatigue and form, and where the planned week
// takes them, in one place.
export function TrainingLoadAndKpis({ vm, plan }: { vm: ViewModel; plan: Plan | null }) {
  const forecast = useMemo(() => buildForecast(plan, vm.current), [plan, vm.current]);
  const zone = formZoneOf(vm.current.tsb);
  const streak = formZoneStreak(vm.loadChart.tsb);
  const domain = vm.hasLoad ? formZoneDomain(vm.loadChart.tsb) : null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 760px", gap: 10 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
        <Card style={{ height: 246 }}>
          <CardHead title="Training Load — Fitness · Fatigue · Form" right="CTL / ATL / TSB · 8 weeks" />
          <ChartBox>
            {vm.hasLoad ? (
              <LineChart
                n={vm.loadChart.ctl.length}
                series={[
                  { values: vm.loadChart.ctl, color: C.ctl },
                  { values: vm.loadChart.atl, color: C.atl },
                  { values: vm.loadChart.tsb, color: C.tsb, area: false },
                ]}
                legend={[
                  { label: "Fitness", color: C.ctl },
                  { label: "Fatigue", color: C.atl },
                  { label: "Form", color: C.tsb },
                ]}
                xLabels={vm.loadChart.xLabels}
                xTip={vm.loadChart.dates}
              />
            ) : (
              <Empty label="No load data yet — run a Garmin sync." />
            )}
          </ChartBox>
        </Card>
        <Card style={{ height: 170 }}>
          <CardHead
            title="Fatigue Zones — Form (TSB)"
            right={
              vm.hasLoad ? (
                // Days in a row in the zone, because Friel's High Risk limit is a
                // few days at a time; past three, the count turns red.
                <>
                  now: <span style={{ color: zone.color }}>{zone.name}</span>
                  {streak > 1 && (
                    <span style={{ color: zone === FORM_ZONES[4] && streak > 3 ? C.red : undefined }}>
                      {`, ${streak} days`}
                    </span>
                  )}
                  {` · ${zone.advice}`}
                </>
              ) : (
                "TSB by zone · 8 weeks"
              )
            }
          />
          <ChartBox>
            {vm.hasLoad && domain ? (
              // h matches the plot box (card 170 less border, head and padding),
              // so the 9px axis text is not scaled down. The form line is the
              // Form colour, as on the chart above; only the bands carry the zones.
              <LineChart
                n={vm.loadChart.tsb.length}
                h={132}
                padTop={10}
                showLegend={false}
                area={false}
                domainPad={0}
                min={domain.min}
                max={domain.max}
                ticks={FORM_ZONE_TICKS}
                bands={FORM_ZONES}
                yFmt={signedInt}
                series={[{ values: vm.loadChart.tsb, color: C.tsb }]}
                legend={[{ label: "Form", color: C.tsb }]}
                xLabels={zoneXLabels(vm.loadChart.dates)}
                xTip={vm.loadChart.dates}
                tipFmt={(v) => `${signedInt(v)} · ${formZoneOf(v).name}`}
              />
            ) : (
              <Empty label="No load data yet — run a Garmin sync." />
            )}
          </ChartBox>
        </Card>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
        <KpiGrid data={vm.kpis} />
        <LoadForecastCard forecast={forecast} height={200} />
      </div>
    </div>
  );
}

// Planned daily TSS against projected CTL for the next seven days. It sits in
// the load block because it is the continuation of the Fitness line beside it.
function LoadForecastCard({ forecast, height }: { forecast: ReturnType<typeof buildForecast>; height: number }) {
  return (
    <Card style={{ height }}>
      <CardHead
        title="Load Forecast — Next 7 Days"
        right={
          forecast
            ? `end of week: CTL ${Math.round(forecast.end.ctl)} (${forecast.end.deltaCtl >= 0 ? "+" : ""}${Math.round(forecast.end.deltaCtl)}) · TSB ${forecast.end.tsb >= 0 ? "+" : ""}${Math.round(forecast.end.tsb)}`
            : "planned daily TSS vs projected CTL"
        }
      />
      {forecast ? (
        <ChartBox>
          {/* h = card height less border, head and padding, so the text keeps its size. */}
          <BarsLine
            h={height - 38}
            cats={forecast.days.map((d) => d.label)}
            bars={{ name: "Planned TSS", color: C.yellow, values: forecast.days.map((d) => d.tss) }}
            line={{ name: "Projected CTL", color: C.ctl, values: forecast.ctl }}
          />
        </ChartBox>
      ) : (
        <Empty label="No plan yet — trigger a recommendation to see the forecast." />
      )}
    </Card>
  );
}

export function Disciplines({ workouts }: { workouts: WorkoutRow[] }) {
  const [days, setDays] = useState<number>(28);
  const cards = useMemo(() => disciplineCards(workouts, days), [workouts, days]);
  return (
    // A tinted band behind the whole row -- the Window dropdown belongs to these
    // four cards, so it sits inside the band with them. The band runs swim ->
    // bike -> run -> strength at 10% opacity, so each card sits on a wash of its
    // own top-edge colour, in the same left-to-right order as the cards.
    // Padding 12 keeps the content where the old 2px border + 10px padding put it.
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        background: `linear-gradient(90deg, ${C.form}1a, ${C.fat}1a 36%, ${C.fit}1a 64%, ${C.strength}1a)`,
        borderRadius: 3,
        padding: 12,
      }}
    >
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: C.muted }}>Window</span>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          style={{
            background: "#181b1f",
            color: C.text,
            border: `1px solid ${C.cardBorder}`,
            borderRadius: 4,
            padding: "4px 8px",
            fontSize: 11.5,
            fontWeight: 600,
            outline: "none",
            cursor: "pointer",
          }}
        >
          {WINDOW_OPTIONS.map((o) => (
            <option key={o.days} value={o.days}>{o.label}</option>
          ))}
        </select>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
      {cards.map((d) => (
        <Card key={d.name} accent={d.color} style={{ padding: "12px 14px", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: C.text, textTransform: "capitalize" }}>{d.name}</span>
            <span style={{ fontSize: 10, color: C.muted }}>TSS {d.tss}</span>
          </div>
          <div style={{ display: "flex", gap: 18 }}>
            <Metric value={d.distLabel} label="volume" />
            <Metric value={d.timeLabel} label="time" />
          </div>
          <div style={{ display: "flex", gap: 18, borderTop: `1px solid ${C.cardBorder}`, paddingTop: 8 }}>
            <Metric value={d.m1v} label={d.m1l} small />
            <Metric value={d.m2v} label={d.m2l} small />
          </div>
        </Card>
      ))}
      </div>
    </div>
  );
}

function Metric({ value, label, small }: { value: string; label: string; small?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: small ? 13 : 19, fontWeight: 600, color: small ? C.textChip : "#fff" }}>{value}</div>
      <div style={{ fontSize: 10, color: C.muted }}>{label}</div>
    </div>
  );
}

export function Distributions({ vm }: { vm: ViewModel }) {
  // Total hours per week, summed across disciplines, for the hours markers.
  const weeklyHours = vm.weeklyVolume.cats.map((_, i) =>
    vm.weeklyVolume.series.reduce((s, se) => s + (se.values[i] || 0), 0),
  );
  return (
    // Heart-Rate Zones is 30% narrower than an even third, and the two cards
    // either side split the freed width equally (1.15 + 0.7 + 1.15 = 3).
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 0.7fr) minmax(0, 1.15fr)", gap: 10 }}>
      <Card style={{ height: 222 }}>
        <CardHead title={<>Weekly TSS &amp; Volume <span style={{ color: C.muted, fontWeight: 400 }}>· last 8 weeks</span></>} />
        <ChartBox>
          <TssVolumeBars cats={vm.weeklyTss.cats} tss={vm.weeklyTss.series} hours={weeklyHours} />
        </ChartBox>
      </Card>
      <Card style={{ height: 222 }}>
        <CardHead title={<>Heart-Rate Zones <span style={{ color: C.muted, fontWeight: 400 }}>· this week</span></>} />
        {vm.hrZones ? (
          <ChartBox>
            <Donut items={vm.hrZones} />
          </ChartBox>
        ) : (
          <Empty label="No HR-zone data this week. Run a Garmin sync to populate." />
        )}
      </Card>
      <Card style={{ height: 222 }}>
        <CardHead title={<>Sleep <span style={{ color: C.muted, fontWeight: 400 }}>· avg per night, last 8 weeks</span></>} />
        {vm.weeklySleep.hasData ? (
          <ChartBox>
            <StackedBars
              cats={vm.weeklySleep.cats}
              series={[{ name: "Sleep", color: C.sleep, values: vm.weeklySleep.hours }]}
              legend={false}
              fmtY={(v) => `${Math.round(v)}h`}
              tipText={(ci) => {
                const n = vm.weeklySleep.nights[ci];
                const cur = ci === vm.weeklySleep.cats.length - 1 ? " so far" : "";
                return `${vm.weeklySleep.cats[ci]} · ${fmtSleep(vm.weeklySleep.hours[ci])} avg · ${n} night${n === 1 ? "" : "s"}${cur}`;
              }}
            />
          </ChartBox>
        ) : (
          <Empty label="No sleep data yet. Run a Garmin sync to populate." />
        )}
      </Card>
    </div>
  );
}

// Hours as "7h 12m".
function fmtSleep(hours: number): string {
  const m = Math.round(hours * 60);
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

// Local "YYYY-MM-DD" for today, to mark the first day of the rolling window.
function todayISO(): string {
  return localISO(new Date());
}

// The plan is a rolling next-7-days window, so days render in date order;
// flag days the athlete marked as recovery so they render as rest.
//
// A day's own sessions always win over the schedule's recovery flag. The
// backend already clears anything Claude puts on a rest day
// (_enforce_rest_days), so sessions surviving on one got there through the
// coach's add/move tools with the athlete's explicit confirmation — hiding
// them here made those edits silently vanish from the week.
function orderDays(days: PlanDay[], settings: AppSettings) {
  return [...days]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => {
      const planned = d.sessions?.length ?? 0;
      const scheduledRecovery = settings.recoveryDays.includes(weekdayOf(d.date));
      const forcedRest = !planned && scheduledRecovery;
      return { day: d, scheduledRecovery, forcedRest, isRest: !planned && (d.is_rest_day || forcedRest) };
    });
}

// The completed counterpart of PredictedWeek: the last 7 synced days (ending
// yesterday, so it never overlaps the predicted row). Same layout, but the
// whole card is painted dark blue to mark it as history.
export function PastWeek({ workouts, onAskCoach }: { workouts: WorkoutRow[]; onAskCoach: (a: CoachAttachment) => void }) {
  const [detail, setDetail] = useState<{ workout: WorkoutRow; date: string; dayName: string } | null>(null);
  const days = useMemo(() => {
    const out: { date: string; dayName: string; dayLong: string; rows: WorkoutRow[] }[] = [];
    for (let delta = 7; delta >= 1; delta--) {
      const d = new Date();
      d.setDate(d.getDate() - delta);
      out.push({
        date: localISO(d),
        dayName: d.toLocaleDateString(undefined, { weekday: "short" }),
        dayLong: d.toLocaleDateString(undefined, { weekday: "long" }),
        rows: [],
      });
    }
    const grouped = groupWorkoutsByDate(workouts);
    for (const o of out) o.rows = grouped.get(o.date) ?? [];
    return out;
  }, [workouts]);

  const title = (
    <>
      Past Training — Last 7 Days
      <span style={{ marginLeft: 6, fontSize: 8, fontWeight: 600, color: C.form, textTransform: "uppercase", letterSpacing: ".06em" }}>completed</span>
    </>
  );
  const cardStyle = { background: C.pastCard, border: `1px solid ${C.pastBorder}` };

  const all = days.flatMap((o) => o.rows);
  if (all.length === 0) {
    return (
      <Card style={cardStyle}>
        <CardHead title={title} right="awaiting synced workouts" />
        <Empty label="No synced workouts in the last 7 days — run a Garmin sync." />
      </Card>
    );
  }
  const totalTss = all.reduce((s, w) => s + (w.tss || 0), 0);
  const totalHours = all.reduce((s, w) => s + (w.duration_sec || 0), 0) / 3600;

  return (
    <Card style={cardStyle}>
      <CardHead title={title} right={`Completed ${totalHours.toFixed(1)} h · ${Math.round(totalTss)} TSS`} />
      <div style={{ padding: "10px 13px 13px", display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 8 }}>
        {days.map(({ date, dayName, dayLong, rows }) => {
          const dayTss = rows.reduce((s, w) => s + (w.tss || 0), 0);
          const dayMin = rows.reduce((s, w) => s + (w.duration_sec || 0), 0) / 60;
          return (
            <div
              key={date}
              style={{ background: C.pastInner, border: `1px solid ${C.pastBorder}`, borderRadius: 3, padding: 8, display: "flex", flexDirection: "column", gap: 6, minHeight: 150 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: C.textSoft }}>{dayName}</span>
                <span style={{ fontSize: 9, color: C.muted }}>{monthDay(date)}</span>
              </div>
              {rows.length === 0 ? (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: C.muted }}>
                  Rest
                </div>
              ) : (
                rows.map((w) => (
                  <div
                    key={w.activity_id + w.discipline}
                    onClick={() => setDetail({ workout: w, date, dayName: dayLong })}
                    title="Click for workout details"
                    style={{ cursor: "pointer", background: C.pastInner2, borderLeft: `3px solid ${DISCIPLINE_COLOR[w.discipline] ?? C.muted}`, borderRadius: 2, padding: "5px 7px", display: "flex", flexDirection: "column", gap: 2 }}
                  >
                    <div style={{ fontSize: 10, fontWeight: 500, color: "#dfe0e6", lineHeight: 1.25 }}>
                      {w.title || w.discipline}
                    </div>
                    <span style={{ fontSize: 9, color: C.muted2 }}>
                      {w.duration_sec != null ? Math.round(w.duration_sec / 60) : "—"} min · {w.tss != null ? Math.round(w.tss) : "—"} TSS
                    </span>
                  </div>
                ))
              )}
              <div style={{ marginTop: "auto", fontSize: 9, color: C.muted }}>
                Σ {(dayMin / 60).toFixed(1)} h · {Math.round(dayTss)} TSS
              </div>
            </div>
          );
        })}
      </div>
      {detail && (
        <WorkoutModal
          workout={detail.workout}
          date={detail.date}
          dayName={detail.dayName}
          onAskCoach={(analysis) => onAskCoach({ kind: "workout", workout: detail.workout, analysis, date: detail.date, dayName: detail.dayName })}
          onClose={() => setDetail(null)}
        />
      )}
    </Card>
  );
}

// The schedule's day keys, in weekdayOf's numbering (Sunday is 0).
const WEEKDAY_KEYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

// A planned session's card: in its day tile, and carried under the cursor. The
// footer slot holds "ask coach", which the carried copy has no use for.
function PlanSessionCard({ session: s, race, footer }: { session: PlanSession; race: boolean; footer?: ReactNode }) {
  return (
    <>
      <div style={{ fontSize: 10, fontWeight: race ? 700 : 500, color: race ? C.white : "#dfe0e6", lineHeight: 1.25 }}>
        {s.title || `${s.discipline} ${s.type ?? ""}`}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 9, color: race ? C.textChip : C.muted2 }}>
          {s.duration_min ?? "—"} min · {s.estimated_tss ?? "—"} TSS
        </span>
        {footer}
      </div>
    </>
  );
}

function sessionCardStyle(s: PlanSession, race: boolean): React.CSSProperties {
  return {
    background: race ? "rgba(0,0,0,.28)" : C.inner2,
    borderLeft: `3px solid ${race ? C.yellow : DISCIPLINE_COLOR[s.discipline] ?? C.muted}`,
    borderRadius: 2,
    padding: "5px 7px",
    display: "flex",
    flexDirection: "column",
    gap: 2,
  };
}

export function PredictedWeek({ plan, settings, workouts, events, onAskCoach, onMoveSession, locked }: { plan: Plan | null; settings: AppSettings; workouts: WorkoutRow[]; events: RaceEvent[]; onAskCoach: (a: CoachAttachment) => void; onMoveSession: (fromDate: string, sessionIndex: number, toDate: string) => Promise<void>; locked: boolean }) {
  const [detail, setDetail] = useState<PlanAttachment | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);
  // recommendation is the coach's rest rationale, only set when the athlete
  // trained on a day the plan marked as rest.
  const [override, setOverride] = useState<{ workout: WorkoutRow; date: string; dayName: string; recommendation?: string } | null>(null);
  const byDate = useMemo(() => groupWorkoutsByDate(workouts), [workouts]);
  // A race day comes from the events, not the plan: Claude's session title is
  // free text. Two events on one date keep the first.
  const raceByDate = useMemo(() => {
    const m = new Map<string, RaceEvent>();
    for (const g of events) if (g.race_date && !m.has(g.race_date)) m.set(g.race_date, g);
    return m;
  }, [events]);
  // Rest days from the schedule itself: settings.recoveryDays is only this
  // browser's copy, empty until Settings has been saved here. It stands in while
  // this loads or if it fails, and the backend refuses a rest day either way.
  const [restWeekdays, setRestWeekdays] = useState<number[]>(settings.recoveryDays);
  useEffect(() => {
    let alive = true;
    setRestWeekdays(settings.recoveryDays);
    api
      .schedule()
      .then((s) => {
        if (!alive) return;
        const week = s.schedule as Record<string, { rest?: boolean } | undefined>;
        setRestWeekdays(WEEKDAY_KEYS.flatMap((name, num) => (week[name]?.rest ? [num] : [])));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [settings.recoveryDays]);
  // Refused: a day that has passed, a race day (the event sets that day, not the
  // plan) and a rest day in the schedule.
  const canDrop = useCallback(
    (date: string, from: SessionRef) =>
      date !== from.date && date >= todayISO() && !raceByDate.has(date) && !restWeekdays.includes(weekdayOf(date)),
    [raceByDate, restWeekdays],
  );
  const handleDrop = useCallback(
    async (from: SessionRef, toDate: string) => {
      setMoveError(null);
      try {
        await onMoveSession(from.date, from.index, toDate);
      } catch (e) {
        setMoveError(e instanceof Error ? e.message : String(e));
        throw e;
      }
    },
    [onMoveSession],
  );
  const { drag, landing, settled, onPointerDown, consumeDragClick, attachFloat } = useSessionDrag({ canDrop, onDrop: handleDrop, enabled: !locked });
  // Which card the carried copy stands for, fixed at the lift: the plan moves
  // under it on a drop, and the copy must not change while it flies.
  const [carried, setCarried] = useState<PlanSession | null>(null);
  useEffect(() => {
    if (!drag) {
      setCarried(null);
      return;
    }
    if (drag.index !== DROPPED) {
      const s = plan?.days.find((x) => x.date === drag.date)?.sessions?.[drag.index];
      if (s) setCarried((c) => c ?? s);
    }
  }, [drag, plan]);
  useEffect(() => {
    if (!moveError) return;
    const t = window.setTimeout(() => setMoveError(null), 5000);
    return () => window.clearTimeout(t);
  }, [moveError]);
  if (!plan || !plan.days?.length) {
    return (
      <Card>
        <CardHead title="Training — Next 7 Days" right="awaiting plan generation" />
        <Empty label="Trigger the recommendation engine (POST /recommend/trigger, or a Garmin sync) to generate the next-7-days plan." />
      </Card>
    );
  }
  const ordered = orderDays(plan.days, settings);
  // Totals count only days actually trained (recovery days are excluded).
  const trained = ordered.filter((o) => !o.isRest);
  const totalTss = trained.reduce(
    (a, o) => a + o.day.sessions.reduce((s, x) => s + (x.estimated_tss || 0), 0),
    0,
  );
  const totalHours =
    trained.reduce(
      (a, o) => a + o.day.sessions.reduce((s, x) => s + (x.duration_min || 0), 0),
      0,
    ) / 60;

  return (
    <Card>
      <CardHead
        title="Training — Next 7 Days"
        right={`Planned ${totalHours.toFixed(1)} h · ${Math.round(totalTss)} TSS · ${plan.periodization_phase ?? ""} week`}
      />
      {moveError && (
        <div style={{ padding: "8px 13px 0", fontSize: 11, color: C.red }}>Could not move the session: {moveError}</div>
      )}
      <div style={{ padding: "10px 13px 13px", display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 8 }}>
        {ordered.map(({ day: d, scheduledRecovery, forcedRest, isRest }) => {
          const isToday = d.date === todayISO();
          // Completed work is shown on every past/current day, not just rest
          // days: otherwise a session finished today is invisible until it
          // rolls into Past Training tomorrow. Future days cannot have any.
          const actual = d.date <= todayISO() ? byDate.get(d.date) ?? [] : [];
          const hasActual = actual.length > 0;
          // Once something was actually done, the day's totals report that
          // rather than what was planned for it.
          const dayTss = hasActual
            ? actual.reduce((s, w) => s + (w.tss || 0), 0)
            : isRest
              ? 0
              : d.sessions.reduce((s, x) => s + (x.estimated_tss || 0), 0);
          const dayMin = hasActual
            ? actual.reduce((s, w) => s + (w.duration_sec || 0), 0) / 60
            : isRest
              ? 0
              : d.sessions.reduce((s, x) => s + (x.duration_min || 0), 0);
          const recommendation = forcedRest
            ? "This day is set as a recovery day in your training schedule (Settings), so no session was planned."
            : "The coach recommended a rest day here based on your current training load.";
          const race = raceByDate.get(d.date);
          // Race day: warm gradient, yellow glow and a swim/bike/run ribbon.
          const tileStyle: React.CSSProperties = race
            ? {
                position: "relative",
                overflow: "hidden",
                background: `linear-gradient(160deg, rgba(250,222,42,.34), rgba(255,152,48,.24) 50%, rgba(242,73,92,.22)), ${C.inner}`,
                border: `1px solid ${C.yellow}`,
                boxShadow: "0 0 0 1px rgba(250,222,42,.4), 0 0 18px rgba(250,222,42,.25)",
              }
            : { background: C.inner, border: `1px solid ${isToday ? C.fit : C.cardBorder}` };
          // While a card is carried: every day says whether it takes the drop,
          // and the one under the cursor takes the card's colour.
          const source = drag ? { date: drag.date, index: drag.index } : null;
          const isSource = source?.date === d.date;
          const dropOk = !!source && source.index !== DROPPED && canDrop(d.date, source);
          const refused = !!source && source.index !== DROPPED && !dropOk && !isSource;
          const hovered = dropOk && drag?.overDate === d.date;
          const dragColor = (carried && DISCIPLINE_COLOR[carried.discipline]) || C.form;
          const dropStyle: React.CSSProperties = hovered
            ? { border: `1px solid ${dragColor}`, boxShadow: `0 0 0 1px ${dragColor}, 0 6px 18px rgba(0,0,0,.35)`, transform: "scale(1.02)", background: `linear-gradient(${dragColor}1f, ${dragColor}1f), ${C.inner}` }
            : dropOk
              ? { border: `1px dashed ${C.muted}` }
              : refused
                ? { opacity: 0.45 }
                : {};
          return (
            <div
              key={d.date}
              className="plan-day-tile"
              data-plan-date={d.date}
              data-plan-refused={refused}
              style={{ ...tileStyle, ...dropStyle, borderRadius: 3, padding: 8, display: "flex", flexDirection: "column", gap: 6, minHeight: 150 }}
            >
              {race && (
                <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 4, background: `linear-gradient(90deg, ${C.form}, ${C.fat}, ${C.fit})` }} />
              )}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontSize: 12, fontWeight: race ? 800 : 600, color: race ? C.white : C.textSoft }}>
                  {String(d.day_name).slice(0, 3)}
                  {race ? (
                    <span style={{ marginLeft: 5, padding: "1px 4px", borderRadius: 2, background: C.yellow, fontSize: 8, fontWeight: 800, color: C.pageBg, textTransform: "uppercase", letterSpacing: ".06em" }}>race day</span>
                  ) : (
                    isToday && (
                      <span style={{ marginLeft: 5, fontSize: 8, fontWeight: 600, color: C.fit, textTransform: "uppercase", letterSpacing: ".06em" }}>today</span>
                    )
                  )}
                  {/* A day the athlete asked the coach to change: kept when the
                      plan is rebuilt, if they choose to keep their changes. */}
                  {d.athlete_requested && (
                    <span
                      title="You asked the coach for this day. It is kept when the plan is rebuilt, if you keep your changes."
                      style={{ marginLeft: 5, fontSize: 8, fontWeight: 600, color: race ? C.white : C.muted3, textTransform: "uppercase", letterSpacing: ".06em" }}
                    >
                      requested
                    </span>
                  )}
                </span>
                <span style={{ fontSize: 9, color: race ? C.yellow : C.muted }}>{monthDay(d.date)}</span>
              </div>
              {race && (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 3, padding: "2px 0 4px" }}>
                  {race.icon && <span style={{ fontSize: 26, lineHeight: 1 }}>{race.icon}</span>}
                  <span
                    style={{ fontSize: 11, fontWeight: 800, color: C.white, lineHeight: 1.25, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
                  >
                    {race.name}
                  </span>
                  {race.type && (
                    <span style={{ fontSize: 8.5, fontWeight: 700, color: C.fat, textTransform: "uppercase", letterSpacing: ".06em" }}>{race.type}</span>
                  )}
                </div>
              )}
              {scheduledRecovery && !isRest && (
                <span style={{ fontSize: 8.5, color: C.muted2, textTransform: "uppercase", letterSpacing: ".05em" }}>
                  added to recovery day
                </span>
              )}
              {actual.map((w) => (
                <div
                  key={w.activity_id + w.discipline}
                  onClick={() => setOverride({ workout: w, date: d.date, dayName: d.day_name, recommendation: isRest ? recommendation : undefined })}
                  title="Click for workout details"
                  style={{ cursor: "pointer", background: C.pastInner2, borderLeft: `3px solid ${DISCIPLINE_COLOR[w.discipline] ?? C.muted}`, borderRadius: 2, padding: "5px 7px", display: "flex", flexDirection: "column", gap: 2 }}
                >
                  <div style={{ fontSize: 10, fontWeight: 500, color: "#dfe0e6", lineHeight: 1.25 }}>
                    {w.title || w.discipline}
                  </div>
                  <span style={{ fontSize: 9, color: C.muted2 }}>
                    {w.duration_sec != null ? Math.round(w.duration_sec / 60) : "—"} min · {w.tss != null ? Math.round(w.tss) : "—"} TSS
                  </span>
                </div>
              ))}
              {hasActual && (
                <span style={{ fontSize: 8.5, color: C.muted2, textTransform: "uppercase", letterSpacing: ".05em" }}>
                  {isRest ? "rest recommended" : "completed"}
                </span>
              )}
              {(isRest || d.sessions.length === 0) && !hasActual ? (
                // On a race day the hero block marks the day; a "Rest" label
                // would contradict it (e.g. if the weekday is set as rest).
                !race && (
                <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 3, fontSize: 10, color: C.muted }}>
                  <span>Rest</span>
                  {forcedRest && <span style={{ fontSize: 8.5, color: C.muted2, textTransform: "uppercase", letterSpacing: ".05em" }}>recovery</span>}
                </div>
                )
              ) : (
                !isRest &&
                d.sessions.map((s, i) => {
                  // A race-day card stays: the event sets the race date, so
                  // moving the card would only contradict the tile.
                  const draggable = !locked && !race && d.date >= todayISO();
                  const lifted = drag?.date === d.date && drag.index === i;
                  const inFlight = landing?.date === d.date && (landing.index === i || (landing.index === DROPPED && i === d.sessions.length - 1));
                  const justLanded = settled?.date === d.date && settled.index === i;
                  return (
                    <div
                      key={i}
                      data-plan-card
                      className={justLanded ? "plan-session-settled" : undefined}
                      onPointerDown={draggable ? (e) => onPointerDown(e, { date: d.date, index: i }) : undefined}
                      onClick={() => {
                        if (consumeDragClick()) return;
                        setDetail({ kind: "plan", session: s, date: d.date, dayName: d.day_name, sessionIndex: i });
                      }}
                      title={draggable ? "Click for session details, drag to another day" : "Click for session details"}
                      style={{
                        ...sessionCardStyle(s, !!race),
                        cursor: draggable ? "grab" : "pointer",
                        // The space it left while the copy is carried; hidden
                        // altogether in the slot the copy is flying to.
                        ...(lifted ? { opacity: 0.28, outline: `1px dashed ${C.muted}`, outlineOffset: -1 } : inFlight ? { opacity: 0 } : {}),
                        ["--settle-color" as string]: DISCIPLINE_COLOR[s.discipline] ?? C.muted,
                      }}
                    >
                      <PlanSessionCard
                        session={s}
                        race={!!race}
                        footer={
                          <button
                            type="button"
                            onPointerDown={(e) => e.stopPropagation()}
                            onClick={(e) => {
                              e.stopPropagation();
                              onAskCoach({ kind: "plan", session: s, date: d.date, dayName: d.day_name, sessionIndex: i });
                            }}
                            title="Ask coach"
                            aria-label="Ask coach"
                            style={{ cursor: "pointer", background: "none", border: "none", padding: 0, display: "flex", color: race ? C.yellow : C.muted2 }}
                          >
                            <ChatIcon size={14} stroke={1.8} />
                          </button>
                        }
                      />
                    </div>
                  );
                })
              )}
              <div style={{ marginTop: "auto", fontSize: 9, color: C.muted }}>
                Σ {(dayMin / 60).toFixed(1)} h · {Math.round(dayTss)} TSS{hasActual ? " actual" : ""}
              </div>
            </div>
          );
        })}
      </div>
      {drag && carried &&
        createPortal(
          <div
            ref={attachFloat}
            style={{
              ...sessionCardStyle(carried, false),
              position: "fixed",
              left: 0,
              top: 0,
              width: drag.width,
              zIndex: 1000,
              pointerEvents: "none",
              transformOrigin: "center",
              boxShadow: `0 10px 26px rgba(0,0,0,.5), 0 0 14px ${DISCIPLINE_COLOR[carried.discipline] ?? C.muted}55`,
            }}
          >
            <PlanSessionCard session={carried} race={false} />
          </div>,
          document.body,
        )}
      {detail && (
        <SessionModal
          session={detail.session}
          date={detail.date}
          dayName={detail.dayName}
          sessionIndex={detail.sessionIndex}
          onClose={() => setDetail(null)}
          onAskCoach={() => onAskCoach(detail)}
        />
      )}
      {override && (
        <WorkoutModal
          workout={override.workout}
          date={override.date}
          dayName={override.dayName}
          coachRecommendation={override.recommendation}
          onAskCoach={(analysis) => onAskCoach({ kind: "workout", workout: override.workout, analysis, date: override.date, dayName: override.dayName })}
          onClose={() => setOverride(null)}
        />
      )}
    </Card>
  );
}

// HRV and Resting HR over the last 28 days, two cards of the same size directly
// under the load block (design system RecoveryTrends): the load block says how
// hard the training was, these say how the body took it. Half width each, so
// their days do not line up with the full-width charts above; that is intended.
// Dashboard only -- none of this reaches the coach.
export function RecoveryTrends({ vm }: { vm: ViewModel }) {
  const r = vm.recovery;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
      <RecoveryCard
        title="HRV — Last 28 Days"
        qualifier="· overnight avg"
        unit="ms"
        color={C.hrv}
        range={HRV_RANGE}
        series={r.hrv}
        dayTips={r.dayTips}
        xLabels={r.xLabels}
        empty="No HRV data yet — run a Garmin sync."
      />
      <RecoveryCard
        title="Resting HR — Last 28 Days"
        qualifier="· daily"
        unit="bpm"
        color={C.restingHr}
        range={RESTING_HR_RANGE}
        series={r.restingHr}
        dayTips={r.dayTips}
        xLabels={r.xLabels}
        empty="No resting HR data yet — run a Garmin sync."
      />
    </div>
  );
}

function RecoveryCard({
  title,
  qualifier,
  unit,
  color,
  range,
  series,
  dayTips,
  xLabels,
  empty,
}: {
  title: string;
  qualifier: string;
  unit: string;
  color: string;
  range: RecoveryRange;
  series: RecoverySeries | null;
  dayTips: string[];
  xLabels: { i: number; label: string }[];
  empty: string;
}) {
  const dom = series ? recoveryDomain(series.values, range) : null;
  return (
    <Card style={{ height: 220 }}>
      <CardHead
        title={<>{title} <span style={{ color: C.muted, fontWeight: 400 }}>{qualifier}</span></>}
        right={
          series ? (
            <span style={{ color: C.muted2 }}>
              28-day avg {series.avg} {unit} · latest {Math.round(series.latest)} {unit}
            </span>
          ) : undefined
        }
      />
      {series && dom ? (
        // Single-series charts with sparse points, so unlike the load charts
        // they keep a ring dot on every day; gaps stay gaps. h is the card less
        // its border, head and the 4px top pad.
        <ChartBox pad="4px 0 0">
          <LineChart
            n={series.values.length}
            series={[{ values: series.values, color }]}
            h={188}
            area={false}
            dots
            smooth
            min={dom.min}
            max={dom.max}
            ticks={dom.ticks}
            domainPad={0}
            padTop={8}
            padL={36}
            padR={16}
            padB={20}
            refLine={series.avg}
            showLegend={false}
            xLabels={xLabels}
            xTip={dayTips}
            tipLayout="single"
            tipFmt={(v) => `${Math.round(v)} ${unit}`}
          />
        </ChartBox>
      ) : (
        <Empty label={empty} />
      )}
    </Card>
  );
}

// Run pace and VO2max, two panels across the full width. The Load Forecast that
// used to be the first of three now lives in the load block at the top.
export function LoadFocusRow({ vm }: { vm: ViewModel }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
      <Card style={{ height: 230 }}>
        <CardHead title={<>Run Pace Trend <span style={{ color: C.muted, fontWeight: 400 }}>· avg /km</span></>} />
        <ChartBox>
          {vm.paceTrend ? (
            <LineChart
              n={vm.paceTrend.values.length}
              series={[{ values: vm.paceTrend.values, color: C.fit, dots: true }]}
              area
              yFmt={(v) => pace(v)}
              xLabels={vm.paceTrend.xLabels}
              legend={[{ label: "Avg pace /km", color: C.fit }]}
              xTip={vm.paceTrend.weekTips}
              tipFmt={(v) => pace(v)}
            />
          ) : (
            <Empty label="No run-pace data in the last 8 weeks." />
          )}
        </ChartBox>
      </Card>
      <Card style={{ height: 230 }}>
        <CardHead
          title={<>VO2max <span style={{ color: C.muted, fontWeight: 400 }}>· ml/kg/min</span></>}
          right={
            vm.vo2max
              ? vm.vo2max.series
                  .map((s) => `${s.label.toLowerCase()} ${s.values[s.values.length - 1].toFixed(1)}`)
                  .join(" · ")
              : undefined
          }
        />
        <ChartBox>
          {vm.vo2max ? (
            // No area fill: two 13%-opacity fills a few points apart read as mud
            // on a narrow column. No dots either -- there are 56 points, where
            // the pace trend has eight. yFmt overrides the default Math.round,
            // which would render duplicate labels whenever the VO2max range is
            // under about four points, i.e. most of the time.
            <LineChart
              n={vm.vo2max.series[0].values.length}
              series={vm.vo2max.series.map((s) => ({ values: s.values, color: s.color }))}
              area={false}
              yFmt={(v) => v.toFixed(1)}
              xLabels={vm.vo2max.xLabels}
              legend={vm.vo2max.series.map((s) => ({ label: s.label, color: s.color }))}
              xTip={vm.vo2max.dayTips}
              tipFmt={(v) => v.toFixed(1)}
            />
          ) : (
            <Empty label="No VO2max data from Garmin in the last 8 weeks." />
          )}
        </ChartBox>
      </Card>
    </div>
  );
}
