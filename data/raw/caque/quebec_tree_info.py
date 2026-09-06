#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Quebec City's tree inventory, from Donnees Quebec.

Source: "Arbres repertories" on `www.donneesquebec.ca/recherche`, resource
`13a51853`, 158,127 rows.  Paging and the freshness watermark live in
`_ckan_shared`.

Note the host carries a **path**: Donnees Quebec serves its CKAN API under
`/recherche`, not at the domain root, so `CkanResource` is given
`www.donneesquebec.ca/recherche` and builds `.../recherche/api/3/action/...`
from it.  It is a provincial aggregator rather than a city portal, which is
also why `datastore_search_sql` there rejects a `CAST` with HTTP 403 -- another
reason `_ckan_shared` reads rows only through the paged search.

**`DIAMETRE` is not always a diameter at breast height**, and this is the one
field mapping here that is a judgement rather than a rename.  `POSITION_MESURE`
says where the trunk was measured, and only `DHP` (diametre a hauteur de
poitrine) is the canonical column's measurement:

    DHP   142,859 rows   median 15 cm    -- breast height, what we want
    DHS     8,527 rows   median  8 cm    -- diametre a hauteur de souche, at
                                            the stump: a different height on a
                                            different part of the tree
    M       6,654 rows   median 35 cm    -- a third convention, systematically
                                            larger again
    (none)     68 rows   median 150 cm   -- unlabelled and implausible

Publishing all four as `diameter_at_breast_height` would put 15,268 rows of
some other measurement into every size chart and every dbh filter, biased in
both directions.  They are kept as trees with no recorded diameter, which is
what they are: the row still carries a species and a location.

`ID` is a clean per-tree key -- 158,127 distinct across 158,127 rows, none
null, checked over the whole table.

**`tree_name` is deliberately left null.**  The only common name this portal
publishes is `NOM_FRANCAIS`, and `tree_name` is what the map renders as the
tree card's title -- behind the enrichment table's English common name, which
wins when the species has a row.  So a French name would surface only for an
unenriched taxon, on an otherwise English card, in place of a fallback chain
that already ends at the scientific name.  Montreal carries a common name here
because its portal publishes an English column alongside the French one.
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
    stream_to_table,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "www.donneesquebec.ca/recherche",
    "13a51853-a5b5-4add-8791-02ccba5c1be7",
    timeout=180,
)

FIELDS = "ID,NOM_LATIN,DIAMETRE,POSITION_MESURE,DATE_PLANTE,LATITUDE,LONGITUDE"

# The measurement convention that *is* a diameter at breast height.  See the
# docstring for the three that are not.
DBH_POSITION = "DHP"

# 16 rows record a diameter of 0, which is an unmeasured tree rather than a
# tree of no width; 2 record more than 3 m.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 300.0


def parse_dbh(rec: dict) -> float | None:
    """Centimetres to inches -- but only where the source measured at DHP."""
    if (rec.get("POSITION_MESURE") or "").strip().upper() != DBH_POSITION:
        return None
    try:
        cm = float(rec.get("DIAMETRE"))
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM or cm > MAX_DBH_CM:
        return None
    return cm_to_inches(cm)


def parse_plant_date(value) -> date | None:
    """ISO, with or without a time part; null on 65,148 of the rows."""
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
        raw_id = str(rec.get("ID") or "").strip()
        if not raw_id:
            continue

        tree_id.append(f"que-{raw_id}")
        species.append(normalize_species(rec.get("NOM_LATIN")))
        # Null by design -- see the docstring.  Still a typed column.
        tree_name.append(None)
        plant_date.append(parse_plant_date(rec.get("DATE_PLANTE")))
        latitude.append(parse_coord(rec.get("LATITUDE")))
        longitude.append(parse_coord(rec.get("LONGITUDE")))
        dbh.append(parse_dbh(rec))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAQUE"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Quebec City OpenData")
    table = validate_coordinates(table, city="Quebec City", city_code="CAQUE")
    table = enforce_tree_schema(
        table, city="Quebec City", data_source="QUEBEC_OPENDATA"
    )
    emit(table)
