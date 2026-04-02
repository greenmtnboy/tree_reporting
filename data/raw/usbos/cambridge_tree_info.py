#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
import requests
import pyarrow as pa
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, normalize_species

DATASET_ID = "82zb-7qc9"
# Only current (non-removed) trees; request all fields we need
BASE_URL = f"https://data.cambridgema.gov/resource/{DATASET_ID}.json"
PAGE_SIZE = 50_000
SELECT = "treeid,scientific,commonname,plantdate,the_geom,diameter"
WHERE = "removaldat IS NULL"


def parse_plant_date(raw: str | None) -> date | None:
    """Parse ISO 8601 date string from Socrata calendar_date field."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def fetch_page(offset: int) -> list[dict]:
    params = {
        "$select": SELECT,
        "$where": WHERE,
        "$limit": PAGE_SIZE,
        "$offset": offset,
        "$order": "treeid ASC",
    }
    r = requests.get(BASE_URL, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_all() -> list[dict]:
    records: list[dict] = []
    offset = 0
    while True:
        page = fetch_page(offset)
        records.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return records


def build_table(records: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    cities: list[str] = []
    sources: list[str] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for rec in records:
        raw_id = rec.get("treeid")
        tree_ids.append(f"cam-{raw_id}" if raw_id else None)
        cities.append("USBOS")
        sources.append("CAMBRIDGE")

        sci = rec.get("scientific")
        species_list.append(normalize_species(sci))

        cn = rec.get("commonname")
        tree_names.append(cn.strip() if cn and cn.strip() else None)

        plant_dates.append(parse_plant_date(rec.get("plantdate")))

        geom = rec.get("the_geom")
        if geom and geom.get("type") == "Point":
            coords = geom.get("coordinates", [None, None])
            longitudes.append(float(coords[0]) if coords[0] is not None else None)
            latitudes.append(float(coords[1]) if coords[1] is not None else None)
        else:
            latitudes.append(None)
            longitudes.append(None)

        raw_dbh = rec.get("diameter")
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
    records = fetch_all()
    emit(build_table(records))
