"""
Backfill: write Garmin's Training Effect (aerobic, anaerobic, primary benefit)
onto every stored workout of the last N days.

The sync stores it for every workout it pulls from 2026-11-09 on, so this
script is for workouts synced before that. It reads the Personal API activity
list over the window (no FIT files) and writes only the three fields, plus the
tags and timestamp that address each existing point; Influx merges them into
the point, so nothing else on it changes. Idempotent; a dry run (the default)
prints what it would write.

Requires GARMIN_PERSONAL_API_KEY in .env and a running InfluxDB.

Usage:
    python scripts/backfill_training_effect.py            # dry run, last 7 days
    python scripts/backfill_training_effect.py --days 60
    python scripts/backfill_training_effect.py --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.garmin import personal_api  # noqa: E402
from app.garmin.parser import parse_activities  # noqa: E402
from app.influx.client import InfluxRepo  # noqa: E402
from app.sync_service import _restore_point_time, _stored_workout_history  # noqa: E402

TE_FIELDS = ("aerobic_te", "anaerobic_te", "te_label")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="window to backfill (default 7)")
    ap.add_argument("--apply", action="store_true", help="write to InfluxDB (default is a dry run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if not personal_api.has_key():
        print(f"{personal_api.KEY_ENV} is not set in .env.")
        return 1

    end = date.today()
    start = end - timedelta(days=args.days - 1)
    cutoff = start.isoformat()

    repo = InfluxRepo()
    try:
        stored = {
            str(r.get("activity_id")): r
            for r in _stored_workout_history(repo)
            if (r.get("date") or "") >= cutoff
        }
        if not stored:
            print(f"No stored workouts since {cutoff}.")
            return 0
        # One day of slack before the window: list dates are UTC, stored dates local.
        raw = personal_api._fold_children(personal_api.list_activities(start - timedelta(days=1), end))
        parsed = {w["activity_id"]: w for w in parse_activities(raw)}

        updates = []
        print(f"{'date':10}  {'discipline':9} {'title':28} {'aerobic':>7} {'anaerobic':>9}  benefit")
        for aid, row in sorted(stored.items(), key=lambda kv: kv[1].get("date") or ""):
            w = parsed.get(aid)
            te = {f: (w or {}).get(f) for f in TE_FIELDS}
            fmt = lambda v: "--" if v is None else f"{v:.1f}"  # noqa: E731
            print(
                f"{row.get('date') or '':10}  {row.get('discipline') or '':9} "
                f"{(row.get('title') or '')[:28]:28} {fmt(te['aerobic_te']):>7} "
                f"{fmt(te['anaerobic_te']):>9}  {te['te_label'] or ('not in Garmin list' if w is None else '--')}"
            )
            if w is None or all(v is None for v in te.values()):
                continue
            point = {
                "activity_id": aid,
                "discipline": row.get("discipline"),
                "session_id": row.get("session_id"),
                "is_brick_split": row.get("is_brick_split"),
                "time": row.get("time"),
                **te,
            }
            _restore_point_time(point)
            updates.append(point)

        print(f"\n{len(updates)} of {len(stored)} stored workouts have a Training Effect to write.")
        if not args.apply:
            print("Dry run: nothing written. Re-run with --apply.")
            return 0
        written = repo.write_workouts(updates) if updates else 0
        print(f"Wrote {written} workouts.")
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(main())
