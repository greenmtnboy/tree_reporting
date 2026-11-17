#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Lisbon's classified heritage and public statuary, from the city's ArcGIS.

Source: the `Patrimonio` FeatureServer of the Câmara Municipal de Lisboa
(owner `geodados_CML`, the same ArcGIS Online organisation as the trees), "a
map service of the buildings and ensembles classified or being classified,
and others of historic and architectural relevance, in the City of Lisbon".
Published as open data through the city's geodados hub and Lisboa Aberta,
whose terms are Creative Commons CCZero.

Seven of its layers are read, one designation each, and the designation is
the `category`:

  13  Património Mundial                                        2 points
  12  Monumentos Nacionais                                     63 points
  11  Imóveis e Monumentos de Interesse Público               222 points
   7  Conjuntos de Interesse Público                           10 points
   8  Sítios de Interesse Público                               1 point
  15  Imóveis, Monumentos e Conjuntos de Interesse Municipal   27 polygons
  10  Estatuária (the city's public statues and monuments)    387 points

The first six are the national and municipal classification register for the
city (the runbook's first preference); the statuary is the city's inventory
of the statues and memorials it keeps in public space, which is what people
name a square or a garden by.  Left out on purpose: the two "em vias de
classificação" layers (9, 16: pending, not designated), the architecture
and azulejo surveys (0-6: a building's interest, not a designation),
archaeological remains, the Valmor prizes, geomonuments, and the local shops
and associations of historic interest (19).

**Keyed on `COD_SIG`**, the city's SIG code for the asset (`2862`, `CML 27`),
present and distinct on every row of every layer read, and distinct across
them; it is the code the city's other GIS layers carry, where the GlobalID
is only this hosted copy's.  Published as `lis-{code}` with spaces dashed.

The World Heritage layer repeats two Monumentos Nacionais (Mosteiro dos
Jerónimos, and Torre de Belém under a longer name at the same point), and
the classification layers and the statuary repeat each other at identical
points (15 features in all, e.g. the Pelourinho and the Cruzeiro de Arroios
are both statuary and classified).  A feature that a higher-ranked layer
already published -- the same name within 250 m, or any name at the same
point (within half a metre) -- is dropped as the same place, so the map
carries one pin per monument under its highest designation.  Within one
layer nothing is merged: statues of the same person in different squares
(Luís de Camões, D. Afonso Henriques) and the many "Sem título" works are
distinct.
"""

import math
import sys
import unicodedata
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features

SERVICE = "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services/Patrimonio/FeatureServer"

# (layer id, category, name field), highest designation first: the order is
# the dedup rank.
LAYERS = (
    (13, "Património Mundial", "INF_NOME"),
    (12, "Monumento Nacional", "INF_NOME"),
    (11, "Imóvel de Interesse Público", "INF_NOME"),
    (7, "Conjunto de Interesse Público", "INF_NOME"),
    (8, "Sítio de Interesse Público", "INF_NOME"),
    (15, "Interesse Municipal", "NOME"),
    (10, "Estatuária", "INF_NOME"),
)

SAME_PLACE_METRES = 250.0  # same name, a higher layer
SAME_POINT_METRES = 0.5  # any name, a higher layer: the same point redrawn


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


def centre(geometry: dict) -> tuple[float, float] | None:
    """(lat, lon) of a point, or the mean vertex of a polygon's rings."""
    if "x" in geometry and "y" in geometry:
        return float(geometry["y"]), float(geometry["x"])
    points = [pt for ring in geometry.get("rings") or [] for pt in ring]
    if not points:
        return None
    return sum(p[1] for p in points) / len(points), sum(p[0] for p in points) / len(points)


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def read_landmarks() -> list[dict]:
    records: list[dict] = []
    seen_ids: set[str] = set()
    # Places published by the layers already read, by folded name.
    earlier_layers: dict[str, list[tuple[float, float]]] = {}
    unusable = duplicates = 0
    for layer_id, category, name_field in LAYERS:
        layer = FeatureLayer(f"{SERVICE}/{layer_id}")
        from_layer = 0
        this_layer: dict[str, list[tuple[float, float]]] = {}
        for page in iter_features(
            layer,
            out_fields=f"OBJECTID,COD_SIG,{name_field}",
            return_geometry=True,
            out_sr=4326,
            order_by="OBJECTID",
        ):
            for feature in page:
                attrs = feature.get("attributes") or {}
                code = clean(attrs.get("COD_SIG"))
                name = clean(attrs.get(name_field))
                geometry = feature.get("geometry") or {}
                wkt = esri_geometry_to_wkt(geometry)
                where = centre(geometry) if wkt else None
                if code is None or name is None or where is None:
                    unusable += 1
                    continue
                landmark_id = "lis-" + code.lower().replace(" ", "-")
                if landmark_id in seen_ids:
                    raise RuntimeError(
                        f"Lisbon heritage: COD_SIG {code!r} repeats across layers; "
                        "it is no longer a key"
                    )
                key = fold(name)
                if any(
                    metres(where, other) < (SAME_PLACE_METRES if other_key == key else SAME_POINT_METRES)
                    for other_key, places in earlier_layers.items()
                    for other in places
                ):
                    print(f"Lisbon heritage: {category} {name!r} is a higher designation's place", file=sys.stderr)
                    duplicates += 1
                    continue
                seen_ids.add(landmark_id)
                this_layer.setdefault(key, []).append(where)
                records.append(
                    {
                        "landmark_id": landmark_id,
                        "name": name,
                        "geometry_raw": wkt,
                        "category": category,
                    }
                )
                from_layer += 1
        for key, places in this_layer.items():
            earlier_layers.setdefault(key, []).extend(places)
        if not from_layer:
            raise RuntimeError(f"Lisbon heritage: layer {layer_id} ({category}) returned nothing")
    print(
        f"Lisbon heritage: {len(records)} landmark(s) kept, {duplicates} dropped as a "
        f"higher designation's same place, {unusable} unusable (no code, name or position)",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["PTLIS"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))
