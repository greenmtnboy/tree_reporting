#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Colac Otway Shire, VIC's municipal tree inventory.

Source: "Colac Otway Shire Trees" on data.gov.au (dataset
`colac-otway-shire-trees`, published by the Colac Otway Shire's own
organisation there): "Point data for location of trees in Colac Otway
Shire", under Creative Commons Attribution 3.0 Australia (the dataset's
licence field).  The shire has no open-data portal or ArcGIS Hub of its own;
data.gov.au is where it publishes.

**A 2015 snapshot.**  Every resource is dated 2015-01-11 and the CSV's HTTP
Last-Modified is the same day; nothing newer has been published (searched
data.gov.au, data.vic.gov.au and ArcGIS Online on 2026-11-22).  It covers
street trees in Colac, Elliminyt, Colac East and Cressy (3,411 rows) and
seven park trees -- not the shire's whole stock.  Cressy's 116 trees lie
north of -38.05, inside the LGA (whose boundary reaches -38.02).

**Read from the CSV resource** (`costreesopendata.csv`, 3,418 rows):
`GPS_lat`/`GPS_lon` are WGS84 decimal degrees.  The SHP zip and KML hold the
same rows; data.gov.au's GeoServer WFS/GeoJSON resources answer "Schema ...
does not exist" (2026-11-22), so they are not used.

**`Tree_ID` is the key**: the shire's asset number, numeric, 6004..13527,
none null, 3,414 distinct over 3,418 rows (not a 1..N row number).  Three
ids repeat over seven rows (`7813` three times, with the note "3 trees";
`8146` and `12130` twice, at points 10-40 m apart); the id cannot tell
those rows apart, so every row of a repeated id is dropped with a logged
count.  Prefixed `col-`.

**`Genus_Desc` + `Spec_Desc` are the scientific name**, written lower case
and often misspelt (`Lophostermon`, `Photina`, `Plantanus`, `stypheliodes`);
misspellings proper are left to the shared misspelling map.  `SOURCE_NAMES`
restores the structural oddities keyed on the exact pair: a hybrid written
without its mark (`Prunus blierana`), cultivars written as the epithet
(`Prunus elvins`, `Fraxinus raywoodii`, `Photina robusta`), a common name written as the
epithet (`Tristania kanooka`, the Water Gum) and a misfiled genus
(`Leptospermum confertus`, "Queensland Brush Box").  A row with no genus
(eight) publishes with no species: its genus would come only from the
common name, which is not read.

**`Diam_Breast_Height_mm` is read as a trunk circumference in mm**, whatever
its name: it tracks height at almost exactly 100 mm per metre (median 100 at
1 m tall, 400 at 4 m, 1000 at 10 m), which as a diameter would give a 1 m
whip a 10 cm trunk and a 5 m Lophostemon confertus an 80 cm one (median 800
for the 1970s plantings); as a girth they are 3 cm and 25 cm.  Values are
estimated to the nearest 50-100 mm.  `0` (155 rows, mostly with height 0)
and three values under 10 mm are unmeasured and publish as null.  `dbh_in = circ_mm / (10 * pi * 2.54)`.

`Year_Planted` is a year, mostly rounded to five; `0` (137 rows) is
unrecorded and `1900` (49 rows, large mature oaks, planes and ashes) is the stamped value
for "old" (the shared `PLACEHOLDER_PLANT_DATE`, nulled here too).  `Height_m` is a recorded
height but the schema has no recorded-height column; `Canopy_width_m` is
filled on six rows only and is not published.  No row is a vacant site or a
stump (`Location_Type` is "Street Trees" or "Parks / Sports Ground trees",
`Life_stage_desc` Young/Semi-Mature/Mature/Senescent).
"""

import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_species_parts,
    validate_coordinates,
)
from shared.platforms.ckan import CkanResource, read_csv_rows  # noqa: E402

# The CSV resource of the data.gov.au dataset; its download URL is read off
# `resource_show` each run.
RESOURCE = CkanResource("data.gov.au/data", "bcf1d62b-9e72-4eca-b183-418f83dedcea")

CITY_CODE = "AUCOL"
ID_PREFIX = "col"

REQUIRED = (
    "Tree_ID",
    "Genus_Desc",
    "Spec_Desc",
    "Diam_Breast_Height_mm",
    "Year_Planted",
    "GPS_lat",
    "GPS_lon",
)

# (genus, epithet) as written -> (scientific name, cultivar).
SOURCE_NAMES: dict[tuple[str, str], tuple[str, str | None]] = {
    # The hybrid purple-leaf plum, written without its mark (and misspelt).
    ("Prunus", "blierana"): ("Prunus x blireana", None),
    ("Prunus", "bilerara"): ("Prunus x blireana", None),
    # Cultivars written as the epithet.
    ("Prunus", "elvins"): ("Prunus", "Elvins"),
    ("Fraxinus", "raywoodii"): ("Fraxinus angustifolia", "Raywood"),
    # Photinia 'Robusta' (genus misspelt), sold under both P. x fraseri and
    # P. glabra, so only the genus is certain.
    ("Photina", "robusta"): ("Photinia", "Robusta"),
    # A common name (kanooka, the Water Gum) written as the epithet.
    ("Tristania", "kanooka"): ("Tristaniopsis laurina", None),
    # Brush box filed under the wrong genus (common name "Queensland Brush Box").
    ("Leptospermum", "confertus"): ("Lophostemon confertus", None),
    # The honey locust's genus misspelt, with a colour word as the epithet.
    ("Gledister", "tricolour"): ("Gleditsia triacanthos", None),
    ("Protea", "hybrid"): ("Protea", None),
}

PLACEHOLDER_YEARS = frozenset({0, 1900})

# A girth under 1 cm is no measurement: `0` (155 rows) and three slips (`1`
# on a 6 m Lophostemon, `5` on a 1 m Eugenia).  The next value up is 20 mm.
MIN_GIRTH_MM = 10.0


def clean(value: str | None) -> str:
    return " ".join((value or "").split())


def name_and_cultivar(genus_raw: str | None, epithet_raw: str | None) -> tuple[str | None, str | None]:
    genus = clean(genus_raw)
    epithet = clean(epithet_raw)
    if not genus:
        return None, None
    override = SOURCE_NAMES.get((genus, epithet))
    if override:
        return normalize_species(override[0]), override[1]
    return normalize_species(normalize_species_parts(genus, epithet)), None


class NumberReader:
    """Reads a numeric text column, refusing one it cannot read."""

    def __init__(self, column: str):
        self.column = column
        self.read = 0
        self.unreadable: Counter[str] = Counter()

    def __call__(self, value: str | None) -> float | None:
        text = clean(value)
        if not text:
            return None
        self.read += 1
        try:
            return float(text)
        except ValueError:
            self.unreadable[text] += 1
            return None

    def check(self) -> None:
        bad = sum(self.unreadable.values())
        if bad:
            print(
                f"Colac Otway ingest: {bad} unreadable `{self.column}` values: "
                f"{self.unreadable.most_common(10)}",
                file=sys.stderr,
            )
        if self.read and bad / self.read > 0.01:
            raise RuntimeError(
                f"Colac Otway: {bad} of {self.read} `{self.column}` values are not "
                "numbers; the column has changed shape"
            )


def transform(rows: list[dict]) -> pa.Table:
    if not rows:
        raise RuntimeError("Colac Otway CSV has no rows")
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        raise RuntimeError(f"Colac Otway CSV lacks columns {missing}: {sorted(rows[0])}")

    id_counts = Counter(clean(r.get("Tree_ID")) for r in rows)
    girth = NumberReader("Diam_Breast_Height_mm")
    year = NumberReader("Year_Planted")
    lat_reader = NumberReader("GPS_lat")
    lon_reader = NumberReader("GPS_lon")

    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    no_id = repeated = unmeasured = placeholder_years = 0
    for rec in rows:
        asset = clean(rec.get("Tree_ID"))
        if not asset.isdigit():
            no_id += 1
            continue
        if id_counts[asset] > 1:
            repeated += 1
            continue
        name, parsed_cultivar = name_and_cultivar(rec.get("Genus_Desc"), rec.get("Spec_Desc"))

        circ_mm = girth(rec.get("Diam_Breast_Height_mm"))
        if circ_mm is not None and circ_mm < MIN_GIRTH_MM:
            unmeasured += 1
            circ_mm = None

        planted = None
        y = year(rec.get("Year_Planted"))
        if y is not None:
            if int(y) in PLACEHOLDER_YEARS:
                placeholder_years += 1
            elif 1500 <= y <= 2100:
                planted = date(int(y), 1, 1)

        tree_id.append(f"{ID_PREFIX}-{int(asset)}")
        species.append(name)
        cultivar.append(parsed_cultivar if name else None)
        plant_date.append(planted)
        latitude.append(lat_reader(rec.get("GPS_lat")))
        longitude.append(lon_reader(rec.get("GPS_lon")))
        dbh.append(None if circ_mm is None else circ_mm / (10 * math.pi * 2.54))

    if no_id:
        print(f"Colac Otway ingest: dropped {no_id} rows with no numeric `Tree_ID`", file=sys.stderr)
    if repeated:
        dupes = sorted(k for k, v in id_counts.items() if v > 1)
        print(
            f"Colac Otway ingest: dropped {repeated} rows sharing a `Tree_ID` "
            f"({len(dupes)} ids: {dupes})",
            file=sys.stderr,
        )
    if unmeasured:
        print(
            f"Colac Otway ingest: {unmeasured} trees with girth under 1 cm (unmeasured); "
            "published with no diameter",
            file=sys.stderr,
        )
    if placeholder_years:
        print(
            f"Colac Otway ingest: {placeholder_years} `Year_Planted` of 0 or 1900 "
            "(unrecorded) published with no plant date",
            file=sys.stderr,
        )
    for reader in (girth, year, lat_reader, lon_reader):
        reader.check()

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array([CITY_CODE] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    rows = read_csv_rows(RESOURCE)
    print(f"Colac Otway ingest: {len(rows)} records in the CSV", file=sys.stderr)
    table = transform(rows)
    table = validate_coordinates(table, city="Colac Otway", city_code=CITY_CODE)
    table = enforce_tree_schema(table, city="Colac Otway", data_source="COLAC_OTWAY_OPENDATA")
    emit(table)
