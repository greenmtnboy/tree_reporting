#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Frankfurt am Main's tree cadastre (Baumkataster), from the city's portal.

Source: "Baumkataster OP" (dataset 1819) on offenedaten.frankfurt.de, published
by the Stadt Frankfurt am Main from the Grünflächenamt's cadastre under the
Datenlizenz Deutschland -- Namensnennung -- 2.0 (the distribution's
`dct:license`).  One semicolon-separated UTF-8 CSV of every tree the
Grünflächenamt manages on public land -- streets, parks, cemeteries,
playgrounds and the Botanical Garden -- **166,895 rows** when this was
written.  The ArcGIS Online layer "Baumkataster - Frankfurt am Main"
(`FFM_Baumkataster`, owner `esri_DE_content`) is Esri Deutschland's geocoded
copy of an older download of this file (163,569 rows, nothing planted after
2024) and is not read.

The catalogue's `modified` (2025-02-06) does not move when the file does: the
file carries 1,441 trees planted in 2026.  See frankfurt_update_time.py.

**Keyed on `Objekt ID`** (`1_40399`: a source prefix -- 1 the general
cadastre, 2 the federal-road trees (`B 105F`), 3 the Botanical Garden -- and
a record number; one row is a bare `477692`): checked over the whole file,
166,895 distinct values in 166,895 rows, none blank.  `Baumnummer` is the
inspector's number within a street or park and repeats.  Against Esri's
older copy, whose integer `Objelt_ID` matches the record number, only a
fifth of position-matched trees kept their number: the city renumbered when
it introduced the prefixed form, so ids from before that are not comparable.

**Species is the part of `Lateinischer Name` before the German name**
(`Tilia x vulgaris 'Pallida', Kaiser-Linde`): a comma separates them, a
` - ` on a few rows, and on four no separator at all, where the Latin is the
first two words (three for a nothogenus `x`) plus a quoted cultivar.  The
quoted cultivar is split off by `enforce_tree_schema`; `Tilia species` is the
genus.  `waldartiger Bestand` (a woodland-like stand, 570 points) and
`Mini-Wäldchen` (a "mini wood") name no taxon and publish as Unknown.  The
heritage fruit trees' names carry a `ZO_` prefix (`ZO_Malus 'Goldparmäne'`),
which is dropped; most name only the genus and a variety.  `Malus Hybriden
in Sorten` (ornamental-apple hybrids, 133 rows) is the genus.

`Stamm Umfang in cm` is the trunk **circumference** in whole centimetres
(median 88; `Stamm Durchmesser` is the same value over pi, rounded), converted
to a DBH in inches; 0 is unmeasured (2,564 rows) and nulls.  `Kronen
Durchmesser in Meter` is the crown diameter in metres (median 6; a few with a
decimal comma, `2,5`), published as `crown_width_m`.  `Pflanzjahr` is a
planting year (1630-2028; decades are heaped -- 1980, 1990, 1970 -- because
old trees' years are estimates, which is not a stamped default), published
as January 1 of that year; the shared schema nulls the future 2028.
`Baumhöhe` (height, m) has no canonical column.  The file has no status
column: no removed trees, stumps or planting sites are flagged in it.

Coordinates are ETRS89 / UTM 32N (`ETRS 89 Rechtswert` easting, `ETRS 89
Hochwert` northing, decimal comma), converted here.
"""

import csv
import io
import math
import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    get_with_retry,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = (
    "https://offenedaten.frankfurt.de/dcat/dataset/"
    "de-he-frankfurtam-baumkataster_op/content.csv"
)
TIMEOUT = 300
CHUNK_ROWS = 25_000
ID_PREFIX = "frankfurt-"

FIELD_ID = "Objekt ID"
FIELD_NORTHING = "ETRS 89 Hochwert"
FIELD_EASTING = "ETRS 89 Rechtswert"
FIELD_NAME = "Lateinischer Name"
FIELD_CROWN = "Kronen Durchmesser in Meter"
FIELD_CIRCUMFERENCE = "Stamm Umfang in cm"
FIELD_YEAR = "Pflanzjahr"
REQUIRED_FIELDS = (
    FIELD_ID,
    FIELD_NORTHING,
    FIELD_EASTING,
    FIELD_NAME,
    FIELD_CROWN,
    FIELD_CIRCUMFERENCE,
    FIELD_YEAR,
)

# Above this share of unreadable non-blank values in a numeric column, the
# column has changed meaning and the build is refused.
MAX_UNREADABLE_SHARE = 0.01

# A second word that means "some member of the genus".
GENUS_ONLY = frozenset({"hybriden"})

# Values of `Lateinischer Name` that name no taxon.
NOT_A_TAXON = frozenset({"waldartiger bestand", "mini-wäldchen"})

# ---------------------------------------------------------------------------
# ETRS89 / UTM 32N -> geographic.  ETRS89 and WGS84 agree to well under a
# metre in Germany.  Krüger's series to n^4 (Karney 2011), sub-millimetre
# inside the zone; no pyproj dependency.
# ---------------------------------------------------------------------------

_A = 6378137.0
_F = 1 / 298.257222101
_K0 = 0.9996
_E0 = 500000.0
_LON0 = math.radians(9.0)  # zone 32
_N = _F / (2 - _F)
_A_RECT = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440,
    17 * _N**3 / 480 - 37 * _N**4 / 840,
    4397 * _N**4 / 161280,
)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45,
    56 * _N**3 / 15 - 136 * _N**4 / 35,
    4279 * _N**4 / 630,
)

# A UTM 32N point anywhere near Frankfurt.  Wider than the city by tens of
# kilometres; its job is to tell metres from degrees or a swapped pair, not
# to bound the city (validate_coordinates does that).
_EASTING_RANGE = (400_000.0, 550_000.0)
_NORTHING_RANGE = (5_480_000.0, 5_630_000.0)


def utm32_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """(latitude, longitude) in degrees for an ETRS89 UTM 32N point."""
    xi = northing / (_K0 * _A_RECT)
    eta = (easting - _E0) / (_K0 * _A_RECT)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, start=1))
    lon = _LON0 + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lat), math.degrees(lon)


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------

_QUOTED_TAIL = re.compile(r"^(.*?'[^']+')")
# The heritage fruit varieties carry a register prefix: `ZO_Malus 'Goldparmäne'`
# (1,024 rows, apples, pears, plums and cherries, mostly in orchard meadows).
_REGISTER_PREFIX = re.compile(r"^[A-Z]{1,3}_(?=[A-Z])")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _genus_if_unnamed(latin: str | None) -> str | None:
    """`Malus Hybriden in Sorten` ("hybrids, in varieties") is the genus."""
    if latin is None:
        return None
    words = latin.split()
    if len(words) >= 2 and words[1].lower() in GENUS_ONLY:
        return words[0]
    return latin


def latin_part(name) -> str | None:
    """The scientific name (with any quoted cultivar) before the German name.

    None for a value that names no taxon; `enforce_tree_schema` publishes it
    as Unknown.
    """
    text = clean(name)
    if text is not None:
        text = _REGISTER_PREFIX.sub("", text)
    if text is None or text.lower() in NOT_A_TAXON:
        return None
    for separator in (",", " - "):
        if separator in text:
            return _genus_if_unnamed(clean(text.split(separator, 1)[0]))
    # No separator: `Prunus serrulata 'Tai Haku' Grosse weisse Kirsche`,
    # `x Cupressocyparis leylandii Bastard-Zypresse`.
    quoted = _QUOTED_TAIL.match(text)
    if quoted:
        return clean(quoted.group(1))
    words = text.split()
    take = 3 if words and (words[0].lower() == "x" or (len(words) > 1 and words[1].lower() == "x")) else 2
    return " ".join(words[:take])


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


class Counts:
    def __init__(self) -> None:
        self.read = 0
        self.no_id = 0
        self.no_point = 0
        self.filled: dict[str, int] = {}
        self.unreadable: dict[str, int] = {}

    def number(self, record: dict, field: str) -> float | None:
        value = clean(record.get(field))
        if value is None:
            return None
        self.filled[field] = self.filled.get(field, 0) + 1
        try:
            return float(value.replace(",", "."))
        except ValueError:
            self.unreadable[field] = self.unreadable.get(field, 0) + 1
            return None

    def check(self) -> None:
        for field, bad in self.unreadable.items():
            total = self.filled.get(field, 0)
            if total and bad / total > MAX_UNREADABLE_SHARE:
                raise RuntimeError(
                    f"Frankfurt OpenData: {bad} of {total} `{field}` values are "
                    "unreadable (>1%); the column changed shape"
                )


def make_transform(counts: Counts):
    def transform(records: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for record in records:
            source_id = clean(record.get(FIELD_ID))
            if source_id is None:
                counts.no_id += 1
                continue
            easting = counts.number(record, FIELD_EASTING)
            northing = counts.number(record, FIELD_NORTHING)
            if easting is None or northing is None:
                counts.no_point += 1
                continue
            if not (
                _EASTING_RANGE[0] <= easting <= _EASTING_RANGE[1]
                and _NORTHING_RANGE[0] <= northing <= _NORTHING_RANGE[1]
            ):
                raise RuntimeError(
                    f"Frankfurt OpenData: {source_id} at ({easting}, {northing}) is "
                    "not an ETRS89 UTM 32N easting/northing near Frankfurt; the "
                    "file's coordinate columns changed"
                )
            lat, lon = utm32_to_wgs84(easting, northing)
            year = counts.number(record, FIELD_YEAR)
            circ = counts.number(record, FIELD_CIRCUMFERENCE)

            tree_id.append(f"{ID_PREFIX}{source_id}")
            species.append(latin_part(record.get(FIELD_NAME)))
            plant_date.append(parse_plant_date_year(int(year)) if year is not None else None)
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(circumference_cm_to_dbh_inches(circ))
            crown.append(counts.number(record, FIELD_CROWN))

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["DEFRA"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    return transform


def fetch_csv() -> str:
    """The whole file (17 MB), refusing a holding page or a truncated body."""
    response = get_with_retry(SOURCE_URL, timeout=TIMEOUT)
    content_type = response.headers.get("Content-Type", "")
    if "csv" not in content_type:
        raise RuntimeError(
            f"Frankfurt OpenData: expected a CSV, got {content_type!r}: "
            f"{response.text[:200]!r}"
        )
    # The portal states the file's size in Content-Disposition (`size=...`);
    # a body of any other length is a cut-off download.
    declared = re.search(r"size=(\d+)", response.headers.get("Content-Disposition", ""))
    if declared and int(declared.group(1)) != len(response.content):
        raise RuntimeError(
            f"Frankfurt OpenData: read {len(response.content)} bytes of a "
            f"{declared.group(1)}-byte file; refusing a partial cadastre"
        )
    return response.content.decode("utf-8-sig")


def iter_record_chunks(text: str, counts: Counts):
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";")
    missing = [f for f in REQUIRED_FIELDS if f not in (reader.fieldnames or [])]
    if missing:
        raise RuntimeError(
            f"Frankfurt OpenData: the CSV lacks {missing}; header is {reader.fieldnames!r}"
        )
    chunk: list[dict] = []
    for record in reader:
        counts.read += 1
        chunk.append(record)
        if len(chunk) >= CHUNK_ROWS:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def main() -> None:
    text = fetch_csv()
    counts = Counts()
    table = stream_to_table(
        iter_record_chunks(text, counts), make_transform(counts), label="Frankfurt OpenData"
    )
    del text
    counts.check()
    print(
        f"Frankfurt OpenData: read {counts.read} row(s); dropped {counts.no_id} with no "
        f"Objekt ID and {counts.no_point} with no coordinates; unreadable numbers "
        f"nulled: {counts.unreadable or 'none'}; leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Frankfurt", city_code="DEFRA")
    table = enforce_tree_schema(table, city="Frankfurt", data_source="FRANKFURT_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
