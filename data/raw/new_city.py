#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Scaffold a new city: every registry edit and every boilerplate file.

    cd data/raw && uv run new_city.py \\
        --code USDEN --name Denver --slug denver \\
        --center 39.7392,-104.9903 \\
        --bounds 39.45,39.95,-105.65,-104.55 \\
        --source-label DENVER_OPENDATA \\
        --ecoregion 402

Adding a city touches roughly twenty files across Python, preql, TOML and
TypeScript, and the city code appears in them about forty times.  Almost none
of those edits fail loudly when they are missed -- that is the whole subject of
EXTENDING.md -- so the mechanical half is worth automating and the judgement
half is not.

**What this writes** is the mechanical half: the registry entries, the
freshness properties, the cross-city imports and merges, the OSM staging model,
the cloud jobs, and the frontend config.  It also drops a *stub* tree model and
a *stub* ingest script into `data/raw/{code}/`.

**What it deliberately leaves to you** is everything that needs a measurement
or a look at the portal:

  * the body of `{slug}_tree_info.py` -- the field mapping is the actual work
  * the dedup cell size, which is calibrated per city and must never be copied
    (`osm_dedup_validation.py --city {CODE}`)
  * the landmark source, which differs in kind from city to city
  * the refresh cadence, which `portal_cadence.py` measures over weeks

Run `pytest tests/` afterwards: `test_city_wiring.py` walks the same list and
names anything still missing, so a half-scaffolded city is a red test rather
than a quiet hole in the map.

Idempotent: an edit already present is skipped, so re-running after filling in
a stub is safe.  Nothing is written until every edit has been located, so a
failure leaves the tree untouched rather than half-patched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAW = Path(__file__).resolve().parent
DATA = RAW.parent
REPO = DATA.parent
SRC = REPO / "src" / "src"


class Edits:
    """A batch of file edits, applied only once all of them are resolved.

    A scaffolder that dies half way through is worse than one that does
    nothing: the tree is left in a state no test describes and no `git
    checkout` obviously fixes.  Everything is staged in memory first.
    """

    def __init__(self) -> None:
        self._pending: dict[Path, str] = {}
        self.applied: list[str] = []
        self.skipped: list[str] = []

    def _text(self, path: Path) -> str:
        if path not in self._pending:
            self._pending[path] = path.read_text(encoding="utf-8")
        return self._pending[path]

    def insert_after(self, path: Path, anchor: str, addition: str, *, what: str) -> None:
        """Insert *addition* directly after *anchor*, unless it is already there."""
        text = self._text(path)
        if addition.strip() and addition.strip() in text:
            self.skipped.append(f"{path.relative_to(REPO)}: {what} (already present)")
            return
        if text.count(anchor) != 1:
            raise SystemExit(
                f"{path.relative_to(REPO)}: could not place {what} -- the anchor\n"
                f"  {anchor.strip()[:100]}\n"
                f"matched {text.count(anchor)} times, expected 1. The file has "
                f"changed shape; add this by hand:\n{addition}"
            )
        self._pending[path] = text.replace(anchor, anchor + addition)
        self.applied.append(f"{path.relative_to(REPO)}: {what}")

    def sub_once(self, path: Path, pattern: str, repl: str, *, what: str, marker: str) -> None:
        """Regex-substitute one occurrence, unless *marker* is already present."""
        text = self._text(path)
        if marker in text:
            self.skipped.append(f"{path.relative_to(REPO)}: {what} (already present)")
            return
        new, n = re.subn(pattern, repl, text, count=1)
        if n != 1:
            raise SystemExit(
                f"{path.relative_to(REPO)}: could not apply {what} -- pattern "
                f"{pattern!r} matched {n} times, expected 1"
            )
        self._pending[path] = new
        self.applied.append(f"{path.relative_to(REPO)}: {what}")

    def write_file(self, path: Path, content: str, *, what: str) -> None:
        if path.exists():
            self.skipped.append(f"{path.relative_to(REPO)}: {what} (already exists)")
            return
        self._pending[path] = content
        self.applied.append(f"{path.relative_to(REPO)}: {what}")

    def commit(self) -> None:
        for path, text in self._pending.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TREE_MODEL = '''import ..tree_common;
import ..community_tree_info;
import ..tree_dedup;

auto {lc}_published_data_updated_through <- greatest({lc}_data_updated_through, {lc}_community_data_updated_through, {lc}_osm_data_updated_through);

key {lc}_source enum<string>['{source}', 'COMMUNITY_{code}', 'OSM_{code}']; # originating dataset for the row

# Feed the shared cross-source merge (../tree_dedup.preql): it classifies rows
# by the label's prefix and records provenance from it.
auto {lc}_source_label <- concat({lc}_source, '');
merge {lc}_source_label into source_label;

root datasource {slug}_update_time (
    data_updated_through: {lc}_data_updated_through
)
file `./{slug}_update_time.py`;

# Only this city's column, so an approval elsewhere does not mark {code}'s
# Parquet stale. Trilogy probes this as MAX over the one column.
root datasource {lc}_community_update_time (
    {lc}_community_data_updated_through: {lc}_community_data_updated_through
)
file `../community_update_time.py`;

# GCS publication time of the staged OSM parquet; it only moves when an
# extraction is re-run and uploaded. Not a local mtime -- git does not preserve
# it, so a committed staging file makes every fresh clone look like new data.
root datasource {lc}_osm_update_time (
    data_updated_through: {lc}_osm_data_updated_through
)
file `./{slug}_osm_probe.py`;

root partial datasource {slug}_raw_tree_info (
    tree_id: tree_id,
    city: city,
    data_source: {lc}_source,
    species: raw_species,
    tree_name: ?raw_tree_name,
    plant_date: ?raw_plant_date,
    latitude: ?raw_latitude,
    longitude: ?raw_longitude,
    diameter_at_breast_height: ?raw_dbh,
    submission_photo_url: ?raw_photo_url,
)
grain (tree_id)
complete where city = '{code}' and {lc}_source = '{source}'
file `./{slug}_tree_info.py`;


# Approved community submissions for this city. The shared ingest script emits
# every city's rows; the `complete where` clause narrows it to {code} and makes
# it a disjoint partition, and the `where` clause is what actually filters --
# `complete where` asserts, it does not promise a predicate.
root partial datasource {lc}_community_tree_info (
    tree_id: tree_id,
    city: city,
    data_source: {lc}_source,
    species: raw_species,
    tree_name: ?raw_tree_name,
    plant_date: ?raw_plant_date,
    diameter_at_breast_height: ?raw_dbh,
    latitude: ?raw_latitude,
    longitude: ?raw_longitude,
    submission_photo_url: ?raw_photo_url,
)
grain (tree_id)
complete where city = '{code}' and {lc}_source = 'COMMUNITY_{code}'
file `../community_tree_info.py`
where city = '{code}';


# OpenStreetMap trees, read from the staged parquet in GCS rather than Overpass
# -- extraction is decoupled from refresh. OSM overlaps the municipal inventory
# by construction; the overlap is resolved by ../tree_dedup.preql.
root partial datasource {lc}_osm_tree_info (
    tree_id: tree_id,
    city: city,
    data_source: {lc}_source,
    species: raw_species,
    tree_name: ?raw_tree_name,
    plant_date: ?raw_plant_date,
    latitude: ?raw_latitude,
    longitude: ?raw_longitude,
    diameter_at_breast_height: ?raw_dbh,
    submission_photo_url: ?raw_photo_url,
)
grain (tree_id)
complete where city = '{code}' and {lc}_source = 'OSM_{code}'
file `https://storage.googleapis.com/trilogy_public_models/duckdb/staging/{lc}_osm_staging.parquet`;


# --- Cross-source dedup and attribute merge --------------------------------
#
# Shared: ../tree_dedup.preql groups the three partitions into one cluster per
# tree and picks each canonical attribute across the cluster; this city's grid
# cell size and its calibration live in DEDUP_CELL_METRES in _ingest_shared.py.
# The only per-city line is the dbh merge, because Boston imputes it.
merge merged_dbh into diameter_at_breast_height;


partial datasource {slug}_tree_info (
    tree_id,
    city,
    data_source: {lc}_source,
    species,
    ?tree_name,
    ?plant_date,
    ?diameter_at_breast_height,
    ?latitude,
    ?longitude,
    ?submission_photo_url,
    merged_sources,
    ?merged_tree_ids,
    is_duplicate,
    {lc}_published_data_updated_through,
)
grain (tree_id)
complete where city = '{code}'
file f`https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{lc}_tree_info_v{{data_version}}.parquet`:f`gcs://trilogy_public_models/duckdb/trees/{lc}_tree_info_v{{data_version}}.parquet`
freshness by {lc}_published_data_updated_through;
'''

TREE_INGEST = '''#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""{name}'s municipal tree inventory.  STUB -- fill in the field mapping.

Emit one Arrow IPC stream on stdout with the canonical tree schema.  The parts
that are not optional:

  * `species` is the Latin binomial ONLY -- strip any "::" common-name suffix,
    or combine separate genus/epithet fields with `normalize_species_parts`.
  * `plant_date` is a real `date32` even when every value is null; an untyped
    `pa.null()` column lands in the parquet as INT32 and breaks `year()`.
  * `enforce_tree_schema` immediately before `emit`, always.  It is the single
    chokepoint for column types and species hygiene.
  * `validate_coordinates` before it, so a wrong-hemisphere geocode is caught
    here rather than as a dot in the ocean.

For an ArcGIS FeatureServer source, `_arcgis_shared` already has the paging,
the watermark and the Esri epoch conversion -- see usden/denver_tree_info.py.
For a large source, stream it: `stream_to_table` over a chunk generator keeps
peak memory at one chunk (a bulk `response.json()` OOM-killed a 2 GiB
container at 216k features).
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = "TODO: the portal endpoint this city publishes"


def iter_row_chunks():
    """Yield lists of source records, one page at a time."""
    raise NotImplementedError("TODO: page {name}'s portal")


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raise NotImplementedError("TODO: map {name}'s fields")

    return pa.table({{
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["{code}"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "tree_name": pa.array(tree_name, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    }})


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="{name} OpenData")
    table = validate_coordinates(table, city="{name}", city_code="{code}")
    table = enforce_tree_schema(
        table, city="{name}", data_source="{source}"
    )
    emit(table)
'''

UPDATE_TIME = '''#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for {name}'s tree inventory.  STUB -- point it at the portal.

Mandatory: without it the pipeline re-downloads the whole dataset on every
tick.  Hit the lightest metadata endpoint the portal exposes:

    ArcGIS     `_arcgis_shared.layer_last_edit(LAYER)`, or `field_max` for a
               layer with no editingInfo
    OpenDataSoft  GET /api/explore/v2.1/catalog/datasets/{{id}} -> .metas.default.modified
    CKAN       GET /api/3/action/resource_show?id={{id}} -> .result.last_modified
    Socrata    GET /api/views/{{id}}.json -> .rowsUpdatedAt (unix seconds)

Fetch with `get_json_with_retry`, never a bare `requests.get(...).json()`: a
portal in maintenance answers every path with an HTML holding page and HTTP
200, and every probe is a root datasource whose exception ends the whole
refresh for every city.  `emit_freshness` turns an availability failure into
the epoch so this city sits out the run; a *parse* failure still raises, because
that means the portal changed its schema and degrading would freeze this city's
Parquet silently and for ever.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

METADATA_URL = "TODO: the portal's metadata endpoint"


def fetch_modified_at() -> datetime:
    raise NotImplementedError("TODO: read {name}'s published-through timestamp")


if __name__ == "__main__":
    emit_freshness("{code}", fetch_modified_at)
'''

OSM_EXTRACT = '''#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-storage", "pyarrow", "pytrilogy", "requests"]
# ///

"""Extract {name}'s OpenStreetMap trees into the staged parquet in GCS.

Everything lives in `_osm_shared.extract_city`; this file exists so each city
has a discoverable entry point and so a city that needs to diverge (a tighter
bbox, an extra tag) has somewhere to do it.

    cd data/raw && uv run {slug}/{slug}_osm_extract.py

The scheduled `osm-{lc}` [[cloud.job]] is the normal path; this is the manual
counterpart, for bootstrapping the city before its job is deployed.  Both share
`fetch_osm_trees` / `build_table`, so they cannot drift on content -- only on
who writes the GCS object.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _osm_shared import extract_city  # noqa: E402

CITY_CODE = "{code}"
CITY_NAME = "{name} OSM"


if __name__ == "__main__":
    extract_city(CITY_CODE, CITY_NAME)
'''

OSM_PROBE = '''#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for {name}'s OSM staging parquet.

Emits the GCS publication time of {lc}_osm_staging.parquet, which only moves
when an extraction is republished -- that is the moment the city's Parquet
becomes stale.  Never Overpass's own `osm_base` timestamp: it advances every
minute and would rebuild the city on every tick.  A missing object emits the
epoch (sits out the run) rather than raising.
"""

from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, staging_modified_at

STAGING_NAME = "{lc}_osm_staging.parquet"


def modified_at() -> datetime:
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("{code}", modified_at, label="{code} OSM staging")
'''

OSM_STAGING_MODEL = '''# Scheduled OSM extraction for {name}, run by the `osm-{lc}` [[cloud.job]] in
# ../trilogy.toml. Everything shared with the other cities -- the canonical tree
# concepts, the Overpass freshness probe, and why extraction is a separate job
# at all -- lives in staging_common.preql; this file is just {name}'s rows and
# the staging object they land in.

import staging_common;

root datasource {lc}_osm_rows (
    tree_id: tree_id,
    city: city,
    data_source: data_source,
    species: ?species,
    tree_name: ?tree_name,
    plant_date: ?plant_date,
    latitude: ?latitude,
    longitude: ?longitude,
    diameter_at_breast_height: ?diameter_at_breast_height,
    submission_photo_url: ?submission_photo_url,
    osm_ref: ?osm_ref,
)
grain (tree_id)
file `./osm_rows.py`
where city = '{code}';

datasource {lc}_osm_staging (
    tree_id,
    city,
    data_source,
    ?species,
    ?tree_name,
    ?plant_date,
    ?latitude,
    ?longitude,
    ?diameter_at_breast_height,
    ?submission_photo_url,
    ?osm_ref,
    osm_extracted_through,
)
grain (tree_id)
file `https://storage.googleapis.com/trilogy_public_models/duckdb/staging/{lc}_osm_staging.parquet`:`gcs://trilogy_public_models/duckdb/staging/{lc}_osm_staging.parquet`
freshness by osm_extracted_through;
'''

LANDMARK_MODEL = '''import ..landmark_common;

root datasource {slug}_landmarks_update_time (
    data_updated_through: {lc}_landmark_data_updated_through
)
file `./{slug}_landmarks_probe.py`;

# STUB. Every city needs a landmarks model even if it yields zero rows -- a
# missing file fails the landmark lane for every city, and the agent has no
# geographic context for this one.
#
# Point the raw datasource at whichever source this city has, in preference
# order: an official designation registry, the same portal as the trees, an
# Overpass extract (STAGE it -- do not fetch at refresh time), or a curated CSV
# geocoded via Nominatim (then add a `landmarks-{lc}` job with NO cron).
root partial datasource raw_{slug}_landmark_info (
    landmark_id: landmark_id,
    city: city,
    name: name,
    geometry_raw: ?geometry_raw,
)
grain (landmark_id)
complete where city = '{code}'
file `./{slug}_landmarks.py`;

partial datasource {slug}_landmark_info (
    landmark_id,
    city,
    name,
    ?geometry,
    ?latitude,
    ?longitude,
    {lc}_landmark_data_updated_through,
)
grain (landmark_id)
complete where city = '{code}'
file f`https://storage.googleapis.com/trilogy_public_models/duckdb/landmarks/{lc}_landmark_info_v{{data_version}}.parquet`:f`gcs://trilogy_public_models/duckdb/landmarks/{lc}_landmark_info_v{{data_version}}.parquet`
freshness by {lc}_landmark_data_updated_through;
'''


# ---------------------------------------------------------------------------
# The scaffold
# ---------------------------------------------------------------------------

def build(args) -> Edits:
    code, lc, slug, name = args.code, args.code.lower(), args.slug, args.name
    source = args.source_label
    lat, lon = args.center
    fields = dict(code=code, lc=lc, slug=slug, name=name, source=source, lat=lat)

    e = Edits()
    city = RAW / lc

    # --- files ------------------------------------------------------------
    e.write_file(city / f"{slug}_tree_info.preql", TREE_MODEL.format(**fields), what="tree model")
    e.write_file(city / f"{slug}_tree_info.py", TREE_INGEST.format(**fields), what="tree ingest (stub)")
    e.write_file(city / f"{slug}_update_time.py", UPDATE_TIME.format(**fields), what="freshness probe (stub)")
    e.write_file(city / f"{slug}_osm_extract.py", OSM_EXTRACT.format(**fields), what="OSM extract shim")
    e.write_file(city / f"{slug}_osm_probe.py", OSM_PROBE.format(**fields), what="OSM freshness probe")
    e.write_file(city / f"{slug}_landmarks.preql", LANDMARK_MODEL.format(**fields), what="landmark model (stub)")
    e.write_file(
        DATA / "osm_staging" / f"{lc}_osm_staging.preql",
        OSM_STAGING_MODEL.format(**fields), what="OSM staging model",
    )

    # --- _ingest_shared.py ------------------------------------------------
    shared = RAW / "_ingest_shared.py"
    e.sub_once(
        shared,
        r"(MUNICIPAL_DATA_SOURCES: dict\[str, tuple\[str, \.\.\.\]\] = \{\n)",
        rf'\1    "{code}": ("{source}",),\n',
        what="MUNICIPAL_DATA_SOURCES", marker=f'"{code}": ("{source}",)',
    )
    e.sub_once(
        shared,
        r"(OSM_DATA_SOURCES: dict\[str, str\] = \{\n)",
        rf'\1    "{code}": "OSM_{code}",\n',
        what="OSM_DATA_SOURCES", marker=f'"{code}": "OSM_{code}"',
    )
    lo_lat, hi_lat, lo_lon, hi_lon = args.bounds
    e.sub_once(
        shared,
        r"(CITY_BOUNDS: dict\[str, tuple\[float, float, float, float\]\] = \{\n)",
        rf'\1    "{code}": ({lo_lat}, {hi_lat}, {lo_lon}, {hi_lon}),\n',
        what="CITY_BOUNDS", marker=f'"{code}": ({lo_lat}, {hi_lat}',
    )
    # The dedup grid cell starts at the common 10 m and is a placeholder until
    # measured: `uv run osm_dedup_validation.py --city {code}` after the first
    # build, then move it to 20 m only if the 5-10 m band is clearly
    # duplicate-dominated.  tree_dedup.preql reads it through dedup_cells.py.
    e.sub_once(
        shared,
        r"(DEDUP_CELL_METRES: dict\[str, int\] = \{\n)",
        rf'\1    # NOT YET CALIBRATED: default; measure after the first build.\n    "{code}": 10,\n',
        what="DEDUP_CELL_METRES", marker=f'"{code}": 10',
    )

    # --- _osm_shared.py ---------------------------------------------------
    e.sub_once(
        RAW / "_osm_shared.py",
        r"(OSM_CITY_NAMES: dict\[str, str\] = \{\n)",
        rf'\1    "{code}": "{name}",\n',
        what="OSM_CITY_NAMES", marker=f'"{code}": "{name}"',
    )

    # --- core.preql -------------------------------------------------------
    core = RAW / "core.preql"
    e.sub_once(
        core, r"(key city enum<string>\[[^\]]*)\]",
        rf"\1, '{code}']",
        what="city enum", marker=f"'{code}'",
    )
    e.sub_once(
        core, r"(\n)(end; # mapped from RESOLVE)",
        rf"\1    when city = '{code}' then {args.ecoregion}\n\2",
        what="ecoregion_id", marker=f"when city = '{code}' then",
    )

    # --- freshness properties --------------------------------------------
    e.insert_after(
        RAW / "tree_common.preql",
        "property <*>.tree_id.submission_photo_url string;"
        if False else "\n\n",  # placed by anchor below instead
        "", what="(noop)",
    ) if False else None
    e.sub_once(
        RAW / "tree_common.preql",
        r"(\nproperty <\*>\.\w+_osm_data_updated_through datetime;[^\n]*\n)(?!property <\*>\.\w+_osm_data)",
        rf"\1property <*>.{lc}_data_updated_through datetime; # when {name} data is through\n"
        rf"property <*>.{lc}_osm_data_updated_through datetime; # GCS publication time of {name}'s staged OSM parquet\n",
        what="tree_common freshness properties",
        marker=f"property <*>.{lc}_osm_data_updated_through",
    )
    e.sub_once(
        RAW / "community_tree_info.preql",
        r"(\nproperty <\*>\.\w+_community_data_updated_through datetime;[^\n]*\n)(?!property)",
        rf"\1property <*>.{lc}_community_data_updated_through datetime; # when approved community submissions for {code} are through\n",
        what="community freshness property",
        marker=f"property <*>.{lc}_community_data_updated_through",
    )

    # --- landmark_common.preql -------------------------------------------
    landmark_common = RAW / "landmark_common.preql"
    e.sub_once(
        landmark_common,
        r"(\nproperty <\*>\.\w+_landmark_data_updated_through datetime;\n)(auto latest_landmark_update_through)",
        rf"\1property <*>.{lc}_landmark_data_updated_through datetime;\n\2",
        what="landmark freshness property",
        marker=f"property <*>.{lc}_landmark_data_updated_through",
    )
    e.sub_once(
        landmark_common,
        r"(auto latest_landmark_update_through <- greatest\([^)]*)\)",
        rf"\1, {lc}_landmark_data_updated_through)",
        what="latest_landmark_update_through",
        marker=f"{lc}_landmark_data_updated_through)",
    )

    # --- cross-city models ------------------------------------------------
    e.sub_once(
        RAW / "landmark_info.preql",
        r"(\nimport \w+\.\w+_landmarks;\n)(?!import)",
        rf"\1import {lc}.{slug}_landmarks;\n",
        what="landmark_info import", marker=f"import {lc}.{slug}_landmarks;",
    )
    tree_info = RAW / "tree_info.preql"
    e.sub_once(
        tree_info,
        r"(\nimport \w+\.\w+_tree_info;\n)(?!import)",
        rf"\1import {lc}.{slug}_tree_info;\n",
        what="tree_info import", marker=f"import {lc}.{slug}_tree_info;",
    )
    e.sub_once(
        tree_info,
        r"(\nmerge \w+_source into data_source;\n)(?!merge)",
        rf"\1merge {lc}_source into data_source;\n",
        what="data_source merge", marker=f"merge {lc}_source into data_source;",
    )
    e.sub_once(
        tree_info,
        r"(auto latest_update_through <- greatest\([^)]*)\)",
        rf"\1,\n{lc}_published_data_updated_through)",
        what="latest_update_through", marker=f"{lc}_published_data_updated_through)",
    )
    e.sub_once(
        RAW / "full_tree_publish.preql",
        r"(trees/\w+_tree_info_v\{data_version\}\.parquet`)(\n\];)",
        rf"\1,\n    f`https://storage.googleapis.com/trilogy_public_models/duckdb/"
        rf"trees/{lc}_tree_info_v{{data_version}}.parquet`\2",
        what="rollup file list", marker=f"trees/{lc}_tree_info_v",
    )

    # --- trilogy.toml -----------------------------------------------------
    jobs = f'''
[[cloud.job]]
key = "city-{lc}"
name = "urban-tree-city-{lc}"
entrypoint = "raw/{lc}/{slug}_tree_info.preql"
operation = "refresh"
# Starts in the twice-weekly tier, placed the day after this city's OSM
# extract. Measure before moving it: `portal_cadence.py --record --city {code}`.
schedule = "{args.city_cron}"
timeout_seconds = 1800
memory_mb = 2048

[[cloud.job]]
key = "osm-{lc}"
name = "urban-tree-osm-{lc}"
entrypoint = "osm_staging/{lc}_osm_staging.preql"
operation = "refresh"
# Overpass allows two slots per client IP and answers an over-budget request
# with HTTP 200 carrying an error remark, so a collision looks like a city with
# no trees in OSM. This minute must be used by no other extract job.
schedule = "{args.osm_cron}"
timeout_seconds = 1800
memory_mb = 1024
'''
    e.sub_once(
        DATA / "trilogy.toml",
        r"(\n# =+\n# Ad-hoc landmark staging publish)",
        jobs.replace("\\", "\\\\") + r"\1",
        what="cloud jobs", marker=f'key = "city-{lc}"',
    )

    # --- frontend ---------------------------------------------------------
    config_path = SRC / "cityConfig.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if code not in config:
        config[code] = {"name": name, "center": [lon, lat]}
        # Written whole rather than by anchor: it is JSON, and the trailing
        # entry has no comma, so a textual insert has two shapes to get right.
        e._pending[config_path] = (
            json.dumps(config, indent=2, ensure_ascii=False) + "\n"
        )
        e.applied.append(f"{config_path.relative_to(REPO)}: city config")
    else:
        e.skipped.append(f"{config_path.relative_to(REPO)}: city config (already present)")

    e.sub_once(
        SRC / "data" / "dataSources.ts",
        r"(export const DATA_SOURCE_LABELS: Record<string, string> = \{\n)",
        rf"\1  {source}: '{name} Open Data',\n",
        what="data source label", marker=f"{source}:",
    )
    e.sub_once(
        SRC / "data" / "sourceCatalog.ts",
        r"(export const TREE_INVENTORY_SOURCES: CitySourceLink\[\] = \[\n)",
        rf"\1  {{ city: '{name}', label: 'TODO: {name} tree inventory source', url: 'TODO' }},\n",
        what="tree source attribution", marker=f"city: '{name}', label: 'TODO: {name} tree",
    )
    e.sub_once(
        SRC / "data" / "sourceCatalog.ts",
        r"(export const LANDMARK_SOURCES: CitySourceLink\[\] = \[\n)",
        rf"\1  {{ city: '{name}', label: 'TODO: {name} landmark source' }},\n",
        what="landmark source attribution", marker=f"city: '{name}', label: 'TODO: {name} landmark",
    )
    return e


def parse_pair(value: str) -> tuple[float, float]:
    lat, lon = (float(p) for p in value.split(","))
    return lat, lon


def parse_bounds(value: str) -> tuple[float, float, float, float]:
    parts = [float(p) for p in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bounds is lat_min,lat_max,lon_min,lon_max")
    return tuple(parts)  # type: ignore[return-value]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--code", required=True, help="Five-letter city code, e.g. USDEN")
    ap.add_argument("--name", required=True, help="Display name, e.g. Denver")
    ap.add_argument("--slug", required=True, help="File-name stem, e.g. denver")
    ap.add_argument("--center", required=True, type=parse_pair, help="lat,lon of the city centre")
    ap.add_argument("--bounds", required=True, type=parse_bounds, help="lat_min,lat_max,lon_min,lon_max")
    ap.add_argument("--source-label", required=True, help="Municipal data_source value, e.g. DENVER_OPENDATA")
    ap.add_argument("--ecoregion", required=True, type=int, help="RESOLVE ECO_ID at the city centre")
    ap.add_argument("--city-cron", default="0 0 12 * * SUN,WED", help="Quartz cron for the city refresh job")
    ap.add_argument("--osm-cron", default="0 0 4 * * SUN", help="Quartz cron for the OSM extract job")
    ap.add_argument("--dry-run", action="store_true", help="Report the edits without writing")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Z]{5}", args.code):
        raise SystemExit(f"--code must be five uppercase letters, got {args.code!r}")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.slug):
        raise SystemExit(f"--slug must be lowercase, got {args.slug!r}")

    edits = build(args)
    for line in edits.applied:
        print(f"  + {line}")
    for line in edits.skipped:
        print(f"  . {line}")
    if args.dry_run:
        print("\ndry run -- nothing written")
        return
    edits.commit()
    print(f"""
{args.code} scaffolded.  What is left is the part that needs judgement:

  1. Fill in {args.slug}_tree_info.py and {args.slug}_update_time.py
     (`_arcgis_shared` covers ArcGIS portals end to end).
  2. Find a landmark source and write {args.slug}_landmarks.py + its probe.
  3. Bootstrap the OSM staging object:
       cd data/raw && uv run {args.slug}/{args.slug}_osm_extract.py
  4. CALIBRATE the dedup cell size -- do not ship the placeholder:
       uv run osm_dedup_validation.py --city {args.code}
  5. Fill in the two TODO attribution lines in src/src/data/sourceCatalog.ts
     and the README source tables.

Then check the wiring:

  cd data/raw && uv run --with pytest python -m pytest tests -q
  cd data && trilogy refresh --dry-run raw/{args.code.lower()}/{args.slug}_tree_info.preql
  cd data && trilogy refresh --dry-run osm_staging/{args.code.lower()}_osm_staging.preql

Each dry run must report exactly ONE asset; more means an import reaches too far.
""")


if __name__ == "__main__":
    main()
