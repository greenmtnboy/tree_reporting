#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Edmonton's municipal tree inventory, from the city's Socrata portal.

Source: "Trees" (`eecg-fc54`) on data.edmonton.ca, 480,744 rows.  Paging, the
freshness watermark and the point-column shapes live in `_socrata_shared`.

Two of Edmonton's columns are not what their names suggest, and both were
worth checking rather than assuming:

* **`species` is the common name.**  "Linden", "Green Ash".  The Latin binomial
  is `species_botanical`, and `genus` duplicates its first word.  Mapping
  `species` onto the canonical `species` key would have keyed the enrichment
  table on English words for every one of Edmonton's 480k trees.
* **`count` is always 1** (checked across all 480,744 rows), so a row is one
  tree and there is no multiplier to apply.

The real work here is the cultivar.  See `split_cultivar`.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from _socrata_shared import SocrataDataset, iter_rows

DATASET = SocrataDataset("data.edmonton.ca", "eecg-fc54", timeout=180)

# The portal publishes WGS84 lat/lon as ordinary columns, so `location` and
# `geometry_point` are redundant weight -- read the columns and skip them.
SELECT = (
    "id,species_botanical,species,diameter_breast_height,planted_date,"
    "latitude,longitude"
)

# Tokens that are a rank marker rather than the start of a cultivar name.
# `x` matters most: "Populus x Northwest" is a hybrid whose *cultivar* is
# Northwest, not a species called "northwest".
_RANK_TOKENS = frozenset(
    {"x", "×", "var", "var.", "ssp", "ssp.", "subsp", "subsp.", "f", "f.", "cv", "cv.",
     "spp", "spp."}
)

# Diameters are centimetres; the column tops out at 256, which is plausible.
MIN_DBH_CM = 1.0


def split_cultivar(value: str | None) -> tuple[str | None, str | None]:
    """Split "Ulmus americana Brandon" into its taxon and its cultivar.

    Edmonton writes the cultivar **unquoted**, as trailing words on the
    botanical name: `Ulmus americana Brandon`, `Picea pungens Blue`,
    `Syringa reticulata Ivory Silk`.  240 of the portal's 358 distinct
    botanical names carry one, covering more than half the city's trees --
    44,843 of them are Brandon elms alone.

    `extract_cultivar` deliberately recognises only the quoted form, because
    quoting is the only thing that separates a cultivar from a trailing note in
    the general case.  Here the source is consistent enough to do better, and
    the rule is **capitalisation**: a cultivar is a proper name and Edmonton
    capitalises it, while an infraspecific epithet is lower case by the
    botanical code.  So `Pinus contorta latifolia` keeps its variety (which
    `sanitize_species` then truncates, correctly) and `Picea pungens Blue`
    gives up its cultivar.

    Without this the cultivar is simply lost: `sanitize_species` truncates to
    species rank and has no way to tell the trailing word was a selection
    somebody propagated.

    Examples:
        "Ulmus americana Brandon"     -> ("Ulmus americana", "Brandon")
        "Syringa reticulata Ivory Silk" -> ("Syringa reticulata", "Ivory Silk")
        "Populus x Northwest"         -> ("Populus", "Northwest")
        "Malus x adstringens Gladiator" -> ("Malus x adstringens", "Gladiator")
        "Pinus contorta latifolia"    -> ("Pinus contorta latifolia", None)
        "Fraxinus pennsylvanica"      -> ("Fraxinus pennsylvanica", None)
    """
    if not value:
        return None, None
    tokens = value.split()
    cut = None
    for index, token in enumerate(tokens):
        if index == 0:
            continue  # the genus is always capitalised
        if token.lower() in _RANK_TOKENS:
            continue
        if token[:1].isupper():
            cut = index
            break
    if cut is None:
        return value, None
    taxon = " ".join(tokens[:cut]).strip()
    cultivar = " ".join(tokens[cut:]).strip()
    return (taxon or None), (cultivar or None)


def parse_dbh(value) -> float | None:
    """Centimetres to inches.  A recorded 0 is unmeasured, not zero-width."""
    try:
        cm = float(value)
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM:
        return None
    return cm_to_inches(cm)


def parse_plant_date(value: str | None) -> date | None:
    """`planted_date` is ISO-8601 text; anything unparseable becomes null."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def iter_row_chunks():
    """One Socrata page at a time; the city is never held whole."""
    return iter_rows(DATASET, select=SELECT)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raw_id = (rec.get("id") or "").strip()
        if not raw_id:
            continue
        taxon, selection = split_cultivar(rec.get("species_botanical"))

        tree_id.append(f"edm-{raw_id}")
        species.append(normalize_species(taxon))
        cultivar.append(selection)
        # `species` is Edmonton's common-name column -- see the module
        # docstring -- and it is inverted for sorting about half the time
        # ("Spruce, Colorado" beside a plain "Linden").
        tree_name.append(normalize_tree_name(rec.get("species")))
        plant_date.append(parse_plant_date(rec.get("planted_date")))
        latitude.append(_as_float(rec.get("latitude")))
        longitude.append(_as_float(rec.get("longitude")))
        dbh.append(parse_dbh(rec.get("diameter_breast_height")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAEDM"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Edmonton OpenData")
    table = validate_coordinates(table, city="Edmonton", city_code="CAEDM")
    table = enforce_tree_schema(
        table, city="Edmonton", data_source="EDMONTON_OPENDATA"
    )
    emit(table)
