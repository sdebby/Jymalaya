import { C } from "../theme";

// The athlete's own rating of a completed workout, answered on the watch when
// the activity is saved and read from the FIT file: perceived effort (RPE,
// 1-10) and feel (Garmin's 0-100 in steps of 25). Drawn directly below the
// Training Effect dials as two slim tiles in the same two centred columns, at a
// third of a dial tile's height. Display only -- the coach never sees it.
//
// Either answer can be missing (Garmin lets the athlete skip one): only the
// tiles with a value are drawn, and the caller leaves the block out with none.

// Garmin's wording for each RPE step.
const RPE_LABELS = [
  "Very Light",
  "Light",
  "Moderate",
  "Somewhat Hard",
  "Hard",
  "Hard",
  "Very Hard",
  "Very Hard",
  "Extremely Hard",
  "Maximum",
];
// Two RPE steps per colour on the zone ramp the Training Effect dials use.
const RPE_COLORS = [C.form, C.fit, C.yellow, C.fat, C.red];
// Feel runs the other way: the weak end is the warning.
const FEELS = [
  { label: "Very Weak", color: C.red },
  { label: "Weak", color: C.fat },
  { label: "Normal", color: C.yellow },
  { label: "Strong", color: C.fit },
  { label: "Very Strong", color: C.form },
];

export function rpeBand(rpe: number): { step: number; label: string; color: string } {
  const step = Math.min(10, Math.max(1, Math.round(rpe)));
  return { step, label: RPE_LABELS[step - 1], color: RPE_COLORS[Math.floor((step - 1) / 2)] };
}

export function feelBand(feel: number): { step: number; label: string; color: string } {
  const step = Math.min(5, Math.max(1, Math.round(feel / 25) + 1));
  return { step, ...FEELS[step - 1] };
}

// Same design width as a Training Effect tile and a third of its 133px height.
// Lengths are in container-width units so the tile shrinks as one picture with
// the dial tile above it.
const TILE_W = 300;
const TILE_H = 44;
const cq = (px: number) => `${((px / TILE_W) * 100).toFixed(3)}cqw`;

function RatingTile({
  title,
  value,
  label,
  color,
  step,
  steps,
  aria,
}: {
  title: string;
  value?: string;
  label: string;
  color: string;
  step: number;
  steps: number;
  aria: string;
}) {
  return (
    <div style={{ containerType: "inline-size", minWidth: 0 }}>
      <div
        role="img"
        aria-label={aria}
        style={{
          background: C.modalInner,
          border: `1px solid ${C.modalControlBorder}`,
          borderRadius: 8,
          padding: `0 ${cq(14)}`,
          display: "flex",
          alignItems: "center",
          gap: cq(10),
          aspectRatio: `${TILE_W} / ${TILE_H}`,
          boxSizing: "border-box",
        }}
      >
        <span style={{ fontSize: cq(11), fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase", color: C.mutedBright, whiteSpace: "nowrap" }}>
          {title}
        </span>
        {value && (
          <span style={{ fontSize: cq(17), fontWeight: 700, lineHeight: 1, color: C.text, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
            {value}
          </span>
        )}
        <span style={{ fontSize: cq(13), fontWeight: 700, color, whiteSpace: "nowrap" }}>{label}</span>
        <div aria-hidden style={{ display: "flex", gap: cq(2), flex: 1, minWidth: 0, marginLeft: cq(2) }}>
          {Array.from({ length: steps }, (_, i) => (
            <span
              key={i}
              style={{ flex: 1, height: cq(6), borderRadius: cq(2), background: color, opacity: i < step ? 1 : 0.22 }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export function WorkoutRating({ rpe, feel }: { rpe?: number | null; feel?: number | null }) {
  const tiles: React.ReactNode[] = [];
  if (rpe != null) {
    const b = rpeBand(rpe);
    tiles.push(
      <RatingTile
        key="rpe"
        title="RPE"
        value={`${b.step}/10`}
        label={b.label}
        color={b.color}
        step={b.step}
        steps={10}
        aria={`Perceived effort ${b.step} of 10, ${b.label}`}
      />,
    );
  }
  if (feel != null) {
    const b = feelBand(feel);
    tiles.push(
      <RatingTile key="feel" title="Feel" label={b.label} color={b.color} step={b.step} steps={5} aria={`Feel: ${b.label}`} />,
    );
  }
  if (tiles.length === 0) return null;
  return (
    <div
      title="Your own rating, entered on the watch when the workout was saved"
      style={{ display: "grid", gridTemplateColumns: `repeat(${tiles.length}, minmax(0, ${TILE_W}px))`, justifyContent: "center", gap: 10 }}
    >
      {tiles}
    </div>
  );
}
