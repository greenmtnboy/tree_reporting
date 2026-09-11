#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Helsinki's plan-protected buildings and protected natural monuments.

Two layers of the City of Helsinki's open WFS (`kartta.hel.fi`, the same
GeoServer the trees come from), both CC BY 4.0:

| layer                                        | rows | geometry |
|----------------------------------------------|-----:|----------|
| `Asemakaavoissa_suojellut_rakennukset_alue`  | 5319 | polygon  |
| `LTJ_avoin_rauhoitettu_luonnonmuistomerkit`  |   31 | point    |

The first is every building a detailed plan (asemakaava) protects -- the
city's own designation register, kept by the Urban Environment Division's
planning unit -- and the second is the protected natural monuments (a
glacial pothole in Käpylä, two birches in Kulosaari, a gorge in Kaivopuisto).

**The buildings are named by their address**, because that is all the plan
register records: `osoite` (`Tapiolantie 12`), the plan number, the
protection mark (`s`, `sr-1`, `sr-2`) and the plan's own explanation of why.
The national Heritage Agency's register would give the buildings names, but
its WFS (`kartta.nba.fi`, moved in December 2023) does not resolve from here,
and an official city register with an address beats no register.  The
protection mark is published as the category and the plan's entry-into-force
year as the designation year.

**Keyed on the layer's `id`**, its own row number, prefixed by which layer:
`pys_raknro` (the permanent building number) is the natural key for the
buildings and is not always present.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit
from _wfs_shared import WfsLayer, geojson_to_wkt, iter_wfs_features

WFS = "https://kartta.hel.fi/ws/geoserver/avoindata/wfs"
BUILDINGS = WfsLayer(WFS, "avoindata:Asemakaavoissa_suojellut_rakennukset_alue", timeout=300)
NATURAL_MONUMENTS = WfsLayer(WFS, "avoindata:LTJ_avoin_rauhoitettu_luonnonmuistomerkit")

BUILDING_PROPERTIES = ["id", "pys_raknro", "osoite", "s_merk", "voimaantulo_pvm", "laji", "geom"]
MONUMENT_PROPERTIES = ["id", "nimi", "luokan_nimi", "geometry1"]


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10]).year
    except ValueError:
        return None


def read_buildings() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for page in iter_wfs_features(BUILDINGS, sort_by="id", properties=BUILDING_PROPERTIES, page_size=5000):
        for feature in page:
            props = feature.get("properties") or {}
            key = clean(props.get("id"))
            wkt = geojson_to_wkt(feature.get("geometry"))
            address = clean(props.get("osoite"))
            name = address or (
                f"Suojeltu rakennus {clean(props.get('pys_raknro'))}"
                if clean(props.get("pys_raknro"))
                else None
            )
            if key is None or wkt is None or name is None or key in seen:
                unusable += 1
                continue
            seen.add(key)
            mark = clean(props.get("s_merk"))
            records.append(
                {
                    "landmark_id": f"hel-b-{key}",
                    "name": name,
                    "geometry_raw": wkt,
                    "address": address,
                    "category": f"Asemakaavalla suojeltu rakennus ({mark})" if mark else "Asemakaavalla suojeltu rakennus",
                    "year_designated": parse_year(props.get("voimaantulo_pvm")),
                }
            )
    print(
        f"Helsinki plan-protected buildings: {len(records)} kept, {unusable} dropped "
        f"(no id, geometry or address)",
        file=sys.stderr,
    )
    return records


def read_natural_monuments() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for page in iter_wfs_features(NATURAL_MONUMENTS, sort_by="id", properties=MONUMENT_PROPERTIES):
        for feature in page:
            props = feature.get("properties") or {}
            key = clean(props.get("id"))
            name = clean(props.get("nimi"))
            wkt = geojson_to_wkt(feature.get("geometry"))
            if key is None or name is None or wkt is None or key in seen:
                unusable += 1
                continue
            seen.add(key)
            records.append(
                {
                    "landmark_id": f"hel-n-{key}",
                    "name": name,
                    "geometry_raw": wkt,
                    "address": None,
                    "category": clean(props.get("luokan_nimi")) or "Luonnonmuistomerkki",
                    "year_designated": None,
                }
            )
    print(
        f"Helsinki natural monuments: {len(records)} kept, {unusable} dropped",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["FIHEL"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "address": pa.array([r["address"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
            "year_designated": pa.array([r["year_designated"] for r in records], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_buildings() + read_natural_monuments()))
