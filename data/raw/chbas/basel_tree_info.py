#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Basel's tree register (Baumkataster: Baumbestand), from data.bs.ch.

Source: OpenDataSoft dataset 100052 on the canton's open-data portal
(https://data.bs.ch/explore/dataset/100052/), published by the Stadtgärtnerei
under CC BY 4.0 ("Geodaten Kanton Basel-Stadt"), refreshed daily.  It is the
same layer as `ms:BA_Baeume` on wfs.geo.bs.ch: the trees the Stadtgärtnerei
maintains in the city of Basel and the trees Riehen maintains in Riehen and
Bettingen -- **32,528 rows** when this was written.  The bulk export endpoint
returns the whole layer as one ~1 MB parquet; the records API caps its offset
at 10,000 and is not used.

**Keyed on `ba_baumnr`**, the tree number (`BS003970`, `BR...` for Riehen,
`BB...` for Bettingen), verified unique and non-null over all 32,528 rows.
`gml_id` (`BA_Baeume.51784`) is the server's feature id, unique too, but a
row number in a map server, not the tree's identity.

**Species is `baumart_lateinisch`**, the Latin half of `ba_art` ("Acer
platanoides (Spitz-Ahorn)"); the German common name is not mapped.  Cultivars
are double-quoted (`Aesculus hippocastanum "Baumannii"`), which the shared
hygiene splits into species and cultivar.  The register's own habits handled
here: `hybr.` before a cultivar (`Ulmus hybr. "Sapporo Autumn Gold"`) is
dropped, as Copenhagen's is, and a placeholder cultivar `"cv."` is removed.
15 rows have no species and publish as `Unknown`.

**No stem size is published.**  The canton's model (BA_Baumkataster) records
`Stammumfang` only on the felling list, not on standing trees; the export and
the WFS both carry none, so `diameter_at_breast_height` is null throughout.

**Planting date.**  `timeposition` is the register's `Pflanzdatum`, set on
22,888 rows (1769 to the current year; many are January 1 or March 1 of a
year, which is how the register writes a year it knows only to the year).
`ba_standjahr` is "years at the site, counted from the planting date", and
8,092 rows carry it with no `Pflanzdatum`; on every dated row
`year(Pflanzdatum) + ba_standjahr` is one of two adjacent values (the stamp
is computed against a reference date late in the year), so the undated rows
are published as January 1 of `modal sum - ba_standjahr`.  The modal sum is
measured from the dated rows on every run, and the ingest refuses to publish
if fewer than 95% of dated rows land within one year of it.  `ba_baumalter`
is `ba_standjahr` plus an assumed nursery age (9 on 86% of rows) and is not
used.

`ba_gemeinde` (Basel or Riehen) rides the shared `borough` column, except
that the trees in Riehen's maintenance district `Bettingen` -- which lie in
the municipality of Bettingen -- are published as Bettingen.  `ba_gruppe`
(street tree, park, school ...) has no row that is not a standing tree; the
felling list is a separate layer.
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    download_parquet,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    parse_wkb_point,
    validate_coordinates,
)

DATASET = "100052"
FIELDS = [
    "ba_baumnr",
    "baumart_lateinisch",
    "timeposition",
    "ba_standjahr",
    "ba_kreis",
    "ba_gemeinde",
    "geo_point_2d",
]
SOURCE_URL = (
    f"https://data.bs.ch/api/explore/v2.1/catalog/datasets/{DATASET}/exports/parquet"
    f"?select={','.join(FIELDS)}&timezone=UTC"
)

# The share of dated rows whose `year + ba_standjahr` must fall within one
# year of the modal sum before `ba_standjahr` is trusted on undated rows.
STANDJAHR_AGREEMENT = 0.95


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(value) -> str | None:
    text = clean(value)
    if text is None:
        return None
    # `Ulmus hybr. "Sapporo Autumn Gold"`: a hybrid cultivar is a cultivar of
    # the genus; `hybr.` would otherwise be read as an epithet.
    text = text.replace(' hybr. "', ' "')
    # `Prunus persica "cv."`: an unnamed cultivar is no cultivar.
    text = text.replace(' "cv."', "")
    return text


def standjahr_offset(dates: list, standjahre: list) -> int:
    """The modal `year(Pflanzdatum) + ba_standjahr` over rows carrying both."""
    sums = Counter(
        d.year + s for d, s in zip(dates, standjahre) if d is not None and s is not None
    )
    if not sums:
        raise RuntimeError("Basel ingest: no row carries both Pflanzdatum and ba_standjahr")
    mode, _ = sums.most_common(1)[0]
    total = sum(sums.values())
    near = sum(n for s, n in sums.items() if abs(s - mode) <= 1)
    if near / total < STANDJAHR_AGREEMENT:
        raise RuntimeError(
            f"Basel ingest: only {near}/{total} dated rows have year + ba_standjahr "
            f"within a year of {mode}; ba_standjahr no longer counts years since planting"
        )
    return mode


def transform(table: pa.Table) -> pa.Table:
    missing = set(FIELDS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Basel ingest: export is missing field(s) {sorted(missing)}")

    ids = table["ba_baumnr"].to_pylist()
    names = table["baumart_lateinisch"].to_pylist()
    dates = table["timeposition"].to_pylist()
    standjahre = table["ba_standjahr"].to_pylist()
    kreise = table["ba_kreis"].to_pylist()
    gemeinden = table["ba_gemeinde"].to_pylist()
    points = table["geo_point_2d"].to_pylist()

    offset = standjahr_offset(dates, standjahre)

    tree_id: list[str] = []
    species: list[str | None] = []
    borough: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    no_id = 0
    from_standjahr = 0

    for nr, name, planted, standjahr, kreis, gemeinde, point in zip(
        ids, names, dates, standjahre, kreise, gemeinden, points
    ):
        nr = clean(nr)
        if nr is None:
            no_id += 1
            continue
        lon, lat = parse_wkb_point(point)
        tree_id.append(f"basel-{nr}")
        species.append(parse_species(name))
        borough.append("Bettingen" if clean(kreis) == "Bettingen" else clean(gemeinde))
        if planted is None and standjahr is not None:
            planted = parse_plant_date_year(offset - standjahr)
            from_standjahr += planted is not None
        plant_date.append(planted)
        latitude.append(lat)
        longitude.append(lon)

    print(
        f"Basel OpenData: {table.num_rows} rows; dropped {no_id} with no ba_baumnr; "
        f"{from_standjahr} plant date(s) from ba_standjahr (year = {offset} - standjahr)",
        file=sys.stderr,
    )
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CHBAS"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "borough": pa.array(borough, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * len(tree_id), type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(pq.read_table(download_parquet(SOURCE_URL)))
    table = validate_coordinates(table, city="Basel", city_code="CHBAS")
    table = enforce_tree_schema(table, city="Basel", data_source="BASEL_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
