#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Prague's listed monuments, from the national cultural-heritage register.

Source: `Kulturní památky` on the National Heritage Institute's (NPÚ)
geoportal -- the protected immovable monuments of the Czech Republic as
the Central List of Cultural Monuments (ÚSKP) records them, one polygon
per protected parcel with the monument's catalogue number, name, register
number and its Památkový katalog page.  CC-BY 4.0 Národní památkový ústav.
This is the runbook's first preference, an official designation registry,
and it is the layer the city's own portal redistributes as `pam/pamatky`.
Read with the trees' territory box as a spatial filter: 2,722 polygons
when this was written.

**Keyed on the catalogue number**, the ten-digit prefix of `hlavniPrvek`
(`1000001492 - Betlémská kaple`), which is the public identifier the
Památkový katalog uses for the protected thing itself.  A monument whose
protection covers several parcels is several polygons with one catalogue
number (the National Theatre is two); the first polygon is kept and the
rest are dropped with a count, so the grain holds and the landmark sits on
one of its parcels.  `OBJECTID` is not used, since a MapServer republished
from a file geodatabase renumbers.

The name is the catalogue's own, the part of `hlavniPrvek` after the
number: `Betlémská kaple`, `Klementinum`, `palác Kinských`.  `PrStavNazev`,
the name on the protection record, is the fallback; it is longer and legal
(`kolej Klementinum s kostelem sv. Salvátora`).  The register number
(`rejstrikoveCisloUSKP`, `11734/1-129`) and the protection type
(`typOchranyNazev`, `kulturní památka`) ride along as `register_number`
and `protection_type`.

Field mapping:

  hlavniPrvek           -> landmark_id (prefixed "czprg-", the catalogue number), name
  PrStavNazev           -> name (fallback)
  rejstrikoveCisloUSKP  -> register_number
  typOchranyNazev       -> protection_type
  geometry (polygon)    -> geometry_raw (WKT); the model takes its centroid
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import CITY_TERRITORY, emit  # noqa: E402
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    esri_geometry_to_wkt,
    iter_features,
)

# Kept in step with `prague_landmarks_probe.py`.
LAYER = FeatureLayer(
    "https://geoportal.npu.cz/arcgis/rest/services/Tematicke/CP_UAP_PVO/MapServer/1",
    timeout=300,
)
FIELDS = "OBJECTID,hlavniPrvek,PrStavNazev,rejstrikoveCisloUSKP,typOchranyNazev"
_CATALOGUE_ENTRY = re.compile(r"^\s*(\d{6,})\s*-\s*(.*?)\s*$", re.S)


def prague_envelope() -> dict[str, str]:
    """The spatial filter for `iter_features`: the city's territory box."""
    ((lat_min, lat_max, lon_min, lon_max),) = CITY_TERRITORY["CZPRG"]
    return {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


def parse_catalogue_entry(value) -> tuple[str | None, str | None]:
    """`("1000001492", "Betlémská kaple")` from `hlavniPrvek`, or `(None, None)`."""
    text = clean(value)
    if text is None:
        return None, None
    match = _CATALOGUE_ENTRY.match(text)
    if match is None:
        return None, None
    return match.group(1), clean(match.group(2))


def read_monuments() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    unusable = repeated = 0
    for page in iter_features(
        LAYER, out_fields=FIELDS, return_geometry=True, out_sr=4326,
        extra_params=prague_envelope(),
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            number, name = parse_catalogue_entry(attrs.get("hlavniPrvek"))
            name = name or clean(attrs.get("PrStavNazev"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if number is None or name is None or wkt is None:
                unusable += 1
                continue
            if number in seen:
                repeated += 1
                continue
            seen.add(number)
            records.append(
                {
                    "landmark_id": f"czprg-{number}",
                    "name": name,
                    "geometry_raw": wkt,
                    "register_number": clean(attrs.get("rejstrikoveCisloUSKP")),
                    # The register writes it in both cases.
                    "protection_type": (clean(attrs.get("typOchranyNazev")) or "").lower() or None,
                }
            )
    print(
        f"Prague landmarks: {len(records)} monuments kept, {repeated} further "
        f"parcel(s) of one, {unusable} dropped (no catalogue number, name or geometry)",
        file=sys.stderr,
    )
    if not records:
        raise RuntimeError(
            f"the register returned no monuments inside Prague's territory from "
            f"{LAYER.base}; check `hlavniPrvek` and the spatial filter"
        )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["CZPRG"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "register_number": pa.array(
                [r["register_number"] for r in records], type=pa.string()
            ),
            "protection_type": pa.array(
                [r["protection_type"] for r in records], type=pa.string()
            ),
        }
    )


if __name__ == "__main__":
    # No coordinate check here: the rows carry a polygon, not a point, and the
    # model takes the centroid; the spatial filter already bounds them.
    emit(transform(read_monuments()))
