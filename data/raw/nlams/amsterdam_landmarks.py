#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Amsterdam municipal monuments (gemeentelijke monumenten) from the
Amsterdam REST API and emit Arrow IPC to stdout.

Source: https://api.data.amsterdam.nl/v1/monumenten/monumenten/
Dataset: Gemeentelijke monumenten Amsterdam

Field mapping:
  identificatie / monumentnummer  -> landmark_id  (prefixed "nlams-")
  naam / monumentnaam             -> name
  geometrie (GeoJSON Point)       -> geometry_raw (WKT POINT), latitude, longitude

The API returns WGS84 coordinates in the geometrie field as a GeoJSON dict.
If coordinates appear to be in RD New (x > 10000), they are converted to WGS84.
"""

import sys
import requests
import pyarrow as pa
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    get_with_retry,
    rd_to_wgs84,
    rd_centroid,
    make_point_wkt,
)

BASE_URL = "https://api.data.amsterdam.nl/v1/monumenten/monumenten/"
PAGE_SIZE = 2000


def extract_coords(geometrie: dict | None) -> tuple[float | None, float | None]:
    """
    Extract (lat, lon) from the geometrie field.

    The API returns RD New (EPSG:28992) coordinates for both Points and Polygons
    (x ~100000–200000, y ~460000–500000). Points use [x, y]; Polygons use the
    centroid of the outer ring.
    """
    if geometrie is None:
        return None, None

    geo_type = geometrie.get("type", "")
    coords = geometrie.get("coordinates")
    if not coords:
        return None, None

    if geo_type == "Point":
        cx, cy = float(coords[0]), float(coords[1])
    elif geo_type in ("Polygon", "MultiPolygon"):
        # outer ring is coords[0] for Polygon, coords[0][0] for MultiPolygon
        outer = coords[0] if geo_type == "Polygon" else coords[0][0]
        cx, cy = rd_centroid(outer)
    else:
        return None, None

    if abs(cx) > 90:
        # RD New — convert to WGS84
        lat, lon = rd_to_wgs84(cx, cy)
    else:
        lon, lat = cx, cy
    return lat, lon


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_all_pages() -> list[dict]:
    """Paginate through the Amsterdam monumenten REST API."""
    url: str | None = f"{BASE_URL}?_format=json&page_size={PAGE_SIZE}"
    results: list[dict] = []
    while url:
        r = get_with_retry(url)
        data = r.json()
        embedded = data.get("_embedded", {})
        # The embedded key may be "monumenten" or similar
        page_rows = (
            embedded.get("monumenten")
            or embedded.get("results")
            or []
        )
        results.extend(page_rows)
        next_link = data.get("_links", {}).get("next", {})
        url = next_link.get("href") if isinstance(next_link, dict) else None
    return results


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

def transform(rows: list[dict]) -> pa.Table:
    landmark_ids: list[str | None] = []
    cities: list[str] = []
    names: list[str | None] = []
    geometry_raws: list[str | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []

    for rec in rows:
        # landmark_id: prefer 'identificatie', fall back to 'monumentnummer'
        raw_id = (
            rec.get("identificatie")
            or rec.get("monumentnummer")
            or rec.get("id")
        )
        landmark_ids.append(f"nlams-{raw_id}" if raw_id is not None else None)

        cities.append("NLAMS")

        # name: prefer 'naam', fall back to 'monumentnaam'
        raw_name = rec.get("naam") or rec.get("monumentnaam") or rec.get("name")
        names.append(raw_name.strip() if raw_name and raw_name.strip() else None)

        # coordinates from geometrie
        lat, lon = extract_coords(rec.get("geometrie"))
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


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def validate(table: pa.Table) -> None:
    n = table.num_rows
    if n == 0:
        # Zero rows is valid per the runbook — the parquet will be empty
        return
    for col in ("latitude", "longitude"):
        null_count = table.column(col).null_count
        null_pct = null_count / n if n > 0 else 0.0
        if null_pct > 0.25:
            raise ValueError(
                f"Amsterdam landmarks: '{col}' is NULL for {null_pct:.0%} of rows "
                f"({null_count}/{n}) — coordinate extraction may have failed"
            )


if __name__ == "__main__":
    rows = download_all_pages()
    table = transform(rows)
    validate(table)
    emit(table)
