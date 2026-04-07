#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
import math
import requests
import pyarrow as pa
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, normalize_species, validate_coordinates

# ArcGIS FeatureServer — Brookline Tree Viewer
QUERY_URL = "https://services1.arcgis.com/Oknk0tvfHOElpgGU/arcgis/rest/services/Brookline_Tree_Viewer_Web_WFL1/FeatureServer/0/query"
PAGE_SIZE = 2000
OUT_FIELDS = "OBJECTID,ScientificNameTxt,CommonNameTxt,DBH,YR_PLANT,IsStump"


def merc_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convert EPSG:3857 (Web Mercator) to WGS84 lon/lat."""
    lon = x / 20037508.342789244 * 180.0
    lat = math.degrees(2.0 * math.atan(math.exp(y / 20037508.342789244 * math.pi)) - math.pi / 2.0)
    return lon, lat


def fetch_page(offset: int) -> list[dict]:
    params = {
        "where": "1=1",
        "outFields": OUT_FIELDS,
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "orderByFields": "OBJECTID ASC",
        "f": "json",
    }
    r = requests.get(QUERY_URL, params=params, timeout=120)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return data.get("features", [])


def fetch_all() -> list[dict]:
    features: list[dict] = []
    offset = 0
    while True:
        page = fetch_page(offset)
        features.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += len(page)
    return features


def yr_to_date(yr: int | None) -> date | None:
    if yr is None:
        return None
    try:
        return date(int(yr), 1, 1)
    except (ValueError, TypeError):
        return None


def build_table(features: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    cities: list[str] = []
    sources: list[str] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for feat in features:
        attrs = feat.get("attributes", {})

        # Skip stumps
        if attrs.get("IsStump") == 1:
            continue

        obj_id = attrs.get("OBJECTID")
        tree_ids.append(f"bkl-{obj_id}" if obj_id is not None else None)
        cities.append("USBOS")
        sources.append("BROOKLINE")

        sci = attrs.get("ScientificNameTxt")
        species_list.append(normalize_species(sci))

        cn = attrs.get("CommonNameTxt")
        tree_names.append(cn.strip() if cn and cn.strip() else None)

        plant_dates.append(yr_to_date(attrs.get("YR_PLANT")))

        geom = feat.get("geometry")
        if geom and geom.get("x") is not None and geom.get("y") is not None:
            lon, lat = merc_to_wgs84(geom["x"], geom["y"])
            longitudes.append(lon)
            latitudes.append(lat)
        else:
            longitudes.append(None)
            latitudes.append(None)

        raw_dbh = attrs.get("DBH")
        dbhs.append(float(raw_dbh) if raw_dbh is not None else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "usbos_source": pa.array(sources, type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "plant_date": pa.array(plant_dates, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    features = fetch_all()
    table = build_table(features)
    table = validate_coordinates(table, city="Brookline", city_code="USBOS")
    emit(table)
