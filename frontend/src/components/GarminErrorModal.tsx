import { useState } from "react";
import { C } from "../theme";
import { api, type GarminFailure } from "../api";
import { useModalKeys } from "../useModalKeys";

// Shown when a sync failed on Garmin's Personal API. It names the cause and the
// suggested action: for a missing, expired or revoked key, a field to paste a
// new one (checked with Garmin, saved to .env, then the sync runs again); for
// Garmin or the network being down, a retry. Same frame as MfaModal.
export function GarminErrorModal({
  failure,
  onClose,
  onRetry,
}: {
  failure: GarminFailure;
  onClose: () => void;
  onRetry: () => void;
}) {
  const needsKey = failure.code === "key_rejected" || failure.code === "no_key";
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Esc closes, Ctrl+Enter runs the primary action (save-and-sync or retry).
  useModalKeys(onClose, primary);

  async function primary() {
    if (busy) return;
    if (!needsKey) {
      onRetry();
      return;
    }
    const trimmed = key.trim();
    if (trimmed.length < 20) {
      setError("Paste the whole key from the Personal API Key page.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.saveGarminKey(trimmed);
      onRetry();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const title = needsKey ? "Garmin API key needed" : "Garmin did not answer";
  const label = needsKey ? (busy ? "Checking key…" : "Save and sync") : "Try again";

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60, padding: 24 }}>
      <div style={{ width: 460, maxWidth: "100%", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "13px 18px", borderBottom: `1px solid ${C.border}`, background: C.bar }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{title}</span>
          <span onClick={onClose} style={{ cursor: "pointer", fontSize: 18, color: C.muted, lineHeight: 1 }}>✕</span>
        </div>

        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={{ fontSize: 12.5, color: C.text, lineHeight: 1.5 }}>The sync stopped: {failure.message}</span>
          <span style={{ fontSize: 12.5, color: C.textChip, lineHeight: 1.5 }}>{failure.action}</span>
          {needsKey && (
            <input
              type="password"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              placeholder="Garmin Personal API key"
              value={key}
              disabled={busy}
              onChange={(e) => setKey(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && primary()}
              style={{
                background: C.bar,
                border: `1px solid ${C.cardBorder}`,
                borderRadius: 6,
                padding: "10px 12px",
                fontSize: 13,
                color: C.text,
                outline: "none",
                opacity: busy ? 0.6 : 1,
              }}
            />
          )}
          {needsKey && (
            <span style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.5 }}>
              The key is checked with Garmin first and saved to the backend's .env file only if Garmin accepts it.
            </span>
          )}
          {error && <span style={{ fontSize: 11.5, color: C.red }}>{error}</span>}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 18px", borderTop: `1px solid ${C.border}`, background: C.bar }}>
          <button
            onClick={onClose}
            style={{ cursor: "pointer", fontSize: 12.5, fontWeight: 600, padding: "9px 16px", borderRadius: 6, border: `1px solid ${C.cardBorder}`, background: "transparent", color: C.textChip }}
          >
            Close
          </button>
          <button
            onClick={primary}
            disabled={busy}
            style={{
              cursor: busy ? "default" : "pointer",
              fontSize: 12.5,
              fontWeight: 600,
              padding: "9px 16px",
              borderRadius: 6,
              border: "none",
              background: C.form,
              color: "#fff",
              opacity: busy ? 0.7 : 1,
            }}
          >
            {label}
          </button>
        </div>
      </div>
    </div>
  );
}
