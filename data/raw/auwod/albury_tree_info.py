#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Albury City Council's street and park trees (NSW), a partition of Wodonga.

No licence stated; displayed with attribution only, not licensed for reuse.
The ArcGIS item's `licenseInfo` and `accessInformation` are empty, the
AlburyCity ArcGIS Online organisation has open data switched off, and the
dataset is on neither data.nsw nor data.gov.au.  The council website's
copyright page (alburycity.nsw.gov.au/copyright) covers "material which
appears on the AlburyCity web site"; the layer is not on, or linked from,
that site.

Source: "Trees (Public)" (ArcGIS Online item
605dc59091604f4b8aa99eaf56d71c3a, owner `gisadmin_acc`, "A public feature
layer view used to display trees"), layer 0 `Trees` on services1.arcgis.com.
43,508 points on 2026-11-23 (`dataLastEditDate` 2026-11-22).  Albury faces
Wodonga across the Murray and is inside AUWOD's territory, so it is a
municipal partition of Wodonga rather than a city of its own.

**`globalid` is the key**: the view's own GlobalID, 43,508 distinct over
43,508 rows, none null.  `OBJECTID` (629,078-679,301) is not dense and not
used.  Prefixed `alb-`.

The view publishes only names: `genus`, `species`, `cultivar` and
`commonname`, each a coded-value domain whose codes are the names
themselves.

* **`species` is the whole scientific name** (`Pyrus calleryana`), not an
  epithet; where it is empty `genus` stands in (89 rows).  Where the two
  disagree (`Hedera` / `Eucalyptus socialis`, 3 rows) `species` wins.
  Rows with neither (511) publish as `Unknown`, as do `Unknown sp.`
* **`cultivar` is its own column** (3,366 rows: `Aurea` on golden ash,
  `Autumn Blaze`, `Nigra`, `Chanticleer`) and is passed through as such.  A
  few names carry the selection unquoted in `species` instead
  (`Fraxinus Raywood`, 691 rows; `Cedrus atlantica Glauca`); those are
  rewritten in `SOURCE_SPELLINGS`.
* `commonname` is not mapped.  There is no status column (every feature is
  a tree on the layer's own say), no trunk measurement, no crown spread, no
  height and no planting date, so `diameter_at_breast_height` and
  `plant_date` are all null.

One point sits in California (lat 33.99, lon -117.7); it and anything else
outside AUWOD's box is dropped, with a count, by `validate_coordinates`.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/UytyQvWmxkbjUtjg/arcgis/rest/services/"
    "Trees_(Public)/FeatureServer/0",
    timeout=180,
)

OUT_FIELDS = "OBJECTID,globalid,genus,species,cultivar"
ORDER_BY = "OBJECTID"

# Exact `species` codes whose shared reading would be wrong: a selection
# written unquoted after the name (the shared rules would keep only the
# first two words, so `Fraxinus Raywood` would publish as a genus that is
# not one), and a hyphen dropped from an epithet.
SOURCE_SPELLINGS: dict[str, str] = {
    "Fraxinus Raywood": "Fraxinus angustifolia 'Raywood'",
    "Cedrus atlantica Glauca": "Cedrus atlantica 'Glauca'",
    "Cupressus macrocarpa Lutea": "Cupressus macrocarpa 'Lutea'",
    "Photinia x fraseri Robusta": "Photinia x fraseri 'Robusta'",
    "Erythrina crista galli": "Erythrina crista-galli",
    "Prunus x amygdalo persica": "Prunus x amygdalopersica",
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def species_of(genus: str | None, species: str | None) -> str | None:
    """The scientific name: `species` when given, else `genus`."""
    text = clean(species) or clean(genus)
    if text is None:
        return None
    text = SOURCE_SPELLINGS.get(text, text)
    if "'" in text:
        # normalize_species lower-cases every word after the genus; keep the
        # quoted selection's own casing for enforce_tree_schema to lift out.
        head, _, tail = text.partition("'")
        return f"{normalize_species(head)} '{tail}"
    return normalize_species(text)


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by=ORDER_BY
    )


MISSING_ID = 0


def transform(features: list[dict]) -> pa.Table:
    global MISSING_ID
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        guid = (attrs.get("globalid") or "").strip().strip("{}").lower()
        if not guid:
            MISSING_ID += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"alb-{guid}")
        species.append(species_of(attrs.get("genus"), attrs.get("species")))
        cultivar.append(clean(attrs.get("cultivar")))
        latitude.append(lat)
        longitude.append(lon)

    n = len(tree_id)
    plant_date: list[date | None] = [None] * n
    dbh: list[float | None] = [None] * n
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["AUWOD"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Albury City")
    if MISSING_ID:
        print(f"Albury ingest: dropped {MISSING_ID} rows with no globalid", file=sys.stderr)
    table = validate_coordinates(table, city="Albury", city_code="AUWOD")
    table = enforce_tree_schema(table, city="Albury", data_source="ALBURY")
    emit(table)
