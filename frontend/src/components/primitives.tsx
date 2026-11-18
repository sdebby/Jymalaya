import type { CSSProperties, ReactNode } from "react";
import { C } from "../theme";

export function Card({
  children,
  style,
  accent,
}: {
  children: ReactNode;
  style?: CSSProperties;
  accent?: string;
}) {
  return (
    <div
      style={{
        background: C.card,
        border: `1px solid ${C.cardBorder}`,
        borderTop: accent ? `3px solid ${accent}` : undefined,
        borderRadius: 3,
        display: "flex",
        flexDirection: "column",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function CardHead({ title, right }: { title: ReactNode; right?: ReactNode }) {
  return (
    <div style={{ padding: "10px 13px 0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ fontSize: 12, fontWeight: 500, color: C.textSoft }}>{title}</span>
      {right != null && <span style={{ fontSize: 10, color: C.muted }}>{right}</span>}
    </div>
  );
}

export function ChartBox({ children, pad = "4px 8px 6px" }: { children: ReactNode; pad?: string }) {
  return <div style={{ flex: 1, padding: pad, minHeight: 0 }}>{children}</div>;
}

export function Empty({ label }: { label: string }) {
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: C.muted,
        fontSize: 11,
        textAlign: "center",
        padding: 16,
      }}
    >
      {label}
    </div>
  );
}
