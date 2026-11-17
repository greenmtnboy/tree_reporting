#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Madrid's designated heritage: Bienes de Interés Cultural and Patrimonial.

Source: the Ayuntamiento's "Catálogo Bienes de Interés Cultural y Patrimonial"
map service on sigma.madrid.es (layer 5, the large-scale polygons), catalogued
on datos.madrid.es as "Bien de interés cultural/patrimonial (BIC/BIP)"
(`300717-0-cultural-bien-mapas`) under CC BY 4.0.  It is the municipal term's
share of the Comunidad de Madrid's register of goods declared, or under
declaration, as BIC (monuments, historic gardens, historic ensembles, ...) or
BIP: the official designation register, the runbook's first preference.

The layer holds four kinds of polygon per good: the protected good itself
(`CTG_TX_DESCRIPCION = 'BIEN PROTEGIDO'`), its protected surroundings
(`ENTORNO`), and the parcels containing or linked to it.  Only the good itself
is a landmark; the others are planning envelopes, often a whole block.

**Keyed on `BPH_ID`**, the register's own number for the good ("Número de
Bien"), which is stable across the service's republishes where the map
service's OBJECTID is not promised to be.  A good drawn as several polygons
(a palace and its outbuildings) appears as several features with one
`BPH_ID`; their rings are gathered into one MULTIPOLYGON.
"""

import sys
from collections import OrderedDict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features

LAYER = FeatureLayer(
    "https://sigma.madrid.es/hosted/rest/services/DESARROLLO_URBANO_ACTUALIZADO/BIC/MapServer/5"
)
OUT_FIELDS = "OBJECTID,BPH_ID,BPH_TX_DESCRIPCION,CATEGORIA,CTG_TX_DESCRIPCION"
WHERE = "CTG_TX_DESCRIPCION = 'BIEN PROTEGIDO'"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def category_of(categoria: str | None) -> str | None:
    """'BIC declarado: Monumento' from 'BIEN PROTEGIDO (BIC DECLARADO EN LA
    CATEGORÍA DE MONUMENTO)'; 'BIP declarado' from 'BIEN PROTEGIDO (BIP DECLARADO)'."""
    text = clean(categoria)
    if text is None:
        return None
    start, end = text.find("("), text.rfind(")")
    inner = text[start + 1 : end] if start != -1 and end > start else text
    head, sep, tail = inner.partition(" EN LA CATEGORÍA DE ")
    words = head.split(" ", 1)
    status = words[0] + (" " + words[1].lower() if len(words) > 1 else "")
    if not sep:
        return status
    kind = tail.replace('"', "").strip().capitalize()
    return f"{status}: {kind}"


def read_goods() -> list[dict]:
    goods: "OrderedDict[int, dict]" = OrderedDict()
    unusable = 0
    for page in iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            number = attrs.get("BPH_ID")
            name = clean(attrs.get("BPH_TX_DESCRIPCION"))
            rings = (feature.get("geometry") or {}).get("rings") or []
            if number is None or name is None or not rings:
                unusable += 1
                continue
            good = goods.setdefault(
                int(number),
                {"name": name, "category": category_of(attrs.get("CATEGORIA")), "rings": []},
            )
            good["rings"].extend(rings)

    records = []
    for number, good in goods.items():
        wkt = esri_geometry_to_wkt({"rings": good["rings"]})
        if wkt is None:
            unusable += 1
            continue
        records.append(
            {
                "landmark_id": f"madrid-bic-{number}",
                "name": good["name"],
                "geometry_raw": wkt,
                "category": good["category"],
            }
        )
    print(
        f"Madrid BIC/BIP: {len(records)} protected good(s) kept, {unusable} "
        "feature(s) dropped (no number, name or geometry)",
        file=sys.stderr,
    )
    if not records:
        raise RuntimeError("Madrid BIC/BIP: the register returned no protected goods")
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["ESMAD"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_goods()))
