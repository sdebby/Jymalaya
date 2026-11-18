import { useEffect, useState } from "react";
import { C, DISCIPLINE_COLOR, zoneColor } from "../theme";
import { monthDay } from "../format";
import { groupStructure, localISO, type StructureItem } from "../derive";
import { useModalKeys } from "../useModalKeys";
import { api } from "../api";
import { Spinner } from "./TopBar";
import { ChatIcon } from "./ChatIcon";
import { WorkoutGraph } from "../charts/WorkoutGraph";
import type { AthleteProfile, PlanSession, StructureSegment } from "../types";

export function SessionModal({
  session,
  date,
  dayName,
  sessionIndex,
  onClose,
  onAskCoach,
}: {
  session: PlanSession;
  date: string;
  dayName: string;
  // Position within its plan day -- the address POST /device/send takes. Set,
  // it shows the Send to device button.
  sessionIndex?: number;
  onClose: () => void;
  onAskCoach?: () => void;
}) {
  const accent = DISCIPLINE_COLOR[session.discipline] ?? C.muted;
  const zones = session.zones_target ? Object.entries(session.zones_target) : [];

  // Send to device: the backend puts the session on the athlete's intervals.icu
  // calendar, and intervals.icu passes it on to the watch. "Sent" holds until
  // the modal closes; sending again later updates the same calendar entry.
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const dayPassed = date < localISO(new Date());

  async function sendToDevice() {
    if (sessionIndex == null) return;
    setSending(true);
    setSendError(null);
    try {
      await api.sendToDevice(date, sessionIndex);
      setSent(true);
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  // The workout graph turns zones into bpm, watts and pace from the profile.
  // Without it (backend down) the graph still draws, in % of threshold.
  const [profile, setProfile] = useState<AthleteProfile | null>(null);
  useEffect(() => {
    let live = true;
    api
      .athlete()
      .then((p) => live && setProfile(p))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  // View-only modal: Esc closes, no confirm action.
  useModalKeys(onClose);

  const tags = [
    <Tag key="discipline" label="Discipline" value={cap(session.discipline)} color={accent} />,
    ...(session.type ? [<Tag key="type" label="Type" value={cap(session.type)} />] : []),
    <Tag key="duration" label="Duration" value={session.duration_min != null ? `${session.duration_min} min` : "—"} />,
    <Tag key="tss" label="Est. TSS" value={session.estimated_tss != null ? String(session.estimated_tss) : "—"} />,
  ];

  const sections: React.ReactNode[] = [
    <TagGrid key="tags" columns={tags.length}>{tags}</TagGrid>,
  ];
  if (zones.length > 0) {
    sections.push(
      <Section key="zones" title="Target zones">
        <ChipRow columns={zones.length}>
          {zones.map(([z, pct]) => (
            <Chip key={z} name={z} detail={String(pct)} color={zoneColor(z, C.white)} />
          ))}
        </ChipRow>
      </Section>,
    );
  }
  if (session.structure && session.structure.length > 0) {
    sections.push(
      <Section key="structure" title="Structure">
        {session.structure.some((s) => (s.duration_min ?? 0) > 0) && (
          <div style={{ ...SEGMENT_CARD, display: "block", padding: "12px 14px 8px" }}>
            <WorkoutGraph discipline={session.discipline} segments={session.structure} profile={profile} />
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {groupStructure(session.structure).map((item, i) =>
            item.kind === "repeat" ? (
              <RepeatBlock key={i} item={item} accent={accent} />
            ) : (
              <div key={i} style={SEGMENT_CARD}>
                <SegmentRow seg={item.seg} accent={accent} nameWidth={96} />
              </div>
            ),
          )}
        </div>
      </Section>,
    );
  }
  if (session.notes) {
    sections.push(
      <Section key="notes" title="Coach notes">
        <BodyText>{session.notes}</BodyText>
      </Section>,
    );
  }
  if (zones.length === 0 && !session.structure?.length && !session.notes) {
    sections.push(<BodyText key="empty">No further detail for this session.</BodyText>);
  }
  if (sendError) {
    sections.push(
      <div
        key="send-error"
        style={{ background: "#2a1416", border: `1px solid ${C.red}`, borderRadius: 8, padding: "10px 12px", fontSize: 12.5, color: C.text }}
      >
        Send failed: {sendError}
      </div>,
    );
  }
  const canSend = sessionIndex != null;
  const sendDisabled = sending || sent || dayPassed;

  return (
    <ModalScrim onClose={onClose}>
      <ModalShell>
        <ModalHeader
          accent={accent}
          title={session.title || `${cap(session.discipline)} session`}
          subtitle={
            <>
              {dayName} · {monthDay(date)}
            </>
          }
          onClose={onClose}
        />

        <ModalBody gap={24}>{withDividers(sections)}</ModalBody>

        {(canSend || onAskCoach) && (
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 12, padding: "16px 26px", borderTop: `1px solid ${C.modalBorder}`, background: C.bar, flex: "none" }}>
            {canSend && (
              <button
                onClick={sendDisabled ? undefined : sendToDevice}
                disabled={sendDisabled}
                title={
                  dayPassed
                    ? "This day has passed"
                    : sent
                      ? "On your intervals.icu calendar, on its way to the watch"
                      : "Send this workout to your watch through intervals.icu"
                }
                style={{
                  cursor: sendDisabled ? "default" : "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  background: C.fit,
                  opacity: dayPassed ? 0.45 : sending || sent ? 0.7 : 1,
                  border: "none",
                  borderRadius: 8,
                  padding: "11px 20px",
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#fff",
                }}
              >
                {sending && <Spinner />}
                {sending ? "Sending…" : sent ? "Sent" : "Send to device"}
              </button>
            )}
            {onAskCoach && (
              <button
                onClick={() => {
                  onAskCoach();
                  onClose();
                }}
                title="Ask coach"
                aria-label="Ask coach"
                style={{ cursor: "pointer", background: C.form, border: "none", borderRadius: 8, width: 44, height: 44, padding: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#fff" }}
              >
                <ChatIcon />
              </button>
            )}
          </div>
        )}
      </ModalShell>
    </ModalScrim>
  );
}

const SEGMENT_CARD: React.CSSProperties = { display: "flex", gap: 12, alignItems: "baseline", background: C.modalInner, border: `1px solid ${C.modalControlBorder}`, borderRadius: 8, padding: "12px 14px" };

// One structure step: name, duration, zone, watts, notes. `nameWidth` lets the
// rows inside a repeat block, which sit 14px further right behind the rail,
// keep their duration/zone columns in line with the single cards.
function SegmentRow({ seg, accent, nameWidth }: { seg: StructureSegment; accent: string; nameWidth: number }) {
  return (
    <>
      <span style={{ fontSize: 14, fontWeight: 600, color: C.white, minWidth: nameWidth }}>{seg.segment || "Segment"}</span>
      <span style={{ fontSize: 13, color: C.mutedBright2, minWidth: 62 }}>
        {seg.duration_min != null ? `${seg.duration_min} min` : ""}
      </span>
      {seg.zone && (
        <span style={{ fontSize: 13, fontWeight: 600, color: zoneColor(seg.zone, accent), minWidth: 32 }}>
          {seg.zone}
        </span>
      )}
      {seg.target_power_w && (
        <span style={{ fontSize: 13, fontWeight: 600, color: C.white, whiteSpace: "nowrap" }}>{seg.target_power_w} W</span>
      )}
      {seg.notes && <span style={{ fontSize: 13, color: C.mutedBright2, lineHeight: 1.5 }}>{seg.notes}</span>}
    </>
  );
}

// An interval set drawn once: "5 x" and the set's total, then one rep's steps
// joined by a rail in the discipline colour.
function RepeatBlock({ item, accent }: { item: Extract<StructureItem, { kind: "repeat" }>; accent: string }) {
  return (
    <div style={{ ...SEGMENT_CARD, flexDirection: "column", alignItems: "stretch", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 18, fontWeight: 700, color: accent }}>{item.count} ×</span>
          <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase", color: C.mutedBright }}>Repeat</span>
        </div>
        <span style={{ fontSize: 13, color: C.mutedBright2 }}>{item.totalMin} min</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, borderLeft: `2px solid ${accent}66`, paddingLeft: 12 }}>
        {item.steps.map((seg, i) => (
          <div key={i} style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
            <SegmentRow seg={seg} accent={accent} nameWidth={82} />
          </div>
        ))}
      </div>
      {item.lastRepShort && (
        <span style={{ fontSize: 12, color: C.mutedBright }}>
          No {(item.steps[item.steps.length - 1].segment || "final step").toLowerCase()} after the last rep
        </span>
      )}
    </div>
  );
}

/* ── Shared modal chrome ─────────────────────────────────────────────────── */

export function ModalScrim({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: 24 }}
    >
      {children}
    </div>
  );
}

export function ModalShell({ children, width = 560 }: { children: React.ReactNode; width?: number | string }) {
  return (
    <div
      onClick={(e) => e.stopPropagation()}
      style={{ width, maxWidth: "100%", maxHeight: "calc(100vh - 48px)", display: "flex", flexDirection: "column", background: C.modalPanel, border: `1px solid ${C.modalBorder}`, borderRadius: 12, overflow: "hidden", boxShadow: "0 24px 70px rgba(0,0,0,0.55)", boxSizing: "border-box" }}
    >
      {children}
    </div>
  );
}

// Detail-modal header: a discipline-coloured top stripe, title + subtitle, and
// the close button. Settings has no stripe or subtitle and builds its own.
export function ModalHeader({
  accent,
  title,
  subtitle,
  onClose,
}: {
  accent: string;
  title: string;
  subtitle: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: "22px 26px", borderTop: `4px solid ${accent}`, borderBottom: `1px solid ${C.modalBorder}`, background: C.bar, flex: "none" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontSize: 19, fontWeight: 700, color: C.white }}>{title}</span>
        <span style={{ fontSize: 13, color: C.mutedBright2 }}>{subtitle}</span>
      </div>
      <CloseButton onClose={onClose} />
    </div>
  );
}

export function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      onClick={onClose}
      title="Close"
      style={{ cursor: "pointer", width: 32, height: 32, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 15, color: C.modalLabel, background: C.modalInner, border: `1px solid ${C.modalControlBorder}`, borderRadius: 8 }}
    >
      ✕
    </button>
  );
}

// `overflowX: hidden` is deliberate — without it a sub-pixel grid overflow can
// raise a stray horizontal scrollbar.
export function ModalBody({ gap, children }: { gap: number; children: React.ReactNode }) {
  return (
    <div style={{ padding: 26, display: "flex", flexDirection: "column", gap, overflowY: "auto", overflowX: "hidden" }}>
      {children}
    </div>
  );
}

export function Divider() {
  return <div style={{ height: 1, background: C.modalDivider }} />;
}

// Sections are separated by a full-width rule rather than by a border under
// each title, so conditional sections still divide cleanly.
export function withDividers(sections: React.ReactNode[]): React.ReactNode[] {
  return sections.flatMap((s, i) => (i === 0 ? [s] : [<Divider key={`div-${i}`} />, s]));
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <span style={{ fontSize: 13, fontWeight: 700, color: C.white }}>{title}</span>
      {children}
    </div>
  );
}

export function TagGrid({ columns = 4, children }: { columns?: number; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${columns}, 1fr)`, gap: 12 }}>{children}</div>
  );
}

export function Tag({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5, background: C.modalInner, border: `1px solid ${C.modalControlBorder}`, borderRadius: 8, padding: "12px 14px", minWidth: 0 }}>
      <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase", color: C.mutedBright }}>{label}</span>
      <span style={{ fontSize: 16, fontWeight: 700, color: color ?? C.white }}>{value}</span>
    </div>
  );
}

// One column per chip, so the whole zone range stays on a single row however
// many zones the workout touched (five would otherwise wrap).
export function ChipRow({ columns, children }: { columns: number; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${columns}, 1fr)`, gap: 8 }}>{children}</div>
  );
}

// `color` tints the chip to the zone's colour on the dashboard's HR-zone
// scale; without it the chip stays neutral.
export function Chip({ name, detail, color }: { name: string; detail: string; color?: string }) {
  return (
    <span style={{ fontSize: 13.5, textAlign: "center", whiteSpace: "nowrap", minWidth: 0, color: "#e2e4ea", background: color ? `${color}14` : C.modalInner, border: `1px solid ${color ? `${color}66` : C.modalControlBorder}`, borderRadius: 8, padding: "8px 6px" }}>
      <strong style={{ color: color ?? C.white }}>{name}</strong> · {detail}
    </span>
  );
}

export function BodyText({ children }: { children: React.ReactNode }) {
  return <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: C.mutedBright2 }}>{children}</p>;
}

export function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
