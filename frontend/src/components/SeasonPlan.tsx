// Season plan panel, directly under the events row: the weekly-load bars of
// the persistent plan and the two template-written lines that say what the
// plan is and where the athlete stands on it. With no plan it offers to make
// one. Everything here reads GET /season-plan; nothing is computed client-side
// beyond layout.
import { useEffect, useState } from "react";
import { C, DISCIPLINE_COLOR } from "../theme";
import { api } from "../api";
import { Card } from "./primitives";
import { loadSeasonCollapsed, saveSeasonCollapsed } from "../settings";
import { PlanBars, PHASE_COLOR, SPLIT_DISCIPLINES, dayMon } from "../charts/PlanBars";
import { draftOf, draftedRows, parseSplit, sameSplit, SplitInputs, SplitTable, tableOf, type SplitDraft } from "./SeasonSplit";
import { localISO } from "../derive";
import { CheckinCard, FatigueBanner } from "./SeasonCheckin";
import type { CoachAttachment, RaceEvent, SeasonFocus, SeasonPlan as SeasonPlanT, SeasonRamp, SeasonSplitAction, SeasonWeek, SeasonWeekAction } from "../types";

// What the create modal opens on: an event, fitness mode, or a conversion of
// the current plan (its event gone) into a fitness plan.
export interface SeasonCreateOpts {
  eventId?: string;
  mode?: "event" | "fitness";
  convert?: boolean;
}

// A fitness chart shows this many finished weeks before the current one.
const FITNESS_PAST_WEEKS = 8;

const PHASES = ["base", "build", "peak", "taper"] as const;

export function SeasonPlan({
  plan,
  events,
  onCreate,
  onChanged,
  onAskCoach,
}: {
  plan: SeasonPlanT | null;
  // Events the athlete is racing (calendar-only ones cannot carry a plan).
  events: RaceEvent[];
  // Opens the create modal (on an event, in fitness mode, or converting).
  onCreate: (opts?: SeasonCreateOpts) => void;
  onChanged: () => void;
  // Attaches a season week to the coach chat.
  onAskCoach: (a: CoachAttachment) => void;
}) {
  const [collapsed, setCollapsed] = useState(loadSeasonCollapsed);
  const fold: Fold = {
    collapsed,
    toggle: () =>
      setCollapsed((c) => {
        saveSeasonCollapsed(!c);
        return !c;
      }),
  };

  if (!plan) return <EmptyPlan events={events} onCreate={onCreate} fold={fold} />;
  if (plan.status === "completed") {
    const done = "Season complete. Recovery first, then start a new plan.";
    return (
      <Card>
        <PanelHead title={<>Season plan{plan.event?.name && <span style={tagStyle}>{plan.event.name}</span>}</>} summary={done} fold={fold} />
        {!fold.collapsed && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "12px 13px 13px" }}>
            <span style={{ fontSize: 12, color: C.textSoft }}>{done}</span>
            <button onClick={() => onCreate()} style={primaryBtn}>Start a new plan</button>
          </div>
        )}
      </Card>
    );
  }
  return <ActivePlan plan={plan} events={events} onCreate={onCreate} onChanged={onChanged} onAskCoach={onAskCoach} fold={fold} />;
}

// The panel's fold: open, or folded to its header row (remembered per browser).
interface Fold {
  collapsed: boolean;
  toggle: () => void;
}

// The header row of every panel state. The title toggles the fold too; while
// folded the actions give way to a one-line summary, so the plan still says
// where the athlete stands.
function PanelHead({ title, summary, actions, fold }: { title: React.ReactNode; summary?: string; actions?: React.ReactNode; fold: Fold }) {
  return (
    <div style={{ padding: fold.collapsed ? "10px 13px" : "10px 13px 0", display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
      <button
        onClick={fold.toggle}
        aria-expanded={!fold.collapsed}
        title={fold.collapsed ? "Show the season plan" : "Hide the season plan"}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", display: "flex", alignItems: "center", gap: 10, fontSize: 12, fontWeight: 500, color: C.textSoft, flex: "none" }}
      >
        {title}
      </button>
      {fold.collapsed && summary && (
        <span title={summary} style={{ flex: 1, minWidth: 0, fontSize: 11, color: C.muted3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {summary}
        </span>
      )}
      <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
        {!fold.collapsed && actions}
        {/* A plain typographic arrow, like the close cross: up folds, down opens. */}
        <button
          onClick={fold.toggle}
          aria-expanded={!fold.collapsed}
          aria-label={fold.collapsed ? "Show the season plan" : "Hide the season plan"}
          title={fold.collapsed ? "Show" : "Hide"}
          style={{ cursor: "pointer", width: 26, height: 24, padding: 0, borderRadius: 4, border: `1px solid ${C.cardBorder}`, background: C.inner, color: C.textSoft, fontSize: 9, lineHeight: 1 }}
        >
          {fold.collapsed ? "▼" : "▲"}
        </button>
      </span>
    </div>
  );
}

// A header action: a small outlined button, red for the destructive one.
function HeadBtn({ children, onClick, active, danger, disabled }: { children: React.ReactNode; onClick: () => void; active?: boolean; danger?: boolean; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      style={{
        cursor: disabled ? "default" : "pointer",
        fontSize: 11,
        fontWeight: 600,
        padding: "4px 10px",
        borderRadius: 4,
        border: `1px solid ${danger ? C.red : active ? C.form : C.cardBorder}`,
        background: active ? C.inner2 : C.inner,
        color: danger ? C.red : C.textSoft,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {children}
    </button>
  );
}

function EmptyPlan({ events, onCreate, fold }: { events: RaceEvent[]; onCreate: (opts?: SeasonCreateOpts) => void; fold: Fold }) {
  const today = localISO(new Date());
  const upcoming = events.filter((e) => (e.race_date || "") >= today);
  return (
    <Card>
      <PanelHead title="Season plan" summary="No plan yet: train for an event, or build fitness week by week." fold={fold} />
      {!fold.collapsed && (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 10, padding: "10px 13px 13px" }}>
        <OptionCard
          title="Train for an event"
          body={
            upcoming.length
              ? "Base, build, peak and taper worked back from your race date, with a recovery week after every three loading weeks."
              : "Add an event you are racing first, with Manage events above."
          }
          action={upcoming.length ? "Create plan" : undefined}
          onClick={() => onCreate()}
        />
        <OptionCard
          title="Build fitness"
          body="No race. Choose each week how hard to go: a check-in on the last day of each week, with a recovery week after every three loading weeks."
          action="Create plan"
          onClick={() => onCreate({ mode: "fitness" })}
        />
      </div>
      )}
    </Card>
  );
}

function OptionCard({ title, body, action, note, onClick }: { title: string; body: string; action?: string; note?: string; onClick?: () => void }) {
  return (
    <div style={{ background: C.inner, border: `1px solid ${C.cardBorder}`, borderRadius: 3, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 8, opacity: action ? 1 : 0.7 }}>
      <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</span>
      <span style={{ fontSize: 11.5, color: C.muted3, lineHeight: 1.45, flex: 1 }}>{body}</span>
      {action && (
        <button onClick={onClick} style={{ ...primaryBtn, alignSelf: "flex-start" }}>
          {action}
        </button>
      )}
      {note && <span style={{ fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: C.muted }}>{note}</span>}
    </div>
  );
}

function ActivePlan({
  plan,
  events,
  onCreate,
  onChanged,
  onAskCoach,
  fold,
}: {
  plan: SeasonPlanT;
  events: RaceEvent[];
  onCreate: (opts?: SeasonCreateOpts) => void;
  onChanged: () => void;
  onAskCoach: (a: CoachAttachment) => void;
  fold: Fold;
}) {
  const [showCtl, setShowCtl] = useState(false);
  const [showChanges, setShowChanges] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  // The week open in the week panel, by its first day (indices move on a rebuild).
  const [selected, setSelected] = useState<string | null>(null);
  const selectedWeek = plan.weeks.find((w) => w.week_start === selected) ?? null;
  // A fitness plan has no end: its chart is the last weeks, this one, the next
  // and the projection. An event plan shows the whole season.
  const shown = (() => {
    if (plan.mode !== "fitness") return plan.weeks;
    const firstLive = plan.weeks.findIndex((w) => w.status !== "past");
    const from = firstLive < 0 ? Math.max(0, plan.weeks.length - FITNESS_PAST_WEEKS) : Math.max(0, firstLive - FITNESS_PAST_WEEKS);
    return plan.weeks.slice(from);
  })();
  const active = plan.status === "active";
  // The sync stamps its revisions with the time it saved the plan.
  const adjustedOnSync = plan.revisions.some((r) => r.trigger === "sync" && r.at === plan.updated_at);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const remaining = plan.weeks.filter((w) => w.status !== "past").length;
  const changes = plan.revisions.length;
  const capped = plan.revisions.some((r) => r.reason_code === "HOURS_CAP");
  const split = plan.weeks.some((w) => w.split);

  async function cancelPlan() {
    setBusy(true);
    setError(null);
    try {
      await api.cancelSeasonPlan();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirmCancel(false);
    }
  }

  const title = (
    <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
      Season plan
      <span style={tagStyle}>{plan.mode === "event" ? `Event: ${plan.event?.name ?? ""}` : "Fitness plan"}</span>
      {adjustedOnSync && <span style={{ ...tagStyle, color: C.fat }}>Adjusted on last sync</span>}
    </span>
  );

  const actions = (
    <>
      {plan.mode === "event" && (
        <span style={{ fontSize: 10, color: C.textChip, marginRight: 4 }}>
          {remaining} week{remaining === 1 ? "" : "s"} left
        </span>
      )}
      <LinkBtn active={showCtl} onClick={() => setShowCtl((v) => !v)}>Show CTL</LinkBtn>
      {active && (
        <HeadBtn active={showSettings} onClick={() => setShowSettings((v) => !v)}>Settings</HeadBtn>
      )}
      {changes > 0 && <HeadBtn active={showChanges} onClick={() => setShowChanges((v) => !v)}>Changes ({changes})</HeadBtn>}
      <HeadBtn onClick={() => onCreate(plan.event ? { eventId: plan.event.event_id } : { mode: "fitness" })}>New plan</HeadBtn>
      {confirmCancel ? (
        <>
          <span style={{ fontSize: 11, color: C.textChip }}>Cancel the plan?</span>
          <HeadBtn danger disabled={busy} onClick={cancelPlan}>Yes, cancel</HeadBtn>
          <HeadBtn disabled={busy} onClick={() => setConfirmCancel(false)}>Keep it</HeadBtn>
        </>
      ) : (
        <HeadBtn danger onClick={() => setConfirmCancel(true)}>Cancel plan</HeadBtn>
      )}
    </>
  );

  return (
    <Card>
      <PanelHead title={title} summary={plan.summary.line2} actions={actions} fold={fold} />
      {!fold.collapsed && (
      <>
      {error && <div style={{ padding: "6px 13px 0", fontSize: 11, color: C.red }}>{error}</div>}
      {plan.status === "event_missing" && (
        <div style={{ margin: "8px 13px 0", padding: "9px 12px", background: "#2a2214", border: `1px solid ${C.cardBorder}`, borderRadius: 3, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: C.textSoft, flex: 1 }}>
            {plan.event?.name ?? "Your event"} was removed, or you are no longer racing it. The plan is kept as it was: plan another event, or cancel this one.
          </span>
          <button onClick={() => onCreate({ mode: "fitness", convert: true })} style={primaryBtn}>Convert to a fitness plan</button>
          <button onClick={() => onCreate()} style={{ ...primaryBtn, background: C.inner2, border: `1px solid ${C.cardBorder}` }}>Plan another event</button>
        </div>
      )}
      <FatigueBanner plan={plan} onChanged={onChanged} />
      {showChanges && <ChangesList plan={plan} />}
      {showSettings && active && <SettingsRow plan={plan} onDone={() => setShowSettings(false)} onChanged={onChanged} />}
      <div style={{ height: 210, padding: "6px 8px 0" }}>
        <PlanBars
          weeks={shown}
          mode={plan.mode}
          h={200}
          showCtl={showCtl}
          revisions={plan.revisions}
          selected={selected}
          onSelect={(i) => setSelected((cur) => (cur === shown[i]?.week_start ? null : shown[i]?.week_start ?? null))}
        />
      </div>
      {selectedWeek && (
        <WeekPanel
          week={selectedWeek}
          plan={plan}
          editable={active}
          onClose={() => setSelected(null)}
          onChanged={onChanged}
          onAskCoach={onAskCoach}
        />
      )}
      {plan.mode === "fitness" && active && <CheckinCard plan={plan} onChanged={onChanged} />}
      <div style={{ padding: "4px 13px 4px", display: "flex", flexDirection: "column", gap: 3 }}>
        <SummaryLine text={plan.summary.line1} color={C.textChip} />
        <SummaryLine text={plan.summary.line2} color={C.text} />
      </div>
      {plan.mode === "event" && active && <SubEventsRow plan={plan} events={events} onChanged={onChanged} />}
      <div style={{ padding: "4px 13px 10px", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14, fontSize: 10, color: C.muted3 }}>
        {plan.mode === "fitness" && (
          <>
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 9, height: 9, borderRadius: 2, background: C.phaseBuild, border: `1px solid ${C.phaseEdge}` }} />
              Loading week
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 9, height: 9, borderRadius: 2, border: `1px dotted ${C.phaseEdge}`, opacity: 0.6 }} />
              Projection
            </span>
          </>
        )}
        {/* A triathlon plan's bars are in discipline colours, and the brackets
            under them already name each phase, so the phase key is left out. */}
        {plan.mode === "event" &&
          !split &&
          PHASES.map((p) => (
            <span key={p} style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 9, height: 9, borderRadius: 2, background: PHASE_COLOR[p], border: `1px solid ${C.phaseEdge}` }} />
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </span>
          ))}
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, border: `1px solid ${C.phaseEdge}`, background: `repeating-linear-gradient(45deg, ${C.phaseBuild} 0 2px, ${C.card} 2px 4px)` }} />
          Recovery week
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, border: `1px solid ${C.phaseEdge}`, background: "transparent", position: "relative" }}>
            <span style={{ position: "absolute", left: 2, right: 2, bottom: 0, height: 6, background: split ? `linear-gradient(${C.fit} 0 50%, ${C.fat} 50%)` : C.textSoft }} />
          </span>
          Done
        </span>
        {plan.targets?.compressed && (
          <span style={badgeStyle}>Short runway: peak lowered to {Math.round(plan.targets.peak_weekly_tss)} TSS/week</span>
        )}
        {capped && !plan.targets?.compressed && plan.settings.weekly_hours_cap != null && (
          <span style={badgeStyle}>Held to {plan.settings.weekly_hours_cap} h a week</span>
        )}
        {plan.baseline.limited_history && (
          <span style={badgeStyle}>Limited history, so the first weeks use a conservative start.</span>
        )}
      </div>
      </>
      )}
    </Card>
  );
}

const KIND_LABEL: Record<string, string> = {
  loading: "loading week",
  recovery: "recovery week",
  return: "return week",
  taper: "taper week",
  race: "race week",
  sub_taper: "mini-taper week",
  sub_race: "sub event week",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// A plan week's first day as a LOCAL date: new Date("2026-10-26") is UTC
// midnight and lands on the day before west of UTC.
function localDay(iso: string, plusDays = 0): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d + plusDays);
}

// The open week: its numbers, and the athlete's own changes to it. Every
// change pins the week -- syncs never move it again -- and the later weeks
// re-flow; warnings say when a change breaks the 10% cap or the 3 + 1 rhythm.
// A past week, and the taper, are read-only here.
function WeekPanel({
  week,
  plan,
  editable,
  onClose,
  onChanged,
  onAskCoach,
}: {
  week: SeasonWeek;
  plan: SeasonPlanT;
  editable: boolean;
  onClose: () => void;
  onChanged: () => void;
  onAskCoach: (a: CoachAttachment) => void;
}) {
  const [note, setNote] = useState(week.pin_note ?? "");
  const [exact, setExact] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [splitDraft, setSplitDraft] = useState<SplitDraft | null>(week.split ? draftOf(week.split) : null);

  // Another week starts clean; warnings belong to the edit that raised them.
  useEffect(() => {
    setNote(week.pin_note ?? "");
    setExact("");
    setError(null);
  }, [week.week_start, week.pin_note]);
  const splitKey = week.split ? `${week.split.swim}/${week.split.bike}/${week.split.run}` : "";
  useEffect(() => setSplitDraft(week.split ? draftOf(week.split) : null), [week.week_start, splitKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setWarnings([]), [week.week_start]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const past = week.status === "past";
  const taper = week.kind === "taper" || week.kind === "race";
  const canEdit = editable && !past && !taper;
  const exactNum = exact.trim() ? Number(exact) : null;
  const exactOk = exactNum != null && exactNum >= 0 && exactNum <= 3000;

  async function act(action: SeasonWeekAction, value?: number | null) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.editSeasonWeek(week.week_start, action, value, note.trim() || null);
      setWarnings(res.warnings ?? []);
      setExact("");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveSplit(action: SeasonSplitAction) {
    const s = splitDraft ? parseSplit(splitDraft) : null;
    if (action === "set_split" && !s) return;
    setBusy(true);
    setError(null);
    try {
      await api.editSeasonSplit(week.week_start, action, s ?? undefined);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function askCoach() {
    onAskCoach({
      kind: "season_week",
      week,
      weeksInPlan: plan.weeks.length,
      date: week.week_start,
      dayName: localDay(week.week_start).toLocaleDateString(undefined, { weekday: "long" }),
    });
  }

  const end = localDay(week.week_start, 6);

  return (
    <div style={{ margin: "4px 13px 6px", background: C.inner, border: `1px solid ${C.cardBorder}`, borderRadius: 3, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: C.text }}>
          Week {week.index}, {dayMon(week.week_start)} to {end.getDate()} {MONTHS[end.getMonth()]}
        </span>
        <span style={{ fontSize: 11, color: C.textChip }}>
          {week.phase.charAt(0).toUpperCase() + week.phase.slice(1)} phase, {KIND_LABEL[week.kind] ?? week.kind}
        </span>
        {week.pinned && <span style={tagStyle}>Pinned{week.pin_note ? `: ${week.pin_note}` : ""}</span>}
        <span onClick={onClose} title="Close (Esc)" style={{ marginLeft: "auto", cursor: "pointer", fontSize: 13, color: C.muted, lineHeight: 1 }}>✕</span>
      </div>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 11.5, color: C.textSoft }}>
        <span>Target {Math.round(week.target_tss)} TSS / {week.target_hours.toFixed(1)} h</span>
        {week.actual_tss != null && (
          <span>
            Done {Math.round(week.actual_tss)} TSS / {(week.actual_hours ?? 0).toFixed(1)} h
          </span>
        )}
        {week.ctl_end != null && <span>CTL at week end {week.ctl_end.toFixed(1)}</span>}
        {week.focus && <span style={{ color: C.muted3 }}>{week.focus}</span>}
      </div>
      {week.split && week.discipline_tss && (
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 11.5, color: C.textSoft }}>
          {SPLIT_DISCIPLINES.map((d) => {
            const done = week.actual_by_discipline?.[d];
            return (
              <span key={d} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: DISCIPLINE_COLOR[d] }} />
                {d.charAt(0).toUpperCase() + d.slice(1)} {week.split![d]}%: {week.discipline_tss![d]} TSS / {(week.discipline_hours?.[d] ?? 0).toFixed(1)} h
                {done ? `, done ${Math.round(done.tss)}` : ""}
              </span>
            );
          })}
          {week.actual_by_discipline?.strength && (
            <span style={{ color: C.muted3 }}>Strength done {Math.round(week.actual_by_discipline.strength.tss)} TSS</span>
          )}
        </div>
      )}
      {week.split && splitDraft && editable && !past && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <SplitInputs label="Split" value={splitDraft} onChange={setSplitDraft} inputStyle={smallInput} />
          <SmallBtn
            disabled={busy || !parseSplit(splitDraft) || sameSplit(parseSplit(splitDraft)!, week.split)}
            onClick={() => saveSplit("set_split")}
          >
            Save split
          </SmallBtn>
          {week.split_source === "user" && (
            <SmallBtn disabled={busy} onClick={() => saveSplit("reset_split")}>Use the table</SmallBtn>
          )}
          <span style={{ fontSize: 10.5, color: C.muted2 }}>
            {week.split_source === "user" ? "Set by hand: kept when the plan changes." : "From the plan's table for this phase."}
          </span>
        </div>
      )}

      {canEdit ? (
        <>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <SmallBtn disabled={busy} onClick={() => act("lighter")}>Lighter (-10%)</SmallBtn>
            <SmallBtn disabled={busy} onClick={() => act("heavier")}>Heavier (+10%)</SmallBtn>
            {week.kind !== "recovery" && (
              <SmallBtn disabled={busy} onClick={() => act("make_recovery")}>Make recovery</SmallBtn>
            )}
            <SmallBtn disabled={busy} onClick={() => act("skip")}>Skip week</SmallBtn>
            <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <input
                type="number"
                min={0}
                max={3000}
                value={exact}
                placeholder="TSS"
                aria-label="Exact TSS for this week"
                onChange={(e) => setExact(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && exactOk && !busy) act("set_target", exactNum);
                }}
                style={{ ...smallInput, width: 70 }}
              />
              <SmallBtn disabled={busy || !exactOk} onClick={() => act("set_target", exactNum)}>
                Set exact TSS
              </SmallBtn>
            </span>
            {week.pinned && (
              <SmallBtn disabled={busy} onClick={() => act("unpin")}>Unpin</SmallBtn>
            )}
            <SmallBtn onClick={askCoach}>Ask coach</SmallBtn>
          </div>
          <input
            value={note}
            maxLength={300}
            placeholder="Why? (travel, illness, busy week)"
            aria-label="Why this change"
            onChange={(e) => setNote(e.target.value)}
            style={{ ...smallInput, width: "100%", maxWidth: 520 }}
          />
          <span style={{ fontSize: 10.5, color: C.muted2 }}>
            A change pins the week: syncs will not move it, and the weeks after it re-flow. The 7-day plan follows at the next Sync or Re-sync plan.
          </span>
        </>
      ) : (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: C.muted3 }}>
            {past
              ? "This week is over."
              : taper
                ? "The taper and race week follow the race date; change the event to move them."
                : "The plan is not active."}
          </span>
          <SmallBtn onClick={askCoach}>Ask coach</SmallBtn>
        </div>
      )}
      {warnings.map((w) => (
        <span key={w} style={{ fontSize: 11, color: C.fat }}>
          {w}
        </span>
      ))}
      {error && <span style={{ fontSize: 11, color: C.red }}>{error}</span>}
    </div>
  );
}

const RAMP_LABELS: { v: SeasonRamp; label: string }[] = [
  { v: "gentle", label: "Gentle (+5%)" },
  { v: "standard", label: "Standard (+8%)" },
  { v: "ambitious", label: "Ambitious (+10%)" },
];

// The plan's own settings, changeable after creation. The weeks from next week
// on re-flow; this week, pinned weeks and the taper stay as they are.
function SettingsRow({ plan, onDone, onChanged }: { plan: SeasonPlanT; onDone: () => void; onChanged: () => void }) {
  const [hours, setHours] = useState(plan.settings.weekly_hours_cap != null ? String(plan.settings.weekly_hours_cap) : "");
  const [ramp, setRamp] = useState<SeasonRamp>(plan.settings.ramp ?? "standard");
  const [focus, setFocus] = useState<SeasonFocus>(plan.settings.focus ?? "triathlon");
  const fitness = plan.mode === "fitness";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hoursNum = hours.trim() ? Number(hours) : null;
  const badHours = hoursNum != null && !(hoursNum > 0 && hoursNum <= 40);
  // The plan's split table (triathlon only), from the weeks still on it.
  const table = tableOf(plan.weeks, fitness);
  const hasSplit = Object.keys(table).length > 0;
  const [splitDrafts, setSplitDrafts] = useState<Record<string, SplitDraft>>({});
  const split = draftedRows(splitDrafts, table);
  const invalid = badHours || split.invalid;
  const SOURCE_LABEL: Record<string, string> = {
    weekly_hours_cap: "weekly hours",
    ramp: "build-up pace",
    recovery_cadence_weeks: `a recovery week after every ${plan.settings.recovery_cadence_weeks} loading weeks`,
    peak_ctl: "peak fitness",
  };
  const fromInstr = Object.entries(plan.settings_source ?? {})
    .filter(([, src]) => src === "instructions")
    .map(([k]) => SOURCE_LABEL[k] ?? k);

  async function save() {
    if (invalid) return;
    const changes: { weekly_hours_cap?: number | null; ramp?: SeasonRamp; focus?: SeasonFocus } = {};
    if (hoursNum !== (plan.settings.weekly_hours_cap ?? null)) changes.weekly_hours_cap = hoursNum;
    if (!fitness && ramp !== plan.settings.ramp) changes.ramp = ramp;
    if (fitness && focus !== plan.settings.focus) changes.focus = focus;
    const splitChanged = Object.keys(split.rows).length > 0;
    if (!Object.keys(changes).length && !splitChanged) return onDone();
    setBusy(true);
    setError(null);
    try {
      // The split table goes on its own: it re-splits weeks, nothing re-flows.
      if (splitChanged) await api.updateSeasonSettings({ split_by_phase: split.rows });
      if (Object.keys(changes).length) await api.updateSeasonSettings(changes);
      onChanged();
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ margin: "8px 13px 0", background: C.inner, border: `1px solid ${C.cardBorder}`, borderRadius: 3, padding: "10px 12px", display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: C.textChip }}>
        Weekly hours available
        <input type="number" min={1} max={40} step={0.5} value={hours} placeholder="no limit" onChange={(e) => setHours(e.target.value)} style={{ ...smallInput, width: 80 }} />
      </label>
      {fitness ? (
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: C.textChip }}>
          Focus
          {(["triathlon", "run", "bike", "swim"] as const).map((f) => (
            <SmallBtn key={f} active={focus === f} onClick={() => setFocus(f)}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </SmallBtn>
          ))}
        </span>
      ) : (
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: C.textChip }}>
          Build-up pace
          {RAMP_LABELS.map((r) => (
            <SmallBtn key={r.v} active={ramp === r.v} onClick={() => setRamp(r.v)}>
              {r.label}
            </SmallBtn>
          ))}
        </span>
      )}
      {plan.instructions_view?.used && (
        <span style={{ width: "100%", fontSize: 10.5, color: C.muted2 }}>
          From your coach instructions: {plan.instructions_view.summary || "nothing changes the season plan"}
          {fromInstr.length > 0 && ` (${fromInstr.join(", ")})`}. Saving new instructions in Settings updates the plan from next week; a value you set here stays yours.
        </span>
      )}
      {hasSplit && (
        <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 11, color: C.textChip }}>Swim / bike / run split of each week's TSS</span>
          <SplitTable rows={table} drafts={splitDrafts} onChange={setSplitDrafts} inputStyle={smallInput} />
        </div>
      )}
      <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
        <SmallBtn onClick={onDone} disabled={busy}>Cancel</SmallBtn>
        <SmallBtn primary onClick={save} disabled={busy || invalid}>
          {busy ? "Saving..." : "Save"}
        </SmallBtn>
      </span>
      <span style={{ width: "100%", fontSize: 10.5, color: invalid || error ? C.red : C.muted2 }}>
        {error ??
          (badHours
            ? "Hours must be between 0 and 40."
            : split.invalid
              ? "Each split must be whole percents adding up to 100."
            : fitness
              ? "The weeks already chosen stay; the new hours and focus apply from the next check-in."
              : "Weeks from next week on re-flow. This week, pinned weeks and the taper stay as they are." +
                (hasSplit ? " A new split applies to every week on the table; a week split by hand keeps its own." : ""))}
      </span>
    </div>
  );
}

function SmallBtn({ children, onClick, disabled, active, primary }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; active?: boolean; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      style={{
        cursor: disabled ? "default" : "pointer",
        fontSize: 11,
        fontWeight: 600,
        padding: "5px 10px",
        borderRadius: 4,
        border: `1px solid ${active ? C.form : C.cardBorder}`,
        background: primary ? C.form : active ? C.inner2 : C.card,
        color: primary ? "#fff" : C.textSoft,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {children}
    </button>
  );
}

const smallInput: React.CSSProperties = {
  background: C.bar,
  border: `1px solid ${C.cardBorder}`,
  borderRadius: 4,
  padding: "5px 8px",
  fontSize: 11.5,
  color: C.text,
  outline: "none",
};

// One summary line, never wrapping: cut with an ellipsis, full text on hover.
// The plan's sub events, each with a Remove link, and an Add link listing the
// racing events before the main one that are not sub events yet.
function SubEventsRow({ plan, events, onChanged }: { plan: SeasonPlanT; events: RaceEvent[]; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const subs = plan.sub_events ?? [];
  const today = localISO(new Date());
  const race = plan.event?.race_date ?? "";
  const eligible = events
    .filter((e) => e.event_id !== plan.event?.event_id && !subs.some((s) => s.event_id === e.event_id))
    .filter((e) => (e.race_date || "") >= today && e.race_date < race)
    .sort((a, b) => a.race_date.localeCompare(b.race_date));
  if (!subs.length && !eligible.length) return null;

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setAdding(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: "2px 13px 4px", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, fontSize: 11, color: C.textChip }}>
      <span style={{ fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: C.muted3 }}>Sub events</span>
      {subs.length === 0 && <span style={{ color: C.muted3 }}>None</span>}
      {subs.map((s) => (
        <span key={s.event_id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: s.race_date < today ? C.muted3 : C.text }}>{s.name}</span>
          <span style={{ color: C.muted3 }}>{dayMon(s.race_date)}</span>
          {s.race_date >= today && (
            <LinkBtn danger disabled={busy} onClick={() => run(() => api.removeSeasonSubEvent(s.event_id))}>Remove</LinkBtn>
          )}
        </span>
      ))}
      {eligible.length > 0 && <LinkBtn active={adding} disabled={busy} onClick={() => setAdding((v) => !v)}>Add sub event</LinkBtn>}
      {adding &&
        eligible.map((e) => (
          <button key={e.event_id} disabled={busy} onClick={() => run(() => api.addSeasonSubEvent(e.event_id))} style={{ ...badgeStyle, cursor: busy ? "default" : "pointer" }}>
            {e.name} {dayMon(e.race_date)}
          </button>
        ))}
      {error && <span style={{ color: C.red }}>{error}</span>}
    </div>
  );
}

function SummaryLine({ text, color }: { text: string; color: string }) {
  return (
    <div title={text} style={{ fontSize: 12, color, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", lineHeight: 1.45 }}>
      {text}
    </div>
  );
}

function ChangesList({ plan }: { plan: SeasonPlanT }) {
  const revisions = [...plan.revisions].reverse();
  return (
    <div style={{ margin: "8px 13px 0", background: C.inner, border: `1px solid ${C.cardBorder}`, borderRadius: 3, padding: "6px 10px", display: "flex", flexDirection: "column", gap: 4, maxHeight: 140, overflowY: "auto" }}>
      {revisions.map((r, i) => (
        <div key={i} style={{ display: "flex", gap: 12, fontSize: 11, color: C.textChip }}>
          <span style={{ color: C.muted, minWidth: 48 }}>{dayMon(r.at.slice(0, 10))}</span>
          <span style={{ color: C.muted2, minWidth: 64 }}>{r.week_start ? `wk ${dayMon(r.week_start)}` : "plan"}</span>
          {r.from != null && r.to != null && (
            <span style={{ minWidth: 80 }}>
              {Math.round(r.from)} to {Math.round(r.to)}
            </span>
          )}
          <span>{r.reason_text}</span>
        </div>
      ))}
    </div>
  );
}

function LinkBtn({ children, onClick, active, danger, disabled }: { children: React.ReactNode; onClick: () => void; active?: boolean; danger?: boolean; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{ background: "none", border: "none", padding: 0, fontSize: 10, cursor: disabled ? "default" : "pointer", color: danger ? C.red : active ? C.text : C.muted3, textDecoration: active ? "underline" : "none", textUnderlineOffset: 3 }}
    >
      {children}
    </button>
  );
}

const primaryBtn: React.CSSProperties = {
  cursor: "pointer",
  fontSize: 12,
  fontWeight: 600,
  padding: "7px 14px",
  borderRadius: 6,
  border: "none",
  background: C.form,
  color: "#fff",
};

const tagStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 500,
  color: C.textChip,
  background: C.inner2,
  border: `1px solid ${C.cardBorder}`,
  borderRadius: 3,
  padding: "2px 7px",
};

const badgeStyle: React.CSSProperties = {
  fontSize: 10,
  color: C.textChip,
  background: C.inner2,
  border: `1px solid ${C.cardBorder}`,
  borderRadius: 3,
  padding: "2px 7px",
};
