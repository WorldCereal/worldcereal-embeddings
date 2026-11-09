"""
Build the optional "non-EU production progress" layer: data/global_progress.geojson.

One feature per S2 tile of the global (non-EU) openEO production, from the job databases in
/vitodata/worldcereal_data/global-production/job_databases/*_job_database.parquet:

    tile, region, epsg, start_date, end_date (most frequent cell window), n_cells,
    finished, failed, pending       cell counts by openEO job status
    status          finished      every cell finished
                    in_progress   some cells still running / queued / not started
                    failed_cells  the rest finished, but one or more cells failed

The footprint is the bounding box of the tile's 50 km cells (tile UTM CRS -> WGS84), as in
build_grid.py. These tiles are produced by openEO only; no S2-tile mosaics exist yet, so the
viewer shows them as outlines without downloads.

    python tools/build_global_progress.py [--job-dbs DIR]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from build_grid import footprint

DEFAULT_JOB_DBS = "/vitodata/worldcereal_data/global-production/job_databases"
OUT = Path(__file__).resolve().parents[1] / "data" / "global_progress.geojson"
PENDING = {"not_started", "queued", "created", "running", "start_failed_retry"}
FAILED = {"error", "start_failed", "skipped", "killed", "canceled"}


def tile_status(statuses: pd.Series) -> str:
    if statuses.isin(PENDING).any():
        return "in_progress"
    if (statuses != "finished").any():
        return "failed_cells"
    return "finished"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dbs", default=DEFAULT_JOB_DBS)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    cols = ["tile", "s2_tile", "epsg", "xmin", "ymin", "xmax", "ymax", "start_date", "end_date", "status"]
    features, counts, regions = [], Counter(), {}
    for path in sorted(glob.glob(os.path.join(args.job_dbs, "*_job_database.parquet"))):
        region = os.path.basename(path).replace("_job_database.parquet", "")
        db = pd.read_parquet(path, columns=cols)
        regions[region] = dict(cells=len(db), statuses=db["status"].value_counts().to_dict())
        for s2, g in db.groupby("s2_tile"):
            window = Counter(zip(g["start_date"].astype(str).str[:10], g["end_date"].astype(str).str[:10]))
            start, end = max(window.items(), key=lambda kv: (kv[1], kv[0][1], kv[0][0]))[0]
            st = tile_status(g["status"])
            counts[st] += 1
            features.append({
                "type": "Feature",
                "properties": {
                    "tile": s2, "region": region, "epsg": int(g["epsg"].iloc[0]),
                    "start_date": start, "end_date": end, "n_cells": len(g),
                    "finished": int((g["status"] == "finished").sum()),
                    "failed": int(g["status"].isin(FAILED).sum()),
                    "pending": int(g["status"].isin(PENDING).sum()),
                    "status": st,
                },
                "geometry": {"type": "Polygon", "coordinates": [footprint(
                    int(g["epsg"].iloc[0]), g["xmin"].min(), g["ymin"].min(), g["xmax"].max(), g["ymax"].max(), n=4)]},
            })

    out = {
        "type": "FeatureCollection",
        "name": "worldcereal_global_production_progress",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": dict(counts),
        "regions": regions,
        "features": features,
    }
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, args.out)
    print(f"{len(features)} tiles -> {args.out} ({os.path.getsize(args.out) / 1e6:.2f} MB): {dict(counts)}")
    for r, v in regions.items():
        print(f"  {r}: {v['cells']} cells {v['statuses']}")


if __name__ == "__main__":
    main()
