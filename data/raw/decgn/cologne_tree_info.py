#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Cologne's tree register (Baumkataster), from the city's WFS.

Source: `ms:baumkataster` on `geoportal.stadt-koeln.de`, Stadt Köln's
MapServer WFS, catalogued on offenedaten-koeln.de as "Baumkataster Köln
Fortlaufend aktualisiert" (dataset 65ed7e99-f144-410e-ae1f-3040f151c8c8)
under Datenlizenz Deutschland – Zero – Version 2.0.  The register is kept by
the Amt für Landschaftspflege und Grünflächen and holds the single trees the
city itself manages -- street trees and trees on municipal sites (schools,
homes, playgrounds, sports grounds); not the park woodland -- **108,049 rows**
when this was written, refreshed daily according to the catalogue.

**The server does not reproject.**  It advertises EPSG:4326 but answers every
`srsName` in EPSG:25832 (ETRS89 / UTM 32N), so the request names 25832 and
the points are converted here (`utm32_to_wgs84`, Krüger's series as in
Madrid's ingest).  A coordinate that is not a UTM 32N easting/northing raises:
that is the server starting to honour `srsName`, and converting degrees as
metres would put Cologne in the Gulf of Guinea.  The GeoJSON output format is
`application/json; subtype=geojson`; MapServer refuses plain
`application/json`, which `shared.platforms.wfs` sends, so the layer here
overrides it.

**Keyed on `Baumnummer`**, which the catalogue describes as the number that
identifies the tree (`66-402-S-0249`: the letter is the site type, S street,
H building or school, J playground, G green space ...).  It is not clean over
the whole layer: 1,497 rows carry none, and 46 numbers are shared by 338
rows, every one of those at a different position -- different trees under a
malformed number (`-504-X-0001` has lost its district prefix and is on 72
trees), not a repeated row.  Both kinds are dropped with a count; which copy of
a shared number is "the" tree cannot be told, and a position-derived id is not
an id.  The layer publishes no other per-feature key (MapServer's `gml:id` is
a row index, absent from the GeoJSON).

**Species is `Botanischer_Name`, the Latin name, with an unquoted cultivar
after the epithet** (`Prunus serrulata Kanzan`, `Crataegus laevigata Pauls
scarlet`) and a trailing space on every value.  A capitalised tail is a
cultivar and is quoted here so `enforce_tree_schema` moves it to `cultivar`;
a lower-case tail is an unranked infraspecific name (`Pinus nigra laricio`,
`Fagus sylvatica atropunicea`) or a note (`Ginkgo biloba weiblich`, female),
which the shared hygiene truncates to the species.  `Malus Hillieri` and
`Ulmus Columella` are cultivars of the genus.  A few cultivars are already
quoted (`Acer platanoides 'Fairview'`) and pass through.  A handful of
one-off spellings are rewritten (`_REWRITES`): `Acer globosum` is the Norway
maple cultivar written as an epithet, `Populus cerasifera Nigra` a poplar
carrying a cherry plum's name.  Misspelt epithets (`Tilia platiphyllos`,
`Pyrus calleriana`) are left to the shared misspelling map.  A genus alone
(`Tilia`, 11,118 rows) is a tree identified to genus.  `Verschiedene`
("various", 310 rows) and `unbekannt` (50) are trees nobody named and
publish as `Unknown`, as do the 112 rows with no name.  `Deutscher_Name` is
the German common name and is not mapped.

The register has no status column and no records of empty sites or stumps.

**`Stammdurchmesser_-_cm` is the trunk diameter in centimetres** (median
35 cm), present on every row; `Stammumfang_-_cm` is the circumference, on
95,714, and the two agree (circumference / diameter has median 3.143).  The
diameter is read, and the circumference only where the diameter is missing or
not positive.  Zero and negative values (a few dozen) are null; the shared
200-inch guard nulls the handful of metre-scale typos (3,218 cm).

**`Kronendurchmesser_-_m` is the crown diameter in metres** and is published
as `crown_width_m` (the shared 40 m guard nulls the typos, up to 814).
`Höhe_-_m` is a recorded height in metres; the schema has no column for a
surveyed height (height comes from LIDAR), so it is not published.

**`Pflanzjahr` is the planting year**, recorded on only 2,293 rows (the
register says the new recording system is filling such gaps at each
inspection), published as January 1 of the year.

Every numeric column is a string in the WFS; a non-blank value that does not
parse is counted, and more than 1% unreadable in any of them refuses the build.
"""

import math
import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.wfs import WfsLayer, iter_wfs_features


class GeoJsonWfsLayer(WfsLayer):
    """MapServer spells its GeoJSON output format with a subtype."""

    def params(self, **extra) -> dict[str, str]:
        params = super().params(**extra)
        params["outputFormat"] = "application/json; subtype=geojson"
        return params


LAYER = GeoJsonWfsLayer(
    "https://geoportal.stadt-koeln.de/wss/service/baumkataster_extern_wfs/guest",
    "ms:baumkataster",
    timeout=300,
)
SOURCE_SRS = "urn:ogc:def:crs:EPSG::25832"
PAGE_SIZE = 25_000

ID_PREFIX = "cgn-"

FIELD_SPECIES = "Botanischer_Name"
FIELD_ID = "Baumnummer"
FIELD_YEAR = "Pflanzjahr"
FIELD_DIAMETER = "Stammdurchmesser_-_cm"
FIELD_CIRCUMFERENCE = "Stammumfang_-_cm"
FIELD_CROWN = "Kronendurchmesser_-_m"

# Above this share of unreadable non-blank values in a numeric column, the
# column has changed meaning and the build is refused.
MAX_UNREADABLE_SHARE = 0.01

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

# A UTM 32N point anywhere near Cologne.  Wider than the city by tens of
# kilometres; its job is to tell metres from degrees, not to bound the city
# (validate_coordinates does that).
_EASTING_RANGE = (250_000.0, 450_000.0)
_NORTHING_RANGE = (5_550_000.0, 5_750_000.0)


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


def point_to_wgs84(geometry: dict | None) -> tuple[float, float] | None:
    """(latitude, longitude) of a GeoJSON point in UTM 32N, or None.

    Raises when the point is not UTM 32N metres -- the server has started
    honouring `srsName`, or changed its default.
    """
    if not geometry or geometry.get("type") != "Point":
        return None
    coords = geometry.get("coordinates") or []
    if len(coords) < 2 or coords[0] is None or coords[1] is None:
        return None
    easting, northing = float(coords[0]), float(coords[1])
    if not (
        _EASTING_RANGE[0] <= easting <= _EASTING_RANGE[1]
        and _NORTHING_RANGE[0] <= northing <= _NORTHING_RANGE[1]
    ):
        raise RuntimeError(
            f"Cologne WFS: point ({easting}, {northing}) is not an EPSG:25832 "
            "easting/northing near Cologne; the server's coordinate system changed"
        )
    return utm32_to_wgs84(easting, northing)


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------

# Values that name no taxon: "various" is a placeholder the shared list does not
# carry in German.  `unbekannt` is already a shared placeholder.
_UNNAMED = frozenset({"verschiedene"})

# One-off spellings the shape rule below cannot read.
_REWRITES = {
    # Idesia polycarpa var. vestita, capitalised throughout.
    "Idesia Polycarpa Vestita": "Idesia polycarpa",
    # The hybrid written in German before a cultivar of the genus
    # (Magnolia 'Norman Gould'), as Vienna's `Malus hybride`.
    "Magnolia hybride NormanGould": "Magnolia 'Norman Gould'",
    # An author citation (Miller), not a cultivar.
    "Fraxinus caroliniana Mill": "Fraxinus caroliniana",
    # subsp. fortunei, capitalised.  The epithet's spelling is left to the
    # shared misspelling map.
    "Fontanesia phillyreodes Fortunei": "Fontanesia phillyreodes",
    # The cultivar written as the epithet (40 rows, no German name): 'Globosum'
    # is the Norway maple's, which the register writes `Acer platanoides
    # Globosum` (Kugelahorn) on 567 other rows.
    "Acer globosum": "Acer platanoides 'Globosum'",
    # A poplar (`Deutscher_Name` "Pappel" on all 14) carrying the cherry
    # plum's epithet and cultivar; only the genus is known.
    "Populus cerasifera Nigra": "Populus",
}

_QUOTES = "'\"‘’“”"
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")


def parse_species(value) -> str | None:
    """The register's name, with an unquoted cultivar quoted.

    Returns None for a blank or unnamed value (published as `Unknown`).
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or text.lower() in _UNNAMED:
        return None
    text = _REWRITES.get(text, text)
    if any(q in text for q in _QUOTES):
        return text
    # The register's parentheticals are an alternative name
    # (`Amelanchier lamarkii (canadensis)`) or a cultivar's synonym
    # (`Unifolia (Monophylla)`); neither belongs in the name.
    text = _PARENTHETICAL.sub("", text).strip()
    tokens = text.split()
    if len(tokens) < 2:
        return text
    genus, rest = tokens[0], tokens[1:]
    if rest[0][:1].isupper():
        # `Malus Hillieri`: a cultivar of the genus.
        return f"{genus} '{' '.join(rest)}'"
    binomial = f"{genus} {rest[0]}"
    tail = rest[1:]
    # Skip an unranked infraspecific epithet (`Prunus dulcis dulcis ...`)
    # and an explicit `var`; what is left, if capitalised, is the cultivar.
    while tail and (tail[0][:1].islower() or tail[0].rstrip(".").lower() == "var"):
        tail = tail[1:]
    if tail and tail[0][:1].isupper():
        return f"{binomial} '{' '.join(tail)}'"
    return binomial


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


class NumberReader:
    """Reads the WFS's string numbers and counts the ones it could not."""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}
        self.unreadable: dict[str, int] = {}
        self.examples: dict[str, list[str]] = {}

    def read(self, field: str, value) -> float | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        self.seen[field] = self.seen.get(field, 0) + 1
        try:
            return float(text.replace(",", "."))
        except ValueError:
            self.unreadable[field] = self.unreadable.get(field, 0) + 1
            self.examples.setdefault(field, [])
            if len(self.examples[field]) < 5:
                self.examples[field].append(text)
            return None

    def check(self) -> None:
        for field, bad in self.unreadable.items():
            total = self.seen.get(field, 0)
            if total and bad / total > MAX_UNREADABLE_SHARE:
                raise RuntimeError(
                    f"Cologne: {bad} of {total} non-blank {field} values are not "
                    f"numbers (e.g. {self.examples.get(field)}); the column changed meaning"
                )
        if self.unreadable:
            print(
                f"Cologne OpenData: unreadable numbers nulled: {self.unreadable}",
                file=sys.stderr,
            )


def parse_dbh_inches(diameter_cm: float | None, circumference_cm: float | None) -> float | None:
    """Diameter in inches from the diameter, else from the circumference."""
    if diameter_cm is not None and diameter_cm > 0:
        return diameter_cm / 2.54
    if circumference_cm is not None and circumference_cm > 0:
        return circumference_cm / (math.pi * 2.54)
    return None


def parse_plant_year(year: float | None) -> date | None:
    if year is None or year != int(year):
        return None
    return parse_plant_date_year(int(year))


# ---------------------------------------------------------------------------
# Fetch and transform
# ---------------------------------------------------------------------------


def iter_feature_chunks():
    """One WFS page at a time, sorted on the tree number so paging is stable."""
    return iter_wfs_features(LAYER, sort_by=FIELD_ID, srs=SOURCE_SRS, page_size=PAGE_SIZE)


def make_transform(numbers: NumberReader, dropped: dict[str, int]):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            props = feature.get("properties") or {}
            number = " ".join(str(props.get(FIELD_ID) or "").split())
            if not number:
                dropped["no_number"] += 1
                continue
            point = point_to_wgs84(feature.get("geometry"))
            if point is None:
                dropped["no_point"] += 1
                continue
            tree_id.append(f"{ID_PREFIX}{number}")
            species.append(parse_species(props.get(FIELD_SPECIES)))
            plant_date.append(parse_plant_year(numbers.read(FIELD_YEAR, props.get(FIELD_YEAR))))
            latitude.append(point[0])
            longitude.append(point[1])
            dbh.append(
                parse_dbh_inches(
                    numbers.read(FIELD_DIAMETER, props.get(FIELD_DIAMETER)),
                    numbers.read(FIELD_CIRCUMFERENCE, props.get(FIELD_CIRCUMFERENCE)),
                )
            )
            crown.append(numbers.read(FIELD_CROWN, props.get(FIELD_CROWN)))

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["DECGN"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    return transform


def drop_shared_numbers(table: pa.Table) -> tuple[pa.Table, int, int]:
    """Drop every row whose tree number another row also carries.

    Returns the table, the number of rows dropped and of numbers involved.
    """
    counts = pc.value_counts(table.column("tree_id"))
    shared = [
        item["values"].as_py()
        for item in counts
        if item["counts"].as_py() > 1
    ]
    if not shared:
        return table, 0, 0
    mask = pc.is_in(table.column("tree_id"), value_set=pa.array(shared, type=pa.string()))
    kept = table.filter(pc.invert(mask))
    return kept, table.num_rows - kept.num_rows, len(shared)


def main() -> None:
    numbers = NumberReader()
    dropped = {"no_number": 0, "no_point": 0}
    table = stream_to_table(
        iter_feature_chunks(), make_transform(numbers, dropped), label="Cologne OpenData"
    )
    numbers.check()
    table, shared_rows, shared_numbers = drop_shared_numbers(table)
    print(
        f"Cologne OpenData: dropped {dropped['no_number']} row(s) with no Baumnummer, "
        f"{dropped['no_point']} with no point, and {shared_rows} row(s) sharing "
        f"{shared_numbers} Baumnummer(s) between different trees, leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Cologne", city_code="DECGN")
    table = enforce_tree_schema(table, city="Cologne", data_source="COLOGNE_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
