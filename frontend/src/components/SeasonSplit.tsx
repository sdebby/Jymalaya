// The swim / bike / run split editors of a triathlon season plan: a row of
// three whole percents of the week's TSS (a week's own split, in the week
// panel) and the plan's table, one such row per phase (the create modal and
// the Settings row). The backend refuses anything that does not add up to
// 100 (app/season/split.py); the editors say so before it is sent.
import type { CSSProperties } from "react";
import { SPLIT_DISCIPLINES } from "../charts/PlanBars";
import { C, DISCIPLINE_COLOR } from "../theme";
import type { SeasonSplit, SeasonWeek, SplitDiscipline } from "../types";

export type SplitDraft = Record<SplitDiscipline, string>;

export const draftOf = (s: SeasonSplit): SplitDraft => ({ swim: String(s.swim), bike: String(s.bike), run: String(s.run) });

function draftSum(d: SplitDraft): number {
  return SPLIT_DISCIPLINES.reduce((sum, k) => sum + (Number(d[k]) || 0), 0);
}

// The draft as a split, or null unless it is three whole percents adding up to 100.
export function parseSplit(d: SplitDraft): SeasonSplit | null {
  const out = {} as SeasonSplit;
  for (const k of SPLIT_DISCIPLINES) {
    const v = d[k].trim() === "" ? NaN : Number(d[k]);
    if (!Number.isInteger(v) || v < 0 || v > 100) return null;
    out[k] = v;
  }
  return out.swim + out.bike + out.run === 100 ? out : null;
}

export const sameSplit = (a: SeasonSplit, b: SeasonSplit) => SPLIT_DISCIPLINES.every((k) => a[k] === b[k]);

const PHASE_ORDER = ["base", "build", "peak", "taper", "fitness"];
const PHASE_LABEL: Record<string, string> = {
  base: "Base",
  build: "Build",
  peak: "Peak",
  taper: "Taper",
  fitness: "Every week",
};

// The table a plan's weeks follow, per phase (a race week reads as taper),
// from the weeks still on it.
export function tableOf(weeks: SeasonWeek[], fitness: boolean): Record<string, SeasonSplit> {
  const out: Record<string, SeasonSplit> = {};
  for (const w of weeks) {
    if (!w.split || w.split_source !== "table") continue;
    const key = fitness ? "fitness" : w.phase === "race" ? "taper" : w.phase;
    out[key] ??= w.split;
  }
  return out;
}

export function SplitInputs({
  value,
  onChange,
  label,
  inputStyle,
  labelColor = C.textChip,
}: {
  value: SplitDraft;
  onChange: (v: SplitDraft) => void;
  label?: string;
  inputStyle: CSSProperties;
  labelColor?: string;
}) {
  const sum = draftSum(value);
  return (
    <span style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      {label && <span style={{ fontSize: 11.5, color: labelColor, minWidth: 92 }}>{label}</span>}
      {SPLIT_DISCIPLINES.map((d) => (
        <label key={d} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: labelColor }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: DISCIPLINE_COLOR[d], flex: "none" }} />
          {d.charAt(0).toUpperCase() + d.slice(1)}
          <input
            type="number"
            min={0}
            max={100}
            step={1}
            value={value[d]}
            aria-label={`${label ? `${label}: ` : ""}${d} percent`}
            onChange={(e) => onChange({ ...value, [d]: e.target.value })}
            style={{ ...inputStyle, width: 56 }}
          />
          %
        </label>
      ))}
      <span style={{ fontSize: 11, color: sum === 100 ? C.muted3 : C.red }}>= {sum}%</span>
    </span>
  );
}

// One row per phase of `rows`; a row the athlete has touched shows its draft.
export function SplitTable({
  rows,
  drafts,
  onChange,
  inputStyle,
  labelColor,
}: {
  rows: Record<string, SeasonSplit>;
  drafts: Record<string, SplitDraft>;
  onChange: (drafts: Record<string, SplitDraft>) => void;
  inputStyle: CSSProperties;
  labelColor?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {PHASE_ORDER.filter((p) => rows[p]).map((p) => (
        <SplitInputs
          key={p}
          label={PHASE_LABEL[p]}
          value={drafts[p] ?? draftOf(rows[p])}
          onChange={(v) => onChange({ ...drafts, [p]: v })}
          inputStyle={inputStyle}
          labelColor={labelColor}
        />
      ))}
    </div>
  );
}

// The drafted rows as splits: `rows` holds the valid ones that differ from the
// table, `invalid` is true when any draft does not add up.
export function draftedRows(drafts: Record<string, SplitDraft>, table: Record<string, SeasonSplit>) {
  const rows: Record<string, SeasonSplit> = {};
  let invalid = false;
  for (const [phase, d] of Object.entries(drafts)) {
    const s = parseSplit(d);
    if (!s) invalid = true;
    else if (!table[phase] || !sameSplit(s, table[phase])) rows[phase] = s;
  }
  return { rows, invalid };
}
