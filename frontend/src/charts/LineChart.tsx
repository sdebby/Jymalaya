// Multi-series line chart with optional area fill, gridlines, a legend, and a
// hover crosshair tooltip showing every series' value at the nearest point.
// Ported from the Option A design's lineChart() generator.
//
// A null value is a gap: the line breaks there, no dot is drawn and the domain
// ignores it (the recovery-trend cards; every other chart passes dense data).
import { useState } from "react";
import { C } from "../theme";
import { useChartWidth } from "./useChartWidth";

export interface Series {
  values: (number | null)[];
  color: string;
  area?: boolean;
  dots?: boolean;
}
interface XLabel {
  i: number;
  label: string;
}

interface Props {
  series: Series[];
  n: number;
  w?: number;
  h?: number;
  area?: boolean;
  dots?: boolean;
  min?: number;
  max?: number;
  yFmt?: (v: number) => string;
  xLabels?: XLabel[];
  legend?: { label: string; color: string }[];
  // Per-index tooltip header (e.g. the point's date). Enables the hover tooltip;
  // series rows are named after the legend entries (same order as `series`).
  xTip?: string[];
  // Value formatting inside the tooltip (default: rounded integer).
  tipFmt?: (v: number) => string;
  // Coloured horizontal bands behind the lines, in data units. An open end
  // (Infinity / -Infinity) runs to the edge of the plot. A `short` label is
  // written inside the band's left edge when the band is tall enough for it.
  bands?: { from: number; to: number; color: string; short?: string }[];
  // Gridline values; default is five evenly spaced across the range.
  ticks?: number[];
  // Headroom added around the data range, as a fraction of it (default 0.14).
  domainPad?: number;
  // The swatch legend along the top (default on). Tooltip rows are still named
  // after the legend entries when it is hidden.
  showLegend?: boolean;
  // Space above the plot; 18 leaves room for the legend (default).
  padTop?: number;
  // The other plot margins (defaults 40 / 14 / 22).
  padL?: number;
  padR?: number;
  padB?: number;
  // A dashed reference line across the plot at this value (e.g. the mean). No
  // label of its own: the card head carries the number.
  refLine?: number;
  // "rows" (default): the date header over one swatch row per series.
  // "single": the compact single-series box -- the date, then the value
  // formatted by tipFmt -- flipped left past the middle of the plot.
  tipLayout?: "rows" | "single";
  // Draw the lines as smooth curves through the points instead of straight
  // segments (default off).
  smooth?: boolean;
}

type Pt = readonly [number, number];
const f1 = (n: number) => n.toFixed(1);

function linearPath(seg: Pt[]): string {
  return seg.map((p, i) => `${i ? "L" : "M"}${f1(p[0])} ${f1(p[1])}`).join(" ");
}

// Monotone cubic (Fritsch-Carlson) through the points: rounded at every day,
// but never overshooting a reading, so the curve shows no peak or dip the data
// does not have -- a plain Catmull-Rom would bulge past a high night's HRV.
function smoothPath(seg: Pt[]): string {
  const n = seg.length;
  if (n < 3) return linearPath(seg);
  const dx: number[] = [];
  const m: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(seg[i + 1][0] - seg[i][0]);
    m.push((seg[i + 1][1] - seg[i][1]) / dx[i]);
  }
  const t: number[] = [m[0]];
  for (let i = 1; i < n - 1; i++) {
    if (m[i - 1] * m[i] <= 0) t.push(0);
    else {
      const w1 = 2 * dx[i] + dx[i - 1];
      const w2 = dx[i] + 2 * dx[i - 1];
      t.push((w1 + w2) / (w1 / m[i - 1] + w2 / m[i]));
    }
  }
  t.push(m[n - 2]);
  let d = `M${f1(seg[0][0])} ${f1(seg[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = seg[i];
    const [x1, y1] = seg[i + 1];
    const h = dx[i] / 3;
    d += ` C${f1(x0 + h)} ${f1(y0 + t[i] * h)} ${f1(x1 - h)} ${f1(y1 - t[i + 1] * h)} ${f1(x1)} ${f1(y1)}`;
  }
  return d;
}

// Breaks a series into runs of consecutive non-null points.
function runs(values: (number | null)[]): [number, number][][] {
  const out: [number, number][][] = [];
  let cur: [number, number][] = [];
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) {
      if (cur.length) out.push(cur);
      cur = [];
    } else cur.push([i, v]);
  });
  if (cur.length) out.push(cur);
  return out;
}

export function LineChart({
  series,
  n,
  w: wProp = 620,
  h = 200,
  area = true,
  dots = false,
  min,
  max,
  yFmt,
  xLabels = [],
  legend = [],
  xTip,
  tipFmt,
  bands,
  ticks,
  domainPad = 0.14,
  showLegend = true,
  padTop,
  padL = 40,
  padR = 14,
  padB = 22,
  refLine,
  tipLayout = "rows",
  smooth = false,
}: Props) {
  const [hoverI, setHoverI] = useState<number | null>(null);
  // Widen the viewBox to the card's real aspect so big screens don't letterbox.
  const { ref, w } = useChartWidth(h, wProp);
  const padT = padTop ?? 18;
  const iw = w - padL - padR,
    ih = h - padT - padB;

  const all = series.flatMap((s) => s.values).filter((v): v is number => v != null && Number.isFinite(v));
  let vmin = min != null ? min : all.length ? Math.min(...all) : 0;
  let vmax = max != null ? max : all.length ? Math.max(...all) : 1;
  if (vmin === vmax) vmax += 1;
  const pad = (vmax - vmin) * domainPad;
  vmin -= pad;
  vmax += pad;
  const range = vmax - vmin;

  const xAt = (i: number) => padL + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const yAt = (v: number) => padT + ih - ((v - vmin) / range) * ih;

  const gridVals = ticks ?? [0, 1, 2, 3, 4].map((t) => vmin + (range * t) / 4);
  const grid = [];
  for (let t = 0; t < gridVals.length; t++) {
    const val = gridVals[t];
    const y = yAt(val);
    grid.push(
      <line key={`g${t}`} x1={padL} x2={w - padR} y1={y} y2={y} stroke={C.gridline} />,
      <text key={`gt${t}`} x={padL - 6} y={y + 3} fontSize={9} fill={C.muted} textAnchor="end">
        {yFmt ? yFmt(val) : Math.round(val)}
      </text>,
    );
  }

  // The svg is scaled by viewBox, so mouse coordinates must be mapped back into
  // viewBox space (getScreenCTM handles the meet letterboxing) before picking
  // the nearest data index.
  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    if (!xTip) return;
    const ctm = e.currentTarget.getScreenCTM();
    if (!ctm) return;
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
    if (p.x < padL - 6 || p.x > w - padR + 6 || p.y < 0 || p.y > h) {
      setHoverI(null);
      return;
    }
    const i = Math.round(((p.x - padL) / iw) * (n - 1));
    setHoverI(Math.max(0, Math.min(n - 1, i)));
  }

  return (
    <div ref={ref} style={{ width: "100%", height: "100%" }}>
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width="100%"
      height="100%"
      preserveAspectRatio="xMidYMid meet"
      style={{ display: "block" }}
      onMouseMove={xTip ? onMove : undefined}
      onMouseLeave={xTip ? () => setHoverI(null) : undefined}
    >
      {bands?.map((b, k) => {
        const top = yAt(Math.min(b.to, vmax));
        const bottom = yAt(Math.max(b.from, vmin));
        if (bottom <= top) return null;
        return (
          <g key={`b${k}`}>
            <rect x={padL} y={top} width={iw} height={bottom - top} fill={b.color} fillOpacity={0.15} />
            {b.short && bottom - top >= 11 && (
              <text x={padL + 4} y={(top + bottom) / 2 + 3} fontSize={8} fill={b.color} fillOpacity={0.85}>
                {b.short}
              </text>
            )}
          </g>
        );
      })}
      {grid}
      {xLabels.map((xl, k) => (
        <text key={`xl${k}`} x={xAt(xl.i)} y={h - 6} fontSize={9} fill={C.muted} textAnchor="middle">
          {xl.label}
        </text>
      ))}
      {refLine != null && Number.isFinite(refLine) && (
        <line
          x1={padL}
          x2={w - padR}
          y1={yAt(refLine)}
          y2={yAt(refLine)}
          stroke={C.muted}
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.7}
        />
      )}
      {series.map((s, si) => {
        const segs = runs(s.values).map((run) => run.map(([i, v]) => [xAt(i), yAt(v)] as const));
        const pts = segs.flat();
        const d = segs.map((seg) => (smooth ? smoothPath(seg) : linearPath(seg))).join(" ");
        // The area closes along the bottom from the first to the last index, so
        // it only holds for an unbroken series.
        const showArea = area && s.area !== false && segs.length === 1 && pts.length === n;
        return (
          <g key={`s${si}`}>
            {showArea && (
              <path
                d={`${d} L ${xAt(n - 1).toFixed(1)} ${yAt(vmin).toFixed(1)} L ${xAt(0).toFixed(1)} ${yAt(vmin).toFixed(1)} Z`}
                fill={s.color}
                fillOpacity={0.13}
              />
            )}
            <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {(dots || s.dots) &&
              pts.map((p, i) => (
                <circle key={`d${si}_${i}`} cx={p[0]} cy={p[1]} r={2.6} fill={C.card} stroke={s.color} strokeWidth={1.6} />
              ))}
          </g>
        );
      })}
      {showLegend && legend.map((lg, k) => {
        const lx = padL + k * 78;
        return (
          <g key={`lg${k}`}>
            <rect x={lx} y={2} width={9} height={9} rx={2} fill={lg.color} />
            <text x={lx + 13} y={10} fontSize={9} fill={C.muted3}>
              {lg.label}
            </text>
          </g>
        );
      })}
      {xTip && hoverI != null && hoverI < n && tipLayout === "single" && (
        <SingleTooltip
          i={hoverI}
          header={xTip[hoverI] ?? ""}
          series={series[0]}
          tipFmt={tipFmt}
          xAt={xAt}
          yAt={yAt}
          padT={padT}
          ih={ih}
          w={w}
        />
      )}
      {xTip && hoverI != null && hoverI < n && tipLayout === "rows" && (
        <Tooltip
          i={hoverI}
          header={xTip[hoverI] ?? ""}
          series={series}
          legend={legend}
          tipFmt={tipFmt}
          xAt={xAt}
          yAt={yAt}
          padT={padT}
          ih={ih}
          w={w}
        />
      )}
    </svg>
    </div>
  );
}

function Tooltip({
  i,
  header,
  series,
  legend,
  tipFmt,
  xAt,
  yAt,
  padT,
  ih,
  w,
}: {
  i: number;
  header: string;
  series: Series[];
  legend: { label: string; color: string }[];
  tipFmt?: (v: number) => string;
  xAt: (i: number) => number;
  yAt: (v: number) => number;
  padT: number;
  ih: number;
  w: number;
}) {
  const hx = xAt(i);
  const rows = series.map((s, si) => {
    const v = s.values[i];
    return {
      color: s.color,
      name: legend[si]?.label ?? `#${si + 1}`,
      text: v == null ? "--" : tipFmt ? tipFmt(v) : String(Math.round(v)),
    };
  });
  const lineH = 12;
  const boxH = 9 + (header ? lineH : 0) + rows.length * lineH;
  const longest = Math.max(header.length, ...rows.map((r) => r.name.length + r.text.length + 2));
  const boxW = longest * 5.1 + 26;
  // Prefer the right side of the crosshair; flip left near the edge.
  const bx = hx + 10 + boxW > w - 2 ? hx - 10 - boxW : hx + 10;
  const by = padT + 2;
  return (
    <g pointerEvents="none">
      <line x1={hx} x2={hx} y1={padT} y2={padT + ih} stroke={C.crosshair} />
      {series.map((s, si) => {
        const v = s.values[i];
        if (v == null) return null;
        return <circle key={`hd${si}`} cx={hx} cy={yAt(v)} r={3} fill={s.color} stroke="#fff" strokeWidth={1} strokeOpacity={0.6} />;
      })}
      <rect x={bx} y={by} width={boxW} height={boxH} rx={4} fill={C.bar} stroke={C.cardBorder} />
      {header && (
        <text x={bx + 8} y={by + 13} fontSize={9} fontWeight={700} fill={C.text}>
          {header}
        </text>
      )}
      {rows.map((r, k) => {
        const ty = by + 13 + (header ? lineH : 0) + k * lineH;
        return (
          <g key={`tr${k}`}>
            <rect x={bx + 8} y={ty - 6.5} width={7} height={7} rx={1.5} fill={r.color} />
            <text x={bx + 19} y={ty} fontSize={9} fill={C.textChip}>
              {r.name}
            </text>
            <text x={bx + boxW - 8} y={ty} fontSize={9} fontWeight={600} fill={C.text} textAnchor="end">
              {r.text}
            </text>
          </g>
        );
      })}
    </g>
  );
}

// The recovery-trend tooltip (design system RecoveryTrends): an 86x34 box, the
// date over the value, on the right of the crosshair until the middle of the
// plot and on its left after it. The hovered day's dot fills with the series
// colour; a day with no reading says so instead.
function SingleTooltip({
  i,
  header,
  series,
  tipFmt,
  xAt,
  yAt,
  padT,
  ih,
  w,
}: {
  i: number;
  header: string;
  series: Series | undefined;
  tipFmt?: (v: number) => string;
  xAt: (i: number) => number;
  yAt: (v: number) => number;
  padT: number;
  ih: number;
  w: number;
}) {
  if (!series) return null;
  const hx = xAt(i);
  const v = series.values[i];
  const text = v == null ? "no reading" : tipFmt ? tipFmt(v) : String(Math.round(v));
  const boxW = 86,
    boxH = 34;
  const bx = hx > w / 2 ? hx - boxW - 8 : hx + 8;
  const by = padT + 4;
  return (
    <g pointerEvents="none">
      <line x1={hx} x2={hx} y1={padT} y2={padT + ih} stroke={C.crosshair} />
      {v != null && <circle cx={hx} cy={yAt(v)} r={3.6} fill={series.color} stroke={C.card} strokeWidth={1.5} />}
      <rect x={bx} y={by} width={boxW} height={boxH} rx={3} fill={C.bar} stroke={C.cardBorder} />
      <text x={bx + 8} y={by + 14} fontSize={10} fill={C.muted3}>
        {header}
      </text>
      <text x={bx + 8} y={by + 28} fontSize={11} fontWeight={600} fill={C.text}>
        {text}
      </text>
    </g>
  );
}
