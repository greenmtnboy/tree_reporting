"""
Shared OpenStreetMap tree extraction, behind both of the ways a city extracts.

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
a city's refresh instead of fetching inline — see EXTENDING.md.

Two callers, one body:

* `stage_city_rows` is the scheduled path and the normal one.  Every city has
  a weekly `osm-{code}` [[cloud.job]] whose `trilogy refresh` materialises
  `osm_staging/{code}_osm_staging.preql`.  All seventeen models point at one
  datasource script (`osm_staging/osm_rows.py`) and name their city in a
  `where` clause, so DuckDB writes the GCS object with the job's own HMAC
  credentials.
* `extract_city` is the manual counterpart, `raw/{code}/{city}_osm_extract.py`,
  which fetches and uploads from a workstation with application-default
  credentials.  It stays because a brand-new city needs its staging object
  before its tree model can build, and its cloud job does not exist until the
  sync runs on merge.

Both share `fetch_osm_trees` / `build_table`, so they cannot drift on content —
the only difference is who writes the object.  Keeping the per-city files thin
is the point: they used to be 160-line copies of each other differing in five
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

# What each city is called in logs, and any column its OSM partition has to
# carry beyond the canonical tree schema.  Both used to be arguments passed by
# a per-city shim script; they live here because the shims are gone -- one
# `osm_staging/osm_rows.py` now serves every city and learns which one from the
# model's pushed-down `where city = '<CODE>'`.
#
# An extra column is not decoration.  A column a city's municipal and community
# sources declare has to exist on its OSM partition too, or that partition
# drops out of the union and the remaining two stop covering the source enum --
# Trilogy reports it as `complete where` clauses "not provably exhaustive over
# that type", which is a long way from "you forgot a column".  London is the
# only instance today.
OSM_CITY_NAMES: dict[str, str] = {
    "USSFO": "San Francisco",
    "USNYC": "New York City",
    "USBOS": "Boston",
    "FRPAR": "Paris",
    "USBTV": "Burlington",
    "CAVAN": "Vancouver",
    "DEBER": "Berlin",
    "NLAMS": "Amsterdam",
    "GBLON": "London",
    "AUMEL": "Melbourne",
    "ARBUE": "Buenos Aires",
    "USLAX": "Los Angeles",
    "USWAS": "Washington DC",
    "USTEM": "Tempe",
    "GRATH": "Athens",
    "USDEN": "Denver",
    "GRMLO": "Milos",
    "GRSAN": "Santorini",
}

OSM_EXTRA_NULL_COLUMNS: dict[str, dict[str, "pa.DataType"]] = {
    "GBLON": {"borough": pa.string()},
}


def osm_city_label(city_code: str) -> str:
    """The name used in log lines, e.g. "London OSM"."""
    return f"{OSM_CITY_NAMES.get(city_code, city_code)} OSM"



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


def build_table(
    elements: list[dict],
    city_code: str,
    city_name: str,
    extra_null_columns: dict | None = None,
) -> pa.Table:
    """Normalise Overpass nodes to the canonical tree schema.

    `extra_null_columns` maps a city-specific column name to its Arrow type and
    adds it as all-null.  OSM carries no such field, but a city whose other
    partitions declare one needs its OSM partition to declare it too: a source
    that cannot supply a requested column drops out of the union, and the
    remaining partitions then fail to cover the source enum -- Trilogy reports
    that as `complete where` clauses "not provably exhaustive", a long way from
    the actual cause.  London's `borough` is the only instance today.
    """
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
    for name, dtype in (extra_null_columns or {}).items():
        table = table.append_column(name, pa.nulls(table.num_rows, type=dtype))
    table = validate_coordinates(table, city=city_name, city_code=city_code)
    return enforce_tree_schema(
        table, city=city_name, data_source=OSM_DATA_SOURCES[city_code]
    )


def extract_city(
    city_code: str,
    city_name: str | None = None,
    extra_null_columns: dict | None = None,
) -> pa.Table:
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
    table = build_table(
        fetch_osm_trees(city_code), city_code, name, extra_null_columns
    )
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


def stage_city_rows(
    city_code: str,
    city_name: str | None = None,
    extra_null_columns: dict | None = None,
) -> None:
    """Fetch and normalise one city's OSM trees, emitted as an Arrow stream.

    The body of `osm_staging/osm_rows.py`, the one datasource script behind
    every city's scheduled extract job: `trilogy refresh` materialises the
    staging parquet from this stream, so DuckDB writes it to GCS with the job's
    HMAC credentials and no google-cloud-storage credential is needed anywhere.

    `extract_city` above is the manual counterpart (fetch + upload from a
    workstation with application-default credentials).  Both paths share
    `fetch_osm_trees` / `build_table`, so they cannot drift on content -- the
    only difference is who writes the GCS object.

    *city_name* and *extra_null_columns* default from `OSM_CITY_NAMES` /
    `OSM_EXTRA_NULL_COLUMNS` and are arguments only so a caller can override
    them; the shared script passes neither.
    """
    from _ingest_shared import emit

    if city_code not in OSM_DATA_SOURCES:
        raise ValueError(
            f"{city_code} is not in OSM_DATA_SOURCES; add it there and to that "
            f"city's `{city_code.lower()}_source` enum before extracting"
        )
    name = city_name or osm_city_label(city_code)
    extras = (
        extra_null_columns
        if extra_null_columns is not None
        else OSM_EXTRA_NULL_COLUMNS.get(city_code)
    )
    emit(build_table(fetch_osm_trees(city_code), city_code, name, extras))
