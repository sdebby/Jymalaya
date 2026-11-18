// Combo chart: bars (e.g. planned daily TSS) + overlaid line (e.g. projected CTL)
// on a shared y-axis. Same hand-rolled SVG conventions as StackedBars/LineChart.
import { C } from "../theme";
import { useChartWidth } from "./useChartWidth";

interface Props {
  cats: string[];
  bars: { name: string; color: string; values: number[] };
  line: { name: string; color: string; values: number[] };
  w?: number;
  h?: number;
  fmtY?: (v: number) => string;
}

export function BarsLine({ cats, bars, line, w: wProp = 620, h = 200, fmtY }: Props) {
  // Widen the viewBox to the card's real aspect so big screens don't letterbox.
  const { ref, w } = useChartWidth(h, wProp);
  const padL = 36,
    padR = 12,
    padT = 18,
    padB = 24;
  const iw = w - padL - padR,
    ih = h - padT - padB;

  const vmax = Math.max(...bars.values, ...line.values, 1) * 1.12;
  const yAt = (v: number) => padT + ih - (v / vmax) * ih;
  const n = cats.length,
    slot = iw / n,
    bw = slot * 0.45;

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

  const cx = (ci: number) => padL + (ci + 0.5) * slot;
  const lined = line.values
    .map((v, i) => `${i ? "L" : "M"}${cx(i).toFixed(1)} ${yAt(v).toFixed(1)}`)
    .join(" ");

  return (
    <div ref={ref} style={{ width: "100%", height: "100%" }}>
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="100%" preserveAspectRatio="xMidYMid meet" style={{ display: "block" }}>
      {grid}
      {cats.map((c, ci) => {
        const v = bars.values[ci] || 0;
        const y = yAt(v);
        return (
          <g key={`c${ci}`}>
            <rect x={cx(ci) - bw / 2} y={y} width={bw} height={Math.max(0, yAt(0) - y)} fill={bars.color} fillOpacity={0.75} rx={1} />
            {v > 0 && (
              <text x={cx(ci)} y={y - 4} fontSize={8.5} fill={C.muted3} textAnchor="middle">
                {Math.round(v)}
              </text>
            )}
            <text x={cx(ci)} y={h - 8} fontSize={9} fill={C.muted} textAnchor="middle">
              {c}
            </text>
          </g>
        );
      })}
      <path d={lined} fill="none" stroke={line.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {line.values.map((v, i) => (
        <circle key={`d${i}`} cx={cx(i)} cy={yAt(v)} r={2.6} fill={C.card} stroke={line.color} strokeWidth={1.6} />
      ))}
      {[bars, line].map((s, k) => {
        const lx = padL + k * 92;
        return (
          <g key={`lg${k}`}>
            <rect x={lx} y={2} width={9} height={9} rx={2} fill={s.color} />
            <text x={lx + 13} y={10} fontSize={9} fill={C.muted3}>
              {s.name}
            </text>
          </g>
        );
      })}
    </svg>
    </div>
  );
}
