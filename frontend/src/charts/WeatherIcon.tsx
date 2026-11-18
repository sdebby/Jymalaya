// Tiny weather glyphs. Ported from weatherIcon().
import { C } from "../theme";

export type Sky = "sun" | "partly" | "rain" | "cloud";

function Sun({ cx, cy, r, rays }: { cx: number; cy: number; r: number; rays: boolean }) {
  const lines = [];
  if (rays) {
    for (let i = 0; i < 8; i++) {
      const ang = (i * Math.PI) / 4;
      lines.push(
        <line
          key={i}
          x1={cx + Math.cos(ang) * (r + 1.8)}
          y1={cy + Math.sin(ang) * (r + 1.8)}
          x2={cx + Math.cos(ang) * (r + 4)}
          y2={cy + Math.sin(ang) * (r + 4)}
          stroke={C.yellow}
          strokeWidth={1.3}
          strokeLinecap="round"
        />,
      );
    }
  }
  return (
    <>
      <circle cx={cx} cy={cy} r={r} fill={C.yellow} />
      {lines}
    </>
  );
}

function Cloud({ fill }: { fill: string }) {
  return (
    <>
      <ellipse cx={10} cy={16} rx={5} ry={4} fill={fill} />
      <ellipse cx={15} cy={16} rx={5} ry={4} fill={fill} />
      <ellipse cx={12.5} cy={12.5} rx={5} ry={4.5} fill={fill} />
      <rect x={5} y={15} width={15} height={5} rx={2.5} fill={fill} />
    </>
  );
}

export function WeatherIcon({ sky, size = 22 }: { sky: Sky; size?: number }) {
  let body;
  if (sky === "sun") body = <Sun cx={12} cy={12} r={5} rays />;
  else if (sky === "partly")
    body = (
      <>
        <Sun cx={8} cy={8} r={3.2} rays />
        <Cloud fill="#aab0bb" />
      </>
    );
  else if (sky === "rain")
    body = (
      <>
        <Cloud fill="#7e858f" />
        <line x1={9} y1={19} x2={8} y2={22} stroke={C.form} strokeWidth={1.6} strokeLinecap="round" />
        <line x1={13} y1={19} x2={12} y2={22.5} stroke={C.form} strokeWidth={1.6} strokeLinecap="round" />
        <line x1={17} y1={19} x2={16} y2={22} stroke={C.form} strokeWidth={1.6} strokeLinecap="round" />
      </>
    );
  else body = <Cloud fill="#8b929d" />;

  return (
    <svg viewBox="0 0 24 24" width={size} height={size} style={{ display: "block", flex: "none" }}>
      {body}
    </svg>
  );
}

// Map an Open-Meteo-ish state to a sky glyph from precipitation probability.
export function skyFromPrecip(pop?: number | null): Sky {
  if (pop == null) return "cloud";
  if (pop >= 60) return "rain";
  if (pop >= 25) return "partly";
  return "sun";
}
