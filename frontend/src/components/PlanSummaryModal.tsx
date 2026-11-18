import { C } from "../theme";
import { PHASE_ACCENT } from "../charts/PlanBars";
import { syncSummary, type SummaryBand, type SummaryDay, type SummaryRow, type SummarySlice } from "../derive";
import { useModalKeys } from "../useModalKeys";
import type { Plan, RaceEvent, SeasonPlan, WorkoutRow } from "../types";

const overline = { fontSize: 9, fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase", color: C.mutedBright } as const;
const dayGrid = { display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 4 } as const;

function DayColumn({ d, planned }: { d: SummaryDay; planned: boolean }) {
  const edge = d.isToday ? C.fit : d.open ? C.restCellBorder : "transparent";
  const value = d.open ? "Open" : d.sessions ? String(Math.round(d.tss)) : "Rest";
  return (
    <div style={{ boxSizing: "border-box", height: 146, padding: "3px 2px", display: "flex", flexDirection: "column", alignItems: "center", gap: 2, border: `1px ${d.open ? "dashed" : "solid"} ${edge}`, borderRadius: 3 }}>
      <span style={{ fontSize: 8, fontWeight: 700, letterSpacing: ".05em", height: 10, color: d.isToday ? C.fit : C.fat }}>
        {d.isToday ? "TODAY" : d.hard ? "HARD" : ""}
      </span>
      <div style={{ flexGrow: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center", gap: 4, width: "100%" }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: d.sessions ? C.text : C.mutedBright }}>{value}</span>
        {d.tss > 0 ? (
          // column-reverse: the first sport sits at the bottom of the stack.
          <div style={{ width: 30, height: d.barPx, display: "flex", flexDirection: "column-reverse", borderRadius: "2px 2px 0 0", overflow: "hidden", opacity: planned ? 0.75 : 1 }}>
            {d.parts.map((p) => (
              <div key={p.discipline} style={{ flexGrow: p.tss, flexBasis: 0, background: p.color }} />
            ))}
          </div>
        ) : (
          <div style={{ width: 30, height: 3, borderRadius: "2px 2px 0 0", background: C.restCellBorder }} />
        )}
      </div>
      <span style={{ fontSize: 10, fontWeight: 600, color: C.modalLabel }}>{d.weekday}</span>
      <span style={{ fontSize: 9, color: C.mutedBright }}>{d.dayOfMonth}</span>
    </div>
  );
}

function bandColor(phase: NonNullable<SummaryBand["phase"]>): string {
  if (phase === "race") return C.yellow;
  return PHASE_ACCENT[phase] ?? C.phaseAccentBase; // a fitness plan has no phases of its own
}

function PhaseBand({ band }: { band: SummaryBand }) {
  const fill = band.phase
    ? { background: bandColor(band.phase), color: C.pageBg }
    : { border: `1px dashed ${C.restCellBorder}`, color: C.mutedBright2 };
  return (
    <div style={{ gridColumn: `span ${band.span}`, boxSizing: "border-box", height: 22, padding: "0 7px", display: "flex", alignItems: "center", borderRadius: 2, fontSize: 10, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", ...fill }}>
      {band.label}
    </div>
  );
}

function Donut({ title, slices, center, centerLabel, empty }: { title: string; slices: SummarySlice[]; center: string; centerLabel: string; empty: string }) {
  let at = 0;
  return (
    <div style={{ boxSizing: "border-box", padding: "8px 10px", display: "flex", flexDirection: "column", gap: 6, justifyContent: "center", background: C.modalPanel, border: `1px solid ${C.modalControlBorder}`, borderRadius: 8 }}>
      <span style={overline}>{title}</span>
      {slices.length === 0 ? (
        <span style={{ fontSize: 11, lineHeight: 1.4, color: C.muted }}>{empty}</span>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <svg width={76} height={76} viewBox="0 0 76 76" role="img" aria-label={`${title}: ${slices.map((s) => `${s.label} ${s.value}`).join(", ")}`} style={{ flexShrink: 0 }}>
            {slices.map((s) => {
              const offset = -at;
              at += s.pct;
              return (
                <circle key={s.label} cx={38} cy={38} r={30} fill="none" stroke={s.color} strokeWidth={11} pathLength={100} strokeDasharray={`${Math.max(s.pct - 0.5, 0)} 100`} strokeDashoffset={offset} transform="rotate(-90 38 38)" />
              );
            })}
            <text x={38} y={39} textAnchor="middle" fontSize={14} fontWeight={700} fill={C.text}>{center}</text>
            <text x={38} y={49} textAnchor="middle" fontSize={8} fill={C.mutedBright2}>{centerLabel}</text>
          </svg>
          <div style={{ flexGrow: 1, display: "flex", flexDirection: "column", gap: 3, fontSize: 10 }}>
            {slices.map((s) => (
              <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0, background: s.color }} />
                <span style={{ flexGrow: 1, color: C.modalLabel }}>{s.label}</span>
                <span style={{ fontWeight: 700 }}>{s.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ row, planned }: { row: SummaryRow; planned: boolean }) {
  const barsLabel = row.days
    .filter((d) => d.tss > 0)
    .map((d) => `${d.weekday} ${Math.round(d.tss)}`)
    .join(", ");
  return (
    <section aria-label={row.title} style={{ boxSizing: "border-box", padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6, background: planned ? C.modalInner : C.pastCard, border: `1px solid ${planned ? C.modalControlBorder : C.pastBorder}`, borderRadius: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>
          {row.title} <span style={{ fontWeight: 400, color: C.mutedBright2 }}>· {row.range}</span>
        </h2>
        {row.status && <span style={{ fontSize: 10, color: C.mutedBright2 }}>{row.status}</span>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "136px minmax(0, 1fr) 176px 176px", gap: 14, alignItems: "stretch" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 7, justifyContent: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span style={overline}>TSS</span>
            <span style={{ fontSize: 30, fontWeight: 700, lineHeight: 1 }}>{row.tss}</span>
            <span style={{ fontSize: 10, color: C.mutedBright2 }}>{row.tssNote}</span>
          </div>
          {row.facts.map((f) => (
            <div key={f.k} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 11 }}>
              <span style={{ color: C.mutedBright2 }}>{f.k}</span>
              <span style={{ fontWeight: 700 }}>{f.v}</span>
            </div>
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
          <div role="img" aria-label={`${planned ? "Planned TSS" : "TSS"} per day: ${barsLabel || "none"}`} style={dayGrid}>
            {row.days.map((d) => (
              <DayColumn key={d.iso} d={d} planned={planned} />
            ))}
          </div>
          <div style={dayGrid}>
            {row.bands.map((b, i) => (
              <PhaseBand key={i} band={b} />
            ))}
          </div>
        </div>

        <Donut title="Load by Sport" slices={row.load} center={String(row.tss)} centerLabel="TSS" empty={planned ? "No load planned in these days." : "No load logged in these days."} />
        <Donut
          title={planned ? "Planned Time in Zone" : "Time in HR Zone"}
          slices={row.zones}
          center={`${row.easyPct}%`}
          centerLabel="easy"
          empty={planned ? "No planned zones in these days." : "No HR-zone data. Run a Garmin sync to populate."}
        />
      </div>
    </section>
  );
}

// Shown once after a sync in which the AI coach generated a fresh 7-day plan:
// the last 7 days over the next 7. The charts carry the numbers, so the note
// under them does not repeat them and plan.summary is not shown here.
export function PlanSummaryModal({ plan, workouts, seasonPlan, primaryEvent, tsb, onClose }: {
  plan: Plan;
  workouts: WorkoutRow[];
  seasonPlan: SeasonPlan | null;
  primaryEvent: RaceEvent | null;
  tsb: number;
  onClose: () => void;
}) {
  // View-only popup: Esc (or the buttons/backdrop) dismisses it.
  useModalKeys(onClose);

  const { last, next, note } = syncSummary(workouts, plan, seasonPlan, primaryEvent, tsb);

  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: 24 }}
    >
      <div
        role="dialog"
        aria-label="Coach, sync summary"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 1000, maxWidth: "100%", maxHeight: "90vh", display: "flex", flexDirection: "column", overflow: "hidden", color: C.text, background: C.modalPanel, border: `1px solid ${C.modalBorder}`, borderTop: `4px solid ${C.form}`, borderRadius: 12, boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 26px", borderBottom: `1px solid ${C.modalBorder}` }}>
          <h1 style={{ margin: 0, fontSize: 19, fontWeight: 700, lineHeight: 1.2 }}>Coach — sync summary</h1>
          <button aria-label="Close" onClick={onClose} style={{ width: 44, height: 44, marginRight: -10, border: "none", background: "transparent", color: C.modalLabel, fontSize: 18, cursor: "pointer" }}>✕</button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 26px", overflowY: "auto", minHeight: 0 }}>
          <Row row={last} planned={false} />
          <Row row={next} planned />
          {plan.load_reduction?.message && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 2 }}>
              <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>Lighter week</h2>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: C.textSoft }}>{plan.load_reduction.message}</p>
            </div>
          )}
          {note && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 2 }}>
              <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>Coach Notes</h2>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: C.textSoft }}>{note}</p>
            </div>
          )}
        </div>

        <div style={{ display: "flex", justifyContent: "center", padding: "10px 26px", borderTop: `1px solid ${C.modalBorder}` }}>
          <button onClick={onClose} style={{ height: 44, padding: "0 20px", border: "none", borderRadius: 8, background: C.form, color: C.pageBg, fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
