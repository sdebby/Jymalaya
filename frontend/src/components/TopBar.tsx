import { useState } from "react";
import { C } from "../theme";
import { SettingsModal } from "./SettingsModal";
import type { AppSettings } from "../settings";
import type { SyncStatus } from "../types";

// Human wording for the backend's live sync_phase keys. Unknown/future keys
// fall back to plain "Syncing…".
const PHASE_LABELS: Record<string, string> = {
  garmin: "Getting data from Garmin…",
  parsing: "Parsing activities…",
  metrics: "Computing training load…",
  coach: "Consulting AI coach…",
};

export function TopBar({
  status,
  syncing,
  syncPhase,
  onSync,
  replanning,
  onReplan,
  settings,
  onSettingsChange,
  onDataDeleted,
  onRecalculated,
  onInstructionsSaved,
  showSettings,
  onShowSettings,
}: {
  status: SyncStatus | null;
  syncing: boolean;
  syncPhase: string | null;
  onSync: () => void;
  replanning: boolean;
  onReplan: () => void;
  settings: AppSettings;
  onSettingsChange: (s: AppSettings) => void;
  onDataDeleted: () => void;
  onRecalculated?: () => void;
  onInstructionsSaved?: (seasonPlanNote: string | null) => void;
  // Owned by App, so the fresh-start welcome note can open Settings too.
  showSettings: boolean;
  onShowSettings: (open: boolean) => void;
}) {
  const last = status?.last_sync ? new Date(status.last_sync).toLocaleString() : "never";
  const busy = syncing || replanning;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 16px",
        background: C.bar,
        borderBottom: `1px solid ${C.border}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 13 }}>
        <Logo />
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text }}>Jymalaya AI Coach</div>
          <div style={{ fontSize: 11, color: C.muted }}>Personal Triathlon Training Intelligence</div>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 10, color: C.muted }}>Last sync · {last}</span>
        <div
          onClick={busy ? undefined : onReplan}
          title="Ask the AI coach for a fresh 7-day plan from current data (no Garmin sync)"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            cursor: busy ? "default" : "pointer",
            padding: "6px 12px",
            background: "#181b1f",
            border: `1px solid ${C.cardBorder}`,
            borderRadius: 4,
            fontSize: 12,
            fontWeight: 600,
            color: C.textChip,
            opacity: busy && !replanning ? 0.5 : 1,
            whiteSpace: "nowrap",
            justifyContent: "center",
          }}
        >
          {replanning && <Spinner />}
          {replanning ? "Re-planning…" : "Re-sync plan"}
        </div>
        <div
          onClick={busy ? undefined : onSync}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            cursor: busy ? "default" : "pointer",
            padding: "6px 14px",
            background: syncing ? C.cardBorder : C.form,
            borderRadius: 4,
            fontSize: 12,
            fontWeight: 600,
            color: "#fff",
            opacity: syncing ? 0.85 : replanning ? 0.5 : 1,
            whiteSpace: "nowrap",
            minWidth: 110,
            justifyContent: "center",
          }}
        >
          {syncing && <Spinner />}
          {syncing ? (syncPhase && PHASE_LABELS[syncPhase]) || "Syncing…" : "Sync Garmin"}
        </div>
        <button
          onClick={() => onShowSettings(true)}
          title="Settings"
          aria-label="Settings"
          style={{ cursor: "pointer", width: 32, height: 32, display: "flex", alignItems: "center", justifyContent: "center", background: "#181b1f", border: `1px solid ${C.cardBorder}`, borderRadius: 4, fontSize: 15, lineHeight: 1, color: C.textChip }}
        >
          ⚙
        </button>
      </div>
      {showSettings && (
        <SettingsModal settings={settings} onClose={() => onShowSettings(false)} onSave={onSettingsChange} onDataDeleted={onDataDeleted} onRecalculated={onRecalculated} onInstructionsSaved={onInstructionsSaved} />
      )}
    </div>
  );
}

// The athlete's Garmin Connect profile picture (cached by the sync, served by
// the backend); the original gradient square remains the fallback until the
// first sync has cached an image (or when the backend is down).
function Logo() {
  const [failed, setFailed] = useState(false);
  const size = { width: 30, height: 30, borderRadius: 7 } as const;
  if (failed) {
    return <div style={{ ...size, background: `linear-gradient(135deg, ${C.form}, ${C.fit})` }} />;
  }
  return (
    <img
      src="/api/athlete/avatar"
      alt=""
      onError={() => setFailed(true)}
      style={{ ...size, objectFit: "cover", display: "block" }}
    />
  );
}

// Self-contained spinning ring (SVG SMIL) — no global CSS keyframes needed.
// Exported so every in-flight button shows the same indicator.
export function Spinner() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" style={{ display: "block" }}>
      <circle cx="12" cy="12" r="9" fill="none" stroke="#fff" strokeOpacity={0.3} strokeWidth={3} />
      <path d="M12 3 a9 9 0 0 1 9 9" fill="none" stroke="#fff" strokeWidth={3} strokeLinecap="round">
        <animateTransform
          attributeName="transform"
          type="rotate"
          from="0 12 12"
          to="360 12 12"
          dur="0.7s"
          repeatCount="indefinite"
        />
      </path>
    </svg>
  );
}
