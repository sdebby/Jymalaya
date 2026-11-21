// Coach-chat history persisted in localStorage with a 7-day retention window.
// Same defensive-parse pattern as settings.ts.

export const RETENTION_DAYS = 7;

export interface ChatMsg {
  role: "user" | "coach";
  text: string;
  ts: number; // epoch ms
  planUpdated?: boolean; // coach turn that actually edited the stored plan
  settingsUpdated?: boolean; // coach turn that saved an approved settings change
  error?: boolean; // the turn failed (backend error or no response): shown in red
  suggestions?: string[]; // the follow-up questions the coach ended this reply with (the chips)
  workoutId?: string; // user turn that sent a completed workout: its full data rides along while in the history window
}

const KEY = "tricoach.coachChat";

export function loadChat(): ChatMsg[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const cutoff = Date.now() - RETENTION_DAYS * 86400000;
    return parsed.filter(
      (m: unknown): m is ChatMsg =>
        !!m &&
        typeof m === "object" &&
        ((m as ChatMsg).role === "user" || (m as ChatMsg).role === "coach") &&
        typeof (m as ChatMsg).text === "string" &&
        typeof (m as ChatMsg).ts === "number" &&
        (m as ChatMsg).ts >= cutoff,
    );
  } catch {
    return [];
  }
}

export function saveChat(msgs: ChatMsg[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(msgs));
  } catch {
    /* ignore quota / private-mode errors */
  }
}

export function clearChat(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

// "Include context" in the composer: whether the athlete's training data goes
// to the coach with the question. Sticky across reloads, and deliberately NOT
// reset by /clear -- it is a preference, not conversation state. Defaults to on,
// so anything but an explicit stored "false" means include.
const CONTEXT_KEY = "tricoach.coachIncludeContext";

export function loadIncludeContext(): boolean {
  try {
    return localStorage.getItem(CONTEXT_KEY) !== "false";
  } catch {
    return true;
  }
}

export function saveIncludeContext(on: boolean): void {
  try {
    localStorage.setItem(CONTEXT_KEY, String(on));
  } catch {
    /* ignore quota / private-mode errors */
  }
}
