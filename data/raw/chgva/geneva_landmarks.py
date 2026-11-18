#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Geneva's classified monuments (objets classés), from SITG.

Source: `DPS_CLASSEMENT` on SITG's ArcGIS server (vector.sitg.ge.ch),
catalogued at https://sitg.ge.ch/donnees/dps-classement and kept by the
canton's Office du patrimoine et des sites: every monument, building, site
and parcel placed under protection by a classification decree under the
cantonal law on the protection of monuments and sites (LPMNS, L 4 05, art.
10), canton-wide.  Diffusion level "A - Accès libre" (SITG open data), with
the publisher's stated condition: "Toute reproduction et tout usage public
doivent mentionner l'auteur: 'Inventaire des monuments d'art et d'histoire du
canton de Genève'".  Classification is the strongest of the canton's
protection measures; the far larger `DPS_INVENTAIRE` (inscription on the
inventory, 1,747 polygons, many named only `Habitation rurale`) is not read.

**One landmark per classification decree, keyed on `NO_CLASSEMENT`**
(`MS-c 236` -> `geneva-MS-c-236`).  The layer is 1,016 polygons over 283
decrees: a decree that protects a domain lists its house, its outbuildings
and its parcels as separate polygons (41 parcels for `Bois au bord de
l'Aire`).  Its polygons are published together as one MULTIPOLYGON, named
by the name most of them carry.

The register's own attributes ride along in existing `landmark_common`
properties: `register_number` is the decree number, `category` the kind of
object (`Maison, immeuble`, `Parcelle`, `Objets divers`; the most common
among the decree's polygons), `protection_type` is `Classé` or, where the
object is also under federal protection (`PROTECTION_FEDERALE` `Oui`/`PF`),
`Classé, protection fédérale`, and `year_designated` is the year of the
earliest `DATE_ACE` (the Conseil d'Etat decree, `dd.mm.yyyy`) among them.
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features

REGISTER = FeatureLayer(
    "https://vector.sitg.ge.ch/arcgis/rest/services/DPS_CLASSEMENT/FeatureServer/0",
    timeout=180,
)
FIELDS = "OBJECTID,NO_CLASSEMENT,NOM,NATURE_OBJET,DATE_ACE,PROTECTION_FEDERALE"
FEDERAL = frozenset({"oui", "pf"})
_DATE = re.compile(r"^\d{2}\.\d{2}\.(\d{4})$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text if text and text != "-" else None


def most_common(values: list[str | None]) -> str | None:
    present = [v for v in values if v]
    if not present:
        return None
    counts = Counter(present)
    # Ties go to the earlier polygon, so the pick is stable across builds.
    return max(present, key=lambda v: (counts[v], -present.index(v)))


def decree_year(value) -> int | None:
    match = _DATE.match(clean(value) or "")
    return int(match.group(1)) if match else None


def build() -> pa.Table:
    groups: dict[str, list[dict]] = defaultdict(list)
    read = 0
    for page in iter_features(REGISTER, out_fields=FIELDS, return_geometry=True, out_sr=4326):
        for feature in page:
            read += 1
            attrs = feature.get("attributes") or {}
            number = clean(attrs.get("NO_CLASSEMENT"))
            if number is None:
                continue
            groups[number].append(
                {"attrs": attrs, "rings": (feature.get("geometry") or {}).get("rings") or []}
            )
    if not groups:
        raise RuntimeError(f"{REGISTER.base} returned no classified monuments")

    rows: dict[str, list] = defaultdict(list)
    dropped = 0
    for number in sorted(groups):
        members = groups[number]
        name = most_common([clean(m["attrs"].get("NOM")) for m in members])
        # Esri rings carry their own orientation (outer clockwise), so the
        # decree's rings together convert to one (multi)polygon.
        wkt = esri_geometry_to_wkt({"rings": [r for m in members for r in m["rings"]]})
        if name is None or wkt is None:
            dropped += 1
            continue
        years = [y for y in (decree_year(m["attrs"].get("DATE_ACE")) for m in members) if y]
        federal = any(
            (clean(m["attrs"].get("PROTECTION_FEDERALE")) or "").lower() in FEDERAL
            for m in members
        )
        rows["landmark_id"].append(f"geneva-{number.replace(' ', '-')}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["category"].append(most_common([clean(m["attrs"].get("NATURE_OBJET")) for m in members]))
        rows["protection_type"].append("Classé, protection fédérale" if federal else "Classé")
        rows["register_number"].append(number)
        rows["year_designated"].append(min(years) if years else None)

    n = len(rows["landmark_id"])
    print(
        f"Geneva classified monuments: {read} polygon(s) over {len(groups)} decree(s); "
        f"{dropped} dropped with no name or geometry; {n} landmark(s)",
        file=sys.stderr,
    )
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["CHGVA"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "category": pa.array(rows["category"], type=pa.string()),
            "protection_type": pa.array(rows["protection_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
