// A planned session's structure as a step graph: one block per segment, as wide
// as its duration and as tall as its target band, coloured on the HR-zone
// scale. The solid part of a block runs up to the band's low end and a lighter
// cap shows the band itself. Hovering a step names it with its target in the
// header line, which otherwise shows the session total.
import { useState } from "react";
import { C, zoneColor } from "../theme";
import { useChartWidth } from "./useChartWidth";
import { segmentTarget, stepDuration, THRESHOLD_LABEL } from "../workoutTargets";
import type { AthleteProfile, StructureSegment } from "../types";

// Height of a step that has no target (strength, or no zone), in % of threshold.
const UNTARGETED_PCT = 55;
// Axis floor: every closed band starts above it, so even Z1 has a visible body.
const Y_MIN = 40;

// Tick spacing in minutes: the first that keeps the axis to six labels or fewer.
const TICK_STEPS = [5, 10, 15, 20, 30, 60, 90, 120];

function tickLabel(min: number): string {
  if (min === 0) return "0";
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h ? `${h}h${m ? `${m}m` : ""}` : `${m}m`;
}

export function WorkoutGraph({
  discipline,
  segments,
  profile,
  h = 150,
}: {
  discipline: string;
  segments: StructureSegment[];
  profile: AthleteProfile | null;
  h?: number;
}) {
  const { ref, w } = useChartWidth(h, 500);
  const [hover, setHover] = useState<number | null>(null);

  const steps = segments
    .filter((s) => (s.duration_min ?? 0) > 0)
    .map((seg) => {
      const target = segmentTarget(discipline, seg, profile);
      return { seg, target, min: seg.duration_min as number, color: target.zone ? zoneColor(target.zone, C.muted) : C.muted };
    });
  const total = steps.reduce((a, s) => a + s.min, 0);
  if (!steps.length || total <= 0) return null;

  const padL = 34,
    padR = 10,
    padT = 8,
    padB = 20;
  const iw = w - padL - padR,
    ih = h - padT - padB;
  const top = Math.max(110, Math.ceil(Math.max(...steps.map((s) => s.target.pct?.[1] ?? 0)) / 10) * 10 + 5);
  const yAt = (pct: number) => padT + ih - ((Math.max(pct, Y_MIN) - Y_MIN) / (top - Y_MIN)) * ih;
  const xAt = (min: number) => padL + (min / total) * iw;
  const base = padT + ih;

  const tick = TICK_STEPS.find((t) => total / t <= 6) ?? 180;
  const ticks: number[] = [];
  for (let t = 0; t <= total + 1e-6; t += tick) ticks.push(t);

  const threshold = THRESHOLD_LABEL[discipline];
  const gridPcts = [50, 75, 100].filter((p) => p < top);

  let at = 0;
  const blocks = steps.map((s, i) => {
    const x0 = xAt(at);
    at += s.min;
    const x1 = xAt(at);
    return { ...s, i, x0, x1 };
  });

  const active = hover != null ? blocks[hover] : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* Header line: the hovered step, else the session total. */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, minHeight: 18 }}>
        {active ? (
          <span style={{ fontSize: 13, color: C.mutedBright2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            <strong style={{ color: C.white, fontWeight: 600 }}>{active.seg.segment || "Step"}</strong>{" "}
            {stepDuration(active.min)}{" "}
            {active.target.text && <span style={{ color: active.color, fontWeight: 600 }}>{active.target.text}</span>}
          </span>
        ) : (
          <span style={{ fontSize: 13, color: C.mutedBright2 }}>
            <strong style={{ color: C.white, fontWeight: 600 }}>{stepDuration(total)}</strong> · {steps.length} step{steps.length === 1 ? "" : "s"}
          </span>
        )}
        {threshold && (
          <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase", color: C.mutedBright, flex: "none" }}>
            % of {threshold}
          </span>
        )}
      </div>

      <div ref={ref} style={{ width: "100%", height: h }} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="100%" preserveAspectRatio="xMidYMid meet" style={{ display: "block" }}>
          {gridPcts.map((p) => (
            <g key={`g${p}`}>
              <line
                x1={padL}
                x2={w - padR}
                y1={yAt(p)}
                y2={yAt(p)}
                stroke="#fff"
                strokeOpacity={p === 100 ? 0.18 : 0.05}
                strokeDasharray={p === 100 ? "3 3" : undefined}
              />
              <text x={padL - 6} y={yAt(p) + 3} fontSize={9} fill={C.muted} textAnchor="end">
                {p === 100 && threshold ? threshold : `${p}%`}
              </text>
            </g>
          ))}

          {blocks.map((b) => {
            const lit = hover == null || hover === b.i;
            const x = b.x0 + 0.5;
            const bw = Math.max(1, b.x1 - b.x0 - 1);
            const [lo, hi] = b.target.pct ?? [UNTARGETED_PCT, UNTARGETED_PCT];
            const yLo = yAt(lo);
            const yHi = yAt(hi);
            return (
              <g key={b.i}>
                <rect x={x} y={yLo} width={bw} height={Math.max(0, base - yLo)} fill={b.color} fillOpacity={lit ? 0.75 : 0.4} rx={1} />
                {hi > lo && (
                  <rect x={x} y={yHi} width={bw} height={Math.max(0, yLo - yHi)} fill={b.color} fillOpacity={lit ? 0.35 : 0.18} rx={1} />
                )}
                {hover === b.i && <line x1={x} x2={x + bw} y1={yHi} y2={yHi} stroke={b.color} strokeWidth={2} />}
                {/* Full-height hit area, so a short step is as easy to point at as a long one. */}
                <rect
                  x={b.x0}
                  y={padT}
                  width={Math.max(1, b.x1 - b.x0)}
                  height={ih}
                  fill="transparent"
                  onMouseEnter={() => setHover(b.i)}
                />
              </g>
            );
          })}

          <line x1={padL} x2={w - padR} y1={base} y2={base} stroke={C.modalControlBorder} />
          {ticks.map((t) => (
            // The first and a last label at the edge align inward, so neither is clipped.
            <text
              key={`t${t}`}
              x={xAt(t)}
              y={h - 6}
              fontSize={9}
              fill={C.muted}
              textAnchor={t === 0 ? "start" : xAt(t) > w - padR - 16 ? "end" : "middle"}
            >
              {tickLabel(t)}
            </text>
          ))}
        </svg>
      </div>
    </div>
  );
}
