// Palette + tokens lifted from the Option A design handoff so the React build
// matches it pixel-faithfully.

export const C = {
  pageBg: "#070809",
  panel: "#111217",
  bar: "#0c0d11",
  border: "#1f2227",
  card: "#181b1f",
  cardBorder: "#2c3235",
  inner: "#1b1e23",
  inner2: "#23262c",

  // Training Days grid. restCell is deliberately lighter than C.card -- an
  // earlier value of #1b1e23 made rest days vanish into the card background.
  restCell: "#262a30",
  restCellBorder: "#31363d",

  // Past-training panel — the whole card is painted dark blue to set the
  // completed week apart from the neutral-dark predicted week.
  pastCard: "#151d2e",
  pastBorder: "#2f4468",
  pastInner: "#1a2438",
  pastInner2: "#223048",

  text: "#e8e8ef",
  textSoft: "#d8d9e0",
  textChip: "#ccccdc",
  muted: "#767b85",
  muted2: "#8e939c",
  muted3: "#a0a3ab",
  white: "#ffffff",

  // Modal chrome (2026-07-26 readability handoff). Modals sit on a slightly
  // lighter panel than the dashboard cards and carry brighter secondary text,
  // so their larger type stays legible; the dashboard tokens above are
  // deliberately left alone.
  modalPanel: "#14161b", // modal background
  modalBorder: "#262a30", // modal outline, header/footer rules
  modalDivider: "#22262c", // full-width rule between body sections
  modalInner: "#1c1f25", // tag / toggle / close-button fill
  modalControlBorder: "#2c3138", // tag, input and button outlines
  modalLabel: "#c7cad2", // field labels, close glyph, cancel button
  mutedBright: "#8b9099", // uppercase tag labels, helper text
  mutedBright2: "#aeb2bb", // section descriptions, subtitles
  successBright: "#8fd489", // computed hints ("= 4:30 min/km"), "completed"
  dangerText: "#c7a3a8", // danger-zone body copy

  // accents. fit / fat / form are named for the load series they first drew,
  // but since 2026-10-27 they mean run / bike / swim (and form blue is also the
  // primary button and the Fresh zone); the load series have their own below.
  fit: "#73bf69", // green  · run
  fat: "#ff9830", // orange · bike
  form: "#5794f2", // blue  · swim
  yellow: "#fade2a",

  // Fitness / Fatigue / Form (CTL / ATL / TSB), wherever they are drawn: KPI
  // gauges, Training Load and Fatigue Zones lines, Load Forecast, season-plan
  // CTL. Their own hues so a load line never reads as a discipline.
  ctl: "#2dd4bf", // teal
  atl: "#ff5fa8", // pink
  tsb: "#c9cdf5", // silver
  red: "#f2495c",
  purple: "#b877d9", // Sessions tile, Analyze button (strength until 2026-11-17)
  // Strength: a whitish teal (2026-11-17, the athlete's ask), pale enough to
  // stay apart from the CTL teal above.
  strength: "#b2f5ea",

  // Recovery series (design system RecoveryTrends). Aliases of purple and red:
  // the card title and the unit say what each line is, so no new hue.
  hrv: "#a8f01f", // bright lime
  restingHr: "#f2495c", // = red
  sleep: "#A855F7", // weekly sleep bars: bright purple (the athlete's pick, 2026-11-16; #6C7AE0 before)

  // Season plan phases: one cool slate hue stepping in lightness, so the bars
  // never borrow a discipline colour or the yellow. The fills alone are too
  // dark to carry a bar on C.card, so every bar is outlined in phaseEdge.
  // Recovery weeks are hatched and the race week is labelled, so neither
  // depends on colour.
  phaseBase: "#4a5866",
  phaseBuild: "#6b7f93",
  phasePeak: "#9fb4c8",
  phaseTaper: "#3a444e",
  phaseEdge: "#8798ab",

  // Season plan phase brackets (the line and label under the bars): bright,
  // but in the hue gaps between the colours that already mean something --
  // swim/form blue, bike/fatigue orange, run/fitness green, strength/HRV
  // purple, HR/danger red, race-day yellow (2026-10-26, the athlete's ask).
  // Sky sits between teal and blue, indigo between blue and purple, pink
  // between purple and red, lime between yellow and green. Base was teal until
  // 2026-10-27, when teal became C.ctl -- the CTL line is drawn on this chart.
  phaseAccentBase: "#38bdf8", // sky
  phaseAccentBuild: "#818cf8", // indigo
  phaseAccentPeak: "#f472b6", // pink
  phaseAccentTaper: "#a3e635", // lime

  // Workout-modal stream graphs (2026-11-06, design system WorkoutStreams).
  // Pace / speed take the discipline colour and heart rate C.red; these three
  // are the ones with no colour of their own. Power and cadence borrow the
  // Peak and Build bracket hues: neither means a sport or a zone, and the
  // season plan never shares a screen with the workout modal.
  power: "#f472b6", // pink = phaseAccentPeak
  cadence: "#818cf8", // indigo = phaseAccentBuild
  elevation: "#a0a3ab", // = muted3, neutral terrain
  // Efficiency (output / HR, 2026-11-14) is aerobic fitness, so it takes the
  // Fitness (CTL) teal; the load charts never share a screen with the modal.
  efficiency: "#2dd4bf", // = ctl

  // Chart chrome shared by every LineChart.
  gridline: "rgba(255,255,255,0.05)",
  crosshair: "rgba(255,255,255,0.18)",
} as const;

// Blue → red intensity ramp for HR zones Z1..Z5. Shared so the Heart-Rate
// Zones donut and the workout modal's zone chips read as the same scale.
export const HR_ZONE_COLOR: Record<string, string> = {
  Z1: C.form,
  Z2: C.fit,
  Z3: C.yellow,
  Z4: C.fat,
  Z5: C.red,
};

// Zone labels written by the AI plan are freer than the donut's keys ("z3",
// "Zone 4", a "Z2-Z3" range): take the first zone named, so a planned segment
// lands on the same scale as the HR-zone panels. Anchored to a Z/Zone prefix
// or a bare digit — a label like "65-75% FTP" must not read as zone 5.
export function zoneColor(label: string | null | undefined, fallback: string): string {
  const digit = label?.match(/z(?:one)?\s*-?\s*([1-5])/i)?.[1] ?? label?.match(/^\s*([1-5])\s*$/)?.[1];
  return (digit && HR_ZONE_COLOR[`Z${digit}`]) || fallback;
}

export const DISCIPLINE_COLOR: Record<string, string> = {
  swim: C.form,
  bike: C.fat,
  run: C.fit,
  strength: C.strength,
  other: C.muted,
};
