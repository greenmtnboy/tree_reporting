#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Copenhagen's tree register (træregister), from the municipality's WFS.

Source: `k101:trae_basis` on `wfs-kbhkort.kk.dk`, the City of Copenhagen's
GeoServer, catalogued as "Træer" / `trae_basis` on the Danish open data portal
(opendata.dk) under CC BY 4.0.  It is the city's master register of every tree
it manages -- street trees, park trees, trees in cemeteries and a small set of
registered private trees -- **68,561 rows** when this was written, ninety-odd
columns of which this reads ten.

**Keyed on `uuid`, and the register carries duplicates of two kinds.**  `id`
looks like the tree number and is not one: 28109 is shared by seventeen
different trees in one park.  `uuid` is unique per registered tree, except that
61 rows are published twice verbatim -- identical uuid, position, species and
stamps -- which reads as a join artefact on the city's side.  The second copy
is dropped here with a count, so the grain holds.

**Species is a Latin name in `traeart`, with the register's own spellings.**
Cultivars are quoted (`Tilia x europaea 'Pallida'`), which `enforce_tree_schema`
already splits into species and cultivar.  Three habits are the register's
own and are normalised before that:

* a hybrid is written `hybr.` -- `Platanus hybr. acerifolia`, `Malus hybr.
  'Evereste'` -- which `sanitize_species` would otherwise read as an epithet
  and truncate to the genus.  `hybr.` before an epithet becomes the `x` mark;
  `hybr.` before a cultivar or a bare capitalised selection is dropped, since
  a hybrid cultivar is a cultivar of the genus (`Malus 'Evereste'`).
* a name is sometimes written with stress accents (`Liriodéndron tulipífera`,
  `Quércus x túrneri`), which the shared hygiene strips.
* `Aesculus hippoc. 'Baumannii'` abbreviates the epithet; it is the only one.

26,799 rows -- most park and cemetery trees -- carry no `traeart`; 170 of
those carry a genus in `slaegt` (`Tilia sp.`), which is used.  The rest
publish as `Unknown`.  `busk_trae` marks 659 rows as a shrub (`Busk`); they
are kept with their binomial, as Bogotá's and Amsterdam's shrubs are.

**`stammeomfang` is a trunk-circumference class, in centimetres, and is
registered on 1,277 rows** -- the nursery size classes a tree was planted at
(`18 - 20`, `20 - 25`, ... `> 60`).  Each is read as its midpoint and
converted circumference to diameter; an open-topped `> 60` takes 70.  A
midpoint is wrong by a centimetre per tree and right in aggregate, which is
the same reconstruction Halifax's and Denver's classes get.  `Ikke
registreret` and null are null.

`planteaar` is the planting year on 40,082 rows and is published as January 1
of that year.  `bydelsnavn` is the bydel, Copenhagen's borough, and rides the
shared `borough` column.  `opdateret_dato` is read by the freshness probe and
not here.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)
from _wfs_shared import WfsLayer, iter_wfs_features

LAYER = WfsLayer("https://wfs-kbhkort.kk.dk/k101/ows", "k101:trae_basis", timeout=300)

PROPERTIES = [
    "uuid", "kategori", "traeart", "slaegt", "planteaar", "busk_trae",
    "bydelsnavn", "stammeomfang", "wkb_geometry",
]

# Circumference classes, cm -> the class midpoint.  `> 60` is open-topped and
# takes its lower bound plus half the width the classes settle at.
CIRCUMFERENCE_CLASS_CM = {
    "< 18": 14.0,
    "18 - 20": 19.0,
    "20 - 25": 22.5,
    "25 - 30": 27.5,
    "30 - 40": 35.0,
    "40 - 60": 50.0,
    "> 60": 70.0,
}

_HYBRID_BEFORE_EPITHET = re.compile(r"\bhybr(?:id|\.)\s+(?=[a-z])")
_HYBRID_BEFORE_SELECTION = re.compile(r"\bhybr(?:id|\.)\s+(?=['A-Z])")
_BARE_SELECTION = re.compile(r"^([A-Z][a-z]+) ([A-Z][A-Za-z.]+)$")
_ABBREVIATIONS = {"hippoc.": "hippocastanum"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(traeart, slaegt) -> str | None:
    """The register's `traeart`, or its genus, spelled the way the hygiene reads."""
    text = clean(traeart) or clean(slaegt)
    if text is None:
        return None
    for short, full in _ABBREVIATIONS.items():
        text = text.replace(f" {short} ", f" {full} ")
    text = _HYBRID_BEFORE_EPITHET.sub("x ", text)
    text = _HYBRID_BEFORE_SELECTION.sub("", text)
    # `Malus Hyslop`: a selection written unquoted after the genus.
    text = _BARE_SELECTION.sub(r"\1 '\2'", text)
    return text


def parse_dbh(value) -> float | None:
    """A circumference class to a diameter in inches, or None."""
    return circumference_cm_to_dbh_inches(CIRCUMFERENCE_CLASS_CM.get(clean(value) or ""))


def make_transform(seen: set[str], dropped: list[int]):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        borough: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            props = feature.get("properties") or {}
            uuid = clean(props.get("uuid"))
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            if uuid is None or not coords:
                dropped[0] += 1
                continue
            if uuid in seen:
                dropped[1] += 1
                continue
            seen.add(uuid)
            tree_id.append(f"cph-{uuid}")
            species.append(parse_species(props.get("traeart"), props.get("slaegt")))
            borough.append(clean(props.get("bydelsnavn")))
            plant_date.append(parse_plant_date_year(clean(props.get("planteaar"))))
            longitude.append(float(coords[0]))
            latitude.append(float(coords[1]))
            dbh.append(parse_dbh(props.get("stammeomfang")))

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["DKCPH"] * len(tree_id), type=pa.string()),
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
    seen: set[str] = set()
    dropped = [0, 0]
    table = stream_to_table(
        iter_wfs_features(LAYER, sort_by="uuid", properties=PROPERTIES, page_size=20000),
        make_transform(seen, dropped),
        label="Copenhagen OpenData",
    )
    print(
        f"Copenhagen OpenData: dropped {dropped[0]} row(s) with no uuid or no point "
        f"and {dropped[1]} verbatim duplicate(s) of a uuid, leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Copenhagen", city_code="DKCPH")
    table = enforce_tree_schema(table, city="Copenhagen", data_source="COPENHAGEN_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
