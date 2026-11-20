#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Waterloo's (Ontario) Municipal Heritage Register.

Source: "Heritage Buildings" (item 3822d7856d034f07a14e67e25c6ced4b, owned by
the City's `OpenData_Waterloo` account), its point layer "Heritage Buildings
(Points)".  The item carries no licence field of its own; the portal's
licence page (https://data.waterloo.ca/pages/open-data-licence) puts the
site's data under the Waterloo Open Data User Licence.  320 rows on
2026-11-19; the layer was last edited 2018-02-13.

**`STATUS` is D (designated, 173), L (listed on the municipal register,
non-designated, 142) or X (de-listed, 5).**  De-listed rows are dropped, as
is any row whose `NAME` says it was demolished (`** DEMOLISHED`).  `ACT` is
the Ontario Heritage Act part a designation is under (IV, V or both); a
designated row with no `ACT` is published as plain "Designated".

**A registered property is rarely named.**  `NAME` is filled for 59 rows
("Hahn House", "Seagram Bonded Warehouse"); the rest are known by their
address (`ADDRESS`, unique over the layer), which is the fallback.  The
register writes addresses in capitals; they are published in capitalised
words.

**`CONST_DATE`** is text: a year ("1896"), sometimes qualified ("pre-1855");
the first four-digit year in it is `year_built`.

**The id is `OBJECTID`** (`waterloo-hb-{oid}`).  The register's own `ID`
is null on 85 rows and repeats once (two Seagram buildings share 30), so it
cannot be the grain; the layer has no GlobalID.  The OBJECTID is stable as
long as the City edits the layer rather than overwriting it, which is all it
has done since 2018.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://services.arcgis.com/ZpeBVw5o1kjit7LT/arcgis/rest/services/"
    "HeritageBuildings/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,ID,STATUS,ACT,NAME,ADDRESS,CONST_DATE"
WHERE = "STATUS IN ('D', 'L')"

ACT_LABELS = {
    "IV": "Designated, Part IV",
    "V": "Designated, Part V",
    "IV,V": "Designated, Part IV and Part V",
}

SMALL_WORDS = frozenset({"and", "at", "for", "in", "of", "on", "the", "to"})
YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def capitalise(word: str) -> str:
    """`(ONTARIO` -> `(Ontario`, `ST.` -> `St.`: the first letter, wherever it is."""
    lower = word.lower()
    for i, char in enumerate(lower):
        if char.isalpha():
            return lower[:i] + char.upper() + lower[i + 1 :]
    return lower


def display_address(value) -> str | None:
    """`29 CENTRAL ST` -> `29 Central St`."""
    words = (clean(value) or "").split()
    if not words:
        return None
    return " ".join(
        word.lower() if i and word.lower() in SMALL_WORDS else capitalise(word)
        for i, word in enumerate(words)
    )


def property_type_of(rec: dict) -> str | None:
    status = clean(rec.get("STATUS"))
    if status == "L":
        return "Listed (non-designated)"
    if status == "D":
        act = (clean(rec.get("ACT")) or "").replace(" ", "")
        return ACT_LABELS.get(act, "Designated")
    return None


def year_of(value) -> int | None:
    match = YEAR.search(clean(value) or "")
    return int(match.group(1)) if match else None


def is_demolished(rec: dict) -> bool:
    return "demolish" in (clean(rec.get("NAME")) or "").lower()


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    property_type: list[str | None] = []
    seen: set[str] = set()

    for feature in features:
        rec = feature.get("attributes") or {}
        if is_demolished(rec):
            continue
        street = display_address(rec.get("ADDRESS"))
        label = clean(rec.get("NAME")) or street
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        oid = rec.get("OBJECTID")
        if label is None or wkt is None or oid is None:
            continue
        key = f"waterloo-hb-{int(oid)}"
        if key in seen:
            raise RuntimeError(f"Waterloo heritage register repeats {key}")
        seen.add(key)
        landmark_id.append(key)
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(year_of(rec.get("CONST_DATE")))
        property_type.append(property_type_of(rec))

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAWAT"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, where=WHERE, return_geometry=True
    ):
        features.extend(page)
    table = transform(features)
    print(
        f"Waterloo landmarks: {table.num_rows:,} of {len(features):,} register rows",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Waterloo heritage register produced no landmarks")
    emit(table)
