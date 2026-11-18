import type { CSSProperties } from "react";
import { C } from "../theme";
import { Card } from "./primitives";
import type { KpiData } from "../derive";

// The eight KPI tiles of the load block, each drawing its number on a scale
// (handoff: assets/KpiGridVisual.tsx). The number always stays on the tile; the
// scale only says where it sits. Fixed 4 x 2 at the height the block gives it.

const GC = 34, GR = 28; // gauge centre / radius in a 68px box
const pt = (deg: number) => {
  const t = (deg * Math.PI) / 180;
  return `${(GC + GR * Math.cos(t)).toFixed(2)} ${(GC + GR * Math.sin(t)).toFixed(2)}`;
};
const arc = (a0: number, a1: number) => `M${pt(a0)}A${GR} ${GR} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${pt(a1)}`;
// 270deg gauge opening at the bottom: 135deg -> 405deg, clockwise.
const ang = (v: number, min: number, max: number) => 135 + 270 * Math.min(1, Math.max(0, (v - min) / (max - min)));

const tileStyle: CSSProperties = { padding: "9px 14px 7px", minWidth: 0, overflow: "hidden" };
const overline = (color: string = C.muted): CSSProperties => ({
  fontSize: 10,
  lineHeight: "13px",
  letterSpacing: ".05em",
  textTransform: "uppercase",
  color,
});
const bigNumber = (color: string): CSSProperties => ({ fontSize: 26, lineHeight: 1, fontWeight: 700, color });
const subStyle: CSSProperties = { fontSize: 11, lineHeight: "14px", color: C.muted2 };
const micro: CSSProperties = { fontSize: 9, lineHeight: "12px", color: C.muted };
const signed = (n: number) => (n > 0 ? `+${n}` : String(n));

function BarTile({ label, value, unit, sub, max, maxLabel, ariaLabel }: {
  label: string; value: number; unit: string; sub: string; max: number; maxLabel: string; ariaLabel: string;
}) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <Card style={tileStyle}>
      <div style={overline()}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 3, marginTop: 5 }}>
        <span style={bigNumber(C.white)}>{value}</span>
        <span style={{ fontSize: 13, color: C.muted }}>{unit}</span>
      </div>
      <div style={{ ...subStyle, marginTop: 3 }}>{sub}</div>
      <div role="img" aria-label={ariaLabel} style={{ display: "flex", alignItems: "center", gap: 6, marginTop: "auto" }}>
        <span style={micro}>0</span>
        <div style={{ flexGrow: 1, height: 8, background: C.cardBorder, borderRadius: 2, overflow: "hidden" }}>
          <div style={{ height: 8, width: `${pct}%`, background: C.white, borderRadius: 2 }} />
        </div>
        <span style={micro}>{maxLabel}</span>
      </div>
    </Card>
  );
}

function GaugeTile({ label, color, value, display, min, max, minLabel, maxLabel, sub, zeroTick, ariaLabel }: {
  label: string; color: string; value: number; display: string;
  min: number; max: number; minLabel: string; maxLabel: string; sub: string;
  /** diverging gauge (TSB): fill from 0 and draw a tick at the top */
  zeroTick?: boolean; ariaLabel: string;
}) {
  const from = zeroTick ? 0 : min;
  const a0 = ang(from, min, max);
  const empty = isNaN(value);
  const fillPath =
    value >= from
      ? arc(a0, Math.max(ang(value, min, max), a0 + 0.01))
      : arc(Math.min(ang(value, min, max), a0 - 0.01), a0);
  const text: CSSProperties = { fontFamily: "inherit" };
  return (
    <Card style={tileStyle}>
      <div style={overline(color)}>{label}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 2 }}>
        <div style={{ position: "relative", width: 68, height: 68, flex: "none" }}>
          <svg width={68} height={68} viewBox="0 0 68 68" role="img" aria-label={ariaLabel}>
            <path d={arc(135, 405)} fill="none" stroke={C.cardBorder} strokeWidth={7} strokeLinecap="round" />
            {!empty && <path d={fillPath} fill="none" stroke={color} strokeWidth={7} strokeLinecap="round" />}
            {zeroTick && <line x1={34} y1={0.5} x2={34} y2={11.5} stroke={C.text} strokeWidth={1.5} />}
            <text x={14} y={65} textAnchor="middle" fontSize={9} fill={C.muted} style={text}>{minLabel}</text>
            <text x={54} y={65} textAnchor="middle" fontSize={9} fill={C.muted} style={text}>{maxLabel}</text>
          </svg>
          <div
            style={{
              position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 20, lineHeight: 1, fontWeight: 700, color,
            }}
          >
            {display}
          </div>
        </div>
        <div style={subStyle}>{sub}</div>
      </div>
    </Card>
  );
}

// This week's TSS against the 4-week average, on a shared baseline. The scale
// is 0-350, stretched in steps of 50 for a week above it so no bar overflows.
function TssTile({ tss, avg, sub }: { tss: number; avg: number; sub: string }) {
  const scale = Math.max(350, Math.ceil(Math.max(tss, avg || 0) / 50) * 50);
  const MAX_H = 38;
  const h = (v: number) => (Math.max(0, v || 0) / scale) * MAX_H;
  const col: CSSProperties = { display: "flex", flexDirection: "column", alignItems: "center", gap: 2, width: 32 };
  return (
    <Card style={tileStyle}>
      <div style={overline()}>Weekly TSS</div>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 8, flexGrow: 1, marginTop: 4 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
          <span style={bigNumber(C.yellow)}>{tss}</span>
          <span style={subStyle}>{sub}</span>
        </div>
        <div
          role="img"
          aria-label={`Weekly TSS ${tss} against a 4-week average of ${avg}`}
          style={{ display: "flex", flexDirection: "column", width: 76, flex: "none" }}
        >
          <div
            style={{
              position: "relative", display: "flex", alignItems: "flex-end", justifyContent: "center", gap: 4,
              height: 51, borderBottom: `1px solid ${C.muted}`,
            }}
          >
            <div style={{ position: "absolute", left: 0, right: 0, bottom: h(avg), borderTop: `1px dashed ${C.muted}` }} />
            <div style={col}>
              <span style={{ ...micro, lineHeight: "11px", color: C.yellow }}>{tss}</span>
              <div style={{ width: 28, height: h(tss), background: C.yellow, borderRadius: "2px 2px 0 0" }} />
            </div>
            <div style={col}>
              <span style={{ ...micro, lineHeight: "11px", color: C.muted3 }}>{avg}</span>
              <div style={{ width: 28, height: h(avg), background: C.muted, borderRadius: "2px 2px 0 0" }} />
            </div>
          </div>
          <div style={{ display: "flex", justifyContent: "center", gap: 4, marginTop: 2 }}>
            <span style={{ ...micro, lineHeight: "11px", width: 32, textAlign: "center" }}>TSS</span>
            <span style={{ ...micro, lineHeight: "11px", width: 32, textAlign: "center" }}>AVG</span>
          </div>
        </div>
      </div>
    </Card>
  );
}

// A day with more than one session carries its count after the weekday letter,
// so the boxes add up to the big number.
function SessionsTile({ count, days, letters }: { count: number; days: number[]; letters: string[] }) {
  return (
    <Card style={tileStyle}>
      <div style={overline()}>Sessions</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 5 }}>
        <span style={bigNumber(C.purple)}>{count}</span>
        <span style={subStyle}>last 7 days</span>
      </div>
      <div
        role="img"
        aria-label={`${count} session${count === 1 ? "" : "s"} on ${days.filter((n) => n > 0).length} of the last 7 days`}
        style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 5, marginTop: "auto" }}
      >
        {days.map((n, i) => {
          const on = n > 0;
          return (
            <div
              key={i}
              title={`${n} session${n === 1 ? "" : "s"}`}
              style={{
                height: 22, boxSizing: "border-box", borderRadius: 2,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 9, lineHeight: "12px", fontWeight: 600,
                background: on ? C.purple : C.inner2,
                border: `1px solid ${on ? C.purple : C.cardBorder}`,
                color: on ? C.pageBg : C.muted2,
              }}
            >
              {letters[i]}
              {n > 1 && <span style={{ fontSize: 7, lineHeight: "12px", marginLeft: 1, alignSelf: "flex-start" }}>{n}</span>}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export function KpiGrid({ data }: { data: KpiData }) {
  const ifText = isNaN(data.intensity) ? "—" : data.intensity.toFixed(2);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gridTemplateRows: "repeat(2, minmax(0, 1fr))", gap: 10, height: 216 }}>
      <BarTile label="Weekly Volume" value={data.volumeH} unit="h" sub={data.volumeSub} max={15} maxLabel="15 h"
        ariaLabel={`Weekly volume ${data.volumeH} hours on a scale of 0 to 15`} />
      <GaugeTile label="Fitness · CTL" color={C.ctl} value={data.ctl} display={String(data.ctl)}
        min={0} max={150} minLabel="0" maxLabel="150" sub={data.ctlSub}
        ariaLabel={`Fitness CTL ${data.ctl} on a scale of 0 to 150`} />
      <GaugeTile label="Fatigue · ATL" color={C.atl} value={data.atl} display={String(data.atl)}
        min={0} max={150} minLabel="0" maxLabel="150" sub={data.atlSub}
        ariaLabel={`Fatigue ATL ${data.atl} on a scale of 0 to 150`} />
      <GaugeTile label="Form · TSB" color={C.tsb} value={data.tsb} display={signed(data.tsb)}
        min={-40} max={40} minLabel="-40" maxLabel="+40" sub={data.tsbSub} zeroTick
        ariaLabel={`Form TSB ${signed(data.tsb)} on a scale of -40 to +40`} />
      <TssTile tss={data.tss} avg={data.tssAvg} sub={data.tssSub} />
      <SessionsTile count={data.sessions} days={data.days} letters={data.dayLetters} />
      <GaugeTile label="Weekly Avg IF" color={C.red} value={data.intensity} display={ifText}
        min={0} max={1} minLabel="0" maxLabel="1" sub={data.intensitySub}
        ariaLabel={`Weekly average IF ${ifText} on a scale of 0 to 1`} />
      <BarTile label="Weekly Distance" value={data.distanceKm} unit="km" sub={data.distanceSub} max={120} maxLabel="120 km"
        ariaLabel={`Weekly distance ${data.distanceKm} kilometres on a scale of 0 to 120`} />
    </div>
  );
}
