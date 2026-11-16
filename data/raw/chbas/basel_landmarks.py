#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Basel's inventory of gardens worthy of protection, from the canton's WFS.

Source: `ms:IG_InventarGartenanlagen` ("Inventar der schützenswerten
Gartenanlagen") on wfs.geo.bs.ch, the canton's MapServer -- the same
geodata infrastructure the tree register (`ms:BA_Baeume`) is served from.
Public geodata of Basel-Stadt with a download service are Open Government
Data under CC BY 4.0, attributed "Quelle: Geodaten Kanton Basel-Stadt"
(https://www.bs.ch/bvd/grundbuch-und-vermessungsamt/geo/anwendungen/agb).
50 polygons when this was written: the parks, promenades, squares, school
gardens, the zoo and single tree sites (`Baumstandort`) the canton
inventories as historic gardens, each named (`Riehenring Promenade`,
`Totentanz`, `St. Alban-Tor-Anlage`).

The runbook's first preference would be the canton's Denkmalverzeichnis
(listed monuments).  It is not published as open data: neither data.bs.ch nor
the WFS carries it (the `HD_*` layers are a roof-survey evaluation that cites
it, not the register).  For a tree map the garden inventory is the better
register in any case -- these are the places the trees stand in.

**Keyed on the inventory sheet.**  The layer has no id column and MapServer
emits no feature id.  Every row links its PDF inventory sheet
(`inventarblatt_riehenring_promenade.pdf`); the sheet's file stem is the canton's
own handle for the entry, and is what this publishes.  A row without one, a
name or a geometry is dropped with a count.  `ig_typderanlage` (Parkanlage,
Platz, Promenade, Baumstandort ...) rides the shared `category`.

The layer is 50 rows, so it is read in one request and the count is checked
against `numberMatched`.  MapServer does not answer to the `application/json`
output format `shared.platforms.wfs` asks for, only to `geojson`.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit, get_json_with_retry
from shared.platforms.wfs import geojson_to_wkt

WFS_URL = "https://wfs.geo.bs.ch/"
TYPE_NAME = "ms:IG_InventarGartenanlagen"
PARAMS = {
    "service": "WFS",
    "version": "2.0.0",
    "request": "GetFeature",
    "typeNames": TYPE_NAME,
    "outputFormat": "geojson",
    "srsName": "EPSG:4326",
}

_SHEET = re.compile(r"inventarblatt_([A-Za-z0-9_\-]+)\.pdf")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def read_gardens() -> list[dict]:
    payload = get_json_with_retry(WFS_URL, params=PARAMS, timeout=120)
    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"{TYPE_NAME}: response carries no feature list")
    matched = payload.get("numberMatched")
    if isinstance(matched, int) and matched != len(features):
        raise RuntimeError(
            f"{TYPE_NAME}: read {len(features)} of {matched} features in one request"
        )

    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for feature in features:
        props = feature.get("properties") or {}
        sheet = _SHEET.search(props.get("ig_inventarblatt") or "")
        key = sheet.group(1).lower() if sheet else None
        name = clean(props.get("ig_name"))
        wkt = geojson_to_wkt(feature.get("geometry"))
        if key is None or name is None or wkt is None or key in seen:
            unusable += 1
            continue
        seen.add(key)
        records.append(
            {
                "landmark_id": f"basel-ig-{key}",
                "name": name,
                "geometry_raw": wkt,
                "category": clean(props.get("ig_typderanlage")),
            }
        )
    print(
        f"Basel garden inventory: {len(records)} kept, {unusable} dropped (no "
        f"inventory sheet, name or geometry, or a repeated sheet)",
        file=sys.stderr,
    )
    if not records:
        raise RuntimeError(f"{TYPE_NAME}: no usable rows")
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["CHBAS"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_gardens()))
