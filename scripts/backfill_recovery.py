"""
Backfill: pull Garmin's daily HRV, resting HR and nightly sleep into InfluxDB,
back to the first stored activity (sleep is one request per night).

The sync already does this on its own the first time it finds no recovery data
stored (and again after a full data reset), so this script is for re-running
the full pull on demand -- or for a different start date -- and for inspecting
Garmin's payloads: --debug turns on the jymalaya.garmin DEBUG dumps, and a dry
run makes the requests without writing anything.

InfluxDB keys a recovery point on measurement + timestamp (midnight UTC of the
day), so each day is overwritten in place: the script is idempotent and can
safely overlap days a sync already wrote. Nothing is recomputed afterwards --
HRV, resting HR and sleep feed no TSS, no CTL/ATL/TSB and no Claude payload.

Requires GARMIN_PERSONAL_API_KEY in .env (the Personal API serves both
series since 2026-11-06) and a running InfluxDB.

Usage:
    python scripts/backfill_recovery.py                          # dry run
    python scripts/backfill_recovery.py --since 2026-10-01 --debug
    python scripts/backfill_recovery.py --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.garmin.personal_api import pull_recovery, pull_sleep  # noqa: E402
from app.influx.client import InfluxRepo  # noqa: E402
from app.influx.queries import oldest_workout_point  # noqa: E402


def _first_activity_day(repo: InfluxRepo) -> date | None:
    """The oldest stored workout's day, less one for the UTC/local offset."""
    for row in repo.query(oldest_workout_point())[:1]:
        t = row.get("time")
        if isinstance(t, datetime):
            return t.date() - timedelta(days=1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the recovery points (default is a dry run)",
    )
    ap.add_argument(
        "--since",
        type=date.fromisoformat,
        help="first day to pull, YYYY-MM-DD (default: the first stored activity)",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="dump the raw Garmin HRV and resting HR payloads",
    )
    args = ap.parse_args()

    # DEBUG on the jymalaya logger alone, never the root: httpx logs request
    # headers at DEBUG, and the Personal API token travels in one.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.debug:
        logging.getLogger("jymalaya").setLevel(logging.DEBUG)

    repo = InfluxRepo()
    try:
        since = args.since or _first_activity_day(repo)
        if since is None:
            print("No stored activities yet: pass --since, or run a Garmin sync first.")
            return 1

        rows = pull_recovery(since)
        # Nightly sleep too: one request per night, so a long backfill is slow.
        sleep = pull_sleep(since)
        if not rows and not sleep:
            print(f"Garmin returned no HRV, resting HR or sleep data since {since}.")
            return 0

        hrv = [r["hrv"] for r in rows if r["hrv"] is not None]
        rhr = [r["resting_hr"] for r in rows if r["resting_hr"] is not None]
        print(f"Asked from {since}:")
        if hrv:
            print(f"  HRV         {len(hrv):4} nights, {min(hrv):.0f}-{max(hrv):.0f} ms")
        if rhr:
            print(f"  resting HR  {len(rhr):4} days,   {min(rhr):.0f}-{max(rhr):.0f} bpm")
        if sleep:
            hours = [r["sleep_sec"] / 3600 for r in sleep]
            print(f"  sleep       {len(sleep):4} nights, {min(hours):.1f}-{max(hours):.1f} h")
        for r in rows[-7:]:
            h = f"{r['hrv']:.0f} ms" if r["hrv"] is not None else "--"
            b = f"{r['resting_hr']:.0f} bpm" if r["resting_hr"] is not None else "--"
            print(f"  {r['date']}  {h:>7}  {b:>8}")

        if not args.apply:
            print("\nDry run: nothing written. Re-run with --apply to backfill.")
            return 0

        written = repo.write_recovery(rows + sleep)
        print(f"\nWrote {written} recovery points.")
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
