#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Longueuil's parks and green spaces.

Source: "Parcs" on `www.donneesquebec.ca/recherche`, the GeoJSON resource
`481ca9a8`, 223 named parks under CC-BY -- with real polygons, which is the
form the landmark schema prefers over a point.

**Not the runbook's first preference, because Longueuil barely appears in one.**
The Repertoire du patrimoine culturel du Quebec -- the provincial designation
registry that gives Montreal 160 landmarks and Quebec City 100 -- holds exactly
**four** entries for Longueuil, all classified immeubles and none cited. Four
pins is not a geographic frame for a 97k-tree city, and the runbook's whole
reason for preferring a designation registry is that it names the ground.
223 parks name it better, they are on the same portal as the trees, and for a
tree map they are the more relevant landmark besides: the inventory is a park
and street inventory, and `NOM_TOPOGRAPHIE`-style park names are what a
question about Longueuil's trees will reach for.

`RECHERCHEPARC` is the key rather than `NUMERO`: the city built it to
disambiguate parks that share a name across boroughs (it appends the borough
code), and it measures 223 distinct over 223 rows with none blank, where
`NUMERO` has three blanks and two collisions. It is slugified, because the raw
value is a display string carrying commas, spaces, accents and parentheses --
"Baronnie, Parc de la (VLO)" -- and a landmark_id ends up in URLs and SQL
literals. The 223 slugs stay distinct.

The polygon-to-WKT helpers below are a second copy of the ones in
`uslax/losangeles_landmarks.py`. Two is under the threshold `EXTENDING.md` sets
for sharing; a third belongs in `_ingest_shared` next to `make_point_wkt`.
"""

import re
import sys
import unicodedata
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, read_geojson_features
from _ingest_shared import emit

RESOURCE = CkanResource(
    "www.donneesquebec.ca/recherche",
    "481ca9a8-7ab7-4937-9092-66d3f4acb6d3",
    timeout=180,
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def slugify(value: str) -> str:
    """"Baronnie, Parc de la (VLO)" -> "baronnie-parc-de-la-vlo"."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", value) if not unicodedata.combining(c)
    )
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", stripped.lower())).strip("-")


def _ring_to_wkt(ring: list[list[float]]) -> str | None:
    points = [
        f"{point[0]} {point[1]}"
        for point in ring
        if isinstance(point, (list, tuple)) and len(point) >= 2
    ]
    return "(" + ", ".join(points) + ")" if len(points) >= 4 else None


def geometry_to_polygons(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    geo_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return []
    if geo_type == "Polygon":
        return [coords]
    if geo_type == "MultiPolygon":
        return [polygon for polygon in coords if polygon]
    return []


def polygons_to_wkt(polygons: list[list[list[float]]]) -> str | None:
    rendered = []
    for polygon in polygons:
        rings = [w for w in (_ring_to_wkt(ring) for ring in polygon if ring) if w]
        if rings:
            rendered.append("(" + ", ".join(rings) + ")")
    if not rendered:
        return None
    if len(rendered) == 1:
        return f"POLYGON{rendered[0]}"
    return f"MULTIPOLYGON({', '.join(rendered)})"


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    property_type: list[str | None] = []

    seen: set[str] = set()
    for feature in features:
        props = feature.get("properties") or {}
        raw_id = clean(props.get("RECHERCHEPARC"))
        label = clean(props.get("NOM"))
        if raw_id is None or label is None or raw_id in seen:
            continue
        wkt = polygons_to_wkt(geometry_to_polygons(feature.get("geometry")))
        if wkt is None:
            continue
        seen.add(raw_id)

        landmark_id.append(f"lgl-{slugify(raw_id)}")
        city.append("CALON")
        # "Baronnie, Parc de la" -- the city files park names inverted so they
        # sort by the distinguishing word. Left as published: a landmark name
        # is a proper name, and un-inverting it reliably needs a rule this
        # source does not justify (there is no single comma convention).
        name.append(label)
        geometry_raw.append(wkt)
        # The GeoJSON resource carries only NOM, TYPE, NUMERO and
        # RECHERCHEPARC -- the borough is in the datastore XLSX and not here,
        # so there is no neighborhood to map.
        property_type.append(clean(props.get("TYPE")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_geojson_features(RESOURCE)))
