"""
Shared OpenStreetMap tree extraction, used by every city's `{city}_osm_extract.py`.

NOT a uv inline script — a regular importable module, like `_ingest_shared`.

**Not a refresh-time ingest.** This is the extraction pass the refresh pipeline
reads *from*.  It queries Overpass for `natural=tree` nodes in a city's
bounding box, normalises them to the canonical tree schema, and publishes
`{code}_osm_staging.parquet` to GCS; `{city}_osm_probe.py` reports that
object's publication time as the freshness watermark, so re-running an extract
is what makes the city's Parquet stale.

Overpass is flaky (429/504 under load, and it answers an over-budget request
with HTTP 200 carrying an error remark rather than a 4xx) and its database
timestamp advances every minute, which is why extraction is decoupled from
`trilogy refresh` instead of fetching inline — see EXTENDING.md.  Each city's
extract stays a thin shim over this module so the eventual move to a weekly
`[[cloud.job]]` is a scheduling change rather than fourteen rewrites.

The per-city scripts used to be 160-line copies of each other differing in five
lines, which is how Boston's shipped with a docstring claiming it extracted
Tempe's trees.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import (  # noqa: E402
    CITY_BOUNDS,
    OVERPASS_HEADERS,
    OSM_DATA_SOURCES,
    circumference_cm_to_dbh_inches,
    enforce_tree_schema,
    normalize_species,
    normalize_species_parts,
    SPECIES_SENTINELS,
    parse_plant_date_year,
    post_json_with_retry,
    upload_staging,
    validate_coordinates,
)

# Default is the main instance; point OVERPASS_URL at a mirror (e.g.
# https://overpass.kumi.systems/api/interpreter) when it is overloaded.
OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")

_CIRCUMFERENCE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(cm|m)?\s*$", re.I)


def staging_name(city_code: str) -> str:
    """Staging object name for a city, e.g. `ussfo_osm_staging.parquet`."""
    return f"{city_code.lower()}_osm_staging.parquet"


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
    """OSM `start_date` is free-ish text; the leading 4-digit year is the only
    part reliable enough to keep."""
    if not value or len(value) < 4 or not value[:4].isdigit():
        return None
    return parse_plant_date_year(value[:4])


def fetch_osm_trees(city_code: str, timeout_s: int = 300) -> list[dict]:
    """Every `natural=tree` node in the city's bounding box."""
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[city_code]
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = f'[out:json][timeout:{timeout_s}];node["natural"="tree"]({bbox});out;'
    payload = post_json_with_retry(
        OVERPASS_URL, data={"data": query}, headers=OVERPASS_HEADERS
    )
    elements = payload.get("elements")
    if not elements:
        raise RuntimeError(
            f"Overpass returned no tree nodes for {city_code} bbox {bbox} — "
            "either the query drifted or the bbox is wrong"
        )
    return elements


def build_table(elements: list[dict], city_code: str, city_name: str) -> pa.Table:
    """Normalise Overpass nodes to the canonical tree schema."""
    rows: dict[str, list] = {
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
        rows["city"].append(city_code)
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
        # Empty for most cities, but ref-rich ones (Berlin ~6k, Paris ~4k) can
        # dedup on it exactly rather than geometrically.
        rows["osm_ref"].append(tags.get("ref"))

    # Explicit types for the columns inference gets wrong: an all-null osm_ref
    # would land as pa.null(), and plant_date needs date32 (see EXTENDING.md on
    # type drift).  The canonical columns are enforced below anyway.
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
    table = validate_coordinates(table, city=city_name, city_code=city_code)
    return enforce_tree_schema(
        table, city=city_name, data_source=OSM_DATA_SOURCES[city_code]
    )


def extract_city(city_code: str, city_name: str | None = None) -> pa.Table:
    """Fetch, normalise and publish one city's OSM trees.

    The whole body of a `{city}_osm_extract.py`.  Publishing to GCS is the last
    step and the meaningful one: it is what marks the city's Parquet stale.
    """
    name = city_name or f"{city_code} OSM"
    if city_code not in OSM_DATA_SOURCES:
        raise ValueError(
            f"{city_code} is not in OSM_DATA_SOURCES; add it there and to that "
            f"city's `{city_code.lower()}_source` enum before extracting"
        )
    table = build_table(fetch_osm_trees(city_code), city_code, name)
    name_on_gcs = staging_name(city_code)
    # Written locally first, then published. The GCS object is the artifact;
    # nothing is committed, because git does not preserve mtime and a committed
    # staging file made every fresh clone look like newly extracted data.
    out = Path(tempfile.gettempdir()) / name_on_gcs
    pq.write_table(table, out)
    upload_staging(out, name_on_gcs)
    # Not `null_count`: since the sentinel landed, `species` is never null, so
    # a null count would report every row as identified.  OSM tree nodes are
    # ~99% species-less, and that ratio is the thing worth seeing in the log.
    species = table.column("species").to_pylist()
    with_species = sum(1 for v in species if v not in SPECIES_SENTINELS)
    with_ref = table.num_rows - table.column("osm_ref").null_count
    print(
        f"{name}: published {table.num_rows} rows "
        f"({with_species} with species, {with_ref} with osm_ref) to {name_on_gcs}",
        file=sys.stderr,
    )
    return table
