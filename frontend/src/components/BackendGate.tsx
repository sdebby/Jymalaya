// Holds the dashboard back until the backend answers. start.cmd opens the browser
// as soon as Vite is up, and uvicorn takes a few seconds longer: without this the
// first load fails with the proxy's HTTP 500 and nothing ever retries it.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { C } from "../theme";
import { api } from "../api";

const POLL_MS = 1000;
// A reload with the backend already up has its data in about 500 ms, so no flash.
const SHOW_AFTER_MS = 800;
const SLOW_AFTER_MS = 60_000;

const HEARTBEAT_POINTS = "2,42 46,42 56,42 64,14 74,68 82,42 94,42 148,42";

type Phase = "backend" | "influx" | "loading";

const MESSAGE: Record<Phase, string> = {
  backend: "Starting the backend…",
  influx: "Waiting for InfluxDB…",
  loading: "Loading your dashboard…",
};

const SLOW_HINT: Record<Phase, string> = {
  backend: "This is taking longer than usual. Check the Jymalaya backend window for errors.",
  influx: "This is taking longer than usual. Check that InfluxDB is running in Docker.",
  loading: "This is taking longer than usual. Check the Jymalaya backend window for errors.",
};

function WaitingScreen({ phase, slow }: { phase: Phase; slow: boolean }) {
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 1, background: C.pageBg, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <img src="/favicon.svg" width={72} height={72} alt="" />
      <svg width={220} height={80} viewBox="0 0 150 80" aria-hidden="true" style={{ marginTop: 20 }}>
        <polyline points={HEARTBEAT_POINTS} fill="none" stroke={C.restCell} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        <polyline className="heartbeat-pulse" pathLength={100} points={HEARTBEAT_POINTS} fill="none" stroke={C.red} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <div style={{ marginTop: 16, color: C.text, fontSize: 18, fontWeight: 500 }}>Jymalaya AI Coach</div>
      <div style={{ marginTop: 2, color: C.muted, fontSize: 12 }}>Personal Triathlon Training Intelligence</div>
      <div className="waiting-status" role="status" style={{ marginTop: 20, color: C.muted3, fontSize: 13, textAlign: "center" }}>
        {MESSAGE[phase]}
      </div>
      <div style={{ marginTop: 6, color: C.muted, fontSize: 12, textAlign: "center", maxWidth: 420, minHeight: 18 }}>
        {slow ? SLOW_HINT[phase] : ""}
      </div>
    </div>
  );
}

// `children` mounts once the backend and InfluxDB are up, and calls `onLoaded`
// when its first load has finished (or failed); the waiting screen stays on top
// until then, so the status line carries straight on into the data load.
export function BackendGate({ children }: { children: (onLoaded: () => void) => ReactNode }) {
  const [phase, setPhase] = useState<Phase>("backend");
  const [loaded, setLoaded] = useState(false);
  const [visible, setVisible] = useState(false);
  const [slow, setSlow] = useState(false);
  const onLoaded = useCallback(() => setLoaded(true), []);

  // Scheduled after each answer rather than on an interval, so a slow answer
  // never stacks requests. A failed request means the backend is not up yet: the
  // Vite proxy answers HTTP 500 while uvicorn is still starting.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function check() {
      let next: Phase = "backend";
      try {
        const health = await api.health();
        next = health.influxdb_connected ? "loading" : "influx";
      } catch {
        /* still "backend" */
      }
      if (cancelled) return;
      setPhase(next);
      if (next !== "loading") timer = window.setTimeout(check, POLL_MS);
    }
    check();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    const show = window.setTimeout(() => setVisible(true), SHOW_AFTER_MS);
    const hint = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => {
      window.clearTimeout(show);
      window.clearTimeout(hint);
    };
  }, []);

  return (
    <>
      {phase === "loading" && children(onLoaded)}
      {visible && !loaded && <WaitingScreen phase={phase} slow={slow} />}
    </>
  );
}
