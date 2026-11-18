import { useEffect, useRef } from "react";

/**
 * Modal keyboard UX: Escape cancels, Ctrl+Enter (Cmd+Enter on Mac) confirms.
 * Listens on window so it works no matter which field inside the modal has
 * focus; omit `onConfirm` for view-only modals with nothing to save.
 */
export function useModalKeys(onCancel: () => void, onConfirm?: () => void) {
  // Refs keep the listener stable across renders while always calling the
  // latest callbacks (they close over modal state like `busy`).
  const handlers = useRef({ onCancel, onConfirm });
  handlers.current = { onCancel, onConfirm };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        handlers.current.onCancel();
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && handlers.current.onConfirm) {
        e.preventDefault();
        handlers.current.onConfirm();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
