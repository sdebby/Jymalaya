"""
One-time backfill: pull about a year of Garmin VO2max history into InfluxDB.

Every sync pulls a rolling 28-day VO2max span, so the history from before the
first sync that ran with VO2max support is simply absent. Garmin keeps its own
daily max-metrics series indefinitely, so the repair needs no reconstruction --
just a longer pull. This fetches --days days in 28-day chunks and writes them
through the same InfluxRepo.write_vo2max the sync uses.

InfluxDB keys a vo2max point on measurement + discipline + timestamp, so each
day is overwritten in place: the script is idempotent, needs no delete pass, and
can safely overlap days a sync already wrote.

Nothing is recomputed afterwards. VO2max is a number Garmin reports, not a
derived metric -- it feeds no TSS, no CTL/ATL/TSB and no plan payload. That is
what makes this backfill trivially safe compared with the swim one.

Also the intended way to inspect Garmin's max-metrics payload: --debug turns on
the jymalaya.garmin DEBUG dumps, and a dry run makes the request without writing
anything.

Requires a working Garmin session (cached tokens or .env credentials) and a
running InfluxDB.

Usage:
    python scripts/backfill_vo2max.py                      # dry run, 365 days
    python scripts/backfill_vo2max.py --days 7 --debug     # inspect the payload
    python scripts/backfill_vo2max.py --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.garmin.client import _connect, fetch_vo2max  # noqa: E402
from app.influx.client import InfluxRepo  # noqa: E402

BACKFILL_DAYS = 365


def _changes(rows: list[dict]) -> list[tuple[str, str, float | None, float]]:
    """
    The days where a discipline's VO2max actually changed.

    A year is up to 730 rows and Garmin repeats the same estimate until a
    qualifying activity revises it, so printing every row would bury the signal.
    The transitions are the whole story.
    """
    last: dict[str, float] = {}
    out = []
    for r in rows:
        prev = last.get(r["discipline"])
        if prev != r["vo2max"]:
            out.append((r["date"], r["discipline"], prev, r["vo2max"]))
            last[r["discipline"]] = r["vo2max"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the VO2max points (default is a dry run)",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=BACKFILL_DAYS,
        help=f"days of history to pull (default {BACKFILL_DAYS})",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="dump the raw Garmin max-metrics payload",
    )
    args = ap.parse_args()

    # The script's own handler: main.py owns the jymalaya logger under uvicorn,
    # so without this --debug would print nothing. The DEBUG level is raised on
    # the jymalaya logger ALONE, never on the root -- garth and oauthlib log
    # their OAuth signing material (including the MFA token) at DEBUG, and none
    # of that belongs on a terminal.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.debug:
        logging.getLogger("jymalaya").setLevel(logging.DEBUG)

    repo = InfluxRepo()
    try:
        rows = fetch_vo2max(_connect(), days=args.days)
        if not rows:
            print(f"Garmin returned no VO2max data for the last {args.days} days.")
            return 0

        by_disc = Counter(r["discipline"] for r in rows)
        counts = ", ".join(f"{n} {d}" for d, n in sorted(by_disc.items()))
        print(
            f"{len(rows)} VO2max points over {args.days} days "
            f"({rows[0]['date']} .. {rows[-1]['date']}): {counts}\n"
        )
        for day, disc, before, after in _changes(rows):
            was = f"{before:.1f}" if before is not None else "--"
            print(f"  {day}  {disc:5}  {was:>6} -> {after:5.1f}")

        if not args.apply:
            print("\nDry run: nothing written. Re-run with --apply to backfill.")
            return 0

        written = repo.write_vo2max(rows)
        print(f"\nWrote {written} VO2max points.")
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
