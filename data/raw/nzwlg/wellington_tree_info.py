#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Wellington, NZ's council tree inventory.

Source: "WCC Trees" (ArcGIS item 93ca9097fd4b487e9fcf55ad75941136, owner
`WellingtonCityCouncil`), `Parks/Parks/MapServer/53` on Wellington City
Council's own ArcGIS Server, gis.wcc.govt.nz, listed on the council's open
data hub data-wcc.opendata.arcgis.com.  The item carries no licence of its
own; the hub states "Wellington City Council GIS data that can be accessed
online is made available under the Creative Commons Attribution 4.0
International (CC BY 4.0) License" (https://data-wcc.opendata.arcgis.com/).
The trees "owned/maintained by Parks, Sport and Recreation Business Unit",
captured by Arborlab in the Fulcrum field app: 13,936 rows on 2026-11-17,
stored in NZTM (EPSG:2193) and read reprojected to WGS84 (`outSR=4326`).

**Id is `fulcrum_id`** (`wlg-{fulcrum_id}`), the field-capture record's
UUID: non-null and unique over the whole layer (checked 2026-11-17 with a
grouped count).  `GlobalID` is unique too, but the layer's OBJECTIDs run
34,145-49,155 over 13,936 rows -- the table has been emptied and reloaded at
least once -- and a reload mints new GlobalIDs and OBJECTIDs while the
capture record's id travels with the row.  A null `fulcrum_id` is dropped
with a count; a repeat raises in `enforce_tree_schema`.

**Which rows are trees.**  The layer has no status column.  `vitality`
(coded: Excellent/Good/Fair/Poor/Dead/Unknown) keeps dead trees, as every
city does.  What is not a tree is recorded in the free-text species and
comment fields: `botanical_name_other` = `Empty cage` / `Empty planting pit`
(a site, 14 rows) and comments saying the surveyor did not find the tree
("Tree not found on site", 18) or found only its stump.  Those are dropped
with counts.  `owner` = Private (15) are trees the council records on private
land and stay.

**Species.**  `botanical_name` has a coded-value domain whose codes *are*
the scientific names (codes equal names; read 2026-11-17), with the domain's
own misspellings (`Alectyron excelsus`, `Liquidamber styraciflua`,
`Prumnopytis`, ...) left to the shared misspelling map.  A tree outside the
domain has `botanical_name` null and its name typed into
`botanical_name_other` (910 rows; the domain's own `Other` code is never
stored).  `NAME_FIXES` are the names the shared rules cannot read: a
cultivar written as a variety (`Populus nigra var. Italica`), a cultivar
with no quotes (`Olea ascolano`, `Metrosideros Springfire`), a fruit tree
named by its common name in brackets (`Prunus domestica (apricot)` is Prunus
armeniaca), and a hybrid written as its parents.

**DBH.**  `girth` is the trunk circumference in millimetres: median 900,
Young (0-7 yr) trees median 100, Semi-mature 900, Mature 2,200, Veteran
6,000 -- 3 cm, 29 cm, 70 cm and 1.9 m of diameter, where reading it as a
diameter would give a 2.2 m trunk on a 26-50 year street tree.  Below 30 mm
(about 1 cm of diameter: 80 zeros and ~75 values from 0.02 to 20, typed in
metres or centimetres) is no measurement and is nulled.

**Not read.**  `csr` (median 3, steps of 0.25-1 m) is a crown spread, but
the layer does not say whether it is the radius or the width and the
species medians fit neither cleanly (Lombardy poplar 3, Cupressus
sempervirens 1, pohutukawa 3), so `crown_width_m` is not published.
`height` has no column in the published schema.  There is no planting date;
`last_visited` is the survey date.
"""

import math
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gis.wcc.govt.nz/arcgis/rest/services/Parks/Parks/MapServer/53",
    timeout=180,
)
FIELDS = "OBJECTID,fulcrum_id,botanical_name,botanical_name_other,girth,comments"

# `botanical_name_other` values that record an empty site, not a tree.
NOT_A_TREE_NAMES = frozenset({"empty cage", "empty planting pit"})
# Comment phrases that say the surveyor found no tree at the point.
NOT_A_TREE_COMMENTS = ("not found on site", "left over stump")

# Girth, millimetres of circumference; see the module docstring.
GIRTH_MIN_MM = 30.0
MM_PER_INCH = 25.4

# Raw names (whitespace collapsed) the shared rules cannot read, mapped to
# (species, cultivar).
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    "Populus nigra var. Italica": ("Populus nigra", "Italica"),
    "Fagus sylvatica var purpurea": ("Fagus sylvatica", "Purpurea"),
    "Olea ascolano": ("Olea europaea", "Ascolano"),
    "Prunus domestica (apricot)": ("Prunus armeniaca", None),
    "Prunus domestica (plum)": ("Prunus domestica", None),
    "Populus deltoides X P.nigra": ("Populus x canadensis", None),
    "Citrus limon X sinensis": ("Citrus", None),
    "Metrosideros Springfire": ("Metrosideros", "Springfire"),
    'Metrosideros X "Mistral"': ("Metrosideros", "Mistral"),
    "Prunus pissardii nigra": ("Prunus cerasifera", "Nigra"),
    "Ulmus procrea Lutescens": ("Ulmus procera", "Lutescens"),
    "Tristaniopsis Laurianne": ("Tristaniopsis laurina", None),
    "Cypresses sp.": ("Cupressus", None),
    "Schefflera digitata?": ("Schefflera digitata", None),
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def raw_name(attrs: dict) -> str | None:
    return clean(attrs.get("botanical_name")) or clean(attrs.get("botanical_name_other"))


def species_and_cultivar(name: str | None) -> tuple[str | None, str | None]:
    if name is None:
        return None, None
    if name in NAME_FIXES:
        return NAME_FIXES[name]
    # The quoted cultivar (`Pseudopanax "Sabre"`) is parsed by the schema.
    return name, None


def dbh_inches(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < GIRTH_MIN_MM:
        return None
    return v / math.pi / MM_PER_INCH


class Counts:
    no_id = 0
    empty_site = 0
    not_found = 0


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    if not clean(attrs.get("fulcrum_id")):
        COUNTS.no_id += 1
        return False
    other = (clean(attrs.get("botanical_name_other")) or "").lower()
    if attrs.get("botanical_name") is None and other in NOT_A_TREE_NAMES:
        COUNTS.empty_site += 1
        return False
    comment = (clean(attrs.get("comments")) or "").lower()
    if any(phrase in comment for phrase in NOT_A_TREE_COMMENTS):
        COUNTS.not_found += 1
        return False
    return True


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, cv = species_and_cultivar(raw_name(attrs))
        tree_id.append(f"wlg-{clean(attrs['fulcrum_id']).lower()}")
        species.append(name)
        cultivar.append(cv)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_inches(attrs.get("girth")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["NZWLG"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array([None] * n, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Wellington OpenData")
    print(
        f"Wellington ingest: dropped {COUNTS.no_id} rows with no fulcrum_id, "
        f"{COUNTS.empty_site} empty sites (Empty cage / Empty planting pit), "
        f"{COUNTS.not_found} trees the survey did not find or found only as a stump",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Wellington", city_code="NZWLG")
    table = enforce_tree_schema(
        table, city="Wellington", data_source="WELLINGTON_OPENDATA"
    )
    emit(table)
