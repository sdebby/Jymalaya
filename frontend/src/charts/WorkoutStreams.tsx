// A completed workout's recorded streams as stacked time-axis graphs (pace or
// speed, heart rate, elevation, cadence, power, then efficiency = output / HR,
// added 2026-11-14), one card each, plus the time
// in HR and power zones as horizontal bars. Design: the "Workout modal charts"
// canvas (2026-11-06). Every graph shares one time axis and one hover: the
// crosshair runs through all of them and each head reads its own value at that
// moment, in place of the average it shows otherwise.
import { useState } from "react";
import { C, DISCIPLINE_COLOR, HR_ZONE_COLOR } from "../theme";
import { pace } from "../format";
import { useChartWidth } from "./useChartWidth";
import type { WorkoutRow, WorkoutStreams } from "../types";

const H = 96;
const PAD_L = 40;
const PAD_R = 8;
const PAD_T = 8;
const PLOT_H = 68;
// Slower than this is standing, not running or swimming: no pace is drawn.
const MIN_SPEED_MPS = 0.5;
const TICK_STEPS_MIN = [5, 10, 15, 20, 30, 60, 90, 120];
const PACE_STEPS_SEC = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600];

type Series = {
  key: string;
  title: string;
  unit: string;
  color: string;
  values: (number | null)[];
  fmt: (v: number) => string;
  stats: string;
  invert?: boolean; // pace: faster (smaller) at the top
  area?: boolean; // elevation
  robust?: boolean; // domain from the 2nd-98th percentile, outliers clamped
  steps?: number[];
  // Efficiency: each half's average drawn as a dashed level over its half.
  halves?: { splitT: number; first: number; second: number };
};

// Efficiency is smoothed over 5 minutes centred on each sample: one 30 s
// bucket of output over HR is mostly noise. Drift is reported only for a
// session of 45 min or more, like aerobic_decoupling in app/metrics/analysis.py.
const EFF_WINDOW_SEC = 300;
const MIN_DRIFT_SEC = 45 * 60;

export function clock(sec: number): string {
  const t = Math.max(0, Math.round(sec));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  const p = (x: number) => String(x).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}

const r0 = (v: number) => String(Math.round(v));
const r1 = (v: number) => v.toFixed(1);

function present(values: (number | null)[]): number[] {
  return values.filter((v): v is number => v != null && Number.isFinite(v));
}

const avg = (a: number[]) => a.reduce((x, y) => x + y, 0) / a.length;

function percentile(sorted: number[], p: number): number {
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * p)));
  return sorted[i];
}

function niceStep(raw: number, steps?: number[]): number {
  if (steps) return steps.find((s) => s >= raw) ?? steps[steps.length - 1];
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= raw) return m * mag;
  return 10 * mag;
}

// Three gridlines (top, middle, bottom) on round values around the data.
function domain(s: Series): { lo: number; hi: number } {
  const v = present(s.values).sort((a, b) => a - b);
  let min = s.robust ? percentile(v, 0.03) : v[0];
  let max = s.robust ? percentile(v, 0.97) : v[v.length - 1];
  if (max - min < 1e-6) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.06;
  min -= pad;
  max += pad;
  // The smallest round step whose two-step range, floored at the data, still
  // reaches its top: the line fills the plot instead of hugging one band.
  let step = niceStep((max - min) / 2, s.steps);
  for (let guard = 0; guard < 20; guard++) {
    const lo = Math.floor(min / step) * step;
    if (lo + 2 * step >= max) return { lo, hi: lo + 2 * step };
    const next = niceStep(step * 1.001, s.steps);
    step = next > step ? next : step * 2;
  }
  return { lo: min, hi: max };
}

// Build the series the workout actually recorded, in the design's order.
function buildSeries(discipline: string, streams: WorkoutStreams, workout: WorkoutRow): Series[] {
  const sm = streams.samples;
  const col = (k: keyof WorkoutStreams["samples"][number]) => sm.map((s) => (s[k] ?? null) as number | null);
  const accent = DISCIPLINE_COLOR[discipline] ?? C.muted;
  const out: Series[] = [];

  const speed = col("speed_mps");
  if (discipline === "bike") {
    const kmh = speed.map((v) => (v == null ? null : v * 3.6));
    const p = present(kmh);
    if (p.length > 1)
      out.push({ key: "speed", title: "Speed", unit: "km/h", color: accent, values: kmh, fmt: r1, robust: true,
        stats: `avg ${r1(avg(p))} · max ${r1(Math.max(...p))} km/h` });
  } else if (discipline === "run" || discipline === "swim") {
    const per = discipline === "swim" ? 100 : 1000;
    const unit = discipline === "swim" ? "/100 m" : "/km";
    const pc = speed.map((v) => (v == null || v < MIN_SPEED_MPS ? null : per / v));
    const p = present(pc);
    if (p.length > 1) {
      // Average pace is distance over time, not the mean of the bucket paces.
      const meanSpeed = avg(present(speed).filter((v) => v >= MIN_SPEED_MPS));
      out.push({ key: "pace", title: "Pace", unit, color: accent, values: pc, fmt: (v) => pace(v), invert: true,
        robust: true, steps: PACE_STEPS_SEC, stats: `avg ${pace(per / meanSpeed)} · best ${pace(Math.min(...p))} ${unit}` });
    }
  }

  const hr = col("hr");
  const hp = present(hr);
  if (hp.length > 1)
    out.push({ key: "hr", title: "Heart rate", unit: "bpm", color: C.red, values: hr, fmt: r0, robust: true,
      stats: `avg ${r0(workout.avg_hr ?? avg(hp))} · max ${r0(Math.max(...hp))} bpm` });

  if (discipline !== "swim") {
    const el = col("elevation_m");
    const ep = present(el);
    if (ep.length > 1) {
      let up = 0, down = 0;
      for (let i = 1; i < ep.length; i++) {
        const d = ep[i] - ep[i - 1];
        if (d > 0) up += d;
        else down -= d;
      }
      const gain = workout.elevation_gain_m ?? up;
      const loss = workout.elevation_loss_m ?? down;
      out.push({ key: "elev", title: "Elevation", unit: "m", color: C.elevation, values: el, fmt: r0, area: true,
        stats: `+${r0(gain)} m · −${r0(loss)} m` });
    }
  }

  const cadUnit = discipline === "bike" ? "rpm" : discipline === "swim" ? "str/min" : "spm";
  const cad = col("cadence");
  const cp = present(cad);
  if (cp.length > 1)
    out.push({ key: "cad", title: "Cadence", unit: cadUnit, color: C.cadence, values: cad, fmt: r0, robust: true,
      stats: `avg ${r0(workout.avg_cadence ?? avg(cp))} ${cadUnit}` });

  const pw = col("power_w");
  const pp = present(pw);
  if (pp.length > 1) {
    const np = workout.normalized_power != null ? ` · NP ${r0(workout.normalized_power)}` : "";
    out.push({ key: "power", title: "Power", unit: "W", color: C.power, values: pw, fmt: r0, robust: true,
      stats: `avg ${r0(workout.avg_power ?? avg(pp))}${np} · max ${r0(workout.max_power ?? Math.max(...pp))} W` });
  }

  const eff = efficiencySeries(streams, workout);
  if (eff) out.push(eff);
  return out;
}

// Output over heart rate through the session: Pw:HR (watts per bpm) with a
// power meter, else Pa:HR as metres per heartbeat. A falling line is cardiac
// drift. The halves and the drift % follow aerobic_decoupling in
// app/metrics/analysis.py exactly (same output pick, same paired samples,
// split by count, ratio of the half means), so the graph and the analysis
// agree on the number.
function efficiencySeries(streams: WorkoutStreams, workout: WorkoutRow): Series | null {
  const sm = streams.samples;
  const usePower = sm.some((s) => typeof s.power_w === "number");
  const outOf = (s: WorkoutStreams["samples"][number]): number | null => {
    const v = usePower ? s.power_w : s.speed_mps;
    if (typeof v !== "number" || !Number.isFinite(v)) return null;
    return usePower ? v : v * 60; // m/s -> m/min, so the ratio is m per beat
  };
  const paired = sm
    .map((s, i) => ({ i, t: s.t, out: outOf(s), hr: s.hr }))
    .filter((p): p is { i: number; t: number; out: number; hr: number } => p.out != null && !!p.hr);
  if (paired.length < 4) return null;

  // Centred rolling window by time, two pointers over the paired samples.
  const values: (number | null)[] = sm.map(() => null);
  let a = 0, b = 0, sumOut = 0, sumHr = 0;
  for (const p of paired) {
    while (b < paired.length && paired[b].t <= p.t + EFF_WINDOW_SEC / 2) {
      sumOut += paired[b].out;
      sumHr += paired[b].hr;
      b++;
    }
    while (paired[a].t < p.t - EFF_WINDOW_SEC / 2) {
      sumOut -= paired[a].out;
      sumHr -= paired[a].hr;
      a++;
    }
    if (sumHr > 0) values[p.i] = sumOut / sumHr;
  }

  const half = Math.floor(paired.length / 2);
  const ratio = (c: typeof paired) => avg(c.map((p) => p.out)) / avg(c.map((p) => p.hr));
  const first = ratio(paired.slice(0, half));
  const second = ratio(paired.slice(half));
  if (!(first > 0) || !(second > 0)) return null;

  const r2 = (v: number) => v.toFixed(2);
  const longEnough = (workout.duration_sec ?? 0) >= MIN_DRIFT_SEC;
  const drift = ((first - second) / first) * 100;
  const driftText = longEnough
    ? ` · drift ${drift >= 0 ? "" : "−"}${Math.abs(drift).toFixed(1)}%`
    : " · drift needs 45+ min";
  return {
    key: "eff",
    title: usePower ? "Efficiency (Pw:HR)" : "Efficiency (Pa:HR)",
    unit: usePower ? "W/bpm" : "m/beat",
    color: C.efficiency,
    values,
    fmt: r2,
    robust: true,
    halves: { splitT: paired[half].t, first, second },
    stats: `1st half ${r2(first)} · 2nd half ${r2(second)}${driftText}`,
  };
}

export function WorkoutStreamGraphs({
  discipline,
  streams,
  workout,
}: {
  discipline: string;
  streams: WorkoutStreams;
  workout: WorkoutRow;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const series = buildSeries(discipline, streams, workout);
  if (series.length === 0) return null;
  const times = streams.samples.map((s) => s.t);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {series.map((s) => (
        <StreamChart key={s.key} s={s} times={times} hover={hover} onHover={setHover} />
      ))}
    </div>
  );
}

function StreamChart({
  s,
  times,
  hover,
  onHover,
}: {
  s: Series;
  times: number[];
  hover: number | null;
  onHover: (i: number | null) => void;
}) {
  const { ref, w } = useChartWidth(H, 600);
  const total = Math.max(1, times[times.length - 1] ?? 1);
  const iw = w - PAD_L - PAD_R;
  const { lo, hi } = domain(s);
  const xAt = (t: number) => PAD_L + (t / total) * iw;
  const yAt = (v: number) => {
    const c = Math.min(hi, Math.max(lo, v));
    const f = s.invert ? (c - lo) / (hi - lo) : (hi - c) / (hi - lo);
    return PAD_T + f * PLOT_H;
  };

  // The line breaks across gaps (a missing sample) rather than bridging them.
  let line = "";
  let pen = false;
  s.values.forEach((v, i) => {
    if (v == null) {
      pen = false;
      return;
    }
    line += `${pen ? "L" : "M"}${xAt(times[i]).toFixed(1)} ${yAt(v).toFixed(1)}`;
    pen = true;
  });
  let area = "";
  if (s.area) {
    const idx = s.values.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0);
    if (idx.length > 1) {
      const base = PAD_T + PLOT_H;
      area =
        `M${xAt(times[idx[0]]).toFixed(1)} ${base}` +
        idx.map((i) => `L${xAt(times[i]).toFixed(1)} ${yAt(s.values[i] as number).toFixed(1)}`).join("") +
        `L${xAt(times[idx[idx.length - 1]]).toFixed(1)} ${base}Z`;
    }
  }

  const top = s.invert ? lo : hi;
  const bot = s.invert ? hi : lo;
  const tickStep = (TICK_STEPS_MIN.find((m) => total / 60 / m <= 5) ?? 120) * 60;
  const ticks: number[] = [];
  for (let t = 0; t <= total + 1; t += tickStep) ticks.push(t);

  const hv = hover != null ? s.values[hover] : null;
  const readout =
    hover != null
      ? `${clock(times[hover])}  ·  ${hv != null ? `${s.fmt(hv)} ${s.unit}` : "no data"}`
      : s.stats;

  function move(e: React.MouseEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * w;
    const t = ((x - PAD_L) / iw) * total;
    // Nearest sample by time (the samples are sorted).
    let a = 0, b = times.length - 1;
    while (b - a > 1) {
      const m = (a + b) >> 1;
      if (times[m] < t) a = m;
      else b = m;
    }
    onHover(Math.abs(times[a] - t) <= Math.abs(times[b] - t) ? a : b);
  }

  const label = { fontSize: 9, fill: C.mutedBright } as const;
  return (
    <div style={{ boxSizing: "border-box", padding: "10px 12px 6px", display: "flex", flexDirection: "column", gap: 4, background: C.modalInner, border: `1px solid ${C.modalControlBorder}`, borderRadius: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, minWidth: 0 }}>
        <span style={{ width: 9, height: 9, borderRadius: 2, flex: "none", alignSelf: "center", background: s.color }} />
        <span style={{ fontSize: 12, fontWeight: 600, color: C.textSoft }}>{s.title}</span>
        <span style={{ fontSize: 11, color: C.mutedBright }}>{s.unit}</span>
        <span style={{ flexGrow: 1 }} />
        <span style={{ fontSize: 12, fontWeight: 600, color: hover != null ? s.color : C.mutedBright2, fontVariantNumeric: "tabular-nums", whiteSpace: "pre" }}>
          {readout}
        </span>
      </div>
      <div ref={ref} style={{ height: H }}>
        <svg
          viewBox={`0 0 ${w} ${H}`}
          width="100%"
          height={H}
          role="img"
          aria-label={`${s.title} over time, ${s.stats}`}
          onMouseMove={move}
          onMouseLeave={() => onHover(null)}
          style={{ display: "block", cursor: "crosshair" }}
        >
          <path d={`M${PAD_L} ${PAD_T}H${w - PAD_R}M${PAD_L} ${PAD_T + PLOT_H / 2}H${w - PAD_R}M${PAD_L} ${PAD_T + PLOT_H}H${w - PAD_R}`} stroke={C.gridline} fill="none" />
          <text x={PAD_L - 6} y={PAD_T + 3} textAnchor="end" style={label}>{s.fmt(top)}</text>
          <text x={PAD_L - 6} y={PAD_T + PLOT_H / 2 + 3} textAnchor="end" style={label}>{s.fmt((top + bot) / 2)}</text>
          <text x={PAD_L - 6} y={PAD_T + PLOT_H + 3} textAnchor="end" style={label}>{s.fmt(bot)}</text>
          {area && <path d={area} fill={s.color} fillOpacity={0.14} stroke="none" />}
          {s.halves && (
            <path
              d={`M${PAD_L} ${yAt(s.halves.first).toFixed(1)}H${xAt(s.halves.splitT).toFixed(1)}M${xAt(s.halves.splitT).toFixed(1)} ${yAt(s.halves.second).toFixed(1)}H${w - PAD_R}`}
              stroke={C.mutedBright2}
              strokeWidth={1.2}
              strokeDasharray="4 3"
              fill="none"
            />
          )}
          <path d={line} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {ticks.map((t, i) => (
            <text
              key={t}
              x={xAt(t)}
              y={H - 5}
              textAnchor={i === 0 ? "start" : t > total - tickStep / 3 ? "end" : "middle"}
              style={label}
            >
              {clock(t)}
            </text>
          ))}
          {hover != null && (
            <>
              <line x1={xAt(times[hover])} x2={xAt(times[hover])} y1={PAD_T - 4} y2={PAD_T + PLOT_H + 2} stroke={C.crosshair} />
              {hv != null && <circle cx={xAt(times[hover])} cy={yAt(hv)} r={2.6} fill={C.modalInner} stroke={s.color} strokeWidth={2} />}
            </>
          )}
        </svg>
      </div>
    </div>
  );
}

// Time in zones as one horizontal bar: a segment per zone, as wide as its
// time, on the shared Z1 blue .. Z5 red scale, with minutes and share under it.
// Time below the Z1 floor (coasting, pool rest) is a grey "Below Z1" segment.
export function ZoneBar({
  title,
  basis,
  seconds,
  belowSec = 0,
}: {
  title: string;
  basis: string;
  seconds: Record<string, number>;
  belowSec?: number;
}) {
  const zones = ["Z1", "Z2", "Z3", "Z4", "Z5"].map((z) => ({ z, sec: Math.max(0, seconds[z] ?? 0), color: HR_ZONE_COLOR[z] }));
  const below = belowSec >= 60 ? belowSec : 0;
  const total = zones.reduce((a, z) => a + z.sec, 0) + below;
  if (total <= 0) return null;
  const segs = [...(below ? [{ z: "Below Z1", sec: below, color: C.muted }] : []), ...zones].filter((z) => z.sec > 0);
  const legend = [...(below ? [{ z: "Below Z1", sec: below, color: C.muted }] : []), ...zones];
  const pct = (sec: number) => `${Math.round((sec / total) * 100)}%`;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: C.textSoft }}>{title}</span>
        <span style={{ fontSize: 11, color: C.mutedBright }}>{basis}</span>
        <span style={{ flexGrow: 1 }} />
        <span style={{ fontSize: 12, color: C.mutedBright2 }}>{Math.round(total / 60)} min</span>
      </div>
      <div
        role="img"
        aria-label={`${title}: ${legend.map((l) => `${l.z} ${Math.round(l.sec / 60)} min`).join(", ")}`}
        style={{ display: "flex", gap: 2, height: 16, borderRadius: 3, overflow: "hidden", background: C.inner2 }}
      >
        {segs.map((s) => (
          <div key={s.z} title={`${s.z} · ${Math.round(s.sec / 60)} min · ${pct(s.sec)}`} style={{ flex: `${s.sec} 1 0px`, background: s.color, opacity: 0.85 }} />
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", columnGap: 16, rowGap: 4 }}>
        {legend.map((l) => (
          <div key={l.z} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, whiteSpace: "nowrap", opacity: l.sec >= 30 ? 1 : 0.4 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, flex: "none", background: l.color }} />
            <span style={{ fontWeight: 600, color: l.color === C.muted ? C.mutedBright2 : l.color }}>{l.z}</span>
            <span style={{ color: C.modalLabel, fontVariantNumeric: "tabular-nums" }}>{Math.round(l.sec / 60)} min</span>
            <span style={{ color: C.mutedBright }}>{pct(l.sec)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
