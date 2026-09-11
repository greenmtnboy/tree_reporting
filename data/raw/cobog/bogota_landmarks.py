#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bogotá's declared monuments and named heritage buildings.

Two layers of one ArcGIS service on the district cadastre's server
(`serviciosgis.catastrobogota.gov.co`, `recreaciondeporte/bienesinterescultural`),
both catalogued on `datosabiertos.bogota.gov.co` by the Instituto Distrital de
Patrimonio Cultural under CC BY-SA 4.0:

| layer | dataset                                              | rows | geometry |
|-------|------------------------------------------------------|-----:|----------|
| 0     | Monumentos - Inventario Patrimonio Mueble            |  726 | point    |
| 1     | Bienes Inmuebles de Interés Cultural (BIC)           | 6492 | polygon  |

This is the runbook's first-preference source -- an official designation
registry, read live -- so there is no geocoding, no committed CSV and no
staging object.

**The BIC layer is filtered to the rows that carry a name.**  5,750 of its
6,492 declared properties are named by their address alone (`NOMBRE` equals
`DIRECCION`: `KR 6 5 B 4 SUR`), which is a designation without a name --
useful to a planner, not to someone asking what is near a tree.  The 742 with
a real name (`Edificio Andes`, `Bolsa de Bogotá`, `Pasaje y Edificio
Hernández`) are the landmarks, and a name that covers several lots (the
Pasaje Hernández is six) publishes one row per lot, since each is its own
declared property with its own polygon.

**The monuments are the sculpture, fountain, plaque, bell and clock inventory
of public space** -- 726 pieces, every one named and positioned, from the
Bolívar equestrian statue down to a designated post box.  They are what a
person means by a landmark far more often than a listed façade is, and they
are the layer with a proper stable id (`CONS`, the inventory's consecutive
number).  The BIC rows are keyed on `LOTCODIGO`, the cadastral lot the
declaration attaches to, which survives a republish where `OBJECTID` does not.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit, make_point_wkt

SERVICE = "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/recreaciondeporte/bienesinterescultural/MapServer"
MONUMENTS = FeatureLayer(f"{SERVICE}/0", timeout=180)
BUILDINGS = FeatureLayer(f"{SERVICE}/1", timeout=180)

MONUMENT_FIELDS = "CONS,TITULO_NOM,DIRECCION,CLASIFICAC,LOCALIDAD,LATITUD,LONGITUD"
BUILDING_FIELDS = "OBJECTID,LOTCODIGO,NOMBRE,DIRECCION,AMBITO,CATEGORIA,LOCALIDAD"

# `LOCALIDAD` on the BIC layer is the two-digit code; the monuments layer
# spells the name out.  The codes are the census layer's domain.
LOCALIDADES = {
    "01": "Usaquén", "02": "Chapinero", "03": "Santa Fe", "04": "San Cristóbal",
    "05": "Usme", "06": "Tunjuelito", "07": "Bosa", "08": "Kennedy",
    "09": "Fontibón", "10": "Engativá", "11": "Suba", "12": "Barrios Unidos",
    "13": "Teusaquillo", "14": "Los Mártires", "15": "Antonio Nariño",
    "16": "Puente Aranda", "17": "Candelaria", "18": "Rafael Uribe Uribe",
    "19": "Ciudad Bolívar", "20": "Sumapaz",
}

# The monuments' `CLASIFICAC` codes, spelled out for the category column.
MONUMENT_KINDS = {
    "E_Antropomorfa": "Estatua", "E_Ecuestre": "Estatua ecuestre",
    "E_Zoomorfa": "Escultura zoomorfa", "E_Fitomorfa": "Escultura fitomorfa",
    "E_Abstracta": "Escultura abstracta", "E_Geometrica": "Escultura geométrica",
    "E_Adosada": "Escultura adosada", "Conj_Escultorico": "Conjunto escultórico",
    "Conj_Funerario": "Conjunto funerario", "Relieve": "Relieve", "Mural": "Mural",
    "Fuente": "Fuente", "Placa": "Placa", "Campana": "Campana", "Reloj": "Reloj",
    "Buzon": "Buzón", "Buzon_Exento": "Buzón", "Luminaria": "Luminaria",
    "Pergola": "Pérgola", "Estruc_Ingenieril": "Estructura de ingeniería",
    "Estruc_Arquitectonica": "Estructura arquitectónica", "Maquinaria": "Maquinaria",
    "Inst_Cientifico": "Instrumento científico",
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


# A "name" that is a Bogotá street address in another spelling: `Calle 35 5 15`
# for `CaL 35 5 15`, `Cra 7 No 12-30`.  The layer's own equality test misses
# these, and they are the address-only designations the filter exists to drop.
_ADDRESS_LIKE = re.compile(
    r"^(?:CA?L(?:LE)?|KR|CRA?|CARRERA|AC|AK|AV(?:ENIDA)?|DG|DIAGONAL|TV|TRANSVERSAL)\s*\d",
    re.IGNORECASE,
)


def is_address(name: str) -> bool:
    return bool(_ADDRESS_LIKE.match(name))


def read_monuments() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for page in iter_features(MONUMENTS, out_fields=MONUMENT_FIELDS, order_by="OBJECTID_1"):
        for feature in page:
            attrs = feature.get("attributes") or {}
            number = attrs.get("CONS")
            name = clean(attrs.get("TITULO_NOM"))
            wkt = make_point_wkt(attrs.get("LONGITUD"), attrs.get("LATITUD"))
            if number is None or name is None or wkt is None:
                unusable += 1
                continue
            landmark_id = f"bog-mon-{number}"
            if landmark_id in seen:
                unusable += 1
                continue
            seen.add(landmark_id)
            kind = clean(attrs.get("CLASIFICAC"))
            records.append(
                {
                    "landmark_id": landmark_id,
                    "name": name,
                    "geometry_raw": wkt,
                    "address": clean(attrs.get("DIRECCION")),
                    "category": MONUMENT_KINDS.get(kind, kind) if kind else "Monumento",
                    "borough": clean(attrs.get("LOCALIDAD")),
                }
            )
    print(
        f"Bogotá monuments: {len(records)} kept, {unusable} dropped (no number, "
        f"name or position, or a repeated number)",
        file=sys.stderr,
    )
    return records


def read_buildings() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for page in iter_features(
        BUILDINGS,
        out_fields=BUILDING_FIELDS,
        where="NOMBRE <> DIRECCION",
        return_geometry=True,
        order_by="OBJECTID",
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            name = clean(attrs.get("NOMBRE"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            key = clean(attrs.get("LOTCODIGO")) or (
                f"oid{attrs['OBJECTID']}" if attrs.get("OBJECTID") is not None else None
            )
            if name is None or wkt is None or key is None or is_address(name):
                unusable += 1
                continue
            landmark_id = f"bog-bic-{key}"
            if landmark_id in seen:
                unusable += 1
                continue
            seen.add(landmark_id)
            scope = clean(attrs.get("AMBITO"))
            records.append(
                {
                    "landmark_id": landmark_id,
                    "name": name,
                    "geometry_raw": wkt,
                    "address": clean(attrs.get("DIRECCION")),
                    "category": (
                        f"Bien de Interés Cultural ({scope})"
                        if scope and scope != "N.A."
                        else "Bien de Interés Cultural"
                    ),
                    "borough": LOCALIDADES.get(clean(attrs.get("LOCALIDAD")) or ""),
                }
            )
    print(
        f"Bogotá heritage buildings: {len(records)} named properties kept, "
        f"{unusable} dropped (no name, geometry or lot code, a repeated lot, or "
        f"a name that is an address in another spelling)",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["COBOG"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "address": pa.array([r["address"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
            "borough": pa.array([r["borough"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_monuments() + read_buildings()))
