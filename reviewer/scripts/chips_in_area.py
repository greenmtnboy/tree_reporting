"""The chips of a training run that fall inside a longitude/latitude box.

Run by the reviewer server inside the imagery model's environment
(``uv run --group imagery python <this file>`` with imagery_model/ as the
working directory), so it needs nothing the reviewer does not already ask
of that package.  Prints one JSON object:

    {"cohorts": {"validation-usbos": ["r000095_c000047", ...], ...}}

A chip is in the box when its footprint intersects it.  Only base chips
(``r000000_c000000``) count: augmented ones (``shift_...``) repeat a base
chip's ground.  Sealed test cohorts are never listed.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pyarrow.parquet as pq
import rasterio
from pyproj import Transformer

BASE_CHIP = re.compile(r"r\d{6}_c\d{6}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="artifacts")
    parser.add_argument("--run", required=True)
    parser.add_argument("--city", required=True, help="five-letter city code")
    parser.add_argument("--bbox", required=True, help="west,south,east,north in degrees")
    parser.add_argument("--raster", required=True, help="the mosaic the run's chips were cut from")
    args = parser.parse_args()

    west, south, east, north = (float(v) for v in args.bbox.split(","))
    city = args.city.upper()
    evaluation = Path(args.artifacts) / "runs" / args.run / "evaluation"
    with rasterio.open(args.raster) as raster:
        affine, crs = raster.transform, raster.crs
    to_lonlat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    cohorts: dict[str, list[str]] = {}
    for metadata_path in sorted(evaluation.glob("*/evaluation-metadata.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        config = metadata.get("config") or {}
        cohort_city = str(metadata.get("city") or (config.get("inventory") or {}).get("city") or "").upper()
        if cohort_city != city or metadata.get("split") == "test":
            continue
        predicted = set(
            pq.read_table(metadata_path.parent / "predictions.parquet", columns=["chip_id"]).column("chip_id").to_pylist()
        )
        chips = pq.read_table(
            Path(args.artifacts) / "chips" / config["dataset"] / "chips.parquet",
            columns=["chip_id", "row_offset", "column_offset"],
        ).to_pylist()
        size = int(config["imagery"]["chip_pixels"])
        inside: list[str] = []
        for chip in chips:
            chip_id = chip["chip_id"]
            if chip_id not in predicted or not BASE_CHIP.fullmatch(chip_id):
                continue
            row, col = chip["row_offset"], chip["column_offset"]
            corners = [affine * (col + dc, row + dr) for dr in (0, size) for dc in (0, size)]
            lons, lats = zip(*(to_lonlat.transform(x, y) for x, y in corners))
            if min(lons) <= east and max(lons) >= west and min(lats) <= north and max(lats) >= south:
                inside.append(chip_id)
        if inside:
            cohorts[metadata_path.parent.name] = sorted(inside)
    print(json.dumps({"cohorts": cohorts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
