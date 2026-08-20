#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Extract Tempe's OpenStreetMap trees into the committed staging parquet.

NOT a refresh-time ingest: this is the (for now, manually run) extraction pass
that the refresh pipeline reads from.  It queries Overpass for `natural=tree`
nodes in the USTEM bounding box, normalises them to the canonical tree schema,
and writes `ustem_osm_staging.parquet` next to itself.  Commit the parquet;
`tempe_osm_probe.py` reports its mtime as the freshness watermark, so a re-run
plus commit is what makes the city's Parquet stale.

Overpass is flaky (429/504 under load) and its DB timestamp advances every
minute, which is why extraction is decoupled from `trilogy refresh` instead of
fetching at refresh time — see EXTENDING.md.  The eventual home for this run is
a weekly `[[cloud.job]]`; the script is deliberately self-contained so that
move is a scheduling change, not a rewrite.
"""

import os
import re
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    CITY_BOUNDS,
    OVERPASS_HEADERS,
    circumference_cm_to_dbh_inches,
    enforce_tree_schema,
    normalize_species,
    normalize_species_parts,
    parse_plant_date_year,
    post_json_with_retry,
    validate_coordinates,
)

CITY_CODE = "USTEM"
CITY_NAME = "Tempe OSM"
# Default is the main instance; point OVERPASS_URL at a mirror (e.g.
# https://overpass.kumi.systems/api/interpreter) when it is overloaded.
OVERPASS_URL = os.environ.get(
    "OVERPASS_URL", "https://overpass-api.de/api/interpreter"
)
OUTPUT = Path(__file__).parent / "ustem_osm_staging.parquet"

_CIRCUMFERENCE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(cm|m)?\s*$", re.I)


def circumference_tag_to_cm(value: str | None) -> float | None:
    """OSM `circumference` in cm, tolerating the tag's unit mess.

    The documented default unit is metres, but bare numbers are frequently
    entered in centimetres anyway.  A unit suffix wins; a bare value above 10
    is treated as cm (no living tree has a 10 m trunk circumference — the
    record holders sit around 36 m and are not street trees).
    """
    if not value:
        return None
    m = _CIRCUMFERENCE_RE.match(value)
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").lower()
    if number <= 0:
        return None
    if unit == "cm":
        return number
    if unit == "m":
        return number * 100
    return number if number > 10 else number * 100


def start_date_to_year(value: str | None):
    """OSM `start_date` is free-ish text; the leading 4-digit year is the
    only part reliable enough to keep."""
    if not value or len(value) < 4 or not value[:4].isdigit():
        return None
    return parse_plant_date_year(value[:4])


def fetch_osm_trees() -> list[dict]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[CITY_CODE]
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = f'[out:json][timeout:300];node["natural"="tree"]({bbox});out;'
    payload = post_json_with_retry(
        OVERPASS_URL, data={"data": query}, headers=OVERPASS_HEADERS
    )
    elements = payload.get("elements")
    if not elements:
        raise RuntimeError(
            f"Overpass returned no tree nodes for {CITY_CODE} bbox {bbox} — "
            "either the query drifted or the bbox is wrong"
        )
    return elements


def main() -> None:
    elements = fetch_osm_trees()
    rows = {
        "tree_id": [],
        "city": [],
        "species": [],
        "plant_date": [],
        "latitude": [],
        "longitude": [],
        "diameter_at_breast_height": [],
        "osm_ref": [],
    }
    for el in elements:
        tags = el.get("tags", {})
        species = normalize_species(tags.get("species")) or normalize_species_parts(
            tags.get("genus"), None
        )
        rows["tree_id"].append(f"osm-{el['id']}")
        rows["city"].append(CITY_CODE)
        rows["species"].append(species)
        rows["plant_date"].append(start_date_to_year(tags.get("start_date")))
        rows["latitude"].append(el.get("lat"))
        rows["longitude"].append(el.get("lon"))
        rows["diameter_at_breast_height"].append(
            circumference_cm_to_dbh_inches(
                circumference_tag_to_cm(tags.get("circumference"))
            )
        )
        # The municipal inventory id this node was imported from, where tagged.
        # Unused for Tempe (nothing carries it) but kept in the staging schema
        # so ref-rich cities (Berlin: ~6k, Paris: ~4k) can dedup on it exactly.
        rows["osm_ref"].append(tags.get("ref"))

    # Explicit types for the columns inference gets wrong: an all-null osm_ref
    # would land as pa.null(), and plant_date needs date32 (see EXTENDING.md on
    # type drift).  The canonical columns are then enforced below anyway.
    table = pa.table(
        {
            **{
                k: pa.array(v)
                for k, v in rows.items()
                if k not in ("plant_date", "osm_ref")
            },
            "plant_date": pa.array(rows["plant_date"], type=pa.date32()),
            "osm_ref": pa.array(rows["osm_ref"], type=pa.string()),
        }
    )
    table = validate_coordinates(table, city=CITY_NAME, city_code=CITY_CODE)
    table = enforce_tree_schema(table, city=CITY_NAME, data_source="OSM_USTEM")
    pq.write_table(table, OUTPUT)
    with_species = table.num_rows - table.column("species").null_count
    print(
        f"{CITY_NAME}: wrote {table.num_rows} rows "
        f"({with_species} with species) to {OUTPUT.name}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
