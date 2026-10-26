"""
Build the S2-tile grid the viewer draws: data/s2_grid.geojson.

One feature per S2 tile of the production index, with the footprint of the mosaic
(bounding box of its 50 km cells, in the tile's UTM CRS, reprojected to WGS84 with
densified edges) and:

    tile, epsg, start_date, end_date, n_cells, countries,
    status          on_s3 | local | todo          (drives the outline colour)
    detail          the index status per product, e.g. "embeddings=locally_available, scale=..."
    emb_file, scale_file    file names (the viewer builds the https links from them)

status:
    on_s3   both the embeddings and the scale COG are on S3 with the size of the local file
            (or the index already says pushed_to_s3)
    local   both products processed locally, not (completely) on S3 yet
    todo    anything else (unprocessed, not_ready, failed, ...)

Reads the index of the processing pipeline (worldcereal-globalproduction/scripts/embeddings):
<root>/index/s2_index.parquet and <root>/index/job_database.parquet. Only HTTP HEADs to the
public bucket, nothing is written there.

    python tools/build_grid.py [--root /vitodata/worldcereal/tmp/roshaan/europe-embeddings]
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pyproj import Transformer

DEFAULT_ROOT = "/vitodata/worldcereal/tmp/roshaan/europe-embeddings"
TILE_COUNTRIES = "/vitodata/worldcereal/tmp/cbutsko/visualization/data/tile_countries.json"
S3_BASE = "https://s3.waw4-1.cloudferro.com/worldcereal-phase2-waw4-1-embeddings/worldcereal_embeddings"
OUT = Path(__file__).resolve().parents[1] / "data" / "s2_grid.geojson"
SUFFIX = {"embeddings": "worldcereal_embeddings", "scale": "worldcereal_embeddings_scale"}


def file_name(tile: str, product: str, start: str, end: str) -> str:
    """Same naming as tile_index.output_name in the processing pipeline."""
    s, e = (str(d)[:10].replace("-", "") for d in (start, end))
    return f"{tile}_{s}_{e}_{SUFFIX[product]}.tif"


def head_size(url: str):
    req = urllib.request.Request(url, method="HEAD")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return int(r.headers["Content-Length"])
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                return None
            if attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
    return None


def footprint(epsg: int, xmin: float, ymin: float, xmax: float, ymax: float, n: int = 8) -> list:
    """Densified WGS84 ring of a UTM rectangle (lon/lat, 5 decimals ~1 m)."""
    tr = Transformer.from_crs(epsg, 4326, always_xy=True)
    pts = []
    for i in range(n):
        pts.append((xmin + (xmax - xmin) * i / n, ymax))
    for i in range(n):
        pts.append((xmax, ymax - (ymax - ymin) * i / n))
    for i in range(n):
        pts.append((xmax - (xmax - xmin) * i / n, ymin))
    for i in range(n):
        pts.append((xmin, ymin + (ymax - ymin) * i / n))
    pts.append(pts[0])
    xs, ys = tr.transform([p[0] for p in pts], [p[1] for p in pts])
    return [[round(x, 5), round(y, 5)] for x, y in zip(xs, ys)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-s3-check", action="store_true", help="trust the index only (no HEAD requests)")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    idx = pd.read_parquet(os.path.join(args.root, "index", "s2_index.parquet"))
    db = pd.read_parquet(os.path.join(args.root, "index", "job_database.parquet"),
                         columns=["s2_tile", "xmin", "ymin", "xmax", "ymax"])
    ext = db.groupby("s2_tile").agg(xmin=("xmin", "min"), ymin=("ymin", "min"),
                                    xmax=("xmax", "max"), ymax=("ymax", "max"))
    tc = json.load(open(TILE_COUNTRIES)) if os.path.exists(TILE_COUNTRIES) else {}
    countries = {}
    for cell, cs in tc.items():
        countries.setdefault(cell[:5], set()).update(cs)

    rows = []
    for r in idx.itertuples():
        rows.append(dict(
            tile=r.s2_tile, epsg=int(r.epsg), start=str(r.start_date)[:10], end=str(r.end_date)[:10],
            n_cells=int(r.n_cells), st={p: getattr(r, f"{p}_status") for p in SUFFIX},
            local_bytes={p: getattr(r, f"{p}_bytes") for p in SUFFIX},
        ))

    def on_s3(row) -> bool:
        if all(row["st"][p] == "pushed_to_s3" for p in SUFFIX):
            return True
        if args.no_s3_check or not all(row["st"][p] in ("locally_available", "pushed_to_s3") for p in SUFFIX):
            return False
        for p in SUFFIX:
            size = head_size(f"{S3_BASE}/{file_name(row['tile'], p, row['start'], row['end'])}")
            local = row["local_bytes"][p]
            if size is None or (row["st"][p] == "locally_available" and local and size != int(local)):
                return False
        return True

    with ThreadPoolExecutor(args.workers) as pool:
        flags = list(pool.map(on_s3, rows))

    features, counts = [], {"on_s3": 0, "local": 0, "todo": 0}
    for row, s3 in zip(rows, flags):
        done = all(row["st"][p] in ("locally_available", "pushed_to_s3") for p in SUFFIX)
        status = "on_s3" if s3 else ("local" if done else "todo")
        counts[status] += 1
        e = ext.loc[row["tile"]]
        features.append({
            "type": "Feature",
            "properties": {
                "tile": row["tile"], "epsg": row["epsg"],
                "start_date": row["start"], "end_date": row["end"], "n_cells": row["n_cells"],
                "countries": sorted(countries.get(row["tile"], [])),
                "status": status,
                "detail": ", ".join(f"{p}={row['st'][p]}" for p in SUFFIX),
                "emb_file": file_name(row["tile"], "embeddings", row["start"], row["end"]),
                "scale_file": file_name(row["tile"], "scale", row["start"], row["end"]),
                "emb_bytes": int(row["local_bytes"]["embeddings"]) if done and pd.notna(row["local_bytes"]["embeddings"]) else None,
                "scale_bytes": int(row["local_bytes"]["scale"]) if done and pd.notna(row["local_bytes"]["scale"]) else None,
            },
            "geometry": {"type": "Polygon",
                         "coordinates": [footprint(row["epsg"], e.xmin, e.ymin, e.xmax, e.ymax)]},
        })

    out = {
        "type": "FeatureCollection",
        "name": "worldcereal_embeddings_s2_grid",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "s3_base": S3_BASE,
        "counts": counts,
        "features": features,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, args.out)
    print(f"{len(features)} tiles -> {args.out} ({os.path.getsize(args.out) / 1e6:.2f} MB): {counts}")


if __name__ == "__main__":
    main()
