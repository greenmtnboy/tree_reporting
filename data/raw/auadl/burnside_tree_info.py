#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""City of Burnside street trees, from data.sa.gov.au.

Source: "Burnside Street Trees" (`burnside-street-trees`) on data.sa.gov.au's
CKAN, published by the City of Burnside under Creative Commons Attribution
(the package's `license_url` is CC BY 4.0).  One GeoJSON file resource,
`6d1912aa` (`burnsidetrees2019.geojson`, 10.7 MB), with no datastore: 38,599
points in CRS84, uploaded 2019-09-02 and not republished since.  The council's
own "Urban Forest Interactive" map (trees.burnside.sa.gov.au) is a live app
over its GIS, not an open dataset, and no newer export is published; this file
is the official inventory, and a 2019 snapshot.  The CSV resource
(`4b538707`, datastore-backed) carries the same rows.

Burnside is the eastern council between Norwood and the Adelaide Hills
(-34.968 to -34.913, 138.616 to 138.697); it is inside AUADL's territory once
that is widened, so it is a partition of Adelaide rather than a city.

Field choices:

  * **`tree_id` <- `TreeID`**, the council's tree number (1 to 46,721),
    non-null on every feature and distinct on all but one value: `39973`
    names two young Jacarandas 1.9 km apart.  Both are dropped with a logged
    count (Glen Eira's rule): either could own the id, and an id that moves
    between trees from one release to the next is worse than neither.
    Prefixed `bur-`.  ~1,700 trees share a point
    with another (mostly pairs, a few clusters of 4-5 young plantings);
    they carry distinct ids and are kept.
  * **`BotanicalN` is the scientific name**; `CommonName` is not mapped.
    `Stump` (80) and `Vacant` (19) are sites, not trees, and are dropped
    with a logged count; blank and the string `null` publish as `Unknown`.
    Cultivars are mostly quoted already (`Prunus cerasifera 'Nigra'`) and
    the schema parses them; `SOURCE_SPELLINGS` quotes the ones written
    with asterisks or none (`*belladonna*`, `Fraxinus Raywood`) and drops
    `'Grafted'`, a nursery note.
  * **`diameter_at_breast_height` <- `Circumfere`**, a trunk circumference
    band (`less than 2m`, `greater than 2m and less than 3m`, `greater than
    3m`, with spelling variants), published as the band midpoint converted
    with `circumference_cm_to_dbh_inches`; the open top band takes 3.5 m
    (Adelaide's and Denver's rule).  89% of trees are in the bottom band,
    so this is coarse: every one of them publishes 1 m of circumference
    (12.5 in).  The bare `2m` (58 rows) names no band and is null, as is
    `null`; another unknown label counts as unreadable and more than 1%
    unreadable refuses the build.
  * `TreeHeight` is a height band and has no canonical column; no crown
    spread or planting date is published.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, read_geojson_features
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    validate_coordinates,
)

# Kept in step with burnside_update_time.py.
RESOURCE = CkanResource(
    "data.sa.gov.au/data", "6d1912aa-4775-4f5e-b00d-18456ad872a5", timeout=300
)

# Circumference band (lower case, whitespace removed) -> midpoint in cm.
# None marks a label that is known and carries no measurement.
CIRCUMFERENCE_BAND_CM: dict[str, float | None] = {
    "lessthan2m": 100.0,
    "greaterthan2mandlessthan3m": 250.0,
    "greaterthan3m": 350.0,
    "2m": None,
    "null": None,
}
MAX_UNPARSED_FRACTION = 0.01

# Exact source values (whitespace collapsed) the generic path would misread.
SOURCE_SPELLINGS: dict[str, str] = {
    "Fraxinus Raywood": "Fraxinus angustifolia 'Raywood'",
    "Callistemon harkness": "Callistemon 'Harkness'",
    "lagerstroemia indica x l. fauriei 'sioux'": "Lagerstroemia indica x fauriei 'Sioux'",
    "Grevillea Hybrid": "Grevillea",
    "Corymbia ficifolia 'Grafted'": "Corymbia ficifolia",
}

UNNAMED = frozenset({"null", "unknown sp.", "unknown"})

# `*Chanticleer*` -> `'Chanticleer'`: the register's other cultivar quote.
_ASTERISK_QUOTE = re.compile(r"\*([^*]+)\*")

_counts: Counter = Counter()


def clean(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def botanical(value) -> str | None:
    text = clean(value)
    if not text or text.lower() in UNNAMED:
        return None
    text = SOURCE_SPELLINGS.get(text, text)
    return _ASTERISK_QUOTE.sub(r"'\1'", text)


def parse_circumference(value) -> float | None:
    label = "".join(clean(value).split()).lower()
    if not label:
        return None
    if label not in CIRCUMFERENCE_BAND_CM:
        _counts["unreadable circumference"] += 1
        return None
    return circumference_cm_to_dbh_inches(CIRCUMFERENCE_BAND_CM[label])


def tree_number(feature: dict) -> int | None:
    value = (feature.get("properties") or {}).get("TreeID")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def standing_trees(features: list[dict]) -> list[dict]:
    """Identified trees: sites dropped, and every tree whose id repeats."""
    trees = []
    for feature in features:
        name = clean((feature.get("properties") or {}).get("BotanicalN"))
        if is_not_a_tree(name):
            _counts[f"site ({name})"] += 1
        elif tree_number(feature) is None:
            _counts["no TreeID"] += 1
        else:
            trees.append(feature)

    by_id: dict[int, list[dict]] = {}
    for feature in trees:
        by_id.setdefault(tree_number(feature), []).append(feature)
    kept = []
    for copies in by_id.values():
        if len(copies) == 1:
            kept.append(copies[0])
        else:
            _counts["sharing a repeated TreeID"] += len(copies)
    dropped = ", ".join(f"{v} {k}" for k, v in sorted(_counts.items()))
    print(
        f"Burnside: read {len(features)} feature(s), kept {len(kept)}"
        + (f"; dropped {dropped}" if dropped else ""),
        file=sys.stderr,
    )
    return kept


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        tree_id.append(f"bur-{tree_number(feature)}")
        species.append(normalize_species(botanical(props.get("BotanicalN"))))
        longitude.append(float(coords[0]) if coords[0] is not None else None)
        latitude.append(float(coords[1]) if coords[1] is not None else None)
        dbh.append(parse_circumference(props.get("Circumfere")))

    n = len(tree_id)
    plant_date: list[date | None] = [None] * n
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUADL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


def check_parse_failures(rows: int) -> None:
    count = _counts["unreadable circumference"]
    if count:
        print(f"Burnside: {count} unreadable circumference value(s)", file=sys.stderr)
    if count / (rows or 1) > MAX_UNPARSED_FRACTION:
        raise RuntimeError(
            f"Burnside: {count} of {rows} trees have an unreadable circumference; "
            "the source format has changed"
        )


if __name__ == "__main__":
    features = read_geojson_features(RESOURCE)
    if not features:
        raise RuntimeError("Burnside: the GeoJSON resource has no features")
    trees = standing_trees(features)
    table = transform(trees)
    check_parse_failures(len(trees))
    table = validate_coordinates(table, city="Adelaide", city_code="AUADL")
    table = enforce_tree_schema(table, city="Adelaide", data_source="BURNSIDE")
    emit(table)
