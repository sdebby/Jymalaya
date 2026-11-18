// Dragging a planned session between days in the Predicted Week. Pointer events
// and the Web Animations API rather than HTML5 drag and drop, whose browser-drawn
// ghost cannot tilt, fly or land. The carried card is written straight to its
// element: a pointer move causes no React render, only entering a new day does.

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { flushSync } from "react-dom";

// A card's index once dropped: the last card of its new day.
export const DROPPED = -1;
// Below this a press is a click, which opens the session modal.
const DRAG_THRESHOLD_PX = 5;
const LIFT_SCALE = 1.04;
const MAX_TILT_DEG = 5;
// Share of the distance to the target tilt covered per pointer move / frame.
const TILT_EASE = 0.25;
const FLY_MS = 280;
// Slightly past the target and back: the card lands rather than stops.
const FLY_EASING = "cubic-bezier(.2, 1.25, .35, 1)";

export interface SessionRef {
  date: string;
  index: number;
}

export interface DragState extends SessionRef {
  // The day under the cursor, whether or not it takes the drop.
  overDate: string | null;
  width: number;
}

interface Press extends SessionRef {
  startX: number;
  startY: number;
  // Cursor position inside the card, so the card does not jump to the cursor.
  offsetX: number;
  offsetY: number;
  rect: DOMRect;
  pointerId: number;
}

function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

function floatTransform(x: number, y: number, tilt: number, scale = LIFT_SCALE): string {
  return `translate(${x}px, ${y}px) rotate(${tilt}deg) scale(${scale})`;
}

function dateUnder(x: number, y: number): string | null {
  // The floating card has pointer-events: none, so this finds what is below it.
  const tile = document.elementFromPoint(x, y)?.closest<HTMLElement>("[data-plan-date]");
  return tile?.dataset.planDate ?? null;
}

export function useSessionDrag({
  canDrop,
  onDrop,
  enabled,
}: {
  // Whether a day takes this drag's card. The source day never does.
  canDrop: (date: string, from: SessionRef) => boolean;
  // Saves the move. Reject to send the card back to where it came from.
  onDrop: (from: SessionRef, toDate: string) => Promise<void>;
  enabled: boolean;
}) {
  const [drag, setDrag] = useState<DragState | null>(null);
  // The card in flight after the release: hidden in its tile until it lands.
  const [landing, setLanding] = useState<SessionRef | null>(null);
  // The card that just landed, for the settle pulse.
  const [settled, setSettled] = useState<SessionRef | null>(null);
  const floatRef = useRef<HTMLDivElement | null>(null);
  const pressRef = useRef<Press | null>(null);
  const draggingRef = useRef(false);
  const busyRef = useRef(false);
  // A release straight after a drag still fires a click on the card.
  const suppressClick = useRef(false);
  const posRef = useRef({ x: 0, y: 0, tilt: 0, scale: LIFT_SCALE, lastX: 0 });
  const tiltFrame = useRef(0);
  const cleanupRef = useRef<(() => void) | null>(null);
  // Latest props for the window listeners, which live across renders.
  const latest = useRef({ canDrop, onDrop });
  latest.current = { canDrop, onDrop };

  useEffect(
    () => () => {
      cleanupRef.current?.();
      document.body.classList.remove("session-dragging");
    },
    [],
  );

  const stopListening = useCallback(() => {
    cleanupRef.current?.();
    cleanupRef.current = null;
    cancelAnimationFrame(tiltFrame.current);
    document.body.classList.remove("session-dragging");
  }, []);

  // Fly the floating card from where it is to `rect`, then drop it.
  const flyTo = useCallback((rect: DOMRect | null) => {
    const el = floatRef.current;
    const { x, y, tilt, scale } = posRef.current;
    if (!el || !rect || reducedMotion()) return Promise.resolve();
    const anim = el.animate(
      [
        { transform: floatTransform(x, y, tilt, scale) },
        { transform: floatTransform(rect.left, rect.top, 0, 1) },
      ],
      { duration: FLY_MS, easing: FLY_EASING, fill: "forwards" },
    );
    // A hidden tab stalls the animation and `finished` waits with it, which
    // would leave the copy on screen and block the next drag.
    return Promise.race([
      anim.finished.then(
        () => undefined,
        () => undefined,
      ),
      new Promise<void>((resolve) => window.setTimeout(resolve, FLY_MS + 120)),
    ]);
  }, []);

  const finish = useCallback(
    async (press: Press, toDate: string | null) => {
      stopListening();
      draggingRef.current = false;
      const from: SessionRef = { date: press.date, index: press.index };
      busyRef.current = true;
      // Anything but a real target: back to where it came from.
      if (!toDate || !latest.current.canDrop(toDate, from)) {
        setLanding(from);
        setDrag((d) => (d ? { ...d, overDate: null } : d));
        await flyTo(press.rect);
        setDrag(null);
        setLanding(null);
        busyRef.current = false;
        return;
      }
      // Commit the optimistic plan now, so the card's new slot can be measured.
      // True/false: a failed save has already put the plan back and said why.
      let saved!: Promise<boolean>;
      flushSync(() => {
        setDrag((d) => (d ? { ...d, overDate: null, date: toDate, index: DROPPED } : d));
        setLanding({ date: toDate, index: DROPPED });
        saved = latest.current.onDrop(from, toDate).then(
          () => true,
          () => false,
        );
      });
      const slots = document.querySelectorAll<HTMLElement>(`[data-plan-date="${toDate}"] [data-plan-card]`);
      const slotRect = slots[slots.length - 1]?.getBoundingClientRect() ?? null;
      await flyTo(slotRect);
      // The copy waits on the slot until the answer is in: the failure path
      // leaves from there.
      if (await saved) {
        setSettled({ date: toDate, index: slots.length - 1 });
        window.setTimeout(() => setSettled(null), 450);
      } else {
        if (slotRect) posRef.current = { ...posRef.current, x: slotRect.left, y: slotRect.top, tilt: 0, scale: 1 };
        setLanding(from);
        await flyTo(press.rect);
      }
      setDrag(null);
      setLanding(null);
      busyRef.current = false;
    },
    [flyTo, stopListening],
  );

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLElement>, session: SessionRef) => {
      if (!enabled || busyRef.current || pressRef.current) return;
      if (e.button !== 0 || e.pointerType === "touch") return; // touch keeps scrolling
      suppressClick.current = false;
      const rect = e.currentTarget.getBoundingClientRect();
      const press: Press = {
        ...session,
        startX: e.clientX,
        startY: e.clientY,
        offsetX: e.clientX - rect.left,
        offsetY: e.clientY - rect.top,
        rect,
        pointerId: e.pointerId,
      };
      pressRef.current = press;

      const place = (clientX: number, clientY: number) => {
        const pos = posRef.current;
        const speed = clientX - pos.lastX;
        pos.lastX = clientX;
        pos.x = clientX - press.offsetX;
        pos.y = clientY - press.offsetY;
        if (!reducedMotion()) {
          const target = Math.max(-MAX_TILT_DEG, Math.min(MAX_TILT_DEG, speed * 0.6));
          pos.tilt += (target - pos.tilt) * TILT_EASE;
        }
        if (floatRef.current) floatRef.current.style.transform = floatTransform(pos.x, pos.y, pos.tilt);
      };

      // Levels the card out when the cursor stops, as no more moves arrive.
      const settleTilt = () => {
        const pos = posRef.current;
        pos.tilt *= 1 - TILT_EASE / 2;
        if (floatRef.current) floatRef.current.style.transform = floatTransform(pos.x, pos.y, pos.tilt);
        tiltFrame.current = requestAnimationFrame(settleTilt);
      };

      const onMove = (ev: PointerEvent) => {
        if (ev.pointerId !== press.pointerId) return;
        if (!draggingRef.current) {
          if (Math.hypot(ev.clientX - press.startX, ev.clientY - press.startY) < DRAG_THRESHOLD_PX) return;
          draggingRef.current = true;
          suppressClick.current = true;
          posRef.current = { x: press.rect.left, y: press.rect.top, tilt: 0, scale: LIFT_SCALE, lastX: ev.clientX };
          document.body.classList.add("session-dragging");
          setDrag({ date: press.date, index: press.index, overDate: dateUnder(ev.clientX, ev.clientY), width: press.rect.width });
          if (!reducedMotion()) tiltFrame.current = requestAnimationFrame(settleTilt);
        }
        place(ev.clientX, ev.clientY);
        const over = dateUnder(ev.clientX, ev.clientY);
        setDrag((d) => (d && d.overDate !== over ? { ...d, overDate: over } : d));
      };

      const release = (toDate: string | null) => {
        pressRef.current = null;
        if (draggingRef.current) void finish(press, toDate);
        else stopListening();
      };
      const onUp = (ev: PointerEvent) => {
        if (ev.pointerId !== press.pointerId) return;
        release(dateUnder(ev.clientX, ev.clientY));
        // The click, if any, comes right after this event. A release over
        // another element fires none, and the flag must not eat a later click.
        window.setTimeout(() => {
          suppressClick.current = false;
        }, 0);
      };
      const onCancel = (ev: PointerEvent) => {
        if (ev.pointerId === press.pointerId) release(null);
      };
      const onKey = (ev: KeyboardEvent) => {
        if (ev.key === "Escape" && draggingRef.current) {
          ev.stopPropagation();
          release(null);
        }
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onCancel);
      window.addEventListener("keydown", onKey, true);
      cleanupRef.current = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onCancel);
        window.removeEventListener("keydown", onKey, true);
      };
    },
    [enabled, finish, stopListening],
  );

  // Call first in a card's click handler: true means the click was a drag's.
  const consumeDragClick = useCallback(() => {
    const was = suppressClick.current;
    suppressClick.current = false;
    return was;
  }, []);

  // The floating element's first frame: where it was lifted from, then the lift.
  const attachFloat = useCallback((el: HTMLDivElement | null) => {
    floatRef.current = el;
    if (!el) return;
    const { x, y, tilt } = posRef.current;
    el.style.transform = floatTransform(x, y, tilt);
    if (!reducedMotion()) {
      // `scale` composes with the transform the pointer moves are writing.
      el.animate(
        [
          { scale: String(1 / LIFT_SCALE), opacity: 0.85 },
          { scale: "1", opacity: 1 },
        ],
        { duration: 140, easing: "ease-out" },
      );
    }
  }, []);

  return { drag, landing, settled, onPointerDown, consumeDragClick, attachFloat };
}
