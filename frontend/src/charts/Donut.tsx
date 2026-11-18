// Donut/ring chart with a legend. Hovering a segment (or legend row) highlights
// it and shows its share in the center. Used for the HR-zone distribution.
import { useState } from "react";
import { C } from "../theme";

export interface DonutItem {
  label: string;
  pct: number;
  color: string;
}

function polar(cx: number, cy: number, r: number, deg: number) {
  const a = ((deg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

// Annular sector (donut slice) path from start->end degrees, clockwise.
function sector(cx: number, cy: number, R: number, r: number, start: number, end: number) {
  const large = end - start > 180 ? 1 : 0;
  const o1 = polar(cx, cy, R, start);
  const o2 = polar(cx, cy, R, end);
  const i2 = polar(cx, cy, r, end);
  const i1 = polar(cx, cy, r, start);
  return `M ${o1.x} ${o1.y} A ${R} ${R} 0 ${large} 1 ${o2.x} ${o2.y} L ${i2.x} ${i2.y} A ${r} ${r} 0 ${large} 0 ${i1.x} ${i1.y} Z`;
}

export function Donut({ items }: { items: DonutItem[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const total = items.reduce((a, s) => a + Math.max(s.pct, 0), 0) || 1;
  // Default the center readout to the dominant zone so it's never blank.
  const maxIdx = items.reduce((best, it, i) => (it.pct > items[best].pct ? i : best), 0);
  const active = hover ?? maxIdx;
  const activeItem = items[active];

  const size = 130,
    cx = size / 2,
    cy = size / 2,
    R = 60,
    r = 40;

  // Cumulative angles, keeping each slice mapped to its original index.
  let acc = 0;
  const arcs = items.map((it) => {
    if (it.pct <= 0) return null;
    const start = (acc / total) * 360;
    acc += it.pct;
    const end = (acc / total) * 360;
    return { start, end, full: end - start >= 359.999 };
  });

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, height: "100%", padding: "2px 4px" }}>
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        style={{ flex: "0 0 auto", display: "block" }}
      >
        {items.map((it, i) => {
          const arc = arcs[i];
          if (!arc) return null;
          const dim = hover !== null && hover !== i;
          const common = {
            fill: it.color,
            fillOpacity: dim ? 0.35 : 1,
            onMouseEnter: () => setHover(i),
            onMouseLeave: () => setHover(null),
            style: { cursor: "pointer", transition: "fill-opacity .12s" as const },
          };
          if (arc.full) {
            // Single zone at 100%: draw a full ring (sector path degenerates).
            return (
              <g key={i}>
                <circle cx={cx} cy={cy} r={R} {...common} />
                <circle cx={cx} cy={cy} r={r} fill={C.card} pointerEvents="none" />
              </g>
            );
          }
          return (
            <path key={i} d={sector(cx, cy, R, r, arc.start, arc.end)} stroke={C.card} strokeWidth={1.5} {...common} />
          );
        })}
        <text x={cx} y={cy - 1} textAnchor="middle" fontSize={23} fontWeight={700} fill="#fff">
          {activeItem.pct}%
        </text>
        <text x={cx} y={cy + 15} textAnchor="middle" fontSize={10} fill={C.muted}>
          {activeItem.label}
        </text>
      </svg>

      <div style={{ display: "flex", flexDirection: "column", gap: 5, flex: 1, minWidth: 0 }}>
        {items.map((it, i) => (
          <div
            key={i}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              cursor: "pointer",
              opacity: hover === null || hover === i ? 1 : 0.5,
              transition: "opacity .12s",
            }}
          >
            <span style={{ width: 9, height: 9, borderRadius: 2, background: it.color, flex: "0 0 auto" }} />
            <span style={{ fontSize: 11, color: C.textChip, flex: 1 }}>{it.label}</span>
            <span style={{ fontSize: 11, fontWeight: 600, color: C.text }}>{it.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
