#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bogotá's urban tree census (Arbolado Urbano), from the Jardín Botánico's geoportal.

Source: `IDECA/CensoArbol` on `geoportal.jbb.gov.co`, the live feature layer
behind the "Arbolado Urbano. Bogotá D.C." dataset on `datosabiertos.bogota.gov.co`,
published by the Jardín Botánico de Bogotá José Celestino Mutis under CC BY 4.0.
It is the public face of SIGAU, the city's operational urban-tree system, and
it is large: **1,390,646 points** across the twenty localidades, updated in
place (the layer's `Fecha_Actualizacion` runs to mid-2026 and the catalogue
entry was republished in August 2026).

Read through `_arcgis_shared` like every other ArcGIS source here: 2,000 rows a
page, ordered by `OBJECTID`, ~700 requests at just under a second each.  The
bulk downloads on the catalogue (a 656 MB GeoJSON, a 288 MB GeoPackage) are
snapshots of the same layer and would be one request instead of seven hundred,
but they are cut by hand and the feature layer is what the catalogue's own
"Esri REST" resource points at; the layer is the source of record.

**Position comes from the `Latitud`/`Longitud` attributes, not the geometry.**
The layer's stored geometry is broken -- its published extent runs to
`x = 5.68e-14`, and a `?f=json` sample returns every point at (0, 0) -- while
the two attribute columns carry a clean WGS84 position on every row
(4.469-4.826 N, 74.221-74.012 W over the whole layer).  So this reads with
`returnGeometry=false`, which also makes the pages a third the size.

**Species is a Spanish common name, and every one of the 503 values is
resolved by `_spanish_species`.**  `Nombre_Esp` is a comma-separated list of
the names one species goes by in Bogotá -- `Chicala, chirlobirlo, flor
amarillo` is *Tecoma stans* -- and `Con_Especie_ID` is SIGAU's own species
code, but the code's dictionary (the binomial) is not in the public layer.
Read that module's docstring before touching a mapping: the table is
curated, checked value by value against POWO, and the common names are
Andean and often mean something different from what the same word means in
Spain or Mexico (`Roble` is *Quercus humboldtii* here, `Cedro` is *Cedrela
montana*).  `NN` (3,994 rows) is the inventory's own "not identified" and
publishes as `Unknown`.

**The layer includes shrubs, palms and tree ferns and says so** ("biotipos
árbol, arbusto, palma y helecho arborescente").  They are kept: Copenhagen and
Amsterdam publish their shrubs in the same register too, the species column
says what each one is, and dropping a *Fuchsia boliviana* because it is not a
tree would throw away 7,480 identified plants the city chose to inventory.

**No diameter is published.**  SIGAU records dimensions internally and the
public layer carries only `Altura_Total` (height in metres, no canonical
column here), so `diameter_at_breast_height` is null for the whole city --
the same position Bogotá's neighbour Buenos Aires would be in without its
census diameters, and what the crown model's age fallback exists for, except
that no planting date is published either.  The reviewed-aerial-imagery lane
is the route to a size for this city.

`Codigo_Localidad` is the localidad, Bogotá's borough, and rides the shared
`borough` column the way London's borough and Tokyo's ward do; the layer
publishes the code and its coded-value domain publishes the name.  `0` (Sin
Localidad) and `99` (Fuera del área urbana) are null.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, coded_value_domain, iter_attributes
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from _spanish_species import species_from_spanish_name

LAYER = FeatureLayer(
    "https://geoportal.jbb.gov.co/agc/rest/services/IDECA/CensoArbol/FeatureServer/0",
    timeout=180,
)

# `Altura_Total`, `Tipo_Emplazamiento`, `Cod_sist_emplaz`, `Codigo_UPZ` and
# `Codigo_Scat` are read and dropped on purpose: none has a canonical column.
OUT_FIELDS = "Codigo_Arbol,Nombre_Esp,Con_Especie_ID,Codigo_Localidad,Latitud,Longitud"

# Localidad codes that are not a localidad.
NO_BOROUGH = {"0", "99"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_coordinate(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN


def make_transform(localidad_names: dict[str, str]):
    def transform(rows: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        borough: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for rec in rows:
            code = clean(rec.get("Codigo_Arbol"))
            # `Codigo_Arbol` is null on no row and repeats on none (checked over
            # the whole layer with a `having count > 1` statistics query, not a
            # first page); a missing one would still be a row the grain cannot
            # hold, so it is dropped here rather than left for the join to lose.
            if code is None:
                continue
            tree_id.append(f"bog-{code}")
            species.append(species_from_spanish_name(rec.get("Nombre_Esp")))
            localidad = clean(rec.get("Codigo_Localidad"))
            borough.append(
                localidad_names.get(localidad)
                if localidad and localidad not in NO_BOROUGH
                else None
            )
            # SIGAU records no planting date in the public layer.  Still a typed
            # date32 column: an untyped pa.null() lands in the parquet as INT32.
            plant_date.append(None)
            latitude.append(parse_coordinate(rec.get("Latitud")))
            longitude.append(parse_coordinate(rec.get("Longitud")))
            dbh.append(None)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["COBOG"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "borough": pa.array(borough, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    return transform


def main() -> None:
    localidad_names = coded_value_domain(LAYER, "Codigo_Localidad")
    table = stream_to_table(
        iter_attributes(LAYER, out_fields=OUT_FIELDS, order_by="OBJECTID"),
        make_transform(localidad_names),
        label="Bogotá OpenData",
    )
    table = validate_coordinates(table, city="Bogotá", city_code="COBOG")
    table = enforce_tree_schema(table, city="Bogotá", data_source="BOGOTA_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
