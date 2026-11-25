#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow"]
# ///
"""Publish data/raw/boundaries/*.json as the one parquet a worker reads.

    cd data/raw && uv run tools/publish_boundaries.py            # report
    cd data/raw && uv run tools/publish_boundaries.py --publish  # upload, then
                                                                 # name it in shared/boundary.py

The boundary files are the source and stay in git, but they never reach a
worker: the cloud sync caps its body, and every town's polygon is tens of
kilobytes (`[cloud] exclude` in trilogy.toml).  This writes them as one
parquet named by their content hash (`boundary_digest`), uploads it beside the
other reference tables with `gcloud storage cp`, and only then rewrites
`BOUNDARY_PARQUET` in shared/boundary.py.  Content-addressed, so an upload
never replaces what a running job reads, and a model or job needs no change.

Run it after adding or changing a boundary file; the offline tests fail until
`BOUNDARY_PARQUET` names the files' hash.

Columns: `city`, `name`, `geoid`, `source`, `retrieved` (from the file's
properties), `geometry` (the GeoJSON geometry, what shared/boundary.py reads),
`wkt` (the same polygon as WKT, for SQL), and the envelope.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW))

from shared.boundary import (  # noqa: E402
    BOUNDARY_PARQUET,
    BOUNDARY_PUBLIC_PREFIX,
    Boundary,
    boundary_feature,
    boundary_files,
    published_boundary_name,
    rings_of,
)

PUBLISHED_PREFIX = "gs://trilogy_public_models/duckdb/trees/reference"
BOUNDARY_MODULE = RAW / "shared" / "boundary.py"


def table() -> pa.Table:
    rows = []
    for path in boundary_files():
        feature = boundary_feature(path)
        props = feature.get("properties") or {}
        boundary = Boundary(rings_of(feature["geometry"]))
        lat_min, lat_max, lon_min, lon_max = boundary.box
        rows.append(
            {
                "city": path.stem.upper(),
                "name": props.get("name"),
                "geoid": props.get("geoid"),
                "source": props.get("source"),
                "retrieved": props.get("retrieved"),
                "geometry": json.dumps(feature["geometry"], separators=(",", ":")),
                "wkt": boundary.wkt(),
                "lat_min": lat_min,
                "lat_max": lat_max,
                "lon_min": lon_min,
                "lon_max": lon_max,
            }
        )
    if not rows:
        raise SystemExit("no boundary files in data/raw/boundaries/")
    return pa.Table.from_pylist(rows)


def name_in_module(name: str) -> None:
    text = BOUNDARY_MODULE.read_text(encoding="utf-8")
    updated, n = re.subn(r'^BOUNDARY_PARQUET = "[^"]*"$', f'BOUNDARY_PARQUET = "{name}"', text, flags=re.M)
    if n != 1:
        raise SystemExit("BOUNDARY_PARQUET not found exactly once in shared/boundary.py")
    BOUNDARY_MODULE.write_text(updated, encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true", help="upload the parquet and name it in shared/boundary.py")
    args = ap.parse_args()

    name = published_boundary_name()
    data = table()
    print(f"{data.num_rows} boundaries -> {name}")
    if name == BOUNDARY_PARQUET:
        print("shared/boundary.py already names it")
        if not args.publish:
            return 0
    elif not args.publish:
        print(f"shared/boundary.py names {BOUNDARY_PARQUET}; run with --publish")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        pq.write_table(data, local, compression="zstd")
        subprocess.run(
            ["gcloud", "storage", "cp", str(local), f"{PUBLISHED_PREFIX}/{name}"],
            check=True, shell=sys.platform == "win32",
        )
    name_in_module(name)
    print(f"published {BOUNDARY_PUBLIC_PREFIX}/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
