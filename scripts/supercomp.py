"""
Supercompensation forecast: when will the athlete be strongest, and how hard
should the next session be?

Reads data/load_export.json (written by scripts/export_load.py) and writes
data/next_workout_forecast.json. Read-only against InfluxDB; no network.

Model (Banister impulse-response, fitted per athlete)
    perf(t) = p0 + k1 * F1(t) - k2 * F2(t)
    F(t)    = sum over earlier days s of TSS(s) * exp(-(t - s) / tau)
F1 is the slow "fitness" kernel (tau1 near 42 days), F2 the fast "fatigue"
kernel (tau2 a few days). perf is run efficiency (running power / heart rate)
measured on steady runs. The fit searches tau1/tau2 on a small grid and solves
p0, k1, k2 by least squares; k1 and k2 must both come out positive, otherwise
the fit is rejected. Then the model is run forward under "rest" and the peak
of perf(t) is the supercompensation day.

When the fit is rejected (too few runs, wrong signs, no explanatory power) the
forecast falls back to the athlete's observed lag after a hard day and says so
in `method` and `confidence`.

Usage:
    python scripts/supercomp.py
    python scripts/supercomp.py --export data/load_export.json --hard-tss 80
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

TAU1_GRID = (35.0, 42.0, 50.0)
TAU2_GRID = tuple(float(x) for x in range(3, 15))
HORIZON_DAYS = 7           # never advise waiting longer than this for the next hard session
READY_SHARE = 0.9          # share of the available rebound that defines the ready day
HARD_TSS = 80.0            # a day at or above this counts as a hard day
MIN_RUNS = 15              # fewer steady runs than this -> fallback
MIN_R2 = 0.15
HARD_SESSION_CTL_X = (1.3, 1.7)   # hard-day TSS as a multiple of CTL
FALLBACK_LAG_DAYS = (2, 3)


# -- inputs ------------------------------------------------------------------

def load_series(export: dict) -> tuple[list[date], np.ndarray, dict]:
    """Daily TSS (zero-filled) from the combined daily_load series, plus today's CTL/ATL/TSB."""
    rows = [r for r in export["daily_load"] if r.get("discipline", "all") == "all"]
    tss = {date.fromisoformat(r["time"][:10]): float(r.get("tss") or 0.0) for r in rows}
    first, last = min(tss), max(tss)
    days = [first + timedelta(d) for d in range((last - first).days + 1)]
    arr = np.array([tss.get(d, 0.0) for d in days])
    latest = rows[-1]
    now = {k: latest.get(k) for k in ("ctl", "atl", "tsb")}
    return days, arr, now


def steady_runs(export: dict) -> list[tuple[date, float]]:
    """(day, efficiency) for steady runs: power per heartbeat, 25 min or longer, HR 125+."""
    out = []
    for w in export["workouts"]:
        if w.get("discipline") != "run":
            continue
        if not (w.get("avg_power") and w.get("avg_hr")):
            continue
        if (w.get("duration_sec") or 0) < 1500 or w["avg_hr"] < 125:
            continue
        out.append((date.fromisoformat(w["date"]), w["avg_power"] / w["avg_hr"]))
    return out


# -- model -------------------------------------------------------------------

def kernel(tss: np.ndarray, tau: float) -> np.ndarray:
    """F[t] = sum over s < t of tss[s] * exp(-(t - s) / tau): a day never loads itself."""
    f = np.zeros(len(tss))
    acc = 0.0
    decay = np.exp(-1.0 / tau)
    for t in range(len(tss)):
        f[t] = acc
        acc = (acc + tss[t]) * decay
    return f


def fit(days: list[date], tss: np.ndarray, obs: list[tuple[date, float]]) -> dict | None:
    index = {d: i for i, d in enumerate(days)}
    pts = [(index[d], y) for d, y in obs if d in index]
    if len(pts) < MIN_RUNS:
        return None
    ix = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    sst = float(((y - y.mean()) ** 2).sum())
    best = None
    for t1 in TAU1_GRID:
        f1 = kernel(tss, t1)
        for t2 in TAU2_GRID:
            f2 = kernel(tss, t2)
            X = np.column_stack([np.ones(len(ix)), f1[ix], -f2[ix]])
            coef, *_ = np.linalg.lstsq(X, y, rcond=None)
            p0, k1, k2 = (float(c) for c in coef)
            if k1 <= 0 or k2 <= 0:
                continue
            sse = float(((y - X @ coef) ** 2).sum())
            if best is None or sse < best["sse"]:
                best = {"tau1": t1, "tau2": t2, "p0": p0, "k1": k1, "k2": k2, "sse": sse}
    if best is None:
        return None
    best["r2"] = 1 - best["sse"] / sst if sst > 0 else 0.0
    best["n"] = len(pts)
    best["noise_sd"] = float(np.sqrt(best["sse"] / max(len(pts) - 3, 1)))
    return best if best["r2"] >= MIN_R2 else None


def perf_curve(model: dict, tss: np.ndarray) -> np.ndarray:
    return (model["p0"] + model["k1"] * kernel(tss, model["tau1"])
            - model["k2"] * kernel(tss, model["tau2"]))


def forecast_peak(model: dict, tss: np.ndarray, horizon: int = HORIZON_DAYS) -> dict:
    """
    Run the model forward on rest days; day 1 is tomorrow.

    Under total rest the fitted curve keeps climbing for weeks (fatigue clears
    faster than fitness fades), so its literal maximum is not a usable answer.
    The ready day is the first day the curve reaches READY_SHARE of the gain
    available within the horizon: most of the rebound is in, and waiting longer
    only costs fitness. If rest buys nothing (gain <= 0) tomorrow is ready.
    """
    n = len(tss)
    future = np.concatenate([tss, np.zeros(horizon)])
    p = perf_curve(model, future)
    path = p[n:]
    today = p[n - 1]
    gain = float(path.max() - today)
    if gain <= 0:
        ready = 0
        window = [1]
    else:
        threshold = today + READY_SHARE * gain
        ready = next(i for i, v in enumerate(path) if v >= threshold)
        window = [i + 1 for i, v in enumerate(path) if v >= threshold]
    return {"peak_day_offset": ready + 1, "window_offsets": window,
            "gain_vs_today": gain, "path": [float(v) for v in path]}


# -- fallback ----------------------------------------------------------------

def empirical_lag(days, tss, obs, hard=HARD_TSS) -> dict:
    """Median days after a hard day on which steady-run efficiency was best (detrended)."""
    index = {d: i for i, d in enumerate(days)}
    pts = [(index[d], y) for d, y in obs if d in index]
    if len(pts) < 6:
        return {"lag_days": list(FALLBACK_LAG_DAYS), "basis": "default (too few runs)"}
    x = np.array([p[0] for p in pts], float)
    y = np.array([p[1] for p in pts])
    b, a = np.polyfit(x, y, 1)
    res = y - (a + b * x)
    by_gap: dict[int, list[float]] = {}
    for (i, _), r in zip(pts, res):
        prev = [j for j in range(i) if tss[j] >= hard]
        if prev:
            by_gap.setdefault(min(i - prev[-1], 7), []).append(r)
    good = {g: st.median(v) for g, v in by_gap.items() if len(v) >= 2}
    if not good:
        return {"lag_days": list(FALLBACK_LAG_DAYS), "basis": "default (no hard-day gaps)"}
    best = max(good, key=good.get)
    return {"lag_days": [best, best + 1], "basis": f"observed best gap {best} d over {len(pts)} runs"}


# -- report ------------------------------------------------------------------

def confidence(model: dict | None) -> str:
    if model is None:
        return "low"
    if model["n"] >= 30 and model["r2"] >= 0.35:
        return "medium"
    return "low"


def build_report(export: dict, hard: float = HARD_TSS) -> dict:
    days, tss, now = load_series(export)
    obs = steady_runs(export)
    today = days[-1]
    ctl = float(now["ctl"] or 0.0)
    last_hard = next((days[i] for i in range(len(days) - 1, -1, -1) if tss[i] >= hard), None)
    model = fit(days, tss, obs)

    if model:
        fc = forecast_peak(model, tss)
        offset, window = fc["peak_day_offset"], fc["window_offsets"]
        method = "banister_fit"
        detail = {k: round(model[k], 5) for k in ("tau1", "tau2", "p0", "k1", "k2", "r2", "noise_sd")}
        detail["runs_used"] = model["n"]
        gain_pct = 100 * fc["gain_vs_today"] / model["p0"] if model["p0"] else None
        # next cycle: put the hard session on the peak day, rest after, find the following peak
        hard_tss = float(np.mean(HARD_SESSION_CTL_X) * ctl)
        scen = np.concatenate([tss, np.zeros(offset - 1), [hard_tss]])
        nxt = forecast_peak(model, scen)
        cycle = {"hard_session_day": (today + timedelta(offset)).isoformat(),
                 "next_peak_day": (today + timedelta(offset + nxt["peak_day_offset"])).isoformat(),
                 "recovery_days_after_hard": nxt["peak_day_offset"]}
    else:
        lag = empirical_lag(days, tss, obs, hard)
        base = (today - last_hard).days if last_hard else 0
        lo = max(lag["lag_days"][0] - base, 1)
        offset, window = lo, list(range(lo, max(lag["lag_days"][1] - base, lo) + 1))
        method, detail, gain_pct, cycle = "empirical_lag", lag, None, None

    low, high = (round(ctl * x) for x in HARD_SESSION_CTL_X)
    return {
        "generated_for": today.isoformat(),
        "method": method,
        "confidence": confidence(model),
        "now": {"ctl": ctl, "atl": now["atl"], "tsb": now["tsb"],
                "last_hard_day": last_hard.isoformat() if last_hard else None,
                "days_since_hard": (today - last_hard).days if last_hard else None},
        "supercompensation": {
            "ready_day": (today + timedelta(offset)).isoformat(),
            "definition": "first day predicted efficiency reaches 90% of the gain available in the next 7 days",
            "window": [(today + timedelta(o)).isoformat() for o in window],
            "predicted_gain_pct_vs_today": round(gain_pct, 1) if gain_pct is not None else None,
        },
        "next_workout": {
            "when": (today + timedelta(offset)).isoformat(),
            "intensity": "hard (Z4-Z5 quality session)",
            "target_tss": [low, high],
            "if_not_ready": "move one day later; the window stays open",
        },
        "next_cycle": cycle,
        "model": detail,
        "caveat": ("Efficiency is a noisy signal. Treat the peak as plus or minus one day "
                   "and confirm with morning resting HR and how the legs feel."),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default=str(ROOT / "data" / "load_export.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "next_workout_forecast.json"))
    ap.add_argument("--hard-tss", type=float, default=HARD_TSS)
    args = ap.parse_args()

    export = json.loads(Path(args.export).read_text(encoding="utf-8"))
    report = build_report(export, args.hard_tss)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    s, w = report["supercompensation"], report["next_workout"]
    print(f"Method: {report['method']} (confidence {report['confidence']})")
    print(f"Ready (strong) day: {s['ready_day']}   window: {s['window'][0]} to {s['window'][-1]}")
    print(f"Next workout: {w['when']}, {w['intensity']}, {w['target_tss'][0]}-{w['target_tss'][1]} TSS")
    if report["next_cycle"]:
        c = report["next_cycle"]
        print(f"Then rest; the following peak is {c['next_peak_day']} ({c['recovery_days_after_hard']} days later)")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
