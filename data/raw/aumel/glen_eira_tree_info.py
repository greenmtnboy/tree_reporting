#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Glen Eira City Council street, park and kindergarten trees, from data.gov.au.

Source: "Park and Street Trees" (`park-and-street-trees`) on data.gov.au's
CKAN, published by Glen Eira City Council under CC BY 2.5 AU.  One GeoJSON
file resource, `85c2d44c` (`streetandparktrees.json`, 22 MB), with no
datastore: 59,259 points in CRS84, last modified 2023-10-12.  Glen Eira
(Caulfield, Elsternwick, Bentleigh, Carnegie, Murrumbeena) is south-east of
the City of Melbourne and shares no boundary with it; it is inside AUMEL's
territory, so it is a partition of Melbourne rather than a city.

**`feature_id` is the per-tree id** -- the council's asset number (`TS...`
street, `TP...` park, `TK...` kindergarten).  Over the whole file it is
non-blank everywhere and distinct on all but three values, each on two
different trees kilometres apart (`TS21-0622`, `TS21-0623`, `TS21-1760`:
data-entry collisions in the 2021 planting batch).  Those six rows are dropped
with a logged count: either one could own the id, and an id that moves between
two trees from one release to the next is worse than neither.  `ogr_fid`/`id`
are unique but are the export's row numbers, so a re-export renumbers them.
Prefixed `gle-`.

* `Botanical` is the scientific name; `Common_Name` is not mapped.  One
  value welds a form onto the epithet, `Eucalyptus leucoxylon-Dwarf` (434
  rows, the 'Euky Dwarf' selection): the `-Dwarf` suffix is cut so the row
  joins its species.
* `DBH` is centimetres (median 20).  2,823 zeros are the survey's "not
  measured" and are nulled by the schema, as are a handful of decimal slips
  (a 3,138 cm entry).
* `Spread` is crown spread in metres, published as `crown_width_m`; a zero
  is not a measurement and is nulled.
* `LocationType` (`Street Tree`, `Park Tree`, `Kinder Tree`) is not a site
  status; every feature is a tree.  `Height` has no canonical column.
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
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "data.gov.au/data", "85c2d44c-8ccf-4a32-9881-872f1a511ef7", timeout=300
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def positive(value) -> float | None:
    v = as_float(value)
    return v if v is not None and v > 0 else None


_FORM_SUFFIX = re.compile(r"\s*-\s*dwarf$", re.IGNORECASE)


def botanical(value) -> str | None:
    text = clean(value)
    return _FORM_SUFFIX.sub("", text) if text else None


def asset_id(feature: dict) -> str | None:
    return clean((feature.get("properties") or {}).get("feature_id"))


def identified(features: list[dict]) -> list[dict]:
    """Features whose asset id is present and names exactly one tree."""
    counts = Counter(asset_id(f) for f in features)
    blank = counts.pop(None, 0)
    repeated = {k for k, v in counts.items() if v > 1}
    kept = [f for f in features if (key := asset_id(f)) is not None and key not in repeated]
    dropped = len(features) - len(kept)
    if dropped:
        print(
            f"Glen Eira: dropped {dropped} feature(s) -- {blank} with no feature_id, "
            f"{dropped - blank} sharing one of {len(repeated)} repeated ids "
            f"({', '.join(sorted(repeated)[:5])})",
            file=sys.stderr,
        )
    return kept


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        tree_id.append(f"gle-{asset_id(feature)}")
        species.append(normalize_species(botanical(props.get("Botanical"))))
        longitude.append(as_float(coords[0]))
        latitude.append(as_float(coords[1]))
        dbh.append(cm_to_inches(as_float(props.get("DBH"))))
        crown.append(positive(props.get("Spread")))

    n = len(tree_id)
    plant_date: list[date | None] = [None] * n
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUMEL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


if __name__ == "__main__":
    features = read_geojson_features(RESOURCE)
    if not features:
        raise RuntimeError("Glen Eira: the GeoJSON resource has no features")
    print(f"Glen Eira: read {len(features)} feature(s)", file=sys.stderr)
    table = transform(identified(features))
    table = validate_coordinates(table, city="Melbourne", city_code="AUMEL")
    table = enforce_tree_schema(table, city="Melbourne", data_source="GLEN_EIRA")
    emit(table)
