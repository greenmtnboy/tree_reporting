#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, iter_features
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    normalize_species,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.burlingtonvt.gov/arcgis/rest/services/"
    "Tree_Sites_Public_View/FeatureServer/0"
)
OUT_FIELDS = "OBJECTID,botanic,common,planted,diameter"

# Active tree sites only (T = Tree; excludes R = Removed, S = Stump, etc.)
WHERE = "site_typ = 'T'"


def iter_row_chunks():
    """One ArcGIS page at a time, reprojected to WGS84 server-side.

    Paging is `_arcgis_shared`'s. The version this replaced computed its page
    offsets from a `returnCountOnly` taken *before* the first page and passed no
    `orderByFields` at all, so a row inserted mid-read shifted every subsequent
    page: offset paging over an unordered ArcGIS result may repeat or skip rows,
    and neither shows up as an error. It also called `requests.get` directly, so
    a single portal blip failed the whole city instead of being retried.

    The source is Vermont State Plane (WKID 103173); `out_sr` reprojects it.
    """
    return iter_features(
        LAYER,
        where=WHERE,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
    )


def transform(features: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}

        obj_id = rec.get("OBJECTID")
        tree_ids.append(f"btv-{obj_id}" if obj_id is not None else None)
        species_list.append(normalize_species(rec.get("botanic")))

        cn = rec.get("common")
        tree_names.append(cn.strip() if cn and cn.strip() else None)

        plant_dates.append(parse_plant_date_year(rec.get("planted")))

        if geom.get("x") is not None:
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
            "city": pa.array(["USBTV"] * len(tree_ids), type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "plant_date": pa.array(plant_dates, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, label="Burlington OpenData"
    )
    table = validate_coordinates(table, city="Burlington", city_code="USBTV")
    table = enforce_tree_schema(
        table, city="Burlington", data_source="BURLINGTON_OPENDATA"
    )
    emit(table)
