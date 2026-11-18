// Form controls shared by the Settings modal and the schedule editor it hosts.
// They live here rather than in SettingsModal so ScheduleEditor can use them
// without the two files importing each other.

import React from "react";
import { C } from "../theme";

export function Field({
  label,
  gap = 7,
  children,
}: {
  label: string;
  gap?: number;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: C.modalLabel }}>{label}</span>
      {children}
    </label>
  );
}

// Segmented (week start, max sessions) and multi-select (disciplines, brick
// days) toggles share one look. `color` tints the active fill — the discipline
// toggles use their own discipline colour so they match the week strip.
export function ToggleButton({
  active,
  onClick,
  label,
  title,
  fontSize,
  padding,
  color,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  title?: string;
  fontSize: number;
  padding: string;
  color?: string;
  disabled?: boolean;
}) {
  const fill = color ?? C.form;
  return (
    <button
      onClick={disabled ? undefined : onClick}
      title={title}
      disabled={disabled}
      style={{
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.4 : 1,
        flex: 1,
        padding,
        borderRadius: 8,
        fontSize,
        fontWeight: 600,
        color: active ? "#fff" : C.modalLabel,
        background: active ? fill : C.modalInner,
        border: `1px solid ${active ? fill : C.modalControlBorder}`,
      }}
    >
      {label}
    </button>
  );
}

// Secondary copy under a control.
export function Hint({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{ fontSize: 12, color: color ?? C.mutedBright, lineHeight: 1.5 }}>{children}</span>
  );
}

// Loading / offline status line shown in place of a section's controls.
export function Note({ children }: { children: React.ReactNode }) {
  return <span style={{ fontSize: 13, color: C.mutedBright, padding: "4px 0" }}>{children}</span>;
}

export const inputStyle: React.CSSProperties = {
  width: "100%",
  background: C.bar,
  border: `1px solid ${C.modalControlBorder}`,
  borderRadius: 8,
  padding: "12px 14px",
  fontSize: 15,
  color: C.white,
  outline: "none",
  boxSizing: "border-box",
};
