#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Kitchener's Municipal Heritage Register.

Source: "Heritage_Properties" (item 806af22b0cf94dacaecece52a4f96ada, owned
by the City's `KitchenerGIS` account, updated daily), published under the
Open Government Licence - The Corporation of the City of Kitchener, version
1.0, named in the item's own licence field
(https://www.kitchener.ca/council-and-city-administration/data-and-maps/open-data-licence/).  One point per
property on the register, 1,359 rows on 2026-11-18, every one `STATUS =
'ACTIVE'`.

**The whole layer is the register, so every row is published.**  `CATEGORY`
is PART V (1,027 -- inside one of the four Heritage Conservation Districts),
LISTED (175 -- listed non-designated under s.27 of the Ontario Heritage Act),
PART IV (149 -- individually designated by by-law), PART IV AND V (7) and
INTENT TO DESIGNATE (1).  `property_type` is that category in words, with the
district named for a Part V property.

**A registered property is rarely named.**  `COMMON_NAME` is filled for 145
rows ("Williamsburg School", "Karl Mueller House"); the rest are houses known
by their address (`HERITAGE_ADDRESS`), which is the fallback.  A row with
neither (37 on 2026-11-18) is skipped.  The register writes both in capitals;
they are published in capitalised words.

**`YEAR_BUILT`** is the construction year (filled for 520 rows; a `c` in
`YEAR_BUILT_QUALIFIER` marks an estimate, not kept).

**The id is `HERITAGEREGISTERID`** (`kit-hp-{id}`), the register's own number:
non-null on every row and checked unique here, so a duplicate fails the run
rather than publishing two landmarks under one id.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://services1.arcgis.com/qAo1OsXi67t7XgmS/arcgis/rest/services/"
    "Heritage_Properties/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = (
    "OBJECTID,HERITAGEREGISTERID,HERITAGE_ADDRESS,COMMON_NAME,YEAR_BUILT,"
    "CATEGORY,HERITAGE_DISTRICT,STATUS"
)
WHERE = "STATUS = 'ACTIVE'"

CATEGORY_LABELS = {
    "PART IV": "Designated, Part IV",
    "PART V": "Designated, Part V",
    "PART IV AND V": "Designated, Part IV and Part V",
    "LISTED": "Listed (non-designated)",
    "INTENT TO DESIGNATE": "Notice of intention to designate",
}

SMALL_WORDS = frozenset({"and", "at", "for", "in", "of", "on", "the", "to"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def display(value) -> str | None:
    """`FORMER P.U.C. BUILDING` -> `Former P.U.C. Building`."""
    words = (clean(value) or "").split()
    if not words:
        return None
    out = []
    for i, word in enumerate(words):
        if "." in word.rstrip("."):
            out.append(word)  # an initialism written with stops (P.U.C.)
        elif i and word.lower() in SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(capitalise(word))
    return " ".join(out)


def capitalise(word: str) -> str:
    """`(ONTARIO` -> `(Ontario`, `ST.` -> `St.`: the first letter, wherever it is."""
    lower = word.lower()
    for i, char in enumerate(lower):
        if char.isalpha():
            return lower[:i] + char.upper() + lower[i + 1 :]
    return lower


def district_name(value) -> str | None:
    text = display(value)
    if text is None:
        return None
    suffix = " Heritage Conservation District"
    return text[: -len(suffix)] + " HCD" if text.endswith(suffix) else text


def property_type_of(rec: dict) -> str | None:
    category = clean(rec.get("CATEGORY"))
    if category is None:
        return None
    label = CATEGORY_LABELS.get(category, display(category))
    district = district_name(rec.get("HERITAGE_DISTRICT"))
    if "Part V" in label and district:
        label = f"{label} ({district})"
    return label


def year_of(value) -> int | None:
    if value is None:
        return None
    year = int(value)
    return year if 1500 <= year <= 2100 else None


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
        street = display(rec.get("HERITAGE_ADDRESS"))
        label = display(rec.get("COMMON_NAME")) or street
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        register_id = rec.get("HERITAGEREGISTERID")
        if label is None or wkt is None or register_id is None:
            continue
        key = f"kit-hp-{int(register_id)}"
        if key in seen:
            raise RuntimeError(f"Kitchener heritage register repeats {key}")
        seen.add(key)
        landmark_id.append(key)
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(year_of(rec.get("YEAR_BUILT")))
        property_type.append(property_type_of(rec))

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAKIT"] * n, type=pa.string()),
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
        f"Kitchener landmarks: {table.num_rows:,} of {len(features):,} register rows",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Kitchener heritage register produced no landmarks")
    emit(table)
