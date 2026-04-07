#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
import requests
import pyarrow as pa
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, normalize_species, validate_coordinates

BASE_URL = "https://maps.burlingtonvt.gov/arcgis/rest/services/Tree_Sites_Public_View/FeatureServer/0/query"
PAGE_SIZE = 2_000
OUT_FIELDS = "OBJECTID,botanic,common,planted,diameter"

# Active tree sites only (T = Tree; excludes R = Removed, S = Stump, etc.)
WHERE = "site_typ = 'T'"


def parse_plant_date(year: int | None) -> date | None:
    """Convert a planted year integer to January 1 of that year, or None if unknown."""
    if not year or year <= 0:
        return None
    try:
        return date(year, 1, 1)
    except ValueError:
        return None


def fetch_page(offset: int) -> tuple[list[dict], list[dict]]:
    """Returns (attributes_list, geometry_list) for one page."""
    params = {
        "where": WHERE,
        "outFields": OUT_FIELDS,
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        # Reproject from Vermont State Plane (WKID 103173) to WGS84 on the server
        "outSR": "4326",
        "f": "json",
    }
    r = requests.get(BASE_URL, params=params, timeout=120)
    r.raise_for_status()
    data = r.json()
    features = data.get("features", [])
    attrs = [f["attributes"] for f in features]
    geoms = [f.get("geometry") for f in features]
    return attrs, geoms


def fetch_all() -> tuple[list[dict], list[dict]]:
    # Get total count first
    r = requests.get(
        BASE_URL,
        params={"where": WHERE, "returnCountOnly": "true", "f": "json"},
        timeout=30,
    )
    r.raise_for_status()
    total = r.json().get("count", 0)

    all_attrs: list[dict] = []
    all_geoms: list[dict] = []
    for offset in range(0, total, PAGE_SIZE):
        attrs, geoms = fetch_page(offset)
        all_attrs.extend(attrs)
        all_geoms.extend(geoms)
    return all_attrs, all_geoms


def build_table(attrs: list[dict], geoms: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    cities: list[str] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for rec, geom in zip(attrs, geoms):
        obj_id = rec.get("OBJECTID")
        tree_ids.append(f"btv-{obj_id}" if obj_id is not None else None)
        cities.append("USBTV")

        bot = rec.get("botanic")
        species_list.append(normalize_species(bot))

        cn = rec.get("common")
        tree_names.append(cn.strip() if cn and cn.strip() else None)

        plant_dates.append(parse_plant_date(rec.get("planted")))

        if geom and geom.get("x") is not None:
            longitudes.append(float(geom["x"]))
            latitudes.append(float(geom["y"]))
        else:
            longitudes.append(None)
            latitudes.append(None)

        raw_dbh = rec.get("diameter")
        dbhs.append(float(raw_dbh) if raw_dbh is not None else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "plant_date": pa.array(plant_dates, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    attrs, geoms = fetch_all()
    table = build_table(attrs, geoms)
    table = validate_coordinates(table, city="Burlington", city_code="USBTV")
    emit(table)
