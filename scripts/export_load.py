"""
Read-only export of the training-load history to data/load_export.json.

Writes the last --days days of the combined daily_load series (CTL/ATL/TSB/TSS)
and the stored workouts, so the load can be analysed outside the app. Reads
InfluxDB through the same InfluxRepo the app uses; writes nothing to InfluxDB.

Usage:
    python scripts/export_load.py              # 90 days
    python scripts/export_load.py --days 120
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.influx import queries  # noqa: E402
from app.influx.client import InfluxRepo  # noqa: E402

def _clean(row: dict, keep=None) -> dict:
    out = {}
    for k, v in row.items():
        if k.startswith("_") and k != "_time":
            continue
        if keep and k not in keep and k != "_time":
            continue
        if isinstance(v, datetime):
            v = v.isoformat()
        out[k] = v
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    repo = InfluxRepo()
    if not repo.ping():
        sys.exit("InfluxDB is not reachable -- is the container running?")
    try:
        load = [_clean(r) for r in repo.query(queries.daily_load_last_n_days(args.days, "all"))]
        workouts = [_clean(r) for r in repo.query(queries.workouts_last_n_days(args.days))]
    finally:
        repo.close()

    out = ROOT / "data" / "load_export.json"
    out.write_text(json.dumps({"exported_at": datetime.now().isoformat(), "days": args.days,
                               "daily_load": load, "workouts": workouts}, indent=1, default=str),
                   encoding="utf-8")
    print(f"Wrote {len(load)} load days and {len(workouts)} workouts to {out}")


if __name__ == "__main__":
    main()
