// Client-side dashboard preferences, persisted in localStorage.
// These shape how the predicted-training-week row is laid out / annotated.

export const WEEKDAYS = [
  { num: 0, label: "Sunday", short: "Sun" },
  { num: 1, label: "Monday", short: "Mon" },
  { num: 2, label: "Tuesday", short: "Tue" },
  { num: 3, label: "Wednesday", short: "Wed" },
  { num: 4, label: "Thursday", short: "Thu" },
  { num: 5, label: "Friday", short: "Fri" },
  { num: 6, label: "Saturday", short: "Sat" },
] as const;

export interface AppSettings {
  weekStart: 0 | 1; // 0 = Sunday, 1 = Monday
  recoveryDays: number[]; // weekday numbers (0–6) treated as recovery/rest
}

export const DEFAULT_SETTINGS: AppSettings = {
  weekStart: 1,
  recoveryDays: [],
};

const KEY = "tricoach.settings";

export function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw);
    return {
      weekStart: parsed.weekStart === 0 ? 0 : 1,
      recoveryDays: Array.isArray(parsed.recoveryDays)
        ? parsed.recoveryDays.filter((n: unknown) => typeof n === "number" && n >= 0 && n <= 6)
        : [],
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(s: AppSettings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* ignore quota / private-mode errors */
  }
}

export function clearSettings(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

// Whether the Season plan panel is folded to its header row. A viewing
// preference per browser, remembered across reloads; default open.
const SEASON_COLLAPSED_KEY = "tricoach.seasonPlanCollapsed";

export function loadSeasonCollapsed(): boolean {
  try {
    return localStorage.getItem(SEASON_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

export function saveSeasonCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(SEASON_COLLAPSED_KEY, String(collapsed));
  } catch {
    /* ignore quota / private-mode errors */
  }
}

// Local-time weekday for a "YYYY-MM-DD" string (avoids UTC-parse off-by-one).
export function weekdayOf(dateStr: string): number {
  const [y, m, d] = dateStr.split("-").map(Number);
  if (!y || !m || !d) return new Date(dateStr).getDay();
  return new Date(y, m - 1, d).getDay();
}

// The fresh-start welcome note is shown once: on a clean database, until the
// athlete closes it or opens Settings from it. A full data reset clears this
// key, since the database is clean again.
const WELCOME_SEEN_KEY = "tricoach.welcomeSeen";

export function loadWelcomeSeen(): boolean {
  try {
    return localStorage.getItem(WELCOME_SEEN_KEY) === "true";
  } catch {
    return false;
  }
}

export function saveWelcomeSeen(): void {
  try {
    localStorage.setItem(WELCOME_SEEN_KEY, "true");
  } catch {
    /* ignore quota / private-mode errors */
  }
}

export function clearWelcomeSeen(): void {
  try {
    localStorage.removeItem(WELCOME_SEEN_KEY);
  } catch {
    /* ignore */
  }
}
