#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Barrie's (Ontario) Municipal Heritage Register: designated and listed properties.

No licence stated; displayed with attribution only, not licensed for reuse.
The layer is the City of Barrie's own (`Heritage Properties`, layer 54 of
`Public/OperationalLayers_Dynamic/MapServer` on gispublic.barrie.ca, the
service the City's "Designated Heritage Properties" and "Listed Heritage
Properties" web maps and its Municipal Heritage Register story map read), but
it is not in the open data catalogue, so the Open Government Licence - Barrie
that covers the catalogue (and the tree layer) does not name it, and the
service states only "City of Barrie" as its copyright.  The open data site's
own "Cultural Heritage" layer (FacilitiesStreets/MapServer/0) is licensed but
is 94 monuments, plaques, sculptures and a shipwreck, not the register; it is
not used, as Kingston did not use its monuments layer.

**Only the register is published: `STATUS` DESIGNATED (28, by by-law under
the Ontario Heritage Act; `BYLAWNO`, `DATEDESIG`) and LISTED (42,
non-designated properties on the register under s.27, `DATELISTED`).**
`OTHER` (72) are properties of interest from the City's walking tours and
inventory, not on the register, and are dropped; so is the one `PENDING`
(a notice of intention to designate, not yet a designation).

**`NAME` is filled on every row** -- a building name where the register has
one ("The Sanders Block", "Maple Hill"), else the street address.
`YEARBUILT` is free text (`1880`, `c1870-1880`); its first four-digit year is
`year_built`.  `DATEDESIG` (`1990-10-01`) gives `year_designated`.
`property_type` is the register status.  The id is the layer's OBJECTID
(`bar-hp-{n}`): there is no GlobalID or register number, so a reload re-keys
the ids, which costs only churn since landmarks are rebuilt whole.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://gispublic.barrie.ca/arcgis/rest/services/Public/"
    "OperationalLayers_Dynamic/MapServer/54",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,NAME,ADDRESS,STATUS,YEARBUILT,DATEDESIG"
PROPERTY_TYPE = {
    "DESIGNATED": "Designated (Ontario Heritage Act)",
    "LISTED": "Listed (non-designated)",
}
WHERE = "STATUS IN ('DESIGNATED','LISTED')"
_YEAR = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    match = _YEAR.search(clean(value) or "")
    return int(match.group(1)) if match else None


def title(value: str | None) -> str | None:
    """`THE SANDERS BLOCK` -> `The Sanders Block`; the register is all capitals."""
    if value is None:
        return None
    words = []
    for word in value.split():
        if any(c.isdigit() for c in word):
            words.append(word.upper())
        else:
            words.append(
                "-".join(p[:1].upper() + p[1:].lower() for p in word.split("-"))
            )
    text = " ".join(words)
    # Possessive and `St`/`Ave` suffixes read naturally after the fold; fix the
    # apostrophe-s the fold capitalises (`Lount'S`).
    return re.sub(r"'S\b", "'s", text)


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    year_designated: list[int | None] = []
    property_type: list[str | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        street = title(clean(rec.get("ADDRESS")))
        label = title(clean(rec.get("NAME"))) or street
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if label is None or wkt is None or rec.get("OBJECTID") is None:
            continue
        landmark_id.append(f"bar-hp-{rec['OBJECTID']}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(parse_year(rec.get("YEARBUILT")))
        year_designated.append(parse_year(rec.get("DATEDESIG")))
        property_type.append(PROPERTY_TYPE.get(clean(rec.get("STATUS")) or ""))

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CABAR"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "year_designated": pa.array(year_designated, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, where=WHERE, return_geometry=True, out_sr=4326
    ):
        features.extend(page)
    table = transform(features)
    print(
        f"Barrie landmarks: {table.num_rows:,} of {len(features):,} register rows",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Barrie heritage register produced no landmarks")
    emit(table)
