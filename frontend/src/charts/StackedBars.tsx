// Stacked bar chart (e.g. weekly volume by discipline). Ported from stackedBars().
import { useState } from "react";
import { C } from "../theme";
import { useChartWidth } from "./useChartWidth";

interface SeriesDef {
  name: string;
  color: string;
  values: number[];
}

interface Props {
  cats: string[];
  series: SeriesDef[];
  w?: number;
  h?: number;
  legend?: boolean;
  fmtY?: (v: number) => string;
  fmtTip?: (v: number) => string; // value formatting inside the hover tooltip
  tipText?: (ci: number, si: number) => string; // replaces the whole tooltip line
}

export function StackedBars({ cats, series, w: wProp = 340, h = 200, legend = true, fmtY, fmtTip, tipText }: Props) {
  const [hover, setHover] = useState<{ ci: number; si: number } | null>(null);
  // Widen the viewBox to the card's real aspect so big screens don't letterbox.
  const { ref, w } = useChartWidth(h, wProp);

  const padL = 36,
    padR = 12,
    padT = 18,
    padB = 24;
  const iw = w - padL - padR,
    ih = h - padT - padB;

  const totals = cats.map((_, ci) => series.reduce((s, se) => s + (se.values[ci] || 0), 0));
  const vmax = Math.max(...totals, 1) * 1.12;
  const yAt = (v: number) => padT + ih - (v / vmax) * ih;
  const n = cats.length,
    slot = iw / n,
    bw = slot * 0.5;

  const grid = [];
  for (let t = 0; t <= 4; t++) {
    const val = (vmax * t) / 4;
    const y = yAt(val);
    grid.push(
      <line key={`g${t}`} x1={padL} x2={w - padR} y1={y} y2={y} stroke="#fff" strokeOpacity={0.05} />,
      <text key={`gt${t}`} x={padL - 6} y={y + 3} fontSize={9} fill={C.muted} textAnchor="end">
        {fmtY ? fmtY(val) : Math.round(val)}
      </text>,
    );
  }

  // Tooltip content + anchor for the hovered segment, in SVG coordinates.
  let tip: { x: number; y: number; text: string } | null = null;
  if (hover) {
    const { ci, si } = hover;
    const v = series[si]?.values[ci] || 0;
    const below = series.slice(0, si).reduce((s, se) => s + (se.values[ci] || 0), 0);
    const pct = totals[ci] > 0 ? Math.round((v / totals[ci]) * 100) : 0;
    const val = fmtTip ? fmtTip(v) : String(Math.round(v * 10) / 10);
    tip = {
      x: padL + (ci + 0.5) * slot,
      y: yAt(below + v),
      text: tipText ? tipText(ci, si) : `${series[si]?.name} · ${val} · ${pct}%`,
    };
  }
  const tipW = tip ? tip.text.length * 4.9 + 14 : 0;
  const tipX = tip ? Math.min(Math.max(tip.x - tipW / 2, 2), w - tipW - 2) : 0;
  const tipY = tip ? Math.max(tip.y - 22, 2) : 0;

  return (
    <div ref={ref} style={{ width: "100%", height: "100%" }}>
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width="100%"
      height="100%"
      preserveAspectRatio="xMidYMid meet"
      style={{ display: "block" }}
      onMouseLeave={() => setHover(null)}
    >
      {grid}
      {cats.map((c, ci) => {
        const cx = padL + (ci + 0.5) * slot;
        let acc = 0;
        const rects = series.map((se, si) => {
          const v = se.values[ci] || 0;
          const y0 = yAt(acc),
            y1 = yAt(acc + v);
          acc += v;
          const active = hover?.ci === ci && hover?.si === si;
          return (
            <rect
              key={`r${ci}_${si}`}
              x={cx - bw / 2}
              y={y1}
              width={bw}
              height={Math.max(0, y0 - y1)}
              fill={se.color}
              rx={1}
              fillOpacity={hover && !active ? 0.45 : 1}
              stroke={active ? "#fff" : "none"}
              strokeOpacity={0.6}
              strokeWidth={0.75}
              style={{ cursor: v > 0 ? "pointer" : "default" }}
              onMouseEnter={() => (v > 0 ? setHover({ ci, si }) : setHover(null))}
            />
          );
        });
        return (
          <g key={`c${ci}`}>
            {rects}
            <text x={cx} y={h - 8} fontSize={9} fill={C.muted} textAnchor="middle">
              {c}
            </text>
          </g>
        );
      })}
      {legend &&
        series.map((s, si) => {
          const lx = padL + si * 68;
          return (
            <g key={`lg${si}`}>
              <rect x={lx} y={2} width={9} height={9} rx={2} fill={s.color} />
              <text x={lx + 12} y={10} fontSize={8.5} fill={C.muted3}>
                {s.name}
              </text>
            </g>
          );
        })}
      {tip && (
        <g pointerEvents="none">
          <rect x={tipX} y={tipY} width={tipW} height={16} rx={4} fill={C.bar} stroke={C.cardBorder} />
          <text x={tipX + tipW / 2} y={tipY + 11} fontSize={9} fill={C.text} textAnchor="middle">
            {tip.text}
          </text>
        </g>
      )}
    </svg>
    </div>
  );
}
