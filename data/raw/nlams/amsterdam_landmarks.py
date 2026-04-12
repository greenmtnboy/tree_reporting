#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Amsterdam municipal monuments (gemeentelijke monumenten) via the CSV
endpoint and emit Arrow IPC to stdout.

The JSON HAL endpoint is extremely slow here; the CSV endpoint returns the full
dataset in a single response much faster.

Field mapping:
  identificatie / monumentnummer  -> landmark_id  (prefixed "nlams-")
  naam / monumentnummer           -> name
  puntCoordinaten                -> geometry_raw (WKT POINT), latitude, longitude

puntCoordinaten is emitted as EWKT in RD New (EPSG:28992), e.g.
  SRID=28992;POINT (121177 486052)
which we convert to WGS84.
"""

import csv
import io
import re
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, make_point_wkt, rd_to_wgs84

BASE_URL = "https://api.data.amsterdam.nl/v1/monumenten/monumenten/"
PAGE_SIZE = 10000
POINT_RE = re.compile(
    r"SRID=\d+;POINT\s*\(\s*([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s*\)"
)


def download_csv_text() -> str:
    url = f"{BASE_URL}?_format=csv&page_size={PAGE_SIZE}"
    r = requests.get(url, timeout=420)
    r.raise_for_status()
    return r.text


def load_rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def parse_point(raw: str | None) -> tuple[float | None, float | None]:
    if not raw or not raw.strip():
        return None, None
    match = POINT_RE.match(raw.strip())
    if not match:
        return None, None
    x = float(match.group(1))
    y = float(match.group(2))
    if abs(x) > 90:
        lat, lon = rd_to_wgs84(x, y)
    else:
        lon, lat = x, y
    return lat, lon


def transform(rows: list[dict[str, str]]) -> pa.Table:
    landmark_ids: list[str] = []
    cities: list[str] = []
    names: list[str] = []
    geometry_raws: list[str | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []

    for row in rows:
        raw_id = row.get("identificatie")
        monument_number = row.get("monumentnummer")
        raw_name = row.get("naam")
        raw_point = row.get("puntCoordinaten")
        stable_id = (raw_id or monument_number or "").strip()
        if not stable_id:
            continue

        lat, lon = parse_point(raw_point)
        if lat is None or lon is None:
            continue

        raw_name_clean = (raw_name or "").strip()
        fallback_name = (monument_number or raw_id or "").strip()
        name = raw_name_clean or fallback_name
        if not name:
            continue

        landmark_ids.append(f"nlams-{stable_id}")
        cities.append("NLAMS")
        names.append(name)
        latitudes.append(lat)
        longitudes.append(lon)
        geometry_raws.append(make_point_wkt(lon, lat))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "name": pa.array(names, type=pa.string()),
            "geometry_raw": pa.array(geometry_raws, type=pa.string()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
        }
    )


def validate(table: pa.Table) -> None:
    n = table.num_rows
    if n == 0:
        return
    for col in ("latitude", "longitude"):
        null_count = table.column(col).null_count
        null_pct = null_count / n if n > 0 else 0.0
        if null_pct > 0.25:
            raise ValueError(
                f"Amsterdam landmarks: '{col}' is NULL for {null_pct:.0%} of rows "
                f"({null_count}/{n}) - coordinate extraction may have failed"
            )


if __name__ == "__main__":
    table = transform(load_rows(download_csv_text()))
    before = table.num_rows
    table = table.filter(
        pc.and_(
            pc.and_(
                pc.is_valid(table["landmark_id"]),
                pc.not_equal(pc.utf8_trim_whitespace(table["landmark_id"]), ""),
            ),
            pc.and_(
                pc.is_valid(table["name"]),
                pc.not_equal(pc.utf8_trim_whitespace(table["name"]), ""),
            ),
        )
    )
    dropped = before - table.num_rows
    if dropped:
        print(
            f"Amsterdam landmarks: dropped {dropped} rows with null or blank required fields",
            file=sys.stderr,
        )
    validate(table)
    emit(table)
