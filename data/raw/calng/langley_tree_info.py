#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Langley Township's tree inventory, from the Township of Langley.

Source: "Tree Inventory" (item 07ed48e3e633476aba03b1a800d616af), a hosted
feature layer on the Township's ArcGIS Online organisation (frpHL0Fv8koQRVWY),
owned by its `OpenDataFME` publishing account and shared into the "Township
of Langley Open Data" group: Township-managed street, park, greenway,
facility, memorial, significant and heritage trees.  Licence: the item links
the Township's Open Government License - Township of Langley
(https://www.tol.ca/en/connect/open-data-licence.aspx, based on OGL-Canada
2.0: copy, modify, publish, distribute, including commercially, with
attribution).  Attribution: "Contains information licensed under the Open
Government License - Township of Langley."

**The id is the Township's `TreeID`** (`langley-{TreeID}`), not the GlobalID.
The layer is reloaded whole by FME: `OBJECTID` runs 1..30,412 without a gap
and in exactly `TreeID` order, including the rows re-measured in November
2026, so the GlobalIDs (and OBJECTIDs) are minted afresh on every reload
while `TreeID` is the asset system's own key.  It is numeric and non-null on
every row; 445 values appear twice, and every one of those pairs is the same
tree loaded twice (identical in every attribute and at the same point,
checked 2026-11-22), so the second copy is dropped.  A repeated `TreeID`
whose copies differ would be dropped whole, with a count.

**Species is `TreeName`**, "Latin binomial 'Cultivar' (Common Name)", free
text without a domain.  The common-name parenthetical is cut here (one row
spelling has lost its opening bracket), the cultivar is left quoted for the
schema to move to `cultivar`, the publisher's UTF-8-read-as-CP437 quotes
(`ΓÇÿ` / `ΓÇÖ`) are repaired, and the trade notes `cvr.` / `cvrs` (cultivar
unnamed) are dropped.  Misspelled genera (`Ulmas`, `Cladastis`) belong in the
shared misspelling table.

**DBH is centimetres** (measured on 1,589 rows, all in 2026: median 5, 95th
percentile 14.5; trees planted in the last five years median 4, those planted
5-9 years ago median 12, a 125-year-old heritage tree 177), converted to
inches.  Zero is "not measured" and the schema nulls it.

**Plant date is the later of `ReplantedDate` and `PlantedDate`**: a replant
is the tree standing there now.  Both are stamped at noon UTC, so the UTC date
is the Pacific date.  Two `PlantedDate` values are bulk-entry stamps, not
plantings, and are nulled: 1999-11-30 (1,897 rows) and 1994-11-29 (1,151),
each spread over the whole `TreeID` range, every tree type and native park
conifers alongside street stock.  1 January is year precision and kept.

The layer has no status column: every row is a tree (no vacant sites, stumps
or removals are published).
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services5.arcgis.com/frpHL0Fv8koQRVWY/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,TreeID,TreeName,DBH,PlantedDate,ReplantedDate"

# Bulk-entry stamps in `PlantedDate`, not planting dates (see the docstring).
PLACEHOLDER_PLANT_DATES = frozenset({date(1999, 11, 30), date(1994, 11, 29)})

# The publisher's curly quotes, UTF-8 bytes read as CP437 somewhere upstream.
MOJIBAKE = {"ΓÇÿ": "‘", "ΓÇÖ": "’"}

_PARENTHETICAL = re.compile(r"\s*\(.*$")
_CULTIVAR_NOTE = re.compile(r"\s+(?:cvrs?|cv)\.?(?=\s|$)", re.IGNORECASE)
_HYBRID_DOT = re.compile(r"\s+x\.\s+")
# `Betula utilis var. 'Jacquemontii'`: a rank with no epithet before a cultivar.
_EMPTY_RANK = re.compile(r"\s+var\.(?=\s+['‘])")

DROPPED: dict[str, int] = {"duplicate copy": 0, "conflicting id": 0, "no id": 0}
PLANT_DATE_NULLED = {"placeholder": 0}

# TreeID -> the row's content, to tell a twice-loaded tree from a reused id.
_SEEN: dict[str, tuple] = {}
_CONFLICTS: set[str] = set()


def scientific_name(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    for bad, good in MOJIBAKE.items():
        text = text.replace(bad, good)
    text = " ".join(text.split())
    if "(" in text:
        text = _PARENTHETICAL.sub("", text)
    elif text.endswith(")"):
        # "Fagus sylvatica 'Asplenifolia'  Fern leaf Beech)": cut after the
        # cultivar's closing quote.
        close = max(text.rfind("'"), text.rfind("’"))
        text = text[: close + 1] if close > 0 else text.rstrip(")")
    text = _CULTIVAR_NOTE.sub("", text)
    text = _HYBRID_DOT.sub(" x ", text)
    text = _EMPTY_RANK.sub("", text)
    text = text.strip()
    return text or None


def plant_date(rec: dict) -> date | None:
    planted = esri_ms_to_date(rec.get("PlantedDate"))
    if planted in PLACEHOLDER_PLANT_DATES:
        PLANT_DATE_NULLED["placeholder"] += 1
        planted = None
    replanted = esri_ms_to_date(rec.get("ReplantedDate"))
    dates = [d for d in (planted, replanted) if d is not None]
    return max(dates) if dates else None


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    tree_id = str(rec.get("TreeID") or "").strip()
    if not tree_id:
        DROPPED["no id"] += 1
        return False
    geometry = feature.get("geometry") or {}
    content = (
        tuple(sorted((k, v) for k, v in rec.items() if k != "OBJECTID")),
        geometry.get("x"),
        geometry.get("y"),
    )
    previous = _SEEN.get(tree_id)
    if previous is None:
        _SEEN[tree_id] = content
        return True
    if previous == content:
        DROPPED["duplicate copy"] += 1
        return False
    _CONFLICTS.add(tree_id)
    return True


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"langley-{str(rec['TreeID']).strip()}")
        species.append(scientific_name(rec.get("TreeName")))
        planted.append(plant_date(rec))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(rec.get("DBH")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CALNG"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def iter_pages():
    yield from iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_pages(), transform, keep=keep, label="Langley Township OpenData"
    )
    if _CONFLICTS:
        conflicting = pa.array(sorted(f"langley-{t}" for t in _CONFLICTS))
        mask = pc.is_in(table["tree_id"], value_set=conflicting)
        DROPPED["conflicting id"] = pc.sum(mask).as_py() or 0
        table = table.filter(pc.invert(mask))
    missing = table["latitude"].null_count
    if missing:
        table = table.filter(pc.is_valid(table["latitude"]))
    print(
        f"Langley Township: dropped {DROPPED['duplicate copy']} duplicate "
        f"copy/copies of a tree, {DROPPED['conflicting id']} row(s) under a "
        f"TreeID whose copies differ, {DROPPED['no id']} row(s) without a "
        f"TreeID and {missing} without a geometry; nulled "
        f"{PLANT_DATE_NULLED['placeholder']} placeholder planting date(s)",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Langley Township", city_code="CALNG")
    table = enforce_tree_schema(
        table, city="Langley Township", data_source="LANGLEY_OPENDATA"
    )
    emit(table)
