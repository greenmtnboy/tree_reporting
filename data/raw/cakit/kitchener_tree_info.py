#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Kitchener's city tree inventory, from the City of Kitchener's open data.

Source: "Tree_Inventory" (item e42a9601b87a4417b3d3d15a0f596f64, owned by the
City's `KitchenerGIS` account, "Inventory of city street trees", updated
daily), the layer the Kitchener Open Data Repository links to.  Licence, from
the item's own licence field: the Open Government Licence - The Corporation
of the City of Kitchener, version 1.0
(https://www.kitchener.ca/council-and-city-administration/data-and-maps/open-data-licence/).  Credit is not
required; the requested form is "Contains information licensed under the Open
Government Licence - The Corporation of the City of Kitchener."

**Only `STATUS = 'ACTIVE'` is a tree in the ground.**  The coded-value domain
is ACTIVE / REMOVED / PLANNED PLANTING / POTENTIAL PLANTING / FIELD MISTAKE -
DELETE; measured 2026-11-18 over 99,731 rows: ACTIVE 76,788, REMOVED 18,114,
PLANNED PLANTING 2,817, POTENTIAL PLANTING 2,007, FIELD MISTAKE - DELETE 3,
null 2.  Everything but ACTIVE is a removed tree, a planting plan or an
error, and is filtered server-side.  `OWNERSHIP` is CITY (98,378) or CITY
SHARED (1,353) on every row -- the domain's PRIVATE is never used -- so
nothing is dropped for ownership.

**The id is `GLOBALID`** (`kit-{guid}`), the layer's system-maintained
GlobalID: non-null and unique over all 99,731 rows (checked 2026-11-18).
`TREEID`, the forestry asset number, was also unique and non-null then, but
the GlobalID is the publisher-guaranteed key, so it is the grain.
`enforce_tree_schema` still raises on a repeat or a null.

**Species is `SPECIES_LATIN`**, a coded-value domain whose codes and names
are identical binomials with cultivars quoted (`Acer X freemanii 'Autumn
Blaze'`); `enforce_tree_schema` splits the cultivar.  The domain also holds
values that are not binomials, rewritten here (see SPECIES_REWRITES):

  * `Ulmus x Accolade` / `Homestead` / `Frontier` / `Pioneer` / `Triumph`:
    hybrid-elm cultivars written as if they were nothoepithets.  They are the
    genus with the cultivar (`Ulmus 'Accolade'`).
  * `Acer truncatum X Acer platanoides 'Keithsform'` and its kin: the
    shantung x Norway maple cross has no published binomial, so the genus
    with the cultivar.
  * `Gingko biloba, female`: the sex note is stripped.
  * `SPECIES NOT FOUND`, `UNKNOWN` and the size-class placeholders
    (`Broadleaf Deciduous Large`, `Conifer Evergreen Small`, ...) name no
    taxon and publish as Unknown.  Where `SPECIES NOT FOUND` carries a genus
    in `SPECIES_GENUS` (Acer, Celtis, Quercus -- that column is the
    scientific genus, not a common name) the genus is used.

`Acer cf. saccharinum` ("Soft Maple", 2,154 trees) is a silver-or-red maple
the inventory did not settle; the shared schema publishes it as `Acer`.
`SPECIES_NAME` is the common name and is never mapped.

**DBH is centimetres** (`MAP_DBH_CM`, mean 25 over ACTIVE rows).  Zero is
"not measured" and is nulled here; keying errors above the schema's 200-inch
ceiling (the layer's maximum is 2,022) are nulled there.

**`YEAR_PLANTED`** is filled for about a fifth of the ACTIVE rows, all
2006 onwards; eleven rows hold `6`, a keying error that falls to the
schema's 1500 floor.

Geometry is stored in NAD83 / UTM 17N (EPSG:26917) and requested in WGS84
(`outSR=4326`).
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/qAo1OsXi67t7XgmS/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,GLOBALID,SPECIES_LATIN,SPECIES_GENUS,MAP_DBH_CM,YEAR_PLANTED"
WHERE = "STATUS = 'ACTIVE'"

# Domain values that name no taxon.  Compared lowercased.
NO_TAXON = frozenset(
    {
        "species not found",
        "unknown",
        "broadleaf deciduous large",
        "broadleaf deciduous medium",
        "broadleaf deciduous small",
        "conifer evergreen large",
        "conifer evergreen medium",
        "conifer evergreen small",
    }
)

# Domain values rewritten to a name the shared schema reads correctly.
SPECIES_REWRITES = {
    "Ulmus x Accolade": "Ulmus 'Accolade'",
    "Ulmus x Homestead": "Ulmus 'Homestead'",
    "Ulmus x Frontier": "Ulmus 'Frontier'",
    "Ulmus x Pioneer": "Ulmus 'Pioneer'",
    "Ulmus x Triumph": "Ulmus 'Triumph'",
    "Acer truncatum X Acer platanoides 'Keithsform'": "Acer 'Keithsform'",
    "Acer truncatum X Acer platanoides 'Warrenred'": "Acer 'Warrenred'",
    "Acer truncatum x platanoides 'Crimson Sunset'": "Acer 'Crimson Sunset'",
    "Gingko biloba, female": "Ginkgo biloba",
    "Elm": "Ulmus",
}

# `SPECIES_GENUS` values that are not genera.
NOT_A_GENUS = frozenset({"unknown", "broadleaf", "conifer", "elm", "gingko"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def scientific_name(latin, genus) -> str | None:
    text = clean(latin)
    if text is not None and text.lower() not in NO_TAXON:
        return SPECIES_REWRITES.get(text, text)
    fallback = clean(genus)
    if fallback is None or fallback.lower() in NOT_A_GENUS or " " in fallback:
        return None
    return fallback


def diameter_inches(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return cm_to_inches(number) if number > 0 else None


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    plant_date = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = str(rec.get("GLOBALID") or "").strip().strip("{}").lower()
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"kit-{raw_id}")
        species.append(scientific_name(rec.get("SPECIES_LATIN"), rec.get("SPECIES_GENUS")))
        plant_date.append(parse_plant_date_year(rec.get("YEAR_PLANTED")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("MAP_DBH_CM")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAKIT"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def has_id(feature: dict) -> bool:
    return bool(str((feature.get("attributes") or {}).get("GLOBALID") or "").strip())


if __name__ == "__main__":
    pages = iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        extra_params={"geometryPrecision": "7"},
    )
    table = stream_to_table(pages, transform, keep=has_id, label="Kitchener OpenData")
    table = validate_coordinates(table, city="Kitchener", city_code="CAKIT")
    table = enforce_tree_schema(
        table, city="Kitchener", data_source="KITCHENER_OPENDATA"
    )
    emit(table)
