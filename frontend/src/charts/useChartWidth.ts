import { useEffect, useRef, useState } from "react";

// Charts draw into a fixed-height viewBox scaled with preserveAspectRatio
// "meet", which letterboxes the drawing when the card is wider than the
// design aspect (big screens). This hook measures the chart's box and widens
// the viewBox to match its real aspect, so the drawing fills the card without
// distorting text (which preserveAspectRatio="none" would do).
export function useChartWidth(h: number, fallbackW: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(fallbackW);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r && r.width > 0 && r.height > 0) setW(Math.max(120, (r.width / r.height) * h));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [h]);
  return { ref, w };
}
