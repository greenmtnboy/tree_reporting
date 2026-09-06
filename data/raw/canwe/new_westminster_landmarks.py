#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""New Westminster's Heritage Register.

Source: "Heritage Register" (`Heritage_Register_view`) on
`opendata.newwestcity.ca` -- the 224 properties on the city's Heritage
Register, each a point with the build year, the register date and the
architect or builder where known.  Read live off the same portal as the trees.

This is the runbook's first-preference landmark source, and it comes with a
caveat worth stating: **New Westminster's register names properties by
address.**  `NAME` is "1405 Nanaimo St", not "Irving House".  That is what the
city publishes, and it is still useful ground for the agent to stand on in a
16 km(2) city -- but it is weaker context than Halifax's named buildings, and
if the city ever publishes the building names it should move to them.

The larger "Heritage Resource Inventory" (924 rows) is deliberately not used:
it is an inventory of buildings of interest rather than a register of listed
ones, its `BLDGNAM` is null on the rows sampled, and it carries demolished
buildings with a `STATUS` flag.

`DESC1` and `DESC2` are HTML fragments the city renders in a popup.  Only the
build year is taken out of them; the prose is left behind rather than being
stripped into a `name`, which would put a paragraph in the map's label.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://services3.arcgis.com/A7O8YnTNtzRPIn7T/arcgis/rest/services/"
    "Heritage_Register_view/FeatureServer/0"
)

OUT_FIELDS = "GlobalID,NAME,DESC1"

# `DESC1` is an HTML fragment shaped "<p>Built: 1907<br /><br />Register Date:
# Apr 27 2009<br />...".  Two labelled fields are worth pulling out of it; the
# rest is prose.
_BUILT = re.compile(r"Built:\s*(\d{4})", re.I)
_REGISTERED = re.compile(r"Register Date:\s*[A-Za-z]{3}\s+\d{1,2}\s+(\d{4})", re.I)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def first_year(pattern: re.Pattern, value) -> int | None:
    """The year named by *pattern* in an HTML description, or None."""
    if not value:
        return None
    found = pattern.search(str(value))
    return int(found.group(1)) if found else None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    year_designated: list[int | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        label = clean(rec.get("NAME"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue
        raw_id = str(rec.get("GlobalID") or "").strip().strip("{}")
        if not raw_id:
            continue

        landmark_id.append(f"nwe-{raw_id}")
        city.append("CANWE")
        name.append(label)
        geometry_raw.append(wkt)
        # The name *is* the address here; carrying it in both columns keeps
        # the schema honest rather than leaving `address` null on every row.
        address.append(label)
        year_built.append(first_year(_BUILT, rec.get("DESC1")))
        year_designated.append(first_year(_REGISTERED, rec.get("DESC1")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "year_designated": pa.array(year_designated, type=pa.int64()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="ObjectId",
    ):
        features.extend(page)
    emit(transform(features))
