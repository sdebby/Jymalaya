import { useState } from "react";
import { C } from "../theme";
import { api } from "../api";
import { useModalKeys } from "../useModalKeys";

// Shown while a sync is blocked on Garmin MFA: instructs the user to fetch the
// emailed code and submits it to the waiting login. The parent unmounts this
// modal when the backend reports the challenge is resolved (mfa_pending clears);
// after a successful submit we just wait in a passive "verifying" state.
export function MfaModal({ onDismiss }: { onDismiss: () => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Esc dismisses, Ctrl+Enter submits (submit() guards busy/sent itself).
  useModalKeys(onDismiss, submit);

  async function submit() {
    const trimmed = code.trim();
    if (busy || sent) return;
    if (!/^\d{4,8}$/.test(trimmed)) {
      setError("The code should be the 6 digits from Garmin's email.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.submitMfa(trimmed);
      setSent(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60, padding: 24 }}>
      <div style={{ width: 420, maxWidth: "100%", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "13px 18px", borderBottom: `1px solid ${C.border}`, background: C.bar }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Garmin verification</span>
          <span onClick={onDismiss} style={{ cursor: "pointer", fontSize: 18, color: C.muted, lineHeight: 1 }}>✕</span>
        </div>

        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={{ fontSize: 12.5, color: C.textChip, lineHeight: 1.5 }}>
            Garmin needs to verify this login. A <b>6-digit code</b> was just emailed to your
            Garmin account address — enter it below. The sync is paused and times out after
            ~5 minutes.
          </span>
          <input
            type="text"
            inputMode="numeric"
            autoFocus
            maxLength={8}
            placeholder="123456"
            value={code}
            disabled={sent}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            style={{
              background: C.bar,
              border: `1px solid ${C.cardBorder}`,
              borderRadius: 6,
              padding: "11px 12px",
              fontSize: 18,
              letterSpacing: "0.35em",
              textAlign: "center",
              color: C.text,
              outline: "none",
              opacity: sent ? 0.6 : 1,
            }}
          />
          {sent && (
            <span style={{ fontSize: 12, color: C.fit }}>
              Code sent — completing the Garmin login and continuing the sync…
            </span>
          )}
          {error && <span style={{ fontSize: 11.5, color: C.red }}>{error}</span>}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 18px", borderTop: `1px solid ${C.border}`, background: C.bar }}>
          <button
            onClick={onDismiss}
            style={{ cursor: "pointer", fontSize: 12.5, fontWeight: 600, padding: "9px 16px", borderRadius: 6, border: `1px solid ${C.cardBorder}`, background: "transparent", color: C.textChip }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy || sent}
            style={{
              cursor: busy || sent ? "default" : "pointer",
              fontSize: 12.5,
              fontWeight: 600,
              padding: "9px 16px",
              borderRadius: 6,
              border: "none",
              background: C.form,
              color: "#fff",
              opacity: busy || sent ? 0.7 : 1,
            }}
          >
            {busy ? "Submitting…" : sent ? "Waiting…" : "Submit code"}
          </button>
        </div>
      </div>
    </div>
  );
}
