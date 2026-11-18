// Season plan weekly-load bars: one bar per plan week, its height the week's
// target TSS, filled by phase (event plans) or kind (fitness plans). Same
// margins, gridlines and tooltip box as StackedBars / LineChart.
//
// A triathlon plan's weeks carry a swim / bike / run split: each bar is then
// stacked in the discipline colours (swim at the bottom), the actual inside it
// too (strength included), and the phase is read off the brackets under the
// axis and the tooltip instead of the fill. A key sits top right.
//
// States, none of them colour alone:
// - recovery / return weeks are hatched;
// - past and current weeks draw the target as an outline with the actual TSS
//   as a narrower bright bar inside it, the part over 115% of target in red;
// - the next week has a dashed outline, a fitness projection a dotted one;
// - the race week is labelled RACE above its bar; today is a vertical line;
// - the phases are named by brackets under the axis, in their own accents;
// - a pinned week has a short bar above it, a revised one a dot (the tooltip
//   says why), and a week the fatigue gate changed an orange tick under the axis.
import { useState, type KeyboardEvent } from "react";
import { C, DISCIPLINE_COLOR } from "../theme";
import { useChartWidth } from "./useChartWidth";
import type { SeasonRevision, SeasonWeek } from "../types";

export const PHASE_COLOR: Record<string, string> = {
  base: C.phaseBase,
  build: C.phaseBuild,
  peak: C.phasePeak,
  taper: C.phaseTaper,
  race: C.phaseTaper,
  fitness: C.phaseBuild,
};

// The phase brackets under the axis: bright accents of their own (see theme.ts).
export const PHASE_ACCENT: Record<string, string> = {
  base: C.phaseAccentBase,
  build: C.phaseAccentBuild,
  peak: C.phaseAccentPeak,
  taper: C.phaseAccentTaper,
};

const OVER_FACTOR = 1.15;
// Bottom to top: the race order.
export const SPLIT_DISCIPLINES = ["swim", "bike", "run"] as const;
const ACTUAL_DISCIPLINES = ["swim", "bike", "run", "strength"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "7 Dec" from an ISO date, read off the string: new Date("2026-12-07") is UTC
// midnight and shows the day before west of UTC.
export function dayMon(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}

export function barColor(w: SeasonWeek, mode: "event" | "fitness"): string {
  if (mode === "fitness") return w.kind === "loading" ? C.phaseBuild : C.phaseBase;
  return PHASE_COLOR[w.phase] ?? C.phaseBuild;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function tipLines(w: SeasonWeek, revision?: SeasonRevision): string[] {
  const kind = w.kind === "loading" || w.kind === w.phase || w.sub_event ? "" : ` (${w.kind})`;
  const lines = [
    `Week ${w.index}, ${dayMon(w.week_start)}; ${cap(w.phase)}${kind}`,
    `Target ${Math.round(w.target_tss)} TSS / ${w.target_hours.toFixed(1)} h`,
  ];
  if (w.actual_tss != null) lines.push(`Actual ${Math.round(w.actual_tss)} / ${(w.actual_hours ?? 0).toFixed(1)} h`);
  if (w.discipline_tss && w.split) {
    for (const d of SPLIT_DISCIPLINES) {
      const done = w.actual_by_discipline?.[d];
      lines.push(
        `${cap(d)} ${w.split[d]}%: ${w.discipline_tss[d]} TSS / ${(w.discipline_hours?.[d] ?? 0).toFixed(1)} h` +
          (done ? `, done ${Math.round(done.tss)}` : w.actual_tss != null ? ", done 0" : ""),
      );
    }
    const strength = w.actual_by_discipline?.strength;
    if (strength) lines.push(`Strength done ${Math.round(strength.tss)} TSS`);
    if (w.split_source === "user") lines.push("Split set by hand");
  }
  if (w.sub_event) {
    const when = `${w.sub_event.name}, ${dayMon(w.sub_event.race_date)}`;
    lines.push(
      w.sub_event.role === "race" ? `Sub event: ${when}` : w.sub_event.role === "pre" ? `Mini-taper for ${when}` : `Recovery after ${when}`,
    );
  }
  if (w.ctl_end != null) lines.push(`CTL end ${w.ctl_end.toFixed(1)}`);
  if (w.focus) lines.push(w.focus);
  if (w.pinned) lines.push(w.pin_note ? `Pinned: ${w.pin_note}` : "Pinned");
  if (revision) lines.push(`Changed: ${revision.reason_text}`);
  return lines;
}

// Runs of consecutive weeks in one phase, for the brackets under the axis. The
// taper and race week read as one TAPER bracket.
function phaseRuns(weeks: SeasonWeek[]) {
  const runs: { phase: string; from: number; to: number }[] = [];
  weeks.forEach((w, i) => {
    const phase = w.phase === "race" ? "taper" : w.phase;
    const last = runs[runs.length - 1];
    if (last && last.phase === phase) last.to = i;
    else runs.push({ phase, from: i, to: i });
  });
  return runs;
}

interface Props {
  weeks: SeasonWeek[];
  mode: "event" | "fitness";
  h?: number;
  showCtl?: boolean;
  // The plan's change log: marks the weeks it changed.
  revisions?: SeasonRevision[];
  // The week open in the week panel, outlined in white.
  selected?: string | null;
  // Click / Enter on a bar.
  onSelect?: (index: number) => void;
}

export function PlanBars({ weeks, mode, h = 200, showCtl = false, revisions = [], selected = null, onSelect }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const [focusI, setFocusI] = useState<number | null>(null);
  const { ref, w } = useChartWidth(h, 900);

  const brackets = mode === "event";
  const padL = 36,
    padR = showCtl ? 30 : 12,
    padT = 16,
    padB = brackets ? 34 : 22;
  const iw = w - padL - padR,
    ih = h - padT - padB;
  const n = weeks.length;
  const slot = iw / Math.max(n, 1);
  const bw = Math.max(3, Math.min(slot * 0.62, 34));

  const peak = Math.max(1, ...weeks.map((wk) => Math.max(wk.target_tss, wk.actual_tss ?? 0)));
  const step = niceStep(peak / 3);
  const vmax = Math.ceil((peak * 1.08) / step) * step;
  const yAt = (v: number) => padT + ih - (v / vmax) * ih;
  const cx = (i: number) => padL + (i + 0.5) * slot;

  const ctls = weeks.map((wk) => wk.ctl_end ?? 0);
  const ctlMax = Math.max(1, ...ctls) * 1.1;
  const ctlY = (v: number) => padT + ih - (v / ctlMax) * ih;

  // Label every Nth week so "17 Dec" labels never collide.
  const labelEvery = Math.max(1, Math.ceil(36 / slot));
  const current = weeks.findIndex((wk) => wk.status === "current");
  // The latest change to each week's own target (plan-level rows have no week).
  const revised = new Map<string, SeasonRevision>();
  const fatigued = new Set<string>();
  for (const r of revisions) {
    if (!r.week_start || r.field !== "target_tss") continue;
    revised.set(r.week_start, r);
    if (r.reason_code === "FATIGUE_GATE") fatigued.add(r.week_start);
  }
  const active = hover ?? focusI;
  const split = weeks.some((wk) => wk.discipline_tss);

  // Rects stacked from the baseline, one per discipline, never above `upTo`.
  function stack(x: number, width: number, parts: [string, number][], upTo: number, opacity: number, key: string) {
    let below = 0;
    return parts.map(([d, v]) => {
      const from = Math.min(below, upTo);
      below += v;
      const to = Math.min(below, upTo);
      if (to <= from) return null;
      return <rect key={`${key}${d}`} x={x} y={yAt(to)} width={width} height={yAt(from) - yAt(to)} fill={DISCIPLINE_COLOR[d] ?? C.muted} fillOpacity={opacity} />;
    });
  }

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (!n) return;
    const i = focusI ?? (current >= 0 ? current : 0);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      setHover(null); // the keyboard takes over from a stale mouse hover
      setFocusI(Math.min(n - 1, Math.max(0, i + (e.key === "ArrowRight" ? 1 : -1))));
    } else if (e.key === "Enter" && onSelect) {
      e.preventDefault();
      onSelect(i);
    } else if (e.key === "Escape") {
      setFocusI(null);
    }
  }

  const grid = [];
  for (let v = 0; v <= vmax + 0.5; v += step) {
    const y = yAt(v);
    grid.push(
      <line key={`g${v}`} x1={padL} x2={w - padR} y1={y} y2={y} stroke={C.gridline} />,
      <text key={`gt${v}`} x={padL - 6} y={y + 3} fontSize={9} fill={C.muted} textAnchor="end">
        {Math.round(v)}
      </text>,
    );
  }

  let tip = null;
  if (active != null && weeks[active]) {
    const lines = tipLines(weeks[active], revised.get(weeks[active].week_start));
    const boxW = Math.max(...lines.map((l) => l.length)) * 5.1 + 18;
    const boxH = 9 + lines.length * 12;
    const hx = cx(active);
    const bx = hx + 12 + boxW > w - 2 ? hx - 12 - boxW : hx + 12;
    tip = (
      <g pointerEvents="none">
        <rect x={bx} y={padT} width={boxW} height={boxH} rx={4} fill={C.bar} stroke={C.cardBorder} />
        {lines.map((l, k) => (
          <text key={k} x={bx + 8} y={padT + 14 + k * 12} fontSize={9} fontWeight={k === 0 ? 700 : 400} fill={k === 0 ? C.text : C.textChip}>
            {l}
          </text>
        ))}
      </g>
    );
  }

  return (
    <div
      ref={ref}
      tabIndex={0}
      role="group"
      aria-label="Season plan weekly load. Arrow keys move between weeks."
      onKeyDown={onKey}
      onBlur={() => setFocusI(null)}
      style={{ width: "100%", height: "100%", outline: "none" }}
      className="plan-bars"
    >
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="100%" preserveAspectRatio="xMidYMid meet" style={{ display: "block" }} onMouseLeave={() => setHover(null)} aria-hidden="true">
        <defs>
          <pattern id="plan-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="5" stroke={C.card} strokeWidth="2.2" />
          </pattern>
        </defs>
        {grid}

        {weeks.map((wk, i) => {
          const x = cx(i) - bw / 2;
          const color = barColor(wk, mode);
          const yT = yAt(wk.target_tss);
          const hT = Math.max(0, padT + ih - yT);
          const done = wk.status === "past" || wk.status === "current";
          const pending = wk.status === "next" || wk.status === "pending_checkin";
          const projection = mode === "fitness" && wk.status === "projected";
          const hatched = wk.kind === "recovery" || wk.kind === "return" || wk.kind === "sub_taper";
          const dim = active != null && active !== i ? 0.55 : 1;
          const dash = projection ? "1.5 2" : pending ? "3 2" : undefined;
          const outlineOnly = done || pending || projection;
          const actual = wk.actual_tss ?? 0;
          const overFrom = wk.target_tss * OVER_FACTOR;
          return (
            <g key={wk.week_start} onMouseEnter={() => setHover(i)} onClick={() => onSelect?.(i)} style={{ cursor: onSelect ? "pointer" : "default" }}>
              <rect x={cx(i) - slot / 2} y={padT} width={slot} height={ih} fill="transparent" />
              {/* The dimming is the bar's only: a projection's date label stays readable. */}
              <g opacity={(projection ? 0.4 : 1) * dim}>
              {wk.discipline_tss && stack(x, bw, SPLIT_DISCIPLINES.map((d) => [d, wk.discipline_tss![d] ?? 0]), wk.target_tss, outlineOnly ? 0.28 : 0.85, "t")}
              <rect
                x={x}
                y={yT}
                width={bw}
                height={hT}
                rx={1}
                fill={wk.discipline_tss ? "none" : color}
                fillOpacity={outlineOnly ? 0.28 : 1}
                stroke={wk.week_start === selected ? C.white : C.phaseEdge}
                strokeOpacity={wk.week_start === selected ? 1 : outlineOnly ? 0.9 : 0.55}
                strokeWidth={wk.week_start === selected ? 1.5 : 0.8}
                strokeDasharray={dash}
              />
              {hatched && <rect x={x} y={yT} width={bw} height={hT} fill="url(#plan-hatch)" />}
              {done && actual > 0 && (
                <>
                  {wk.discipline_tss && wk.actual_by_discipline ? (
                    stack(x + bw * 0.2, bw * 0.6, ACTUAL_DISCIPLINES.map((d) => [d, wk.actual_by_discipline?.[d]?.tss ?? 0]), Math.min(actual, overFrom), 1, "a")
                  ) : (
                    <rect x={x + bw * 0.2} y={yAt(Math.min(actual, overFrom))} width={bw * 0.6} height={Math.max(0, padT + ih - yAt(Math.min(actual, overFrom)))} rx={1} fill={C.textSoft} fillOpacity={0.85} />
                  )}
                  {actual > overFrom && (
                    <rect x={x + bw * 0.2} y={yAt(actual)} width={bw * 0.6} height={yAt(overFrom) - yAt(actual)} rx={1} fill={C.red} />
                  )}
                </>
              )}
              {wk.pinned && <rect x={cx(i) - 4} y={yT - 6} width={8} height={2} rx={1} fill={C.textSoft} />}
              {revised.has(wk.week_start) && <circle cx={cx(i) + bw / 2 - 2} cy={yT - 5} r={2} fill={C.text} />}
              {fatigued.has(wk.week_start) && <rect x={cx(i) - 4} y={padT + ih + 1} width={8} height={2.5} rx={1} fill={C.fat} />}
              {(wk.kind === "race" || wk.sub_event?.role === "race") && (
                <text x={cx(i)} y={yT - (wk.pinned ? 9 : 4)} fontSize={8} fontWeight={700} fill={wk.kind === "race" ? C.textSoft : C.muted3} textAnchor="middle" letterSpacing=".06em">
                  {wk.kind === "race" ? "RACE" : "SUB"}
                </text>
              )}
              </g>
              {(i % labelEvery === 0 || i === current) && (
                <text x={cx(i)} y={padT + ih + 12} fontSize={9} fill={i === current ? C.text : C.muted} fontWeight={i === current ? 700 : 400} textAnchor="middle">
                  {dayMon(wk.week_start)}
                </text>
              )}
            </g>
          );
        })}

        {current >= 0 && (
          <line x1={cx(current) - slot / 2} x2={cx(current) - slot / 2} y1={padT - 4} y2={padT + ih} stroke={C.text} strokeOpacity={0.35} strokeDasharray="2 2" pointerEvents="none" />
        )}

        {brackets &&
          phaseRuns(weeks).map((r) => {
            const x1 = cx(r.from) - slot / 2 + 2;
            const x2 = cx(r.to) + slot / 2 - 2;
            const label = r.phase.toUpperCase();
            const y = padT + ih + 20;
            return (
              <g key={`b${r.from}`} pointerEvents="none">
                <line x1={x1} x2={x2} y1={y} y2={y} stroke={PHASE_ACCENT[r.phase] ?? C.muted} strokeWidth={3} strokeLinecap="round" />
                {x2 - x1 > label.length * 5.6 && (
                  <text x={(x1 + x2) / 2} y={y + 11} fontSize={8} fontWeight={700} fill={PHASE_ACCENT[r.phase] ?? C.muted3} textAnchor="middle" letterSpacing=".06em">
                    {label}
                  </text>
                )}
              </g>
            );
          })}

        {showCtl && n > 1 && (
          <g pointerEvents="none">
            <polyline points={weeks.map((_, i) => `${cx(i).toFixed(1)},${ctlY(ctls[i]).toFixed(1)}`).join(" ")} fill="none" stroke={C.ctl} strokeWidth={1.5} />
            {[0.5, 1].map((f) => (
              <text key={f} x={w - padR + 4} y={ctlY((ctlMax / 1.1) * f) + 3} fontSize={9} fill={C.ctl}>
                {Math.round((ctlMax / 1.1) * f)}
              </text>
            ))}
          </g>
        )}

        {split && (
          <g pointerEvents="none">
            {SPLIT_DISCIPLINES.map((d, k) => {
              const x0 = w - padR - (SPLIT_DISCIPLINES.length - k) * 40;
              return (
                <g key={d}>
                  <rect x={x0} y={3} width={7} height={7} rx={1} fill={DISCIPLINE_COLOR[d]} />
                  <text x={x0 + 10} y={10} fontSize={9} fill={C.textChip}>
                    {cap(d)}
                  </text>
                </g>
              );
            })}
          </g>
        )}

        {tip}
      </svg>

      {/* The chart's text alternative. */}
      <table style={visuallyHidden}>
        <caption>Season plan, weekly load</caption>
        <thead>
          <tr>
            <th>Week</th>
            <th>Phase</th>
            <th>Kind</th>
            <th>Target TSS</th>
            <th>Actual TSS</th>
            {split && SPLIT_DISCIPLINES.map((d) => <th key={d}>{cap(d)} TSS, target and done</th>)}
          </tr>
        </thead>
        <tbody>
          {weeks.map((wk) => (
            <tr key={wk.week_start}>
              <td>{dayMon(wk.week_start)}</td>
              <td>{wk.phase}</td>
              <td>{wk.kind}</td>
              <td>{Math.round(wk.target_tss)}</td>
              <td>{wk.actual_tss == null ? "" : Math.round(wk.actual_tss)}</td>
              {split &&
                SPLIT_DISCIPLINES.map((d) => (
                  <td key={d}>
                    {wk.discipline_tss ? `${wk.discipline_tss[d]} (${wk.split?.[d]}%)` : ""}
                    {wk.actual_by_discipline ? `, done ${Math.round(wk.actual_by_discipline[d]?.tss ?? 0)}` : ""}
                  </td>
                ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 1, 2 or 5 times a power of ten, at least `raw`.
function niceStep(raw: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

const visuallyHidden: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};
