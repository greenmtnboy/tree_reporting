#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Oakville's Municipal Heritage Register.

Source: "Heritage Properties Open Data" (item 6a57f4591e314fb5b5c88e067add8872,
owned by the Town's `Oakville_GIS` account), layer 26 of the Town's
`ArcGISOnline/AGO_OpenData` map service, listed on the Town of Oakville Open
Data Portal (https://portal-exploreoakville.opendata.arcgis.com).  The item
carries no licence field of its own; the service describes itself as "Town of
Oakville Open Data", and the Town publishes its portal under the Town of
Oakville Open Data Licence (OGL - Canada 2.0 based;
https://www.oakville.ca/town-hall/town-studies-plans-projects/town-initiatives/open-data/open-data-licence/),
as the trees' own item states.  One point per property on the register, 906
rows on 2026-11-20.

**The whole layer is the register, so every row is published.**  `STATUS` is
Part V (435 -- inside a Heritage Conservation District), Part IV (222 --
individually designated by by-law), Listed (195 -- listed non-designated
under s.27 of the Ontario Heritage Act), Part IV / Part V (35) and Heritage
Easement Agreement (19).  `property_type` is that status in words.

**A registered property is not named.**  The register has no name column
(`ALIAS` is the road's alias, `Highway 2`), so a landmark is known by its
address (`STREET_NUM` + `SNAME`: "114 Balsam Dr"), and by the street alone for
the six rows with no number.  The register writes addresses in capitals;
they are published in capitalised words.  The owner columns (`Owner`,
`OwnerAddress`) are personal information and are never read.

**`YR_BUILT`** is free text (`1878`, `c.1890s`, `1840/1887`, `Early 20th
century`); `year_built` is its first four-digit year, null when there is none
(filled for ~440 rows).

**The id is `GLOBALID`** (`oakville-hp-{guid}`): non-null on every row and
checked unique here, so a duplicate fails the run rather than publishing two
landmarks under one id.  `HER_ID` is filled on 99 rows only.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://maps.oakville.ca/oakgis/rest/services/ArcGISOnline/"
    "AGO_OpenData/MapServer/26",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GLOBALID,STREET_NUM,SNAME,STATUS,YR_BUILT"

STATUS_LABELS = {
    "Part IV": "Designated, Part IV",
    "Part V": "Designated, Part V (Heritage Conservation District)",
    "Part IV / Part V": "Designated, Part IV and Part V",
    "Listed": "Listed (non-designated)",
    "Heritage Easement Agreement": "Heritage easement agreement",
}

_YEAR = re.compile(r"(1[5-9]\d\d|20\d\d)")


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


def display(value) -> str | None:
    """`LAKESHORE RD E` -> `Lakeshore Rd E`."""
    text = clean(value)
    return " ".join(capitalise(w) for w in text.split()) if text else None


def address_of(rec: dict) -> str | None:
    street = display(rec.get("SNAME"))
    if street is None:
        return None
    number = clean(rec.get("STREET_NUM"))
    return f"{number} {street}" if number else street


def year_of(value) -> int | None:
    match = _YEAR.search(clean(value) or "")
    return int(match.group(1)) if match else None


def property_type_of(rec: dict) -> str | None:
    status = clean(rec.get("STATUS"))
    return STATUS_LABELS.get(status, status) if status else None


def global_id(rec: dict) -> str | None:
    value = clean(rec.get("GLOBALID"))
    return value.strip("{}").lower() if value else None


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
        street = address_of(rec)
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        guid = global_id(rec)
        if street is None or wkt is None or guid is None:
            continue
        key = f"oakville-hp-{guid}"
        if key in seen:
            raise RuntimeError(f"Oakville heritage register repeats {key}")
        seen.add(key)
        landmark_id.append(key)
        name.append(street)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(year_of(rec.get("YR_BUILT")))
        property_type.append(property_type_of(rec))

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAOAK"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True):
        features.extend(page)
    table = transform(features)
    print(
        f"Oakville landmarks: {table.num_rows:,} of {len(features):,} register rows",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Oakville heritage register produced no landmarks")
    emit(table)
