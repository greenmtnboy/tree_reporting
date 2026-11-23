#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Whitby's (Ontario) boulevard tree inventory, from the Town of Whitby.

Source: "Trees" (ArcGIS item 5be6efcf4f264639874bf07009f155d2, owner
`WhitbyGIS`, catalogued on the Town's open data site Whitby GeoHub,
geohub-whitby.hub.arcgis.com), served as the hosted layer
`Trees/FeatureServer/0`, 53,079 rows on 2026-11-23.  "The boulevard trees in
the Town of Whitby ... does not include information regarding trees in parks
or open spaces"; updated by Town staff through the year, with a consultant's
full re-inventory every seven years.

LICENCE: The Corporation of the Town of Whitby Open Government License v1.0
(based on OGL-Canada 2.0), linked from the item's licence field:
https://whitby.maps.arcgis.com/sharing/rest/content/items/223810efc31c40b3aff99dd74f809a97/data
-- "a worldwide, royalty-free, perpetual, non-exclusive licence to use the
Information, including for commercial purposes", free to "copy, modify,
publish, translate, adapt, distribute", attribution "Contains information
licensed under the Open Government Licence - The Corporation of the Town of
Whitby."

**The id is `FACILITYID`** (`TA18263`, "the unique ID for the asset which
identifies it in the Town of Whitby Databases"), checked over the whole layer
on 2026-11-23: 53,079 rows, 53,070 distinct values, one null.  The layer has
no GlobalID.  The nine repeated ids are the same tree twice -- eight Blenheim
Circle pears at OBJECTIDs 52587-52616 copied to 52700-52707 with identical
attributes and coordinates, and one maple a metre apart -- so the first
(lowest OBJECTID) is kept and the copy dropped, with a count in the log; the
row with no id is dropped.  `OBJECTID` (1..53,094, 15 gaps) is not used.

**Species is `LATIN_NAME`**, in capitals with cultivars typed unquoted after
the epithet (`ACER PLATANOIDES CRIMSON KING`, `PYRUS CALLERYANA GLENS FORM`)
and sometimes quoted (`ACER X FREEMANNI 'JEFFERSRED'`).  `latin_name` quotes
the trailing words as the cultivar so the schema keeps them on the row rather
than truncating them away; a rank word (`SSP`, `VAR`, `F.`) and its name stay
with the taxon, and `SP`/`SPP.` publish the genus.  A few names are not a
species plus a cultivar and are mapped in `WHOLE_NAMES` (`ULMUS X PIONEER` is
the elm cultivar 'Pioneer', `MALUS ROYALTY` the crabapple 'Royalty').
`UNIDENTIFIED DEAD` is a dead tree and publishes as `Dead`.  Misspellings
(`GLEDITISIA`, `PLANTANUS`, `FREEMANNI`, ...) are left for the shared table.

**443 rows have no `LATIN_NAME`**, only `COMMON_NAME` (`HACKBERRY`,
`SERVICEBERRY SHADBLOW`).  The Town fills `COMMON_NAME` from "a spread sheet of
accepted common names based on the Latin species description", so the layer
itself pairs every common name with its Latin one; that pairing is read with
one grouped-statistics request and a common name resolves to the Latin name
the layer gives it on at least 80% of its named rows.  A common name the
layer never pairs falls back to the shared English table
(`shared.species.english`), and then to `Unknown`.

**`DBH` is centimetres**, whatever the item's metadata PDF says ("measured in
millimetres"): the median Norway maple is 32 and the largest value 195, which
as millimetres would make every tree in Whitby a sapling.  **`CROWN_RESERVE`
(alias "CROWN DIAMETER") is the crown spread in metres** -- the metadata says
so, and it grows with DBH as a crown does (median 2 m under 10 cm DBH, 10 m at
30-40 cm, 20 m at a metre).  `HEIGHT` is a five-band class and `AGE_CLASS` an
age band; neither has a column.  No planting date is published.

The layer has no status column: no vacant site, stump or removal is recorded
in it.
"""

import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import species_from_common_name
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services5.arcgis.com/ATdLnvuMRJk8AGkQ/arcgis/rest/services/"
    "Trees/FeatureServer/0",
    timeout=300,
)

OUT_FIELDS = "OBJECTID,FACILITYID,COMMON_NAME,LATIN_NAME,DBH,CROWN_RESERVE"

# A common name resolves through the layer's own pairing only when this share
# of its Latin-named rows agree.
PAIRING_SHARE = 0.8

# Whole LATIN_NAME values that are not "species + cultivar" (uppercased,
# whitespace collapsed) -> the name to publish.
WHOLE_NAMES = {
    "ULMUS X PIONEER": "Ulmus 'Pioneer'",
    "ULMUS X HOMESTEAD": "Ulmus 'Homestead'",
    "ULMUS HYBRID": "Ulmus",
    "MALUS ROYALTY": "Malus 'Royalty'",
    "UNIDENTIFIED DEAD": "Dead",
}

_RANK_WORDS = {"SSP", "SSP.", "SUBSP", "SUBSP.", "VAR", "VAR.", "F", "F.", "CV", "CV."}
_GENUS_ONLY = {"SP", "SP.", "SPP", "SPP."}
_QUOTES = ("'", '"', "‘", "’")

DROPPED: dict[str, int] = {"no FACILITYID": 0, "repeated FACILITYID (copy of a kept row)": 0}
CLEANED: dict[str, int] = {
    "cultivar quoted": 0,
    "resolved from layer's common-name pairing": 0,
    "resolved from shared English table": 0,
    "no name at all": 0,
}
UNRESOLVED_COMMON: Counter = Counter()
SEEN_IDS: set[str] = set()


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _cultivar(words: list[str]) -> str:
    return " ".join(w.capitalize() for w in words)


def latin_name(raw) -> str | None:
    """Whitby's LATIN_NAME as a species value with any cultivar quoted."""
    text = _clean(raw)
    if not text:
        return None
    for q in _QUOTES[1:]:
        text = text.replace(q, "'")
    whole = WHOLE_NAMES.get(text.upper())
    if whole:
        return whole
    if "'" in text:
        head, _, rest = text.partition("'")
        cultivar = rest.split("'")[0].strip()
        words = head.split()
        base = " ".join([words[0].capitalize()] + [w.lower() for w in words[1:]])
        return f"{base} '{_cultivar(cultivar.split())}'" if cultivar else base
    words = text.split()
    genus = words[0].capitalize()
    if len(words) == 1:
        return genus
    keep = [genus]
    i = 1
    if words[i].upper() in _GENUS_ONLY:
        return genus
    if words[i].upper() == "X" and i + 1 < len(words):
        keep.append("x")
        i += 1
    keep.append(words[i].lower())
    i += 1
    while i + 1 < len(words) and words[i].upper() in _RANK_WORDS:
        keep.extend([words[i].lower(), words[i + 1].lower()])
        i += 2
    rest = words[i:]
    if not rest:
        return " ".join(keep)
    CLEANED["cultivar quoted"] += 1
    return " ".join(keep) + f" '{_cultivar(rest)}'"


def common_key(value) -> str:
    return _clean(value).upper()


def layer_pairing() -> dict[str, str]:
    """COMMON_NAME -> the LATIN_NAME the layer itself gives it, by grouped count."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "COMMON_NAME IS NOT NULL AND LATIN_NAME IS NOT NULL",
            "groupByFieldsForStatistics": "COMMON_NAME,LATIN_NAME",
            "outStatistics": (
                '[{"statisticType":"count","onStatisticField":"OBJECTID",'
                '"outStatisticFieldName":"n"}]'
            ),
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    features = payload.get("features")
    if not features:
        raise RuntimeError("Whitby: the COMMON_NAME/LATIN_NAME pairing query returned nothing")
    by_common: dict[str, Counter] = defaultdict(Counter)
    for feature in features:
        rec = feature.get("attributes") or {}
        species = latin_name(rec.get("LATIN_NAME"))
        if species is None:
            continue
        # Compare at species rank: 'MAPLE SUGAR' is both ACER SACCHARUM and
        # ACER SACCHARUM SSP SACCHARUM, which is one tree.
        words = species.split("'")[0].split()
        taxon = " ".join(words[:3] if len(words) > 1 and words[1] == "x" else words[:2])
        by_common[common_key(rec.get("COMMON_NAME"))][taxon] += int(rec.get("n") or 0)
    pairing: dict[str, str] = {}
    for common, counts in by_common.items():
        taxon, n = counts.most_common(1)[0]
        if n >= PAIRING_SHARE * sum(counts.values()):
            pairing[common] = taxon
    return pairing


PAIRING = layer_pairing()


def scientific_name(rec: dict) -> str | None:
    latin = latin_name(rec.get("LATIN_NAME"))
    if latin:
        return latin
    common = common_key(rec.get("COMMON_NAME"))
    if not common:
        CLEANED["no name at all"] += 1
        return None
    if common in PAIRING:
        CLEANED["resolved from layer's common-name pairing"] += 1
        return PAIRING[common]
    resolved = species_from_common_name(common)
    if resolved:
        CLEANED["resolved from shared English table"] += 1
        return resolved
    UNRESOLVED_COMMON[common] += 1
    return None


def iter_row_chunks():
    """One ArcGIS page at a time, in OBJECTID order, so a repeated id keeps its first row."""
    return iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


def _positive(value) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = _clean(rec.get("FACILITYID"))
        if not raw_id:
            DROPPED["no FACILITYID"] += 1
            continue
        tid = f"whitby-{raw_id}"
        if tid in SEEN_IDS:
            DROPPED["repeated FACILITYID (copy of a kept row)"] += 1
            continue
        SEEN_IDS.add(tid)

        tree_id.append(tid)
        species.append(scientific_name(rec))
        plant_date.append(None)
        lat, lon = esri_point(feature.get("geometry"))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(_positive(rec.get("DBH"))))
        crown.append(_positive(rec.get("CROWN_RESERVE")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAWHI"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Whitby OpenData")
    print(f"Whitby: dropped {DROPPED}", file=sys.stderr)
    print(f"Whitby: species cleanup {CLEANED}", file=sys.stderr)
    if UNRESOLVED_COMMON:
        print(
            f"Whitby: common names with no Latin name: {dict(UNRESOLVED_COMMON.most_common())}",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city="Whitby", city_code="CAWHI")
    table = enforce_tree_schema(table, city="Whitby", data_source="WHITBY_OPENDATA")
    emit(table)
