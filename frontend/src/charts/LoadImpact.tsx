import { C } from "../theme";

// What one completed workout did to the day's Fitness, Fatigue and Form,
// against a rest day: three slim tiles above the Training Effect dials, each a
// signed change and a dot bar. Design: the "Workout Load Impact" canvas.
//
// The change is the workout's own share of the day's load, from the linear EWMA
// in app/metrics/calculator.py (`_step`, mirrored in forecast.ts): a day's TSS
// adds TSS/42 to CTL and TSS/7 to ATL, and same-day TSB is CTL - ATL. It is
// not the day-over-day change: an easy session on a high-CTL day still lowers
// CTL from yesterday, which would read as the workout costing fitness.
//
// All three share one scale, 1 dot = 1 point, so the bar shows Fatigue jumping
// about six times as far as Fitness. 15 dots; a bigger change fills the bar and
// the number carries the rest. The last dot fills partially.

const CTL_DAYS = 42;
const ATL_DAYS = 7;
const DOTS = 15;

// The tiles are as tall as the RPE / Feel tile: 44px while its dial column is
// 300px wide (a 610px dial row, 2 x 300 + 10 gap), shrinking with that column
// below it. Every length follows the same rule, so the two stay equal at any
// modal width. Three tiles want more room than 610px, so this row may run to
// 780px, centred.
const ROW_W = 780;
const GAP = 10;
const DIAL_ROW = 610;
const u = (px: number) => `min(${px}px, calc((100cqw - ${GAP}px) * ${(px / (DIAL_ROW - GAP)).toFixed(5)}))`;

export function loadImpact(tss: number): { ctl: number; atl: number; tsb: number } {
  const ctl = tss / CTL_DAYS;
  const atl = tss / ATL_DAYS;
  return { ctl, atl, tsb: ctl - atl };
}

function signed(v: number): string {
  const r = Math.round(v * 10) / 10;
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(1)}`;
}

function ImpactTile({ name, abbr, value, color }: { name: string; abbr: string; value: number; color: string }) {
  const mag = Math.abs(value);
  const delta = signed(value);
  return (
    <div
      role="img"
      aria-label={`${name} ${delta} against a rest day`}
      title={`${name} (${abbr}) ${delta} from this workout, against a rest day`}
      style={{
        background: C.modalInner,
        border: `1px solid ${C.modalControlBorder}`,
        borderRadius: 8,
        padding: `0 ${u(12)}`,
        display: "flex",
        alignItems: "center",
        gap: u(8),
        height: u(44),
        minWidth: 0,
        boxSizing: "border-box",
      }}
    >
      <span style={{ fontSize: u(11), fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase", color: C.mutedBright, whiteSpace: "nowrap" }}>
        {name}
      </span>
      <span
        style={{
          fontSize: u(15),
          fontWeight: 700,
          lineHeight: 1,
          color,
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
          minWidth: u(38),
        }}
      >
        {delta}
      </span>
      <div aria-hidden style={{ flex: 1, minWidth: 0, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 0 }}>
        {Array.from({ length: DOTS }, (_, i) => {
          const f = Math.max(0, Math.min(1, mag - i));
          const pct = Math.round(f * 100);
          const dim = `color-mix(in srgb, ${color} 22%, transparent)`;
          const bg = f >= 1 ? color : f <= 0.05 ? dim : `linear-gradient(to right, ${color} ${pct}%, ${dim} ${pct}%)`;
          return <span key={i} style={{ width: u(5), height: u(5), borderRadius: "50%", flex: "none", background: bg }} />;
        })}
      </div>
    </div>
  );
}

// The whole section: title row over the three tiles. The caller renders it
// only for a workout with a positive TSS.
export function LoadImpactSection({ tss }: { tss: number }) {
  const { ctl, atl, tsb } = loadImpact(tss);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: C.white }}>Load impact</span>
        <span style={{ fontSize: 12, color: C.mutedBright }}>{Math.round(tss)} TSS against a rest day · 1 dot = 1 point</span>
      </div>
      <div style={{ width: "100%", maxWidth: ROW_W, margin: "0 auto", containerType: "inline-size" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: GAP }}>
          <ImpactTile name="Fitness" abbr="CTL" value={ctl} color={C.ctl} />
          <ImpactTile name="Fatigue" abbr="ATL" value={atl} color={C.atl} />
          <ImpactTile name="Form" abbr="TSB" value={tsb} color={C.tsb} />
        </div>
      </div>
    </div>
  );
}
