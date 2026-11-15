#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Milan's municipal tree inventory, from the Comune's own export.

Source: "Territorio: localizzazione degli alberi" (DS2484) on
dati.comune.milano.it, the georeferenced register of every tree the Comune
manages -- 255,075 rows in the 2023-03-31 release, CC BY 4.0.

**Read from a pinned copy, because the portal no longer serves it.**  Since the
portal's 2026 migration the dataset is gone from CKAN (`package_show`,
`package_search`, the dataset page and every download path answer 404/403),
and the city geoportal does not answer at all.  The 2023-03-31 CSV survives,
byte for byte as the Comune published it, in a public thesis repository, and
this reads it at a pinned commit so the bytes cannot change under the city.
The watermark is therefore a constant (milan_update_time.py).  When DS2484
comes back, point `SOURCE_URL` at its CKAN resource and the probe at
`shared.platforms.ckan.data_last_modified`.

Columns, as the Comune publishes them (`;`-separated, UTF-8):

  * `obj_id` -- the Comune's object id, unique and non-null over all 255,075
    rows; the key (`mil-{obj_id}`).  `codice` is one constant, `id_area` the
    green area.
  * `genere`, `specie`, `varieta` -- genus, epithet (`x acerifolia`, `spp`),
    cultivar (`Kanzan`, `Pissardii`).  105 rows have no genus; they publish
    as Unknown.
  * `diam_tronc` -- trunk diameter in **centimetres** (median 13; 8 cm is the
    size the Comune records a new planting at, 70k rows); 0 is unmeasured.
  * `diam_chiom` -- crown diameter in metres.
  * `data_ini` -- the planting date, `2002-12-20T00:00+01:00[Europe/Paris]`.
    It tracks trunk size decade by decade (1950s 70 cm, 1990s 25 cm, 2010s
    8 cm), so it is an age; the day is often stamped (`-10-21` on 60k rows),
    the year is not.  `1899-12-30` -- the spreadsheet zero date, 194 rows of
    saplings -- is a placeholder, and so is any other pre-1900 date on a trunk
    under 50 cm (the 1890s average 8 cm), so both publish as null.
  * `h_m` -- height in metres; not published (height comes from canopy
    surveys, docs/TREE_HEIGHT.md).
  * `LONG_X_4326`, `LAT_Y_4326` -- WGS84.
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    get_with_retry,
    normalize_species_parts,
    validate_coordinates,
)

SOURCE_URL = (
    "https://raw.githubusercontent.com/CBeneduce/computer-science-bachelor-degree-thesis/"
    "65c3331803ea74010a577b46fee761c1697be0b7/data-integration/datasets/ds2484_alberi_20230331.csv"
)
DATA_SOURCE = "MILAN_OPENDATA"

COLUMNS = (
    "obj_id", "genere", "specie", "varieta", "diam_tronc", "diam_chiom",
    "data_ini", "LONG_X_4326", "LAT_Y_4326",
)

# The spreadsheet zero date; see the module docstring.
PLACEHOLDER_PLANT_DATE = date(1899, 12, 30)
# A tree planted before 1900 has a trunk of at least this many centimetres in
# Milan's own records (the 1880s and earlier average 65-340 cm).
OLD_PLANTING_MIN_DIAMETER_CM = 50.0

CM_PER_INCH = 2.54


def download() -> bytes:
    return get_with_retry(SOURCE_URL, timeout=300).content


def read_source(body: bytes) -> pa.Table:
    table = pv.read_csv(
        io.BytesIO(body),
        parse_options=pv.ParseOptions(delimiter=";"),
        convert_options=pv.ConvertOptions(
            include_columns=list(COLUMNS),
            column_types={c: pa.string() for c in COLUMNS},
            strings_can_be_null=True,
        ),
    )
    missing = set(COLUMNS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Milan inventory: columns {sorted(missing)} are missing")
    return table


def _float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value.replace(",", "."))


def parse_plant_date(value: str | None, diameter_cm: float | None) -> date | None:
    if not value or len(value) < 10:
        return None
    try:
        planted = date.fromisoformat(value[:10])
    except ValueError:
        return None
    if planted == PLACEHOLDER_PLANT_DATE:
        return None
    if planted.year < 1900 and (diameter_cm is None or diameter_cm < OLD_PLANTING_MIN_DIAMETER_CM):
        return None
    return planted


def transform(source: pa.Table) -> pa.Table:
    """The canonical tree columns from the Comune's export."""
    rows = source.to_pydict()
    n = source.num_rows
    unparsed = 0
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []
    lat: list[float | None] = []
    lon: list[float | None] = []
    for i in range(n):
        obj_id = (rows["obj_id"][i] or "").strip()
        tree_id.append(f"mil-{obj_id}" if obj_id else None)
        species.append(normalize_species_parts(rows["genere"][i], rows["specie"][i]))
        variety = (rows["varieta"][i] or "").strip()
        cultivar.append(variety or None)
        try:
            diameter_cm = _float(rows["diam_tronc"][i])
            crown_m = _float(rows["diam_chiom"][i])
            lat.append(_float(rows["LAT_Y_4326"][i]))
            lon.append(_float(rows["LONG_X_4326"][i]))
        except ValueError:
            unparsed += 1
            diameter_cm = crown_m = None
            lat.append(None)
            lon.append(None)
        dbh.append(diameter_cm / CM_PER_INCH if diameter_cm is not None else None)
        crown.append(crown_m)
        plant_date.append(parse_plant_date(rows["data_ini"][i], diameter_cm))
    # A numeric field that stops parsing is a format change, not noise.
    if n and unparsed / n > 0.01:
        raise RuntimeError(
            f"Milan inventory: {unparsed} of {n} rows have unreadable numbers"
        )
    table = pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["ITMIL"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(lat, type=pa.float64()),
            "longitude": pa.array(lon, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )
    unidentified = pc.sum(pc.is_null(table["tree_id"])).as_py() or 0
    if unidentified:
        table = table.filter(pc.is_valid(table["tree_id"]))
        print(f"Milan inventory: dropped {unidentified} row(s) with no obj_id", file=sys.stderr)
    return table


def load_published() -> pa.Table:
    """The inventory exactly as this partition publishes it.

    milan_monumental_trees.py links the register to these rows, so it must see
    the same ids after the same filters.
    """
    table = transform(read_source(download()))
    table = validate_coordinates(table, city="Milan", city_code="ITMIL")
    return enforce_tree_schema(table, city="Milan", data_source=DATA_SOURCE)


if __name__ == "__main__":
    emit(load_published())
