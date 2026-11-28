#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Scaffold a new city: its manifest and every boilerplate file.

    cd data/raw && uv run tools/new_city.py \\
        --code USDEN --name Denver --slug denver \\
        --center 39.7392,-104.9903 \\
        --bounds 39.45,39.95,-105.65,-104.55 \\
        --source-label DENVER_OPENDATA \\
        --ecoregion 402 --usda-zone 6

A city's entries in the shared registries -- the city enum, bounds and
territory, data sources and labels, dedup cell, ecoregion and dashboard
context, freshness properties, rollup file lists, cloud jobs, city config and
attribution rows -- are generated from its manifest, `raw/{code}/city.toml`,
by `tools/sync_cities.py` (tools/city_manifest.py describes the keys).  So a
city addition adds files and edits no shared one by hand, and two branches
that each add a city meet only in generated files, where rerunning the sync
(`sync_cities.py --resolve`) is the merge.

**What this writes** is the mechanical half: the manifest, the OSM staging
model, and a *stub* tree model, ingest script, freshness probe and landmark
model in `data/raw/{code}/`; then it runs the sync.

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

Idempotent: a file that already exists is skipped, so re-running after filling
in a stub is safe.  To change a city's registry facts later, edit its manifest
and rerun `tools/sync_cities.py`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

RAW = Path(__file__).resolve().parents[1]
DATA = RAW.parent
REPO = DATA.parent
SRC = REPO / "src" / "src"
sys.path.insert(0, str(RAW))

from shared.ingest import territory_where  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_manifest import dumps  # noqa: E402

RESOLVE_QUERY_URL = (
    "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
    "Resolve_Ecoregions/FeatureServer/0/query"
)
# RESOLVE's REALM values as the frontend's dashboard context spells them.
REALM_KEYS = {
    "Nearctic": "nearctic",
    "Palearctic": "palearctic",
    "Neotropic": "neotropical",
    "Australasia": "australasia",
    "Indomalayan": "indo_malay",
    "Afrotropic": "afrotropic",
    "Oceania": "oceania",
}


def resolve_ecoregion(eco_id: int) -> dict:
    """ECO_NAME, BIOME_NAME and REALM for one RESOLVE ecoregion."""
    import requests

    response = requests.get(
        RESOLVE_QUERY_URL,
        params={
            "where": f"ECO_ID={eco_id}",
            "outFields": "ECO_NAME,BIOME_NAME,REALM",
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=60,
    )
    response.raise_for_status()
    features = response.json().get("features") or []
    if not features:
        raise SystemExit(f"RESOLVE has no ecoregion {eco_id}")
    return features[0]["attributes"]


class Edits:
    """A batch of new files, written only once all of them are rendered.

    A scaffolder that dies half way through is worse than one that does
    nothing: the tree is left in a state no test describes and no `git
    checkout` obviously fixes.  Everything is staged in memory first.
    """

    def __init__(self) -> None:
        self._pending: dict[Path, str] = {}
        self.applied: list[str] = []
        self.skipped: list[str] = []

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

# Every partition extends ../tree_dedup.preql's `tree_partition`, which maps
# the shared raw_* columns; a source that names a column differently re-binds
# it here (`dbh: ?raw_dbh`).
root partial datasource {slug}_raw_tree_info extends tree_partition (
    data_source: {lc}_source,
)
complete where city = '{code}' and {lc}_source = '{source}'
file `./{slug}_tree_info.py`;


# Approved community submissions for this city. The shared ingest script emits
# every city's rows; the `complete where` clause narrows it to {code} and makes
# it a disjoint partition, and the `where` clause is what actually filters --
# `complete where` asserts, it does not promise a predicate.
root partial datasource {lc}_community_tree_info extends tree_partition (
    data_source: {lc}_source,
)
complete where city = '{code}' and {lc}_source = 'COMMUNITY_{code}'
file `../community_tree_info.py`
where city = '{code}';


# OpenStreetMap trees, read from the staged parquet in GCS rather than Overpass
# -- extraction is decoupled from refresh. OSM overlaps the municipal inventory
# by construction; the overlap is resolved by ../tree_dedup.preql. The `where`
# is the city's CITY_TERRITORY (tools/sync_osm_territory.py keeps it so): a
# staged extract keeps the territory it was cut with until its next firing.
root partial datasource {lc}_osm_tree_info extends tree_partition (
    data_source: {lc}_source,
)
complete where city = '{code}' and {lc}_source = 'OSM_{code}'
file `https://storage.googleapis.com/trilogy_public_models/duckdb/staging/{lc}_osm_staging.parquet`
where {osm_territory};


# --- Cross-source dedup and attribute merge --------------------------------
#
# Shared: ../tree_dedup.preql groups the three partitions into one cluster per
# tree and picks each canonical attribute across the cluster; this city's grid
# cell size and its calibration live in DEDUP_CELL_METRES in shared/ingest.py.
# The per-city lines are the dbh merge, because Boston imputes it, and the
# position policy: a city with an Overture lookup imports ../tree_position.preql
# instead of the two position merges (see that file).
merge merged_dbh into diameter_at_breast_height;
merge merged_latitude into latitude;
merge merged_longitude into longitude;


# The published target extends ../tree_dedup.preql's `published_tree_info`:
# the canonical columns, the prune, and the guard that refuses a build which
# lost a quarter of the city.  The two `@source_kept` lines are the same guard
# per bulk source; add one for every municipal or OSM label in the enum
# (`test_every_city_refuses_a_collapsed_build`).
partial datasource {slug}_tree_info extends published_tree_info (
    data_source: {lc}_source,
    {lc}_published_data_updated_through,
)
complete where city = '{code}'
file f`https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{lc}_tree_info_v{{data_version}}.parquet`:f`gcs://trilogy_public_models/duckdb/trees/{lc}_tree_info_v{{data_version}}.parquet`
freshness by {lc}_published_data_updated_through
assert @source_kept(tree_id, {lc}_source, '{source}', 0.75)
assert @source_kept(tree_id, {lc}_source, 'OSM_{code}', 0.75);
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

For an ArcGIS FeatureServer source, `shared.platforms.arcgis` already has the paging,
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
from shared.ingest import (
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

    ArcGIS     `shared.platforms.arcgis.layer_last_edit(LAYER)`, or `field_max` for a
               layer with no editingInfo
    OpenDataSoft  GET /api/explore/v2.1/catalog/datasets/{{id}} -> .metas.default.modified
    CKAN       GET /api/3/action/resource_show?id={{id}} -> .result.last_modified
    Socrata    `shared.platforms.socrata.rows_updated_at(DATASET)` -- reads
               /api/views/{{id}}.json .rowsUpdatedAt (unix SECONDS, not
               Esri's milliseconds)

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
from shared.ingest import emit_freshness, get_json_with_retry

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

Everything lives in `shared.osm.extract_city`; this file exists so each city
has a discoverable entry point and so a city that needs to diverge (a tighter
bbox, an extra tag) has somewhere to do it.

    cd data/raw && uv run {lc}/{slug}_osm_extract.py

The scheduled `osm-{lc}` [[cloud.job]] is the normal path; this is the manual
counterpart, for bootstrapping the city before its job is deployed.  Both share
`fetch_osm_trees` / `build_table`, so they cannot drift on content -- only on
who writes the GCS object.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.osm import extract_city  # noqa: E402

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
from shared.ingest import emit_freshness, staging_modified_at

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

root datasource {lc}_osm_rows extends osm_rows (
)
file `./osm_rows.py`
where city = '{code}';

datasource {lc}_osm_staging extends osm_staging (
)
file `https://storage.googleapis.com/trilogy_public_models/duckdb/staging/{lc}_osm_staging.parquet`:`gcs://trilogy_public_models/duckdb/staging/{lc}_osm_staging.parquet`;
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
#
# Both datasources extend ../landmark_common.preql's templates: add the
# register's own attributes to both, mapped in one and stored in the other.
root partial datasource raw_{slug}_landmark_info extends landmark_partition (
)
complete where city = '{code}'
file `./{slug}_landmarks.py`;

partial datasource {slug}_landmark_info extends published_landmark_info (
    {lc}_landmark_data_updated_through,
)
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
    fields = dict(code=code, lc=lc, slug=slug, name=name, source=source, lat=lat,
                  # A new city's territory is its envelope (the CITY_TERRITORY edit below).
                  osm_territory=territory_where((tuple(args.bounds),)))

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

    # --- the manifest --------------------------------------------------------
    # Everything the shared registries hold for a city; tools/sync_cities.py
    # writes them from it.  See tools/city_manifest.py for the keys.
    eco = resolve_ecoregion(args.ecoregion)
    realm = REALM_KEYS.get(eco["REALM"])
    if realm is None:
        raise SystemExit(f"no realm key for RESOLVE realm {eco['REALM']!r}; add it to REALM_KEYS")
    toml_text = (DATA / "trilogy.toml").read_text(encoding="utf-8")
    manifest = {
        "code": code,
        "name": name,
        "slug": slug,
        "center": [lon, lat],
        "ecoregion": args.ecoregion,
        "ecoregion_name": eco["ECO_NAME"],
        "usda_zone": args.usda_zone,
        "biome": eco["BIOME_NAME"],
        "realm": realm,
        "bounds": list(args.bounds),
        # The territory starts as the envelope.  A city with a neighbour has
        # to carve both -- test_city_territory.py fails on the overlap.
        "territory": [list(args.bounds)],
        "municipal_sources": [source],
        # A placeholder until measured: `uv run tools/osm_dedup_validation.py
        # --city {code}` after the first build, then move it to 20 m only if
        # the 5-10 m band is clearly duplicate-dominated.
        "dedup_note": "NOT YET CALIBRATED: default; measure after the first build.\n",
        "dedup_cell_metres": 10,
        "data_freshness": "probe",
        "labels": {source: f"{name} Open Data"},
        "tree_sources": [{"label": f"TODO: {name} tree inventory source", "url": "TODO"}],
        "landmark_sources": [{"label": f"TODO: {name} landmark source"}],
        "jobs": {
            "city": {
                "schedule": args.city_cron,
                "timeout_seconds": 1800,
                "memory_mb": 2048,
                "schedule_note": (
                    "Starts in the twice-weekly tier, placed the day after this city's OSM\n"
                    f"extract. Measure before moving it: `portal_cadence.py --record --city {code}`.\n"
                ),
            },
            "osm": {
                "schedule": args.osm_cron,
                "timeout_seconds": 1800,
                "memory_mb": 1024,
                "schedule_note": (
                    "Overpass allows two slots per client IP and answers an over-budget request\n"
                    "with HTTP 200 carrying an error remark, so a collision looks like a city with\n"
                    "no trees in OSM. This minute must be used by no other extract job.\n"
                ),
            },
            "landmarks": {
                "schedule": next_landmark_slot(toml_text),
                "timeout_seconds": 900,
                "memory_mb": 2048,
                "schedule_note": (
                    "The next free two-minute Sunday slot; every one must land before the 05:00\n"
                    "union (tests/test_cloud_jobs.py).\n"
                ),
            },
        },
    }
    e.write_file(city / "city.toml", dumps(manifest), what="city manifest")
    return e


def parse_pair(value: str) -> tuple[float, float]:
    lat, lon = (float(p) for p in value.split(","))
    return lat, lon


def parse_bounds(value: str) -> tuple[float, float, float, float]:
    parts = [float(p) for p in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bounds is lat_min,lat_max,lon_min,lon_max")
    return tuple(parts)  # type: ignore[return-value]


def next_landmark_slot(toml_text: str) -> str:
    """The Sunday two-minute slot after the latest per-city landmark job."""
    minutes = [
        int(hour) * 60 + int(minute)
        for minute, hour in re.findall(
            r'key = "refresh-landmarks-[a-z]{5}"[^\[]*?schedule = "0 (\d+) (\d+) \* \* SUN"',
            toml_text,
        )
    ]
    after = max(minutes, default=178) + 2
    if after < 5 * 60:
        return f"0 {after % 60} {after // 60} * * SUN"
    # 03:00-05:00 is full: take the first free two-minute slot from 00:00 whose
    # cron no other job uses (a shared cron string is one schedule row, so it
    # would fire as one tick with that job).  02:00-03:00 filled in Nov 2026,
    # so the window opened at 01:00, and 01:00-03:00 filled too, so it now opens
    # at 00:00; landmark refreshes read only their own portals, so nothing
    # before them has to land first.
    # Every even minute from 00:00 filled in Nov 2026 (batch 16), so the odd
    # minutes come after them: a landmark refresh takes well under a minute.
    taken = set(minutes)
    used = set(re.findall(r'schedule = "([^"]+)"', toml_text))
    for slot in [*range(0, 5 * 60, 2), *range(1, 5 * 60, 2)]:
        cron = f"0 {slot % 60} {slot // 60} * * SUN"
        if slot not in taken and cron not in used:
            return cron
    raise SystemExit(
        "no landmark slot left between 00:00 and the 05:00 union; widen the "
        "window in tools/new_city.py"
    )


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
    ap.add_argument("--usda-zone", required=True, type=int, help="USDA hardiness zone, whole number (5 for 5a/5b)")
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
    # Every shared registry, then the generators that read them (the dedup
    # grid, the derived jobs, the app's tree model): tools/sync_cities.py.
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("sync_cities.py"))],
        check=True,
    )
    print(f"""
{args.code} scaffolded.  What is left is the part that needs judgement:

  1. Fill in {args.slug}_tree_info.py and {args.slug}_update_time.py
     (`shared.platforms.arcgis` covers ArcGIS portals end to end, `shared.platforms.socrata`
      Socrata ones).
  2. Find a landmark source and write {args.slug}_landmarks.py + its probe.
  3. Bootstrap the OSM staging object:
       cd data/raw && uv run {args.code.lower()}/{args.slug}_osm_extract.py
  4. CALIBRATE the dedup cell size -- do not ship the placeholder:
       uv run tools/osm_dedup_validation.py --city {args.code}
     then set dedup_cell_metres (and dedup_note) in raw/{args.code.lower()}/city.toml.
  5. Fill in the TODO attribution rows (tree_sources, landmark_sources) and
     the source label in raw/{args.code.lower()}/city.toml, and the README
     source tables.
  6. Rerun `uv run tools/sync_cities.py` after any manifest edit.

Then check the wiring:

  cd data/raw && uv run --no-project --with pytest --with pyarrow --with "pytrilogy>=0.3.541" --with "duckdb<2" --with requests python -m pytest tests -q
  cd data && trilogy refresh --dry-run raw/{args.code.lower()}/{args.slug}_tree_info.preql
  cd data && trilogy refresh --dry-run osm_staging/{args.code.lower()}_osm_staging.preql

Each dry run must report exactly ONE asset; more means an import reaches too far.
""")


if __name__ == "__main__":
    main()
