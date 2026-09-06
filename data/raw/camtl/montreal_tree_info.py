#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Montreal's public tree inventory, from the city's CKAN portal.

Source: "Arbres publics sur le territoire de la Ville" on
`donnees.montreal.ca`, the **consolidated** resource `64e28fe6`, 335,052 rows.
Paging and the freshness watermark live in `_ckan_shared`.

**The package holds seven resources and only one of them is this city.**
Alongside the consolidated inventory it publishes a DBH history file, a
per-borough coverage summary, a data dictionary, and one file each for
Montreal-Nord and Outremont -- the two boroughs outside the corporate system
the consolidated file names.  Reading the resource list and taking the first
CSV picks a 7,683-row borough and publishes it as Montreal.

The two borough files are deliberately **not** unioned in, and the reason is
not tidiness: neither can be placed on a map.  Outremont carries MTM
(EPSG:32188) `Coord_X`/`Coord_Y` and its `Latitude`/`Longitude` columns are
null on all 7,683 rows; Montreal-Nord's `X`/`Y` are empty on all 11,158.
Reprojecting Outremont is a real option for later -- it needs a projection
dependency this ingest does not have -- and Montreal-Nord has no coordinates
at all, in any column.

**The id is composite, and the obvious column is not unique.**  `EMP_NO` is an
*emplacement* number unique only within its own inventory: the city runs two,
`INV_TYPE` `R` (rue, 218,595 rows) and `H` (hors rue, 116,457), and they reuse
numbers.  `ARROND` + `EMP_NO` measures 326,521 distinct over 335,052 rows --
8,531 collisions, each of which would fan out every join built on the declared
grain.  Emplacement 121164 in Ahuntsic-Cartierville is a *Larix laricina* under
`H` and a *Quercus robur* 1.3 km away under `R`: two real trees, not a
duplicate.  `INV_TYPE` + `ARROND` + `EMP_NO` measures 335,052 distinct, none
null.

`DHP` is centimetres, as every Canadian portal publishes.  Cultivars are
written quoted inside `Essence_latin` (`Gleditsia triacanthos 'Skyline'`),
which `enforce_tree_schema` lifts onto the tree row by itself -- so there is no
cultivar handling here, deliberately.  2,503 rows write one unquoted instead
and give it up, which is the right trade: the capitalisation rule Edmonton uses
to recover those would also strip the epithet off `Viburnum Lentago` and
`Picea pungens Iseli Fastigiata`, and this portal is not consistent enough to
tell the two apart.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, iter_datastore_rows
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "donnees.montreal.ca",
    # "Inventaire arbres publics - Fichier consolide".  See the docstring: the
    # package's other CSVs are a DHP history, a coverage summary and two
    # unmappable borough extracts.
    "64e28fe6-ef37-437a-972d-d1d3f1f7d891",
    timeout=180,
)

FIELDS = (
    "INV_TYPE,ARROND,EMP_NO,Essence_latin,Essence_ang,DHP,Date_Plantation,"
    "Latitude,Longitude"
)

# 383 rows record a diameter of 0, which is an unmeasured tree rather than a
# tree of no width, and 8 record more than 3 m -- the largest 9.25 m.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 300.0


def tree_key(rec: dict) -> str | None:
    """`H-1-121164` -- inventory, borough, emplacement.

    All three parts are needed; see the docstring.  A row missing any of them
    is dropped rather than published under a partial key, because a partial key
    is exactly the collision this exists to avoid.  The source has none.
    """
    parts = [str(rec.get(key) or "").strip() for key in ("INV_TYPE", "ARROND", "EMP_NO")]
    if not all(parts):
        return None
    return "-".join(parts)


def parse_dbh(value) -> float | None:
    """Centimetres to inches, with the implausible ends dropped to null."""
    try:
        cm = float(value)
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM or cm > MAX_DBH_CM:
        return None
    return cm_to_inches(cm)


def parse_plant_date(value) -> date | None:
    """`2004-06-10T00:00:00` -- ISO with a zero time, on 200,682 of the rows."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip()).date()
    except ValueError:
        return None


def parse_coord(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iter_row_chunks():
    """One CKAN datastore page at a time."""
    return iter_datastore_rows(RESOURCE, fields=FIELDS)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        key = tree_key(rec)
        if key is None:
            continue

        tree_id.append(f"mtl-{key}")
        species.append(normalize_species(rec.get("Essence_latin")))
        # The portal publishes the common name in French *and* English; the map
        # renders tree_name as the card's title, so the English column is the
        # one to carry.  Title case there ("Silver Maple"), sentence case here.
        tree_name.append(normalize_tree_name(rec.get("Essence_ang")))
        plant_date.append(parse_plant_date(rec.get("Date_Plantation")))
        latitude.append(parse_coord(rec.get("Latitude")))
        longitude.append(parse_coord(rec.get("Longitude")))
        dbh.append(parse_dbh(rec.get("DHP")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAMTL"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Montreal OpenData")
    # 48 rows sit outside the island, two of them grossly -- latitude 2.64, and
    # a longitude of -42.7 in the middle of the Atlantic.  CITY_BOUNDS drops
    # them here rather than letting them render as dots in the ocean.
    table = validate_coordinates(table, city="Montreal", city_code="CAMTL")
    table = enforce_tree_schema(
        table, city="Montreal", data_source="MONTREAL_OPENDATA"
    )
    emit(table)
