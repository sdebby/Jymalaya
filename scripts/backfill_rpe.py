"""
Backfill: write the athlete's own RPE and feel, read from each workout's FIT
file, onto every stored workout of the last N days.

The sync stores them for every workout whose FIT file it fetches from
2026-11-14 on, so this script is for workouts whose samples were stored before
that (the sync never fetches their file again). It downloads each FIT file,
reads the session message, deletes the file, and writes only the two fields,
plus the tags and timestamp that address each existing point; Influx merges
them into the point, so nothing else on it changes. Idempotent; a dry run (the
default) prints what it would write.

Requires GARMIN_PERSONAL_API_KEY in .env and a running InfluxDB.

Usage:
    python scripts/backfill_rpe.py            # dry run, last 30 days
    python scripts/backfill_rpe.py --days 60
    python scripts/backfill_rpe.py --apply
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

from app.garmin import fit, personal_api  # noqa: E402
from app.garmin.parser import _is_multisport, leg_activity_id  # noqa: E402
from app.influx.client import InfluxRepo  # noqa: E402
from app.sync_service import _restore_point_time, _stored_workout_history  # noqa: E402


def _garmin_ids(start: date, end: date) -> dict[str, object]:
    """Stored activity id -> the real Garmin id whose FIT file holds it."""
    out: dict[str, object] = {}
    for a in personal_api._fold_children(personal_api.list_activities(start, end)):
        if _is_multisport(a):
            for leg in a.get("_legs") or []:
                out[leg_activity_id(a.get("activityId"), leg)] = leg.get("activityId")
        else:
            out[str(a.get("activityId"))] = a.get("activityId")
    return out


def _rating(garmin_id) -> dict | None:
    """The FIT file's {"rpe", "feel"}, or None when there is no file."""
    path = None
    try:
        path = personal_api.download_fit(garmin_id)
        if path is None:
            return None
        return fit.decode(path)[2]
    except Exception:
        logging.getLogger("backfill_rpe").warning("FIT failed for %s", garmin_id, exc_info=True)
        return None
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="window to backfill (default 30)")
    ap.add_argument("--apply", action="store_true", help="write to InfluxDB (default is a dry run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if not personal_api.has_key():
        print(f"{personal_api.KEY_ENV} is not set in .env.")
        return 1

    end = date.today()
    start = end - timedelta(days=args.days - 1)
    cutoff = start.isoformat()
    personal_api.purge_fit_tmp()

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
        ids = _garmin_ids(start - timedelta(days=1), end)

        updates = []
        fmt = lambda v: "--" if v is None else f"{v:g}"  # noqa: E731
        print(f"{'date':10}  {'discipline':9} {'title':28} {'RPE':>4} {'feel':>5}")
        for aid, row in sorted(stored.items(), key=lambda kv: kv[1].get("date") or ""):
            rating = _rating(ids[aid]) if aid in ids else None
            rating = rating or {}
            note = "" if aid in ids else "  not in Garmin list"
            print(
                f"{row.get('date') or '':10}  {row.get('discipline') or '':9} "
                f"{(row.get('title') or '')[:28]:28} {fmt(rating.get('rpe')):>4} "
                f"{fmt(rating.get('feel')):>5}{note}"
            )
            if rating.get("rpe") is None and rating.get("feel") is None:
                continue
            point = {
                "activity_id": aid,
                "discipline": row.get("discipline"),
                "session_id": row.get("session_id"),
                "is_brick_split": row.get("is_brick_split"),
                "time": row.get("time"),
                "rpe": rating.get("rpe"),
                "feel": rating.get("feel"),
            }
            _restore_point_time(point)
            updates.append(point)

        print(f"\n{len(updates)} of {len(stored)} stored workouts have a rating to write.")
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
