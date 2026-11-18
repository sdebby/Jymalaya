import { C } from "../theme";

// Garmin's Training Effect for one completed workout: two 270° dials,
// Aerobic and Anaerobic, on Garmin's 0-5.0 scale, with the primary benefit
// centred above them. Design: the "Training Effect Gauges" canvas and the
// design system's TrainingEffect card. Display only -- the coach never sees it.
//
// Bands are Garmin's, coloured on the dashboard's zone ramp (blue to red, as
// HR_ZONE_COLOR) with grey for No Benefit; every band stays drawn at 30% so the
// scale reads without a legend, and the filled arc takes the colour of the band
// the value lands in, like the KPI gauges.

export const TE_BANDS: { from: number; label: string; color: string }[] = [
  { from: 0, label: "No Benefit", color: C.muted },
  { from: 1, label: "Minor Benefit", color: C.form },
  { from: 2, label: "Maintaining", color: C.fit },
  { from: 3, label: "Impacting", color: C.yellow },
  { from: 4, label: "Highly Impacting", color: C.fat },
];
const OVERREACHING = { label: "Overreaching", color: C.red };

// Garmin's trainingEffectLabel keys, as the list rows send them. An unknown key
// is shown title-cased with the muted dot rather than dropped.
const BENEFITS: Record<string, { name: string; color: string }> = {
  RECOVERY: { name: "Recovery", color: C.form },
  AEROBIC_BASE: { name: "Base", color: C.fit },
  BASE: { name: "Base", color: C.fit },
  TEMPO: { name: "Tempo", color: C.yellow },
  LACTATE_THRESHOLD: { name: "Lactate Threshold", color: C.fat },
  THRESHOLD: { name: "Lactate Threshold", color: C.fat },
  VO2MAX: { name: "VO2 Max", color: C.red },
  ANAEROBIC_CAPACITY: { name: "Anaerobic Capacity", color: C.red },
  SPEED: { name: "Sprint", color: C.red },
  SPRINT: { name: "Sprint", color: C.red },
};

export function teBand(v: number): { label: string; color: string } {
  if (v >= 5) return OVERREACHING;
  return TE_BANDS[Math.min(4, Math.max(0, Math.floor(v + 1e-9)))];
}

export function teBenefit(key?: string | null): { name: string; color: string } | null {
  if (!key) return null;
  const k = key.toUpperCase();
  if (k === "NO_BENEFIT" || k === "NONE") return null;
  return (
    BENEFITS[k] ?? {
      name: k.toLowerCase().split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
      color: C.muted,
    }
  );
}

// Geometry: viewBox 160 x 150, centre (80, 80), radius 62. The dial opens at
// the bottom: 0 sits at 135°, 5.0 at 405°, so one point of TE is 54°.
const R = 62;
function pt(v: number, r: number): [number, number] {
  const a = ((135 + 54 * v) * Math.PI) / 180;
  return [80 + r * Math.cos(a), 80 + r * Math.sin(a)];
}
function arc(v0: number, v1: number): string {
  if (v1 <= v0) return "";
  const [x0, y0] = pt(v0, R);
  const [x1, y1] = pt(v1, R);
  const large = (v1 - v0) * 54 > 180 ? 1 : 0;
  return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${R} ${R} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
const SEPARATORS = [1, 2, 3, 4]
  .map((s) => {
    const [a, b] = pt(s, 55);
    const [c, d] = pt(s, 69);
    return `M${a.toFixed(2)} ${b.toFixed(2)} L${c.toFixed(2)} ${d.toFixed(2)}`;
  })
  .join(" ");

// Tile geometry, designed at TILE_W x TILE_H px: as tall as a workout stream
// graph card (head + the 96px plot) at full size. Every length inside a tile is
// written in container-width units (cq below), so when the modal narrows the
// tile shrinks as one picture -- dial, number, labels and padding together --
// instead of wrapping or squashing. The two tiles never stack.
const TILE_W = 300;
const TILE_H = 133;
const DIAL_H = 109;
const DIAL_W = (DIAL_H * 160) / 150;
// px at the design width -> a length that scales with the tile's width.
const cq = (px: number) => `${((px / TILE_W) * 100).toFixed(3)}cqw`;

function Gauge({ title, value }: { title: string; value: number }) {
  const v = Math.round(Math.max(0, Math.min(5, value)) * 10) / 10;
  const band = teBand(v);
  const [dx, dy] = pt(v, R);
  // The dial on the left, the overline and band name beside it.
  return (
    <div style={{ containerType: "inline-size", minWidth: 0 }}>
      <div
        style={{
          background: C.modalInner,
          border: `1px solid ${C.modalControlBorder}`,
          borderRadius: 8,
          padding: `${cq(11)} ${cq(18)} ${cq(11)} ${cq(12)}`,
          display: "flex",
          alignItems: "center",
          gap: cq(14),
          aspectRatio: `${TILE_W} / ${TILE_H}`,
          boxSizing: "border-box",
        }}
      >
        <div style={{ position: "relative", width: cq(DIAL_W), aspectRatio: "160 / 150", flex: "none" }}>
          <svg
            viewBox="0 0 160 150"
            style={{ display: "block", width: "100%", height: "100%" }}
            role="img"
            aria-label={`${title} Training Effect ${v.toFixed(1)}, ${band.label}`}
          >
            {TE_BANDS.map((b) => (
              <path key={b.from} d={arc(b.from, b.from + 1)} fill="none" stroke={b.color} strokeWidth={11} opacity={0.3} />
            ))}
            {v > 0 && <path d={arc(0, v)} fill="none" stroke={band.color} strokeWidth={11} />}
            <path d={SEPARATORS} fill="none" stroke={C.modalInner} strokeWidth={3} />
            {v > 0 && <circle cx={dx} cy={dy} r={5.5} fill={C.modalInner} stroke={band.color} strokeWidth={3} />}
            <text x={36} y={147} textAnchor="middle" fontSize={13} fontWeight={600} fill={C.muted}>
              0
            </text>
            <text x={124} y={147} textAnchor="middle" fontSize={13} fontWeight={600} fill={C.muted}>
              5.0
            </text>
          </svg>
          <div
            aria-hidden
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              // The dial's centre is at y=80 of 150.
              top: `${((80 / 150) * 100).toFixed(2)}%`,
              transform: "translateY(-50%)",
              textAlign: "center",
              fontSize: cq(24),
              fontWeight: 700,
              lineHeight: 1,
              color: C.text,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {v.toFixed(1)}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: cq(6), minWidth: 0 }}>
          <span style={{ fontSize: cq(11), fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase", color: C.mutedBright, whiteSpace: "nowrap" }}>
            {title}
          </span>
          <span style={{ fontSize: cq(14), fontWeight: 700, color: v < 1 ? C.muted2 : band.color, whiteSpace: "nowrap" }}>{band.label}</span>
        </div>
      </div>
    </div>
  );
}

// The whole modal section: title row (benefit centred) over the two dials.
// The caller renders it only when Garmin sent at least one of the two scores.
export function TrainingEffectSection({
  aerobic,
  anaerobic,
  label,
}: {
  aerobic?: number | null;
  anaerobic?: number | null;
  label?: string | null;
}) {
  const benefit = teBenefit(label);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto minmax(0, 1fr)", alignItems: "center", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: C.white }}>Training Effect</span>
          <span style={{ fontSize: 12, color: C.mutedBright }}>Garmin's estimate · 0–5.0</span>
        </div>
        {benefit ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
            <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".08em", color: C.mutedBright }}>PRIMARY BENEFIT</span>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", background: benefit.color, flexShrink: 0 }} />
              <span style={{ fontSize: 19, fontWeight: 700, color: C.text }}>{benefit.name}</span>
            </span>
          </div>
        ) : (
          <span />
        )}
        <span />
      </div>
      {/* Always two columns, each at most the design width: centred while
          there is room, shrinking together (never wrapping) when there is not. */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(2, minmax(0, ${TILE_W}px))`, justifyContent: "center", gap: 10 }}>
        <Gauge title="Aerobic" value={aerobic ?? 0} />
        <Gauge title="Anaerobic" value={anaerobic ?? 0} />
      </div>
    </div>
  );
}
