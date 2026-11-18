"""
Backfill: fetch the FIT file of every stored workout of the last N days that
has no samples yet, store its 5 s samples, laps and planned workout, set NGP on
runs, delete the file, and recompute TSS and CTL/ATL/TSB from the full history.

The sync does this on its own for every new workout (and for any workout in its
window still without samples), so this script is for the history synced before
FIT samples were kept -- the first run is `--days 30` -- and for reaching
further back later. Workouts older than the window keep average-pace run TSS.

A dry run downloads and decodes the FIT files (NGP needs them) and prints what
would change, writing nothing; each file is deleted straight after decoding
either way. Every write is an upsert on the same points, so the script is
idempotent.

Requires GARMIN_PERSONAL_API_KEY in .env and a running InfluxDB.

Usage:
    python scripts/backfill_fit.py                 # dry run, last 30 days
    python scripts/backfill_fit.py --days 60
    python scripts/backfill_fit.py --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.config import get_athlete_profile  # noqa: E402
from app.garmin import personal_api  # noqa: E402
from app.garmin.parser import _discipline, _is_multisport, leg_activity_id  # noqa: E402
from app.influx.client import InfluxRepo  # noqa: E402
from app.metrics.calculator import compute_load, daily_tss, estimate_tss_method  # noqa: E402
from app.sync_service import (  # noqa: E402
    _restore_point_time,
    _sampled_ids,
    _stored_workout_history,
    recompute_stored_metrics,
)


def _units(start: date, end: date) -> dict[str, tuple[dict, str]]:
    """Garmin's activities over the window, keyed by the id they are stored under."""
    out: dict[str, tuple[dict, str]] = {}
    for a in personal_api._fold_children(personal_api.list_activities(start, end)):
        if _is_multisport(a):
            for leg in a.get("_legs") or []:
                disc = _discipline((leg.get("activityType") or {}).get("typeKey"))
                out[leg_activity_id(a.get("activityId"), leg)] = (leg, disc)
        else:
            disc = _discipline((a.get("activityType") or {}).get("typeKey"))
            out[str(a.get("activityId"))] = (a, disc)
    return out


def _form_today(history: list[dict]) -> float | None:
    rows = compute_load(daily_tss(history), discipline="all", through=date.today())
    return rows[-1]["tsb"] if rows else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="window to backfill (default 30)")
    ap.add_argument("--apply", action="store_true", help="write to InfluxDB (default is a dry run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if not personal_api.has_key():
        print(f"{personal_api.KEY_ENV} is not set in .env.")
        return 1

    profile = get_athlete_profile()
    end = date.today()
    start = end - timedelta(days=args.days - 1)
    cutoff = start.isoformat()
    personal_api.purge_fit_tmp()

    repo = InfluxRepo()
    try:
        history = _stored_workout_history(repo)
        form_before = _form_today(history)
        sampled = _sampled_ids(repo, args.days + 1)
        todo = [
            r for r in history
            if (r.get("date") or "") >= cutoff and str(r.get("activity_id")) not in sampled
        ]
        if not todo:
            print(f"Every stored workout since {cutoff} already has samples.")
            return 0
        units = _units(start - timedelta(days=1), end)
        print(f"{len(todo)} stored workouts since {cutoff} without samples; fetching FIT files...\n")

        cache: dict = {}
        detailed: list[dict] = []
        print(f"{'date':10}  {'discipline':9} {'title':28} {'old TSS':>8} {'new TSS':>8}  method")
        for row in sorted(todo, key=lambda r: r.get("date") or ""):
            aid = str(row.get("activity_id"))
            match = units.get(aid)
            old = row.get("tss")
            if match is None:
                print(f"{row.get('date'):10}  {row.get('discipline'):9} {str(row.get('title'))[:28]:28} {old!s:>8}  not on Garmin")
                continue
            unit, disc = match
            if not personal_api.enrich_with_fit(unit, disc, cache):
                print(f"{row.get('date'):10}  {disc:9} {str(row.get('title'))[:28]:28} {old!s:>8}  no FIT samples")
                continue
            updated = copy.copy(row)
            if disc == "run" and unit.get("_ngp_sec_per_km"):
                updated["ngp_sec_per_km"] = unit["_ngp_sec_per_km"]
            for key in ("rpe", "feel"):
                if unit.get(f"_{key}") is not None:
                    updated[key] = unit[f"_{key}"]
            if unit.get("_hr_zones") and not row.get("zones_time_sec"):
                updated["zones_time_sec"] = json.dumps(unit["_hr_zones"])
            new, intensity, method = estimate_tss_method(updated, profile)
            if new is not None:
                updated["tss"], updated["intensity_factor"], updated["tss_method"] = new, intensity, method
            updated["_detail"] = unit["_detail"]
            detailed.append(updated)
            row.update({k: v for k, v in updated.items() if k != "_detail"})
            ngp = updated.get("ngp_sec_per_km")
            pace = f"  NGP {int(ngp // 60)}:{int(ngp % 60):02d}/km" if ngp else ""
            print(f"{row.get('date'):10}  {disc:9} {str(row.get('title'))[:28]:28} {old!s:>8} {new!s:>8}  {method}{pace}")

        form_after = _form_today(history)
        samples = sum(len(w["_detail"]["samples"]) for w in detailed)
        print(
            f"\n{len(detailed)} workouts with samples ({samples} points). "
            f"Form (TSB) today: {form_before} -> {form_after}"
        )
        leftover = list(personal_api.FIT_TMP.glob("*.fit")) if personal_api.FIT_TMP.is_dir() else []
        print(f"FIT files left on disk: {len(leftover)}")

        if not args.apply:
            print("\nDry run: nothing written. Re-run with --apply to store it.")
            return 0

        for w in detailed:
            _restore_point_time(w)
        written = repo.write_workout_detail(detailed)
        repo.write_workouts(detailed)
        result = recompute_stored_metrics(repo, profile)
        print(
            f"\nWrote {written} samples for {len(detailed)} workouts; recomputed "
            f"{result['rewritten']} workouts and {result['load_days']} daily load rows "
            f"({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)."
        )
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
