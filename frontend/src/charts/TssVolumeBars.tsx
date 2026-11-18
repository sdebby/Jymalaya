// Weekly TSS stacked by discipline, total hours as diamond markers on a right
// axis, and TSS per hour (intensity) in a strip under the week labels. Replaces
// the separate "Weekly Volume by Discipline" chart, so load, volume and how hard
// the volume was ridden read together. The current week is hatched: it is
// still in progress.
import { useId, useState } from "react";
import { C } from "../theme";
import { useChartWidth } from "./useChartWidth";

interface SeriesDef {
  name: string;
  color: string;
  values: number[];
}

interface Props {
  cats: string[];
  tss: SeriesDef[]; // per-discipline weekly TSS
  hours: number[]; // total weekly hours, same length as cats
  w?: number;
  h?: number;
}

// A round top for an axis: 1, 2, 2.5 or 5 times a power of ten, at or above v.
function niceTop(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

const fmtH = (v: number) => (Math.round(v * 10) / 10).toString();

export function TssVolumeBars({ cats, tss, hours, w: wProp = 340, h = 200 }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const { ref, w } = useChartWidth(h, wProp);
  const hatchId = `hatch${useId().replace(/:/g, "")}`;

  const padL = 34,
    padR = 26,
    padT = 20,
    plotB = 130, // bottom of the TSS plot
    labelY = 142, // week labels
    divY = 150, // rule above the intensity strip
    stripT = 164,
    stripB = 190;
  const iw = w - padL - padR,
    ih = plotB - padT;
  const n = cats.length,
    slot = iw / n,
    bw = slot * 0.5;
  const cx = (i: number) => padL + (i + 0.5) * slot;

  const totals = cats.map((_, i) => tss.reduce((s, se) => s + (se.values[i] || 0), 0));
  const tTop = niceTop(Math.max(...totals, 1) * 1.05);
  const hTop = niceTop(Math.max(...hours, 0.5) * 1.05);
  const yT = (v: number) => plotB - (v / tTop) * ih;
  const yH = (v: number) => plotB - (v / hTop) * ih;

  // TSS per hour; weeks without training have none.
  const ipw = cats.map((_, i) => (hours[i] > 0.05 ? totals[i] / hours[i] : NaN));
  const known = ipw.filter((v) => !isNaN(v));
  const iLo = known.length ? Math.floor(Math.min(...known) - 3) : 0;
  const iHi = known.length ? Math.ceil(Math.max(...known) + 3) : 1;
  const yI = (v: number) => stripB - ((v - iLo) / Math.max(iHi - iLo, 1)) * (stripB - stripT);
  const linePts = ipw
    .map((v, i) => (isNaN(v) ? null : `${cx(i)},${yI(v)}`))
    .filter(Boolean)
    .join(" ");

  const grid = [];
  for (let t = 0; t <= 4; t++) {
    const y = plotB - (ih * t) / 4;
    grid.push(
      <line key={`g${t}`} x1={padL} x2={w - padR} y1={y} y2={y} stroke="#fff" strokeOpacity={0.05} />,
      <text key={`gl${t}`} x={padL - 6} y={y + 3} fontSize={9} fill={C.muted} textAnchor="end">
        {Math.round((tTop * t) / 4)}
      </text>,
      <text key={`gr${t}`} x={w - padR + 6} y={y + 3} fontSize={9} fill={C.muted}>
        {fmtH((hTop * t) / 4)}h
      </text>,
    );
  }

  // Tooltip for the hovered week: totals on line 1, the discipline split on line 2.
  let tip: { x: number; lines: string[] } | null = null;
  if (hover != null) {
    const i = hover;
    const intensity = isNaN(ipw[i]) ? "" : ` · ${Math.round(ipw[i])} TSS/h`;
    const split = tss
      .filter((se) => (se.values[i] || 0) > 0)
      .map((se) => `${se.name} ${Math.round(se.values[i])}`)
      .join(" · ");
    const head = `${cats[i]}${i === n - 1 ? " (so far)" : ""} · ${Math.round(totals[i])} TSS · ${fmtH(hours[i])} h${intensity}`;
    tip = { x: cx(i), lines: split ? [head, split] : [head] };
  }
  const tipW = tip ? Math.max(...tip.lines.map((l) => l.length)) * 4.9 + 14 : 0;
  const tipH = tip ? tip.lines.length * 12 + 6 : 0;
  const tipX = tip ? Math.min(Math.max(tip.x - tipW / 2, 2), w - tipW - 2) : 0;
  const tipY = padT - 2;

  const legend = [...tss.map((s) => ({ name: s.name, color: s.color })), { name: "Hours", color: "" }];

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
        <defs>
          <pattern id={hatchId} width={5} height={5} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width={2} height={5} fill={C.card} fillOpacity={0.6} />
          </pattern>
        </defs>
        {grid}
        {cats.map((c, i) => {
          let acc = 0;
          const dim = hover != null && hover !== i;
          const rects = tss.map((se, si) => {
            const v = se.values[i] || 0;
            const y0 = yT(acc),
              y1 = yT(acc + v);
            acc += v;
            return (
              <rect
                key={`r${i}_${si}`}
                x={cx(i) - bw / 2}
                y={y1}
                width={bw}
                height={Math.max(0, y0 - y1)}
                fill={se.color}
                rx={1}
                fillOpacity={dim ? 0.35 : 0.75}
              />
            );
          });
          const current = i === n - 1 && totals[i] > 0;
          return (
            <g key={`c${i}`}>
              {rects}
              {current && (
                <rect x={cx(i) - bw / 2} y={yT(totals[i])} width={bw} height={plotB - yT(totals[i])} fill={`url(#${hatchId})`} />
              )}
              <text x={cx(i)} y={labelY} fontSize={9} fill={C.muted} textAnchor="middle">
                {c}
              </text>
            </g>
          );
        })}
        {cats.map((_, i) =>
          hours[i] > 0 ? (
            <rect
              key={`d${i}`}
              x={cx(i) - 3.5}
              y={yH(hours[i]) - 3.5}
              width={7}
              height={7}
              transform={`rotate(45 ${cx(i)} ${yH(hours[i])})`}
              fill={C.card}
              stroke={C.text}
              strokeWidth={1.5}
              strokeOpacity={hover != null && hover !== i ? 0.4 : 1}
              pointerEvents="none"
            />
          ) : null,
        )}

        <line x1={padL} x2={w - padR} y1={divY} y2={divY} stroke={C.cardBorder} />
        <text x={padL - 6} y={(stripT + stripB) / 2 + 3} fontSize={9} fill={C.muted} textAnchor="end">
          TSS/h
        </text>
        {linePts && (
          <polyline points={linePts} fill="none" stroke={C.text} strokeOpacity={0.6} strokeWidth={1.5} strokeLinejoin="round" />
        )}
        {ipw.map((v, i) =>
          isNaN(v) ? null : (
            <g key={`i${i}`}>
              <circle cx={cx(i)} cy={yI(v)} r={2.6} fill={C.card} stroke={C.text} strokeWidth={2} />
              <text x={cx(i)} y={yI(v) - 6} fontSize={8.5} fill={C.muted3} textAnchor="middle">
                {Math.round(v)}
              </text>
            </g>
          ),
        )}

        {legend.map((s, si) => {
          const lx = padL + si * 62;
          return (
            <g key={`lg${si}`}>
              {s.color ? (
                <rect x={lx} y={2} width={9} height={9} rx={2} fill={s.color} />
              ) : (
                <rect x={lx + 1.5} y={3.5} width={6} height={6} transform={`rotate(45 ${lx + 4.5} 6.5)`} fill={C.card} stroke={C.text} strokeWidth={1.5} />
              )}
              <text x={lx + 12} y={10} fontSize={8.5} fill={C.muted3}>
                {s.name}
              </text>
            </g>
          );
        })}

        {/* One hover target per week, the full height of plot and strip. */}
        {cats.map((_, i) => (
          <rect
            key={`h${i}`}
            x={padL + i * slot}
            y={padT}
            width={slot}
            height={stripB - padT}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
        {hover != null && (
          <line x1={cx(hover)} x2={cx(hover)} y1={padT} y2={plotB} stroke="#fff" strokeOpacity={0.18} pointerEvents="none" />
        )}
        {tip && (
          <g pointerEvents="none">
            <rect x={tipX} y={tipY} width={tipW} height={tipH} rx={4} fill={C.bar} stroke={C.cardBorder} />
            {tip.lines.map((l, li) => (
              <text key={li} x={tipX + tipW / 2} y={tipY + 13 + li * 12} fontSize={9} fill={li ? C.muted3 : C.text} textAnchor="middle">
                {l}
              </text>
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}
