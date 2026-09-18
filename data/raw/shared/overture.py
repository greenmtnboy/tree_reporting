"""Overture Maps position lookups: which grid cells are under a building or a
road, and where the nearest open ground is.

NOT a uv inline script -- a regular importable module, like `ingest` and
`osm`.  The scheduled path is `overture_staging/overture_snap_rows.py`
(`stage_city_snap`); the manual counterpart is
`raw/{code}/{slug}_overture_extract.py` (`extract_city_snap`).  Both share
`build_snap_table`, so they cannot differ on content, only on who writes the
GCS object.  The design, the measurements and the language limits it works
around are in docs/POSITION_CORRECTION.md; this docstring is the summary.

For one city the table is ONE ROW PER GRID CELL whose centre is inside an
Overture building footprint or a road's carriageway, carrying the centre of
the nearest cell that is neither.  Open ground has no row, so a tree there
looks its cell up, finds nothing, and keeps its coordinates.  The grid is the
fixed ~2 m one whose constants live in shared/ingest.py (SNAP_CELL_*):
raw/tree_position.preql derives each row's cell from its own coordinates with
those same constants and joins it to this table, so nothing geometric happens
at refresh time.

The whole of the geometry is one DuckDB statement over the spatial extension
(`snap_sql`); Python names the release, points it at S3 and hands the result
to Trilogy.  Its stages, each a CTE:

1. `buildings`, `segments`: the two Overture themes read from the public S3
   release with a bounding-box predicate (a row-group prune; the cost is a
   footer read per file, minutes not seconds, and the job is monthly).
2. `road_polys`: centrelines buffered to half a carriageway in a transverse
   Mercator frame centred on the city: the segment's recorded width when it
   has one, else ROAD_HALF_WIDTH_M by class.  Footways, paths and steps are
   not roads here, tunnels are skipped, caps are flat.
3. `pieces`: every polygon clipped to a coarse tile, so enumerating a
   polygon's bounding box stays close to its area (a diagonal kilometre of
   road has a box thirty times its carriageway).
4. `blocked`: every cell in a piece's box whose CENTRE it contains, buildings
   over roads.  A tree straddling a kerb by centimetres stays put.
5. `frontier`: the open cells adjacent to a blocked one.  The nearest open
   cell to any blocked cell is always on the frontier (else its neighbour
   towards the blocked cell would be open and nearer).
6. `nearest`: each blocked cell's nearest frontier cell, by `arg_min` over
   hash-join candidates in passes of widening reach (SEARCH_PASSES), each
   accepting only a provable minimum.  Most blocked cells are a cell or two
   from open ground, so the first pass settles the bulk.

The output is the centre of the nearest open cell, so a moved tree lands
about a cell outside the edge.  A cell farther than SNAP_MAX_METRES from open
ground gets no row: that is a bad geocode, not a tree by a wall.

Adding a city: OVERTURE_SNAP_CITIES, `overture_staging/{code}_overture_
staging.preql`, an `overture-{code}` job, the city-model wiring
raw/tree_position.preql describes, and one manual staging before the city's
next refresh.  `tests/test_position_grid.py` names what is missing.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from shared.ingest import (
    CITY_BOUNDS,
    SNAP_CELL_LAT_DEG,
    SNAP_CELL_LON_DEG,
    SNAP_CELL_LON_MULTIPLIER,
    UpstreamUnavailable,
    emit,
    get_json_with_retry,
    upload_staging,
)

# Cities whose tree model imports raw/tree_position.preql and reads the
# staged table this module builds.  A city listed here needs its staging
# model and job; a city model importing tree_position without being listed
# here has a lookup nothing publishes.  test_position_grid.py checks both.
OVERTURE_SNAP_CITIES: tuple[str, ...] = ("USBTV",)

OVERTURE_STAC_CATALOG = "https://stac.overturemaps.org/catalog.json"
OVERTURE_S3_RELEASES = "s3://overturemaps-us-west-2/release"
OVERTURE_S3_REGION = "us-west-2"

# The farthest a tree is moved.  Past this the point is not a tree by a wall
# but a geocode that landed somewhere else entirely, and the honest answer is
# to leave it and let the map show it where the source put it.
SNAP_MAX_METRES = 60.0

# Cells per side of the clipping tile (stage 3).
CLIP_TILE_CELLS = 64

# The nearest-open-cell search (stage 6) runs in passes over whatever the
# previous pass left unresolved.  A `window` pass pairs each blocked cell
# with the frontier at every exact offset within r cells (8 offsets at r=1,
# 80 at r=4), a hash join with a bounded fan-out; a `bucket` pass keys both
# sides by a square bucket of R cells and pairs each blocked cell with the
# frontier of its own bucket and the eight around it, which reaches farther
# per pass but pairs with everything in a (3R)^2 window.  Most blocked cells
# are a cell or two from open ground -- a 7 m road is three cells wide -- so
# the two window passes settle the bulk and the bucket passes see only the
# deep interiors of large footprints.  The last bucket must reach
# SNAP_MAX_METRES at the narrowest cell any city has (1.7 m at 60 deg):
# 40 cells is 68 m there.
SEARCH_PASSES: tuple[tuple[str, int], ...] = (("window", 1), ("window", 4), ("bucket", 12), ("bucket", 40))
WIDE_SEARCH_BUCKET_CELLS = SEARCH_PASSES[-1][1]

# Half of the carriageway, in metres, by Overture road class, used when a
# segment carries no width of its own.  Carriageway only -- the verge and the
# pavement are where street trees are planted, so the buffer must not reach
# them.  Classes not listed (footway, cycleway, path, steps, track,
# pedestrian, bridleway, sidewalk, crosswalk, unknown) are not roads here.
ROAD_HALF_WIDTH_M: dict[str, float] = {
    "motorway": 12.0,
    "trunk": 9.0,
    "primary": 6.5,
    "secondary": 5.5,
    "tertiary": 4.5,
    "residential": 3.5,
    "living_street": 3.0,
    "unclassified": 3.0,
    "service": 2.5,
}

# Bounds on a recorded width before it is trusted: a `width=0.5` is a data
# error and a `width=80` is a plaza that was tagged as a road.
ROAD_WIDTH_MIN_M = 2.0
ROAD_WIDTH_MAX_M = 40.0

SNAP_COLUMNS: dict[str, pa.DataType] = {
    "city": pa.string(),
    "snap_cell": pa.int64(),
    "snap_latitude": pa.float64(),
    "snap_longitude": pa.float64(),
    "snap_reason": pa.string(),
    "snap_distance_m": pa.float64(),
    "overture_release": pa.string(),
}

SNAP_SCHEMA = pa.schema(SNAP_COLUMNS)


def snap_staging_name(city_code: str) -> str:
    """The staged object's name, `{code}_overture_snap.parquet`."""
    return f"{city_code.lower()}_overture_snap.parquet"


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


def latest_release() -> str:
    """The current Overture release name, e.g. `2026-08-19.0`.

    From the STAC catalog's `latest` field, or `OVERTURE_RELEASE` in the
    environment to pin one.  Unreachable is `UpstreamUnavailable`, so the
    freshness probe degrades to "no new data" and the rows script fails
    loudly, which is the right way round: a refresh that cannot see the
    release list should not rebuild, and an extract that cannot should not
    pretend to.
    """
    pinned = os.environ.get("OVERTURE_RELEASE")
    if pinned:
        return pinned
    payload = get_json_with_retry(OVERTURE_STAC_CATALOG, timeout=60)
    release = payload.get("latest") if isinstance(payload, dict) else None
    if not release and isinstance(payload, dict):
        for link in payload.get("links") or []:
            if link.get("latest") and link.get("href"):
                release = link["href"].rstrip("/").split("/")[-2]
                break
    if not release:
        raise UpstreamUnavailable(
            f"{OVERTURE_STAC_CATALOG} names no latest release; the catalog's shape changed"
        )
    return str(release)


def release_published_at(release: str) -> datetime:
    """A release name is a date plus a patch number; the date is the watermark."""
    day = release.split(".", 1)[0]
    try:
        return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as err:
        raise RuntimeError(f"Overture release {release!r} is not YYYY-MM-DD.n") from err


def release_path(release: str, theme: str, kind: str) -> str:
    return f"{OVERTURE_S3_RELEASES}/{release}/theme={theme}/type={kind}/*"


# ---------------------------------------------------------------------------
# The grid, in metres
# ---------------------------------------------------------------------------


def metres_per_degree(latitude: float) -> tuple[float, float]:
    """(metres per degree of latitude, metres per degree of longitude) here."""
    return 111320.0, 111320.0 * math.cos(math.radians(latitude))


def city_centre(city_code: str) -> tuple[float, float]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[city_code]
    return (lat_min + lat_max) / 2, (lon_min + lon_max) / 2


def cell_metres(city_code: str) -> tuple[float, float]:
    """(cell height, cell width) in metres at the centre of the city's box."""
    m_lat, m_lon = metres_per_degree(city_centre(city_code)[0])
    return SNAP_CELL_LAT_DEG * m_lat, SNAP_CELL_LON_DEG * m_lon


def search_margin_cells(city_code: str) -> int:
    """Cells of padding round the box so open ground just outside it is seen."""
    return math.ceil(SNAP_MAX_METRES / min(cell_metres(city_code))) + 1


def grid_extent(city_code: str) -> tuple[int, int, int, int]:
    """(lon_index_min, lat_index_min, lon_index_max, lat_index_max), inclusive."""
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[city_code]
    return (
        math.floor(lon_min / SNAP_CELL_LON_DEG),
        math.floor(lat_min / SNAP_CELL_LAT_DEG),
        math.floor(lon_max / SNAP_CELL_LON_DEG),
        math.floor(lat_max / SNAP_CELL_LAT_DEG),
    )


def local_projection(city_code: str) -> str:
    """A transverse Mercator PROJ string centred on the city, in metres."""
    lat, lon = city_centre(city_code)
    return (
        f"+proj=tmerc +lat_0={lat:.4f} +lon_0={lon:.4f} +k=1 +x_0=0 +y_0=0 "
        "+ellps=WGS84 +units=m +no_defs"
    )


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------


def overture_relations(city_code: str, release: str) -> tuple[str, str]:
    """The two S3 reads for a city, as SQL relations `snap_sql` accepts.

    `buildings` yields `geometry`; `segments` yields `geometry`, `class`,
    `is_tunnel` and `width_m` (the first recorded width, or null).
    """
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[city_code]
    margin = search_margin_cells(city_code)
    pad_lat, pad_lon = margin * SNAP_CELL_LAT_DEG, margin * SNAP_CELL_LON_DEG
    bbox = (
        f"bbox.xmin <= {lon_max + pad_lon} AND bbox.xmax >= {lon_min - pad_lon} "
        f"AND bbox.ymin <= {lat_max + pad_lat} AND bbox.ymax >= {lat_min - pad_lat}"
    )
    classes = ", ".join(f"'{c}'" for c in ROAD_HALF_WIDTH_M)
    buildings = (
        f"SELECT geometry FROM read_parquet('{release_path(release, 'buildings', 'building')}', "
        f"hive_partitioning = 1) WHERE {bbox}"
    )
    segments = (
        "SELECT geometry, class, "
        "CAST(road_flags AS VARCHAR) LIKE '%is_tunnel%' AS is_tunnel, "
        "TRY_CAST(width_rules[1].value AS DOUBLE) AS width_m "
        f"FROM read_parquet('{release_path(release, 'transportation', 'segment')}', "
        f"hive_partitioning = 1) WHERE subtype = 'road' AND class IN ({classes}) AND {bbox}"
    )
    return buildings, segments


def snap_sql(
    city_code: str,
    release: str,
    *,
    buildings: str,
    segments: str,
) -> str:
    """The whole build as one DuckDB statement; see the module docstring.

    *buildings* and *segments* are SQL relations (see `overture_relations`
    for the columns each must yield); tests pass local ones.
    """
    lat_deg, lon_deg = SNAP_CELL_LAT_DEG, SNAP_CELL_LON_DEG
    cell_h, cell_w = cell_metres(city_code)
    i_min, j_min, i_max, j_max = grid_extent(city_code)
    tile = CLIP_TILE_CELLS
    proj = local_projection(city_code)
    half_width_case = " ".join(
        f"WHEN '{road_class}' THEN {half}" for road_class, half in ROAD_HALF_WIDTH_M.items()
    )
    mult = SNAP_CELL_LON_MULTIPLIER
    neighbours = ", ".join(f"({dx}, {dy})" for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    min_cell = min(cell_h, cell_w)
    dist = f"sqrt(((f.i - c.i) * {cell_w}) ** 2 + ((f.j - c.j) * {cell_h}) ** 2)"

    def offsets_within(reach: int) -> str:
        """Every exact offset within `reach` cells, as a VALUES list."""
        return ", ".join(
            f"({dx}, {dy})"
            for dx in range(-reach, reach + 1)
            for dy in range(-reach, reach + 1)
            if (dx, dy) != (0, 0)
        )

    # Every pairing is a hash join on ONE packed bigint: the candidate side
    # packs the cell (or bucket) it wants, the frontier carries its own.  A
    # condition spelled as arithmetic across two relations made the planner
    # fall back to a nested loop and spill tens of gigabytes on a 10 km2 box.
    def search_pass(source: str, kind: str, reach: int, name: str) -> str:
        # Accept only a provable minimum: nothing outside the reach can be
        # nearer than (reach + 1) cells, so a candidate within that many of
        # the narrowest cell is the nearest open cell, full stop.
        bound = min((reach + 1) * min_cell, SNAP_MAX_METRES)
        if kind == "window":
            candidates = f"""
    SELECT b.i, b.j, b.kind, (b.i + o.dx) * {mult} + (b.j + o.dy) AS key
    FROM {source} b, (VALUES {offsets_within(reach)}) AS o(dx, dy)"""
            frontier_key = "f.cell"
        else:
            candidates = f"""
    SELECT b.i, b.j, b.kind, ((b.i // {reach}) + o.dx) * {mult} + ((b.j // {reach}) + o.dy) AS key
    FROM {source} b, (VALUES {neighbours}) AS o(dx, dy)"""
            frontier_key = f"f.bucket_{reach}"
        return f"""
{name}_candidates AS ({candidates}
),
{name} AS (
    SELECT * FROM (
        SELECT c.i, c.j, c.kind,
               arg_min(f.i, {dist}) AS fi,
               arg_min(f.j, {dist}) AS fj,
               min({dist}) AS dist
        FROM {name}_candidates c
        JOIN frontier f ON {frontier_key} = c.key
        GROUP BY c.i, c.j, c.kind
    )
    WHERE dist <= {bound}
)"""

    bucket_keys = "".join(
        f",\n           (c.i // {reach}) * {mult} + (c.j // {reach}) AS bucket_{reach}"
        for kind, reach in SEARCH_PASSES
        if kind == "bucket"
    )
    passes = []
    source = "blocked"
    for n, (kind, reach) in enumerate(SEARCH_PASSES):
        passes.append(search_pass(source, kind, reach, f"pass_{n}"))
        if n + 1 < len(SEARCH_PASSES):
            passes.append(f"""
left_{n} AS (
    SELECT b.* FROM {source} b
    LEFT JOIN pass_{n} p ON p.i = b.i AND p.j = b.j
    WHERE p.i IS NULL
)""")
            source = f"left_{n}"
    search = ",".join(passes)
    union = " UNION ALL ".join(f"SELECT * FROM pass_{n}" for n in range(len(SEARCH_PASSES)))

    return f"""
WITH
buildings AS (
    SELECT geometry AS geom FROM ({buildings})
),
segments AS (
    SELECT geometry AS geom, class, is_tunnel, width_m FROM ({segments})
),
road_polys AS (
    SELECT ST_Transform(
               ST_Buffer(
                   ST_Transform(geom, 'EPSG:4326', '{proj}', true),
                   half_width, 8, 'CAP_FLAT', 'JOIN_ROUND', 5.0),
               '{proj}', 'EPSG:4326', true) AS geom
    FROM (
        SELECT geom,
               CASE WHEN width_m BETWEEN {ROAD_WIDTH_MIN_M} AND {ROAD_WIDTH_MAX_M}
                    THEN width_m / 2.0
                    ELSE CASE class {half_width_case} END
               END AS half_width
        FROM segments
        WHERE NOT coalesce(is_tunnel, false)
    )
    WHERE half_width IS NOT NULL
),
polys AS (
    SELECT 1 AS kind, geom FROM buildings WHERE geom IS NOT NULL
    UNION ALL
    SELECT 2 AS kind, geom FROM road_polys WHERE geom IS NOT NULL
),
tiles AS (
    SELECT ST_MakeEnvelope(ti * {tile} * {lon_deg}, tj * {tile} * {lat_deg},
                           (ti + 1) * {tile} * {lon_deg}, (tj + 1) * {tile} * {lat_deg}) AS geom
    FROM generate_series({i_min // tile - 1}, {i_max // tile + 1}) AS a(ti),
         generate_series({j_min // tile - 1}, {j_max // tile + 1}) AS b(tj)
),
pieces AS (
    SELECT p.kind, ST_Intersection(p.geom, t.geom) AS geom
    FROM polys p
    JOIN tiles t ON ST_Intersects(p.geom, t.geom)
),
blocked AS (
    SELECT i, j, min(kind) AS kind
    FROM pieces p,
         generate_series(CAST(floor(ST_XMin(p.geom) / {lon_deg}) AS BIGINT),
                         CAST(floor(ST_XMax(p.geom) / {lon_deg}) AS BIGINT)) AS gi(i),
         generate_series(CAST(floor(ST_YMin(p.geom) / {lat_deg}) AS BIGINT),
                         CAST(floor(ST_YMax(p.geom) / {lat_deg}) AS BIGINT)) AS gj(j)
    WHERE ST_Contains(p.geom, ST_Point((i + 0.5) * {lon_deg}, (j + 0.5) * {lat_deg}))
    GROUP BY i, j
),
blocked_cells AS (
    SELECT i * {mult} + j AS cell FROM blocked
),
frontier_candidates AS (
    SELECT DISTINCT b.i + o.dx AS i, b.j + o.dy AS j
    FROM blocked b, (VALUES {offsets_within(1)}) AS o(dx, dy)
),
frontier AS (
    SELECT c.i, c.j, c.i * {mult} + c.j AS cell{bucket_keys}
    FROM frontier_candidates c
    LEFT JOIN blocked_cells x ON x.cell = c.i * {mult} + c.j
    WHERE x.cell IS NULL
),{search},
nearest AS (
    {union}
)
SELECT '{city_code}' AS city,
       i * {SNAP_CELL_LON_MULTIPLIER} + j AS snap_cell,
       (fj + 0.5) * {lat_deg} AS snap_latitude,
       (fi + 0.5) * {lon_deg} AS snap_longitude,
       CASE kind WHEN 1 THEN 'building' ELSE 'road' END AS snap_reason,
       dist AS snap_distance_m,
       '{release}' AS overture_release
FROM nearest
WHERE i BETWEEN {i_min} AND {i_max} AND j BETWEEN {j_min} AND {j_max}
ORDER BY snap_cell
"""


def connect():
    """A DuckDB connection with the spatial and S3 extensions loaded."""
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute(f"SET s3_region = '{OVERTURE_S3_REGION}';")
    return con


def build_snap_table(
    city_code: str,
    release: str | None = None,
    *,
    connection=None,
    buildings: str | None = None,
    segments: str | None = None,
) -> pa.Table:
    """The whole lookup for one city, keyed exactly as tree_position.preql keys a tree.

    *buildings* and *segments* override the S3 reads with other relations of
    the same shape (`overture_relations`); tests use that, and so does a
    workstation iterating on the query against a local copy.
    """
    if city_code not in OVERTURE_SNAP_CITIES:
        raise ValueError(
            f"{city_code} is not in OVERTURE_SNAP_CITIES; add it there, give it a "
            "staging model and job, and have its tree model import tree_position"
        )
    release = release or latest_release()
    default_buildings, default_segments = overture_relations(city_code, release)
    sql = snap_sql(
        city_code,
        release,
        buildings=buildings or default_buildings,
        segments=segments or default_segments,
    )
    con = connection or connect()
    print(f"{city_code}: building the Overture {release} position lookup", file=sys.stderr)
    result = con.execute(sql)
    table = (result.to_arrow_table() if hasattr(result, "to_arrow_table") else result.arrow()).cast(SNAP_SCHEMA)
    if connection is None:
        con.close()
    _check_cell_grain(table, city_code)
    by_reason = {
        reason: table.filter(pc.equal(table.column("snap_reason"), reason)).num_rows
        for reason in ("building", "road")
    }
    print(
        f"{city_code}: {table.num_rows} blocked cells "
        f"({by_reason['building']} building, {by_reason['road']} road)",
        file=sys.stderr,
    )
    return table


def _check_cell_grain(table: pa.Table, city_code: str) -> None:
    """`snap_cell` is the declared grain; a repeat would fan the lookup out."""
    import pyarrow.compute as pc

    n = table.num_rows
    distinct = pc.count_distinct(table.column("snap_cell")).as_py() if n else 0
    if distinct != n:
        raise RuntimeError(
            f"{city_code} snap table repeats {n - distinct} cell keys; the query "
            "emitted a cell twice"
        )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def stage_city_snap(city_code: str) -> None:
    """Build and emit one city's table as an Arrow stream.

    The body of `overture_staging/overture_snap_rows.py`: `trilogy refresh`
    materialises the staging parquet from this stream and DuckDB writes it to
    GCS with the job's HMAC credentials, the same way the OSM extracts land.
    """
    emit(build_snap_table(city_code))


def extract_city_snap(city_code: str, release: str | None = None) -> pa.Table:
    """Build and publish one city's table from a workstation.

    The manual counterpart of the scheduled job, for staging a city before
    its job is deployed.  Needs application-default credentials.
    """
    import pyarrow.parquet as pq

    table = build_snap_table(city_code, release)
    name = snap_staging_name(city_code)
    out = Path(tempfile.gettempdir()) / name
    pq.write_table(table, out)
    upload_staging(out, name)
    print(f"{city_code}: published {table.num_rows} rows to {name}", file=sys.stderr)
    return table
