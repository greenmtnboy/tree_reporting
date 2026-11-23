#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Manningham City Council street trees, from data.gov.au.

Source: "Manningham Street Trees" (`manningham-streettrees`) on data.gov.au's
CKAN, published by Manningham City Council under Creative Commons Attribution
3.0 Australia (the package's licence field).  The CSV resource `4797e8de`
(`manningham_street_trees.csv`, csv-geo-au, 8 MB, no datastore): 66,904 rows,
last modified 2019-11-10, with survey dates from 2008 to 2018.  A shapefile, a
WMS and a GeoServer WFS/GeoJSON sit beside it; on 2026-11-23 the WFS feature
type was gone ("Feature type ... unknown").  The council has no open-data
portal of its own and publishes no park or reserve trees.  Manningham
(Doncaster, Templestowe, Bulleen, Donvale, Warrandyte) is east of the City of
Melbourne, shares no boundary with it, and is inside AUMEL's territory, so it
is a partition of Melbourne rather than a city.

**The id is the coordinate, because the source has none.**  The columns are
`date1` (the survey date), `house`, `street`, `str_type`, `suburb`, `pcode`,
`alphatree` (the name's first letter), `species`, `height`, `dbh`,
`treearea` (a maintenance area) and `lat`/`lon`: no asset number, and the row
order is a file position.  So the id is
`coordinate_tree_id("manningham", lon, lat)`, Spokane's rule
(`usgeg/spokane_tree_info.py`).  One point carries two rows;
`drop_stacked_coordinates` drops both unless they name the same taxon.

* `species` is the scientific name, with the cultivar quoted
  (`Prunus cerasifera 'Nigra'`).  `Vacant Planting` (5,585 rows) and `Stump`
  (511) are site records, not trees, and are dropped here, because the flag is
  the source's own.  A common name welded on with a hyphen
  (`Acer saccharinum-Silver Maple`) and a parenthesised synonym
  (`Acacia provincialis (retinodes)`) are cut; `SOURCE_SPELLINGS` restores the
  structurally odd names keyed on the exact value.  Misspellings proper
  (`eugenoides`, `salibrus`) are left for the shared misspelling map.
* **`dbh` is a band in millimetres** (`0 - 100mm`, `100 - 250mm`,
  `250 - 500mm`, `500 - 1000mm`, `1000mm+`), read as the band's midpoint, the
  Hobsons Bay reconstruction; the open-topped `1000mm+` is its bound plus half
  the band below's width (1,250 mm).  An unknown band label counts as
  unreadable and more than 1% refuses the build.  `height` is a height band
  and is not published.  No crown spread or planting date is recorded;
  `date1` is when the tree was surveyed (a 15 m+ Monterey pine carries 2010).
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, read_csv_rows  # noqa: E402
from shared.ingest import (  # noqa: E402
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    normalize_species,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "data.gov.au/data", "4797e8de-9750-4ed7-9d84-ea318302b881", timeout=300
)
ID_PREFIX = "manningham"
REQUIRED = ("species", "dbh", "lat", "lon")

NOT_A_TREE = frozenset({"vacant planting", "stump"})

# Exact values (whitespace collapsed) that are not a binomial plus an optional
# quoted cultivar as written.
SOURCE_SPELLINGS: dict[str, str] = {
    "oleander": "Nerium oleander",
    "x Cupressocyparis leylandii": "Cupressus x leylandii",
    "xCupressocyparis leylandii": "Cupressus x leylandii",
    "cupressus x leylandii": "Cupressus x leylandii",
    "Prunus Sato-zakura Group": "Prunus serrulata",
    # A grafting note, not a selection (805 rows).
    "Corymbia ficifolia 'Grafted'": "Corymbia ficifolia",
    # Not a name this inventory's other rows explain.
    "Helliptica": "Unknown",
}

# `dbh` band -> midpoint in centimetres.
DBH_BAND_CM: dict[str, float] = {
    "0 - 100mm": 5.0,
    "100 - 250mm": 17.5,
    "250 - 500mm": 37.5,
    "500 - 1000mm": 75.0,
    "1000mm+": 125.0,
}
MAX_BAND_PARSE_FAILURES = 0.01

# `Acer saccharinum-Silver Maple`: a hyphen with no space before a capital.
WELDED_COMMON_NAME = re.compile(r"(?<=[a-z])-[A-Z].*$")
PARENTHESISED = re.compile(r"\s*\([^)]*\)")


def clean(value) -> str:
    return " ".join(str(value or "").split())


def as_float(value) -> float | None:
    text = clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def species_of(value) -> str | None:
    """The scientific name, the quoted cultivar kept as written."""
    text = clean(value)
    text = SOURCE_SPELLINGS.get(text, text)
    text = WELDED_COMMON_NAME.sub("", text)
    text = PARENTHESISED.sub("", text).strip()
    if not text:
        return None
    quote = text.find("'")
    if quote == -1:
        return normalize_species(text)
    taxon = normalize_species(text[:quote])
    return f"{taxon} {text[quote:]}" if taxon else None


def is_tree(rec: dict) -> bool:
    return clean(rec.get("species")).lower() not in NOT_A_TREE


class BandReader:
    """Reads the `dbh` band, counting the labels it does not know."""

    def __init__(self) -> None:
        self.read = 0
        self.failures: dict[str, int] = {}

    def __call__(self, value) -> float | None:
        self.read += 1
        text = clean(value)
        if not text:
            return None
        mid = DBH_BAND_CM.get(text)
        if mid is None:
            self.failures[text] = self.failures.get(text, 0) + 1
        return mid

    def check(self) -> None:
        bad = sum(self.failures.values())
        if not bad:
            return
        share = bad / max(self.read, 1)
        print(
            f"Manningham: {bad} unreadable `dbh` values ({share:.2%}): "
            f"{sorted(self.failures.items(), key=lambda kv: -kv[1])[:10]}",
            file=sys.stderr,
        )
        if share > MAX_BAND_PARSE_FAILURES:
            raise RuntimeError(
                f"Manningham `dbh`: {share:.2%} of rows carry a band label this "
                "ingest does not know; the council has changed its bands"
            )


def transform(rows: list[dict]) -> pa.Table:
    dbh_reader = BandReader()
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    not_a_tree = no_point = 0
    for rec in rows:
        if not is_tree(rec):
            not_a_tree += 1
            continue
        lat, lon = as_float(rec.get("lat")), as_float(rec.get("lon"))
        if lat is None or lon is None:
            no_point += 1
            continue
        cm = dbh_reader(rec.get("dbh"))
        tree_id.append(coordinate_tree_id(ID_PREFIX, lon, lat))
        species.append(species_of(rec.get("species")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm / 2.54 if cm is not None else None)

    print(
        f"Manningham: dropped {not_a_tree} vacant site/stump row(s), "
        f"{no_point} with no point",
        file=sys.stderr,
    )
    dbh_reader.check()

    n = len(tree_id)
    plant_date: list[date | None] = [None] * n
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUMEL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    rows = read_csv_rows(RESOURCE)
    if not rows:
        raise RuntimeError("Manningham: the CSV resource has no rows")
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        raise RuntimeError(f"Manningham CSV has no {missing} column(s) ({list(rows[0])})")
    print(f"Manningham: read {len(rows)} row(s)", file=sys.stderr)
    table = transform(rows)
    table = drop_stacked_coordinates(table, label="Manningham", identical=("species",))
    table = validate_coordinates(table, city="Melbourne", city_code="AUMEL")
    table = enforce_tree_schema(table, city="Melbourne", data_source="MANNINGHAM")
    emit(table)
