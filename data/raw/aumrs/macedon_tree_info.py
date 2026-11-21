#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Macedon Ranges Shire, VIC's municipal tree inventory.

Source: "Macedon Ranges Shire Trees" on data.gov.au (dataset
`macedon-ranges-shire-trees`, published by the Macedon Ranges Shire Council's
own organisation there): "tree species, common name, diameter and height
information for Macedon Ranges Shire managed trees", under Creative Commons
Attribution 2.5 Australia (the dataset's licence field).  The council has no
open-data portal of its own; data.gov.au is where it publishes.

**Read from the zipped shapefile.**  The dataset lists five resources: the
shapefile zip (`mrsc_trees.zip`, the council's upload, re-uploaded
2026-11-10, files inside dated the same day), two GeoJSON files (one stale
since 2026-08-10, one re-uploaded with the zip) and data.gov.au's GeoServer
WMS/WFS (resource stamps 2026-08-10).  The zip is the resource the council
keeps current; the shapefile is read here with the standard library, as
Greater Bendigo's is (`aubxg/bendigo_tree_info.py`).  56,538 points on
2026-11-21, every one a Point.  The archive's member names are not fixed
(`MRSC_Trees_Forestree.*` today), so the one `.shp` in it is read.  The
`.cpg` says UTF-8.

**Coordinates are projected here**: the shapefile is GDA2020 / MGA zone 55
(EPSG:7855, the `.prj`), and so is the GeoJSON.  The inverse is the standard
Transverse Mercator series on GRS80 (Hobsons Bay's, `aumel/`); GDA2020 is
WGS84 to well under a metre, so no datum shift is applied.  A `.prj` that is
no longer MGA zone 55 raises.

**`id` is the key**: the asset number from the council's Forestree register,
numeric, 56,538 distinct values over 56,538 rows, none null, spread over
4149625..7473576 (an asset register, not a row number).  It is stable across
republishes: 55,835 of the 56,307 ids in the August 2026 GeoJSON are still
present in November's file.  Prefixed `mrs-`.

**`genus` + `species` are the scientific name**, `species` usually the bare
epithet (`viminalis`) but sometimes the whole binomial (`Tilia cordata`,
`Cupressus` + `Hesperocyparis macrocarpa`, where the binomial wins), an
abbreviated genus (`S. sempervirens`), a quoted cultivar (`alba
'Fastigiata'`), an unquoted one (`sempervirens Stricta`) or `cv` for an
unnamed one.  A cultivar becomes `cultivar`.  A formula hybrid (`indica x
fauriei`, `truncatum x A. platanoides`) publishes as its genus.
`SOURCE_NAMES` restores a handful of structural oddities keyed on the exact
pair; misspellings proper (`Ceonothus`, `Euonomys`, `betulaefolia`) are left
for the shared misspelling map.  `common_nam` is not read.

Not trees, dropped here because the flag is the source's own (the `genus`
column): `Stump`, `Hedge` (whose `species` is a street name) and `Copse
Group` (one point for a stand).  `Rural Road Tree` (1,287 rows, a roadside
tree whose species was never recorded), `Not specified` and `Unknown` stay,
with no species.

**`dbh` is centimetres** as text (E. viminalis median 56, Lagerstroemia
indica median 3), divided by 2.54.  `0` (6,192 rows, mostly the rural road
trees) is an unmeasured tree and publishes as null; the shared guard nulls a
handful of impossible values (24124, 3035, ...).  `height` is a band (`<
5m`, `5-10m`, ...) on 11% of rows, and the schema has no recorded-height
column, so it is not published.  No planting date, crown or status is
recorded.
"""

import io
import math
import re
import struct
import sys
import zipfile
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    get_with_retry,
    normalize_species,
    validate_coordinates,
)
from shared.platforms.ckan import CkanResource, resource_download_url  # noqa: E402

# The shapefile resource of the data.gov.au dataset.  Its download URL ends in
# the uploaded filename, so it is read off `resource_show` each run.
RESOURCE = CkanResource("data.gov.au/data", "22a9c292-1351-4332-89da-0ff6e87f55e2")

ID_PREFIX = "mrs"

# `genus` values that say the point is not a tree.
NOT_TREES = frozenset({"stump", "hedge", "copse group"})
# `genus` values that say the species was not recorded.
UNRECORDED = frozenset({"rural road tree", "not specified", "unknown"})

# (genus, species) as written (after quote and whitespace cleaning) ->
# (scientific name, cultivar).
SOURCE_NAMES: dict[tuple[str, str], tuple[str, str | None]] = {
    # The purple cherry plum, written as a hybrid it is not (926 rows).
    ("Prunus", "x cerasifera"): ("Prunus cerasifera", None),
    ("Platanus", "Xacerifolia"): ("Platanus x acerifolia", None),
    # An epithet written with a capital and a typo.
    ("Betula", "Oxidentalis"): ("Betula occidentalis", None),
    # Cultivars written as the epithet.
    ("Callistemon", "Endeavour"): ("Callistemon", "Endeavour"),
    ("Prunus", "Prunus elvins"): ("Prunus", "Elvins"),
}

QUOTES = str.maketrans({"‘": "'", "’": "'", "`": "'", " ": " "})
QUOTED = re.compile(r"'([^']*)'")
ABBREVIATED_GENUS = re.compile(r"^[A-Z][a-z]?\.$")
RANKS = frozenset({"subsp.", "ssp.", "var.", "subso.", "f.", "forma"})
HYBRID_MARKS = frozenset({"x", "×"})


def clean_text(value: str | None) -> str:
    return " ".join((value or "").translate(QUOTES).split())


def tidy_cultivar(value: str | None) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    return value[0].upper() + value[1:]


def name_and_cultivar(genus_raw: str | None, species_raw: str | None) -> tuple[str | None, str | None]:
    """`(scientific name, cultivar)` from the `genus` and `species` columns."""
    genus = clean_text(genus_raw)
    epithet = clean_text(species_raw)
    if not genus or genus.lower() in UNRECORDED:
        return None, None
    override = SOURCE_NAMES.get((genus, epithet))
    if override:
        return normalize_species(override[0]), override[1]

    cultivar = None
    match = QUOTED.search(epithet)
    if match:
        cultivar = tidy_cultivar(match.group(1))
        epithet = (epithet[: match.start()] + epithet[match.end():]).strip()
    tokens = epithet.split()
    # `cv`: a cultivar the survey did not name.
    if tokens and tokens[-1].lower() in {"cv", "cv."}:
        tokens = tokens[:-1]

    if not tokens:
        name_tokens = [genus]
    elif ABBREVIATED_GENUS.match(tokens[0]):
        name_tokens = [genus, *tokens[1:]]
    elif tokens[0][0].isupper() and tokens[0].isalpha() and tokens[0].lower() not in HYBRID_MARKS:
        # The whole name, possibly under another genus (`Hesperocyparis`).
        name_tokens = tokens
    else:
        name_tokens = [genus, *tokens]

    # An unquoted cultivar after the epithet: `sempervirens Stricta`.
    if (
        cultivar is None
        and len(name_tokens) >= 3
        and name_tokens[-1][0].isupper()
        and name_tokens[-2].lower() not in RANKS
        and name_tokens[1].lower() not in HYBRID_MARKS
    ):
        cultivar = tidy_cultivar(name_tokens[-1])
        name_tokens = name_tokens[:-1]

    # A formula hybrid between two named species publishes as the genus.
    if any(tok.lower() in HYBRID_MARKS for tok in name_tokens[2:]):
        name_tokens = name_tokens[:1]

    return normalize_species(" ".join(name_tokens)), cultivar


class DbhReader:
    """Reads `dbh` (centimetres, as text), refusing a column it cannot read."""

    def __init__(self):
        self.read = 0
        self.unreadable: dict[str, int] = {}
        self.zero = 0

    def __call__(self, value: str | None) -> float | None:
        text = clean_text(value)
        if not text:
            return None
        self.read += 1
        try:
            cm = float(text)
        except ValueError:
            self.unreadable[text] = self.unreadable.get(text, 0) + 1
            return None
        if cm <= 0:
            self.zero += 1
            return None
        return cm / 2.54

    def check(self) -> None:
        bad = sum(self.unreadable.values())
        if self.zero:
            print(
                f"Macedon Ranges ingest: {self.zero} trees with `dbh` 0 (unmeasured); "
                "published with no diameter",
                file=sys.stderr,
            )
        if bad:
            print(
                f"Macedon Ranges ingest: {bad} unreadable `dbh` values: "
                f"{sorted(self.unreadable.items(), key=lambda kv: -kv[1])[:10]}",
                file=sys.stderr,
            )
        if self.read and bad / self.read > 0.01:
            raise RuntimeError(
                f"Macedon Ranges: {bad} of {self.read} `dbh` values are not numbers; "
                "the column has changed shape"
            )


def mga55_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """MGA zone 55 (GDA2020, EPSG:7855) easting, northing to (lat, lon).

    Transverse Mercator on GRS80, central meridian 147 E, scale 0.9996, false
    easting 500 km, false northing 10,000 km.  GDA2020 is WGS84 to well under
    the accuracy of a street-tree survey, so the inverse is the whole
    conversion.  Snyder 1987, eq. 8-17 to 8-25 (as `aumel/hobsons_bay_tree_info.py`).
    """
    a = 6378137.0
    f = 1 / 298.257222101
    k0 = 0.9996
    lon0 = math.radians(147.0)

    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

    x = easting - 500000.0
    m = (northing - 10000000.0) / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = a / math.sqrt(1 - e2 * sin1 * sin1)
    t1 = tan1 * tan1
    c1 = ep2 * cos1 * cos1
    r1 = a * (1 - e2) / (1 - e2 * sin1 * sin1) ** 1.5
    d = x / (n1 * k0)

    lat = phi1 - (n1 * tan1 / r1) * (
        d * d / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d**6 / 720
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d**5 / 120
    ) / cos1
    return math.degrees(lat), math.degrees(lon)


# An MGA zone 55 point anywhere near the shire, by tens of kilometres; outside
# it the file is not in the projection the .prj names.
EASTING_RANGE = (150_000.0, 450_000.0)
NORTHING_RANGE = (5_750_000.0, 5_950_000.0)


# ---------------------------------------------------------------------------
# Shapefile reading (point .shp + dBase III .dbf), as Greater Bendigo's
# ---------------------------------------------------------------------------

def read_dbf(data: bytes, encoding: str) -> tuple[list[str], list[dict]]:
    """Every record of a dBase table as a dict of stripped strings."""
    if len(data) < 32:
        raise RuntimeError("Macedon Ranges shapefile: the .dbf is truncated")
    count = struct.unpack("<I", data[4:8])[0]
    header_len, record_len = struct.unpack("<HH", data[8:12])
    fields: list[tuple[str, int]] = []
    off = 32
    while off < header_len - 1 and data[off] != 0x0D:
        name = data[off:off + 11].split(b"\0")[0].decode("ascii")
        fields.append((name, data[off + 16]))
        off += 32
    names = [name for name, _ in fields]
    rows: list[dict] = []
    for i in range(count):
        start = header_len + i * record_len
        record = data[start:start + record_len]
        if len(record) < record_len:
            raise RuntimeError(f"Macedon Ranges shapefile: the .dbf ends at record {i} of {count}")
        row = {"_deleted": record[0:1] == b"*"}
        pos = 1
        for name, width in fields:
            row[name] = record[pos:pos + width].decode(encoding, errors="replace").strip()
            pos += width
        rows.append(row)
    return names, rows


def read_points(data: bytes) -> list[tuple[float | None, float | None]]:
    """`(x, y)` of every record of a point shapefile, None for a null shape."""
    if len(data) < 100 or struct.unpack(">i", data[0:4])[0] != 9994:
        raise RuntimeError("Macedon Ranges shapefile: the .shp has no shapefile header")
    shape_type = struct.unpack("<i", data[32:36])[0]
    if shape_type != 1:
        raise RuntimeError(f"Macedon Ranges shapefile: shape type {shape_type}, expected 1 (Point)")
    points: list[tuple[float | None, float | None]] = []
    pos = 100
    while pos + 12 <= len(data):
        _, content_words = struct.unpack(">ii", data[pos:pos + 8])
        record_type = struct.unpack("<i", data[pos + 8:pos + 12])[0]
        if record_type == 1:
            x, y = struct.unpack("<dd", data[pos + 12:pos + 28])
            points.append((x, y))
        elif record_type == 0:
            points.append((None, None))
        else:
            raise RuntimeError(f"Macedon Ranges shapefile: record of shape type {record_type}")
        pos += 8 + content_words * 2
    return points


def download() -> tuple[list[dict], list[tuple[float | None, float | None]]]:
    response = get_with_retry(resource_download_url(RESOURCE), timeout=300)
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"Macedon Ranges shapefile download is not a zip ({len(response.content)} bytes)"
        ) from exc
    shps = [n for n in archive.namelist() if n.lower().endswith(".shp")]
    if len(shps) != 1:
        raise RuntimeError(f"Macedon Ranges archive holds {len(shps)} shapefiles: {archive.namelist()}")
    stem = shps[0][:-4]
    members = {n.lower(): n for n in archive.namelist()}

    def member(ext: str) -> bytes:
        name = members.get(f"{stem}{ext}".lower())
        if name is None:
            raise RuntimeError(f"Macedon Ranges shapefile: no `{stem}{ext}` in the archive")
        return archive.read(name)

    prj = member(".prj").decode("ascii", errors="replace")
    if "MGA_Zone_55" not in prj:
        raise RuntimeError(f"Macedon Ranges shapefile is no longer MGA zone 55: {prj[:200]}")
    encoding = "utf-8"
    if f"{stem}.cpg".lower() in members:
        encoding = member(".cpg").decode("ascii", errors="replace").strip() or encoding
    names, rows = read_dbf(member(".dbf"), encoding)
    points = read_points(member(".shp"))
    if len(points) != len(rows):
        raise RuntimeError(f"Macedon Ranges shapefile: {len(points)} shapes but {len(rows)} records")
    for required in ("id", "genus", "species", "dbh"):
        if required not in names:
            raise RuntimeError(f"Macedon Ranges shapefile has no `{required}` column ({names})")
    return rows, points


def transform(rows: list[dict], points: list[tuple[float | None, float | None]]) -> pa.Table:
    dbh_reader = DbhReader()
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    deleted = missing_id = 0
    not_trees: dict[str, int] = {}
    for rec, (x, y) in zip(rows, points):
        if rec["_deleted"]:
            deleted += 1
            continue
        asset = rec.get("id") or ""
        if not asset.isdigit():
            missing_id += 1
            continue
        genus = clean_text(rec.get("genus"))
        if genus.lower() in NOT_TREES:
            not_trees[genus] = not_trees.get(genus, 0) + 1
            continue
        if x is None or y is None:
            lat = lon = None
        else:
            if not (EASTING_RANGE[0] < x < EASTING_RANGE[1] and NORTHING_RANGE[0] < y < NORTHING_RANGE[1]):
                raise RuntimeError(
                    f"Macedon Ranges: point ({x}, {y}) of asset {asset} is not an MGA zone 55 "
                    "position near the shire"
                )
            lat, lon = mga55_to_wgs84(x, y)
        name, parsed_cultivar = name_and_cultivar(genus, rec.get("species"))

        tree_id.append(f"{ID_PREFIX}-{int(asset)}")
        species.append(name)
        cultivar.append(parsed_cultivar if name else None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_reader(rec.get("dbh")))

    if deleted:
        print(f"Macedon Ranges ingest: skipped {deleted} deleted .dbf records", file=sys.stderr)
    if missing_id:
        print(f"Macedon Ranges ingest: dropped {missing_id} rows with no numeric `id`", file=sys.stderr)
    if not_trees:
        print(
            f"Macedon Ranges ingest: dropped {sum(not_trees.values())} rows that are not "
            f"trees: {sorted(not_trees.items(), key=lambda kv: -kv[1])}",
            file=sys.stderr,
        )
    dbh_reader.check()

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["AUMRS"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    rows, points = download()
    print(f"Macedon Ranges ingest: {len(rows)} records in the shapefile", file=sys.stderr)
    table = transform(rows, points)
    table = validate_coordinates(table, city="Macedon Ranges", city_code="AUMRS")
    table = enforce_tree_schema(table, city="Macedon Ranges", data_source="MACEDON_OPENDATA")
    emit(table)
