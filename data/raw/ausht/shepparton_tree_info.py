#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Greater Shepparton, VIC's municipal tree inventory.

Source: "Greater Shepparton City Council Street and Park Trees" on
data.gov.au (dataset `greater-shepparton-city-council-street-and-park-trees`,
published by the Greater Shepparton City Council's own organisation there),
under Creative Commons Attribution 4.0 International (the dataset's licence
field).  The council has no open-data portal of its own; data.gov.au is where
it publishes.

**Read from the CSV resource**, the council's own upload (re-uploaded
2023-03-30, HTTP Last-Modified the same day).  The dataset's other
resources are the same upload as a zipped shapefile and a GeoJSON, plus
data.gov.au's GeoServer WMS/WFS built from it; all carry the 2023-03-30
stamp and the same 44,201 features.  The CSV is the one that is already
WGS84 `lat`/`lon` and needs no geometry reader.  Every row has a position,
all inside the City of Greater Shepparton (-36.63..-36.22 N,
145.12..145.69 E).  `common` is empty on every row.

**`ref` is the key**: the council's tree asset reference (`TR0031221`),
44,195 distinct values over the 44,195 rows that have one (none repeated);
6 rows have none and are dropped with a logged count.  Prefixed `sht-`,
the reference kept as written (`sht-TR0031221`).

**`genus` + `species` are the scientific name**, the epithet written
capitalised and carrying the cultivar (`Excelsior 'Aurea'`, `Rubrum '
Fairview Flame'`, `minor "Variegata"`, and a few with only a closing quote,
`Superb'`).  A quoted cultivar becomes `cultivar`.  `Sp.`, `Not Specified`
and `Unknown` as the epithet leave the genus; a trailing `Cv` (a cultivar
the survey did not name) and a bare trailing `Subsp.` are dropped; `Aff.`
(a tree resembling a species, not identified as it) leaves the genus.
`Not Specified`, `Unknown` and `AAA - Species Unknown` as the genus publish
with no species.  `SOURCE_NAMES` restores cultivars written as the epithet
(`Callistemon Harkness`, `Fraxinus Raywood`) and the two Acer formula
hybrids, keyed on the exact pair; a cultivar group written as the epithet
(`Sato-Zakura Group`) becomes the cultivar.  A genus welded to another genus's epithet
(`Eucalyptus Platanoides 'Globosum'`, 38 rows; `Corymbia Excelsior 'Aurea'`)
is published as written: which half is wrong is not knowable here, and the
enrichment job's chimera list is where such names are kept out.
Misspellings proper (`Geijera Parvifolia`, `Lagunaria Patersonia`) are left
for the shared misspelling map.

**`dbh` is a band midpoint in centimetres**, published beside its band
(`dbh_min`/`dbh_max`: 0-10, 11-20, 21-30, 31-50, 51-70, ... 131-150, the
top band closed), so it is a reconstruction good in aggregate and wrong per
tree by up to half a band.  Centimetres of diameter by the values
(E. camaldulensis median band 31-50, Lagerstroemia 'Tuscarora' 0-10).
**`crown` is crown spread in metres, banded the same way** (0-5, 6-10, 11-20,
21-50), its midpoint published as `crown_width_m`.  A midpoint that is not
the middle of its band, or is not a number, counts as unreadable, and more
than 1% unreadable in either column refuses the build.  `height` is a band
as well and the schema has no recorded-height column, so it is not
published.  No planting date or status is recorded; `location`
(`Naturestrip`, `Park`, `Median`, ...) is the setting, not a site status.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    iter_csv_row_chunks,
    normalize_species,
    validate_coordinates,
)
from shared.platforms.ckan import CkanResource, resource_download_url  # noqa: E402

# The CSV resource of the data.gov.au dataset.  Its download URL ends in the
# uploaded filename, so it is read off `resource_show` each run.
RESOURCE = CkanResource("data.gov.au/data", "d56d39f9-1e15-48c4-bad1-0cbd8091ece6")

CITY_CODE = "AUSHT"
ID_PREFIX = "sht"
REQUIRED = ("lat", "lon", "genus", "species", "dbh", "dbh_min", "dbh_max", "crown", "crown_min", "crown_max", "ref")

# `genus` values that say the tree was not named.
UNRECORDED_GENUS = frozenset({"not specified", "unknown", "aaa - species unknown"})
# Epithets that say the species was not named.
UNRECORDED_EPITHET = frozenset({"sp.", "sp", "not specified", "unknown"})

# (genus, species) as written (after quote and whitespace cleaning) ->
# (scientific name, cultivar).
SOURCE_NAMES: dict[tuple[str, str], tuple[str, str | None]] = {
    # Cultivars written as the epithet.
    ("Callistemon", "Harkness"): ("Callistemon", "Harkness"),
    ("Callistemon", "Kings Park Special"): ("Callistemon", "Kings Park Special"),
    ("Callistemon", "Little John"): ("Callistemon", "Little John"),
    ("Grevillea", "Robin Gordon"): ("Grevillea", "Robyn Gordon"),
    ("Juniperus", "Skyrocket"): ("Juniperus", "Skyrocket"),
    ("Fraxinus", "Raywood"): ("Fraxinus angustifolia", "Raywood"),
    ("Eucalyptus", "Globulus Compacta"): ("Eucalyptus globulus", "Compacta"),
    # A place name, not an epithet or a registered cultivar.
    ("Leptospermum", "Talyuberlup"): ("Leptospermum", None),
    # Acer truncatum x platanoides selections, written as a formula.
    ("Acer", "X Plat. 'Norwegian Sunset'"): ("Acer", "Norwegian Sunset"),
    ("Acer", "X Plat.'Pacific Sunset'"): ("Acer", "Pacific Sunset"),
}

QUOTES = str.maketrans({"‘": "'", "’": "'", "`": "'", '"': "'", "“": "'", "”": "'", " ": " "})
QUOTED = re.compile(r"'([^']*)'")
RANKS = frozenset({"subsp.", "subsp", "ssp.", "var.", "var", "f.", "forma"})


def clean_text(value: str | None) -> str:
    return " ".join((value or "").translate(QUOTES).split())


def tidy_cultivar(value: str | None) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in value.split(" "))


def name_and_cultivar(genus_raw: str | None, species_raw: str | None) -> tuple[str | None, str | None]:
    """`(scientific name, cultivar)` from the `genus` and `species` columns."""
    genus = clean_text(genus_raw)
    epithet = clean_text(species_raw)
    if not genus or genus.lower() in UNRECORDED_GENUS:
        return None, None
    override = SOURCE_NAMES.get((genus, epithet))
    if override:
        return normalize_species(override[0]), override[1]

    # A cultivar group written as the epithet: `Sato-Zakura Group`.
    if epithet.lower().endswith(" group"):
        return normalize_species(genus), tidy_cultivar(epithet)

    cultivar = None
    match = QUOTED.search(epithet)
    if match:
        cultivar = tidy_cultivar(match.group(1))
        epithet = (epithet[: match.start()] + epithet[match.end():]).strip()
    elif epithet.endswith("'"):
        # Only the closing quote survived: `Superb'`, `Donard Star'`.
        before, _, after = epithet[:-1].rpartition("'")
        cultivar = tidy_cultivar(after)
        epithet = before.strip()

    # `var.insularis` -> `var. insularis`
    tokens = re.sub(r"(?i)\b(var|subsp|ssp)\.(?=\S)", r"\1. ", epithet).split()
    if tokens and tokens[-1].lower() in {"cv", "cv."}:
        tokens = tokens[:-1]
    # A rank with no name after it: `Angustifolia Subsp.`
    while tokens and tokens[-1].lower() in RANKS:
        tokens = tokens[:-1]
    if not tokens or tokens[0].lower() in UNRECORDED_EPITHET or epithet.lower() in UNRECORDED_EPITHET:
        return normalize_species(genus), cultivar
    if tokens[0].lower() in {"aff.", "aff", "cf.", "cf"}:
        return normalize_species(genus), cultivar
    return normalize_species(" ".join([genus, *tokens])), cultivar


class BandReader:
    """Reads a published band midpoint, checked against its own bounds."""

    def __init__(self, name: str):
        self.name = name
        self.read = 0
        self.unreadable: dict[str, int] = {}

    def __call__(self, mid: str | None, low: str | None, high: str | None) -> float | None:
        text = clean_text(mid)
        if not text:
            return None
        self.read += 1
        try:
            value, lo, hi = float(text), float(clean_text(low)), float(clean_text(high))
        except ValueError:
            key = f"{text} ({low}-{high})"
            self.unreadable[key] = self.unreadable.get(key, 0) + 1
            return None
        if not (lo <= value <= hi and abs(value - (lo + hi) / 2) <= 0.5):
            key = f"{text} ({low}-{high})"
            self.unreadable[key] = self.unreadable.get(key, 0) + 1
            return None
        return value if value > 0 else None

    def check(self) -> None:
        bad = sum(self.unreadable.values())
        if bad:
            print(
                f"Greater Shepparton ingest: {bad} unreadable `{self.name}` values: "
                f"{sorted(self.unreadable.items(), key=lambda kv: -kv[1])[:10]}",
                file=sys.stderr,
            )
        if self.read and bad / self.read > 0.01:
            raise RuntimeError(
                f"Greater Shepparton: {bad} of {self.read} `{self.name}` values are not the "
                "midpoint of their band; the column has changed shape"
            )


def parse_float(value: str | None) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def transform(rows: list[dict], dbh_reader: BandReader, crown_reader: BandReader, stats: dict) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for rec in rows:
        if not stats["checked_header"]:
            missing = [c for c in REQUIRED if c not in rec]
            if missing:
                raise RuntimeError(f"Greater Shepparton CSV has no {missing} column(s): {sorted(rec)}")
            stats["checked_header"] = True
        stats["records"] += 1
        ref = clean_text(rec.get("ref"))
        if not ref:
            stats["missing_id"] += 1
            continue
        name, parsed_cultivar = name_and_cultivar(rec.get("genus"), rec.get("species"))
        dbh_cm = dbh_reader(rec.get("dbh"), rec.get("dbh_min"), rec.get("dbh_max"))

        tree_id.append(f"{ID_PREFIX}-{ref}")
        species.append(name)
        cultivar.append(parsed_cultivar if name else None)
        latitude.append(parse_float(rec.get("lat")))
        longitude.append(parse_float(rec.get("lon")))
        dbh.append(dbh_cm / 2.54 if dbh_cm is not None else None)
        crown.append(crown_reader(rec.get("crown"), rec.get("crown_min"), rec.get("crown_max")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array([CITY_CODE] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def build() -> pa.Table:
    dbh_reader = BandReader("dbh")
    crown_reader = BandReader("crown")
    stats = {"records": 0, "missing_id": 0, "checked_header": False}
    tables = [
        transform(chunk, dbh_reader, crown_reader, stats)
        for chunk in iter_csv_row_chunks(resource_download_url(RESOURCE), delimiter=",")
    ]
    if not tables or stats["records"] == 0:
        raise RuntimeError("Greater Shepparton CSV has no rows")
    print(f"Greater Shepparton ingest: {stats['records']} records in the CSV", file=sys.stderr)
    if stats["missing_id"]:
        print(
            f"Greater Shepparton ingest: dropped {stats['missing_id']} rows with no `ref`",
            file=sys.stderr,
        )
    dbh_reader.check()
    crown_reader.check()
    return pa.concat_tables(tables)


if __name__ == "__main__":
    table = build()
    table = validate_coordinates(table, city="Greater Shepparton", city_code=CITY_CODE)
    table = enforce_tree_schema(table, city="Greater Shepparton", data_source="SHEPPARTON_OPENDATA")
    emit(table)
