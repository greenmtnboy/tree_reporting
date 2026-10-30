"""Tree height from LIDAR canopy segmentations, staged as a grid lookup.

NOT a uv inline script — a regular importable module, like `shared.overture`.

Some cities publish the output of a LIDAR canopy survey: the canopy split into
one object per crown (an inverse-watershed segmentation of the normalised
surface model), each with its height and a crown radius.  None of them is a
tree inventory -- a crown has no species, no id that survives the next survey,
and one crown can be two trees the segmentation merged -- so a crown never
becomes a tree row.  It is an ATTRIBUTE source: it answers "how tall is the
canopy over this point", and every tree row the city already has, municipal,
OSM, satellite or predicted, asks it.

The table this module builds is that question precomputed on a grid, the way
`shared.overture` precomputes "is this point under a roof":

  * every crown is drawn as a circle of its own radius around its centroid
    and rasterised onto the fixed position grid (`SNAP_CELL_LAT_DEG` x
    `SNAP_CELL_LON_DEG` in `shared.ingest`, ~2.2 x 2.5 m at Boston), so a cell
    belongs to a crown when its centre is inside the circle;
  * a cell two circles both cover goes to the crown it is relatively nearer
    -- distance over radius, so a big crown is not carved up by a small one
    whose centroid happens to be closer -- which makes the table one row per
    cell;
  * raw/tree_height.preql keys each raw tree row into the same grid, looks its
    cell up, and carries the crown's height through the cluster merge.

Circles, not the crowns' own polygons, so every source has one shape:
Boston publishes centroids and a radius only, and Cambridge's radius is the
equal-area radius of its polygon.  The ~10% of area a circle misses on an
elongated crown is a tree on the edge going unmatched, not a wrong height.

Heights are published in US feet by both cities and converted here; the
staged column is metres.  Both surveys are 2024 snapshots that will not move
until the next survey, so the job that stages this runs by hand, not on a
cron (see docs/TREE_HEIGHT.md).
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from shared.ingest import (
    SNAP_CELL_LAT_DEG,
    SNAP_CELL_LON_DEG,
    SNAP_CELL_LON_MULTIPLIER,
    emit,
    upload_staging,
)
from shared.platforms.arcgis import FeatureLayer, iter_features

FEET_TO_METRES = 0.3048

# The staging watermark.  Neither portal exposes an edit date for its crown
# layer (Boston's publishes no editingInfo at all) and both are one-off 2024
# surveys, so the table is rebuilt when this moves: bump it when a city adds a
# survey, a portal republishes one, or the rasteriser below changes.
CANOPY_STAGED_THROUGH: dict[str, datetime] = {
    "USBOS": datetime(2026, 10, 29, tzinfo=timezone.utc),
}

# A crown shorter than this is a shrub or a hedge the segmentation kept, and a
# taller one is a building edge or a tower crane; both surveys already clip at
# about 10 ft, so the floor is a guard against a units change, not a filter.
HEIGHT_MIN_M = 2.0
HEIGHT_MAX_M = 60.0

# A circle smaller than one cell would rasterise to nothing when its centre
# misses the cell centres, so every crown covers at least this radius: a
# small street tree is still found by a trunk point a metre off its centroid.
RADIUS_MIN_M = 1.5
# Past this the object is a stand of trees the segmentation could not split
# (Boston's largest "crown" is 48 m across); its centroid says nothing about
# any one tree and its height is the tallest of them, so it is capped rather
# than allowed to colour a whole park.
RADIUS_MAX_M = 15.0


@dataclass(frozen=True)
class CanopyLayer:
    """One published crown segmentation."""

    label: str  # crown_source in the staged table, e.g. BOSTON_LIDAR_2024
    layer: FeatureLayer
    height_field: str
    radius_field: str
    geometry: str  # 'point' (centroids) or 'polygon' (crowns; read as centroids)
    survey_year: int


CANOPY_LAYERS: dict[str, tuple[CanopyLayer, ...]] = {
    "USBOS": (
        CanopyLayer(
            label="BOSTON_LIDAR_2024",
            layer=FeatureLayer(
                "https://gisportal.boston.gov/arcgis/rest/services/BPRD/"
                "Tree_Canopy_Data_2024/FeatureServer/12",
                timeout=180,
            ),
            # A string column on the portal; the values are decimal feet.
            height_field="Height",
            radius_field="Radius",
            geometry="point",
            survey_year=2024,
        ),
        CanopyLayer(
            label="CAMBRIDGE_LIDAR_2024",
            layer=FeatureLayer(
                "https://services1.arcgis.com/WnzC35krSYGuYov4/arcgis/rest/services/"
                "Tree_Canopy_2024/FeatureServer/0",
                timeout=180,
            ),
            height_field="Height_ft",
            radius_field="Radius",
            geometry="polygon",
            survey_year=2024,
        ),
    ),
}

CROWN_COLUMNS: dict[str, pa.DataType] = {
    "crown_id": pa.string(),
    "crown_source": pa.string(),
    "latitude": pa.float64(),
    "longitude": pa.float64(),
    "height_m": pa.float64(),
    "radius_m": pa.float64(),
}
CROWN_SCHEMA = pa.schema(CROWN_COLUMNS)

HEIGHT_COLUMNS: dict[str, pa.DataType] = {
    "city": pa.string(),
    "canopy_cell": pa.int64(),
    "canopy_crown_id": pa.string(),
    "canopy_height_m": pa.float64(),
    "canopy_crown_radius_m": pa.float64(),
    "canopy_source": pa.string(),
}
HEIGHT_SCHEMA = pa.schema(HEIGHT_COLUMNS)


def height_staging_name(city_code: str) -> str:
    """The staged object's name, `{code}_canopy_height.parquet`."""
    return f"{city_code.lower()}_canopy_height.parquet"


# ---------------------------------------------------------------------------
# Reading the crowns
# ---------------------------------------------------------------------------


def _as_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def fetch_layer_crowns(source: CanopyLayer) -> pa.Table:
    """One layer's crowns as centroid, height and radius, in metres.

    A polygon layer is asked for its centroids (`returnCentroid`), so neither
    shape ever crosses the wire as a ring.
    """
    columns: dict[str, list] = {name: [] for name in CROWN_COLUMNS}
    extra = {"returnCentroid": "true", "outSR": "4326"} if source.geometry == "polygon" else None
    for page in iter_features(
        source.layer,
        out_fields=f"OBJECTID,{source.height_field},{source.radius_field}",
        return_geometry=source.geometry == "point",
        extra_params=extra,
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            point = feature.get("centroid") if source.geometry == "polygon" else feature.get("geometry")
            if not point:
                continue
            height_ft = _as_float(attrs.get(source.height_field))
            radius_ft = _as_float(attrs.get(source.radius_field))
            if height_ft is None or radius_ft is None:
                continue
            columns["crown_id"].append(f"{source.label}-{attrs['OBJECTID']}")
            columns["crown_source"].append(source.label)
            columns["latitude"].append(point.get("y"))
            columns["longitude"].append(point.get("x"))
            columns["height_m"].append(round(height_ft * FEET_TO_METRES, 2))
            columns["radius_m"].append(round(radius_ft * FEET_TO_METRES, 2))
        print(f"{source.label}: {len(columns['crown_id'])} crowns read", file=sys.stderr)
    return pa.table(columns, schema=CROWN_SCHEMA)


def fetch_city_crowns(city_code: str) -> pa.Table:
    """Every configured layer's crowns for one city, concatenated."""
    if city_code not in CANOPY_LAYERS:
        raise ValueError(f"{city_code} has no canopy layers; add it to CANOPY_LAYERS")
    return pa.concat_tables(fetch_layer_crowns(source) for source in CANOPY_LAYERS[city_code])


# ---------------------------------------------------------------------------
# Rasterising
# ---------------------------------------------------------------------------


def height_sql(crowns: str, city_code: str) -> str:
    """One row per grid cell under a crown, keyed as raw/tree_height.preql keys a tree.

    *crowns* is a relation shaped like CROWN_SCHEMA.  Each crown enumerates the
    cells of its bounding square, keeps those whose centre is inside its
    (clamped) circle, and each cell keeps the crown with the smallest
    distance-over-radius; ties go to the taller crown, then the lower id, so
    the build is deterministic.
    """
    lat_deg, lon_deg, mult = SNAP_CELL_LAT_DEG, SNAP_CELL_LON_DEG, SNAP_CELL_LON_MULTIPLIER
    return f"""
WITH crowns AS (
    SELECT
        crown_id, crown_source, latitude, longitude, height_m,
        least(greatest(radius_m, {RADIUS_MIN_M}), {RADIUS_MAX_M}) AS r,
        111320.0 AS m_lat,
        111320.0 * cos(radians(latitude)) AS m_lon
    FROM {crowns}
    WHERE height_m BETWEEN {HEIGHT_MIN_M} AND {HEIGHT_MAX_M}
      AND latitude IS NOT NULL AND longitude IS NOT NULL
),
spans AS (
    SELECT *,
        CAST(floor((longitude - r / m_lon) / {lon_deg}) AS BIGINT) AS x0,
        CAST(floor((longitude + r / m_lon) / {lon_deg}) AS BIGINT) AS x1,
        CAST(floor((latitude - r / m_lat) / {lat_deg}) AS BIGINT) AS y0,
        CAST(floor((latitude + r / m_lat) / {lat_deg}) AS BIGINT) AS y1
    FROM crowns
),
cells AS (
    SELECT
        s.crown_id, s.crown_source, s.height_m, s.r,
        x.x AS xi, y.y AS yi,
        sqrt(
            power(((x.x + 0.5) * {lon_deg} - s.longitude) * s.m_lon, 2)
            + power(((y.y + 0.5) * {lat_deg} - s.latitude) * s.m_lat, 2)
        ) AS d
    FROM spans s,
        LATERAL (SELECT unnest(range(s.x0, s.x1 + 1)) AS x) x,
        LATERAL (SELECT unnest(range(s.y0, s.y1 + 1)) AS y) y
)
SELECT
    '{city_code}' AS city,
    xi * {mult} + yi AS canopy_cell,
    arg_min(crown_id, [d / r, -height_m]) AS canopy_crown_id,
    arg_min(height_m, [d / r, -height_m]) AS canopy_height_m,
    arg_min(r, [d / r, -height_m]) AS canopy_crown_radius_m,
    arg_min(crown_source, [d / r, -height_m]) AS canopy_source
FROM cells
WHERE d <= r
GROUP BY xi, yi
ORDER BY canopy_cell
"""


def build_height_table(city_code: str, crowns: pa.Table | None = None, *, connection=None) -> pa.Table:
    """The whole lookup for one city.  *crowns* overrides the portal reads."""
    import duckdb

    crowns = crowns if crowns is not None else fetch_city_crowns(city_code)
    con = connection or duckdb.connect()
    con.register("crowns_in", crowns)
    result = con.execute(height_sql("crowns_in", city_code))
    table = (result.to_arrow_table() if hasattr(result, "to_arrow_table") else result.arrow()).cast(HEIGHT_SCHEMA)
    con.unregister("crowns_in")
    if connection is None:
        con.close()
    _check_cell_grain(table, city_code)
    crowns_kept = pc.count_distinct(table.column("canopy_crown_id")).as_py() if table.num_rows else 0
    print(
        f"{city_code}: {table.num_rows} canopy cells from {crowns_kept} of {crowns.num_rows} crowns",
        file=sys.stderr,
    )
    return table


def _check_cell_grain(table: pa.Table, city_code: str) -> None:
    """`canopy_cell` is the declared grain; a repeat would fan the lookup out."""
    n = table.num_rows
    distinct = pc.count_distinct(table.column("canopy_cell")).as_py() if n else 0
    if distinct != n:
        raise RuntimeError(f"{city_code} canopy table repeats {n - distinct} cell keys")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def stage_city_height(city_code: str) -> None:
    """Build and emit one city's table as an Arrow stream (the staging job's body)."""
    emit(build_height_table(city_code))


def extract_city_height(city_code: str) -> pa.Table:
    """Build and publish one city's table from a workstation."""
    import pyarrow.parquet as pq

    table = build_height_table(city_code)
    name = height_staging_name(city_code)
    out = Path(tempfile.gettempdir()) / name
    pq.write_table(table, out)
    upload_staging(out, name)
    print(f"{city_code}: published {table.num_rows} rows to {name}", file=sys.stderr)
    return table
