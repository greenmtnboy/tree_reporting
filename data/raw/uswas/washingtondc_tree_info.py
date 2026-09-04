#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, iter_features
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    'https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/'
    'Urban_Tree_Canopy/MapServer/23'
)
OUT_FIELDS = 'FACILITYID,SCI_NM,CMMN_NM,DATE_PLANT,DBH'


def parse_plant_date(value) -> date | None:
    """DC publishes planting dates two different ways.

    The GeoJSON export this ingest used to read renders them as ISO strings;
    the `f=json` query API returns Esri's native epoch milliseconds. Accept
    both, so the parser does not depend on which endpoint fed it.
    """
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return date.fromisoformat(str(value)[:10].replace('/', '-'))
    except ValueError:
        return None


def normalize_common_name(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    return stripped.capitalize() if stripped.islower() else stripped


def iter_row_chunks():
    """One ArcGIS page at a time.

    A generator rather than one bulk read: the previous version fetched the
    whole layer as GeoJSON and called `response.json()`, so 216k features
    existed simultaneously as the response body, a dict per feature and a
    Python list per column. That OOM-killed the 2 GiB city container
    (`Pipe process exited abnormally code=137`) while passing locally, which
    is the same failure `_ingest_shared.stream_to_table` was written for.

    Paging, ordering and the page size are `_arcgis_shared`'s -- see there for
    why the size comes from the layer rather than a constant and why the loop
    ends on `exceededTransferLimit`. Both matter here: this layer's
    `maxRecordCount` is exactly the 2000 that used to be hardcoded, so the two
    agreed by luck rather than by construction.

    Retired trees are excluded server-side rather than skipped after transfer,
    which is both the old behaviour (`RETIREDDT is not None` was dropped) and
    ~5,400 rows we no longer move.
    """
    return iter_features(
        LAYER,
        where='RETIREDDT IS NULL',
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
    )


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in rows:
        props = feature.get('attributes', {})
        geom = feature.get('geometry') or {}
        sci = normalize_species(props.get('SCI_NM'))
        common = normalize_common_name(props.get('CMMN_NM'))
        facility_id = props.get('FACILITYID')
        tree_id.append(f"was-{facility_id}" if facility_id else None)
        species.append(sci)
        tree_name.append(common or sci)
        plant_date.append(parse_plant_date(props.get('DATE_PLANT')))
        longitude.append(geom.get('x'))
        latitude.append(geom.get('y'))
        raw_dbh = props.get('DBH')
        dbh.append(float(raw_dbh) if raw_dbh not in (None, '') else None)

    n = len(rows)
    return pa.table({
        'tree_id': pa.array(tree_id, type=pa.string()),
        'city': pa.array(['USWAS'] * n, type=pa.string()),
        'species': pa.array(species, type=pa.string()),
        'tree_name': pa.array(tree_name, type=pa.string()),
        'plant_date': pa.array(plant_date, type=pa.date32()),
        'latitude': pa.array(latitude, type=pa.float64()),
        'longitude': pa.array(longitude, type=pa.float64()),
        'diameter_at_breast_height': pa.array(dbh, type=pa.float64()),
    })


if __name__ == '__main__':
    table = stream_to_table(
        iter_row_chunks(), transform, label='Washington DC OpenData'
    )
    table = validate_coordinates(table, city='Washington DC', city_code='USWAS')
    table = enforce_tree_schema(table, city='Washington DC', data_source="WASHINGTONDC_OPENDATA")
    emit(table)
