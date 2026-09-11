#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Helsinki's street and park tree register (puurekisteri), from the city's WFS.

Source: `avoindata:Puurekisteri_piste` on `kartta.hel.fi`, the City of
Helsinki's open GeoServer, catalogued on Helsinki Region Infoshare as
"Helsingin kaupungin puurekisteri" under CC BY 4.0.  It is the Urban
Environment Division's register of the trees on public land -- streets,
squares and parks; no forest trees and no trees on private plots --
**66,382 points** when this was written.  The register's own caveat, which
belongs here too: the street trees are covered fairly completely and the park
trees only partly, and the data is not updated systematically.

**`tunnus` is the register number and the key** (`K-` for a street tree, `P-`
for a park tree).  Checked over the whole layer: 66,381 distinct values in
66,382 rows, none null -- one number (`K-49811`) is carried by two trees, and
both are dropped with a count rather than one of them being guessed at.

**Species is a genus and an epithet in two columns**, `suku` and `laji`, and
the epithet column is used for more than epithets:

* `sp.` (7,700 rows) and `Määrittelemättä` ("unspecified", 294) mean the
  genus alone, and `Määrittelemättä` in *both* columns is `Unknown`;
* a hybrid is written `x vulgaris`, `x rubens 'Lasipalatsi'`, which the
  shared hygiene reads as written (`Tilia x vulgaris` is a synonym it folds
  onto *Tilia x europaea*);
* a capitalised value is a cultivar or a group, not an epithet -- `Makamik`
  and `'Dodong'` are selections of *Malus* and *Sorbus*, `Purpurea-Ryhmä`
  is the purple-beech Group.  A selection becomes the tree's `cultivar`; a
  Group is not a cultivar and is dropped to the genus.  Left alone, the
  hygiene would lowercase `Makamik` into an invented epithet.

**`kokoluokka` is a diameter size class, in centimetres**, on 57,436 rows
(`0 - 10 cm`, `10 - 20 cm`, `20 - 30 cm`, `30 - 50 cm`, `50 - 70 cm`,
`70 cm -`).  Each is read as its midpoint, the open-topped top class as its
lower bound plus half the previous class's width -- the reconstruction
Halifax's and Denver's classes get, wrong by a few centimetres per tree and
right in aggregate.

`istutusvuosi` is a planting year on 15,122 rows and publishes as January 1
of that year.  `paatyyppi` (street or park) and the street and park names
are read and dropped: no canonical column.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)
from _wfs_shared import WfsLayer, iter_wfs_features

LAYER = WfsLayer("https://kartta.hel.fi/ws/geoserver/avoindata/wfs", "avoindata:Puurekisteri_piste", timeout=300)

PROPERTIES = ["id", "tunnus", "suku", "laji", "kokoluokka", "istutusvuosi", "geom"]

# Diameter classes, cm -> midpoint.  `70 cm -` is open-topped.
DBH_CLASS_MIDPOINT_CM = {
    "0 - 10 cm": 5.0,
    "10 - 20 cm": 15.0,
    "20 - 30 cm": 25.0,
    "30 - 50 cm": 40.0,
    "50 - 70 cm": 60.0,
    "70 cm -": 80.0,
}

UNSPECIFIED = "Määrittelemättä"
GENUS_ONLY = {"", "sp.", "sp", UNSPECIFIED}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(suku, laji) -> tuple[str | None, str | None]:
    """`(species, cultivar)` from the register's genus and epithet columns."""
    genus = clean(suku)
    if genus is None or genus == UNSPECIFIED:
        return None, None
    epithet = clean(laji) or ""
    if epithet in GENUS_ONLY:
        return genus, None
    if epithet[0].islower():
        # `platanoides`, `x vulgaris`, `x rubens 'Lasipalatsi'`: a real name,
        # with any quoted cultivar for the shared hygiene to split off.
        return f"{genus} {epithet}", None
    if epithet.lower().endswith("ryhmä"):
        # `Purpurea-Ryhmä`: a cultivar Group, which is not a cultivar.
        return genus, None
    return genus, epithet.strip("'\"") or None


def parse_dbh(value) -> float | None:
    return cm_to_inches(DBH_CLASS_MIDPOINT_CM.get(clean(value) or ""))


def make_transform(counts: dict[str, int]):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            props = feature.get("properties") or {}
            number = clean(props.get("tunnus"))
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            if number is None or not coords:
                counts["unusable"] += 1
                continue
            if counts["by_number"].get(number, 0) > 1:
                counts["repeated"] += 1
                continue
            name, selection = parse_species(props.get("suku"), props.get("laji"))
            tree_id.append(f"hel-{number}")
            species.append(name)
            cultivar.append(selection)
            plant_date.append(parse_plant_date_year(props.get("istutusvuosi")))
            longitude.append(float(coords[0]))
            latitude.append(float(coords[1]))
            dbh.append(parse_dbh(props.get("kokoluokka")))

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["FIHEL"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    return transform


def main() -> None:
    # Two passes over the register: the numbers first, so that a register
    # number carried by two trees can be dropped from both rows rather than
    # the first one winning by accident of sort order.
    pages = list(iter_wfs_features(LAYER, sort_by="id", properties=PROPERTIES, page_size=20000))
    by_number: dict[str, int] = {}
    for page in pages:
        for feature in page:
            number = clean((feature.get("properties") or {}).get("tunnus"))
            if number is not None:
                by_number[number] = by_number.get(number, 0) + 1
    counts = {"unusable": 0, "repeated": 0, "by_number": by_number}
    table = stream_to_table(pages, make_transform(counts), label="Helsinki OpenData")
    print(
        f"Helsinki OpenData: dropped {counts['unusable']} row(s) with no register "
        f"number or no point and {counts['repeated']} carrying a register number "
        f"another tree also carries, leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Helsinki", city_code="FIHEL")
    table = enforce_tree_schema(table, city="Helsinki", data_source="HELSINKI_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
