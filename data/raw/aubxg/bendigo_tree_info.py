#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Greater Bendigo, VIC's municipal tree inventory.

Source: "City of Greater Bendigo : Environment - Trees" on data.gov.au
(dataset `city-of-greater-bendigo-environment-trees`, published by the City
of Greater Bendigo's own organisation there), "urban street and park trees
within the City of Greater Bendigo", under Creative Commons Attribution 2.5
Australia (the dataset's licence field).  The council has no open-data
portal of its own; data.gov.au is where it publishes.

**Read from the zipped shapefile, not the WFS.**  The dataset lists a
GeoServer WFS/GeoJSON resource beside the shapefile, but on 2026-11-21 that
feature type was gone ("Feature type ... unknown"), and its resource dates
from 2022; the shapefile (`.shz`, a zip of .shp/.shx/.dbf/.prj) is the
resource the council re-uploads -- last on 2024-11-29, its files dated
2024-10-24.  121,481 points, WGS84 (the .prj is GCS_WGS_1984), every one a
Point.  The shapefile is read here with the standard library (a point .shp
and a dBase table are both fixed-width), because no shared reader exists and
the cloud jobs run with the repo's usual dependencies only.  The .dbf's
language driver is 3 (Windows ANSI), so text is cp1252.

**`AssetID` is the key**: the council's asset number, numeric, 121,481
distinct values over 121,481 rows, none null, with gaps over 267123..846278
(an asset register, not a row number).  Prefixed `bxg-`.

**`Species` is "binomial - common name"** (`Eucalyptus microcarpa - Grey
Box`; once `Betula nigra- River Birch`), with a selection sometimes quoted in
the name (`Prunus cerasifera 'Nigra'`) and once in the common name (`Ilex
alterclerensis - Holly 'Lawsoniana'`).  The name before the dash is kept and
the common name dropped.  `Genus` is a picklist grouping (`Eucalyptus A to
L`), not a taxon, and is ignored.  `SOURCE_SPELLINGS` restores structural
oddities keyed on the exact name (an unquoted or half-quoted cultivar
written as an epithet: `Melia Elite'`, `Fraxinus raywoodii`, `Lagerstroemia
tuscarora`); misspellings proper (`Myoprum`, `Vibernum`, `Eucalytpus`) are
left for the shared misspelling map.  `Unknown sp.` publishes as `Unknown`.

**`Cultivar` is a picklist that is often wrong**: 118,918 rows say `Not
Specified`, and the rest include selections that cannot belong to the tree
(`Cimmaron` -- an ash -- on Angophora costata, `Aristocrat` -- a pear -- on
three eucalypts, `Banksia Seed Collection - Tooberac`, a seed provenance).
`CULTIVARS` maps each label to its cultivar name and the genera it is a
cultivar of; a label on any other genus is dropped (count logged), as is an
unknown label.  A kept value wins over a cultivar parsed from the name.

Not published: `Type` (Native / Exotic / Victorian Species), `Desc` (the
street segment and common name), and the address columns.  The inventory
records no diameter, height, crown, planting date or status, so those are
null; it records no empty sites or stumps.  The council's yearly "Tree
Planting 20xx" datasets list trees *to be* planted, not trees, and are not
read.
"""

import io
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
RESOURCE = CkanResource("data.gov.au/data", "b3f01081-924c-41b7-989a-cf521ca136ea")
LAYER = "CoGB Environment - Trees"

ID_PREFIX = "bxg"

# Exact names (after quotes are straightened and whitespace collapsed) that
# are not a binomial plus an optional quoted cultivar as written.
SOURCE_SPELLINGS: dict[str, str] = {
    # A cultivar written as the epithet, or half-quoted.
    "Melia Elite'": "Melia azedarach 'Elite'",
    "Melia 'Elite'": "Melia azedarach 'Elite'",
    "Melia 'Caroline'": "Melia azedarach 'Caroline'",
    "Fraxinus raywoodii": "Fraxinus angustifolia 'Raywood'",
    "Lagerstroemia tuscarora": "Lagerstroemia 'Tuscarora'",
    # A grafting note, not a selection.
    "Hakea bucculenta 'Grafted'": "Hakea bucculenta",
    # A variety written as a quoted cultivar.
    "Cupressus himilaica var 'Darjeelingensis'": "Cupressus himilaica var. darjeelingensis",
    # A nothospecies written as two words.
    "Prunus x amygdalo persica": "Prunus x amygdalopersica",
}

NOT_A_NAME = frozenset({"unknown sp.", "unknown"})

# Cultivar picklist label -> (cultivar name, genera it is a cultivar of).
CULTIVARS: dict[str, tuple[str, frozenset[str]]] = {
    "Chauncticleer (NARROW CONICAL)": ("Chanticleer", frozenset({"Pyrus"})),
    "Aristocrat (OPEN)": ("Aristocrat", frozenset({"Pyrus"})),
    "Capital (COLUMNAR)": ("Capital", frozenset({"Pyrus"})),
    "Bradford": ("Bradford", frozenset({"Pyrus"})),
    "Cimmaron": ("Cimmaron", frozenset({"Fraxinus"})),
    "Urbanite": ("Urbanite", frozenset({"Fraxinus"})),
    "Autumn Applause": ("Autumn Applause", frozenset({"Fraxinus"})),
    "Raywoodii": ("Raywood", frozenset({"Fraxinus"})),
    "Plena": ("Plena", frozenset({"Malus"})),
    "Sutyzam": ("Sutyzam", frozenset({"Malus"})),
    "Gorgeous": ("Gorgeous", frozenset({"Malus"})),
    "Nana": ("Nana", frozenset({"Corymbia"})),
    "Sensation": ("Sensation", frozenset({"Acer"})),
    "Natchez (White)": ("Natchez", frozenset({"Lagerstroemia"})),
    "Miami (Red)": ("Miami", frozenset({"Lagerstroemia"})),
    "Sioux (Hot Pink)": ("Sioux", frozenset({"Lagerstroemia"})),
    "Tuscarora (Dark coral-pink)": ("Tuscarora", frozenset({"Lagerstroemia"})),
    "Tuscarora (Red)": ("Tuscarora", frozenset({"Lagerstroemia"})),
    "Lipan (Pink)": ("Lipan", frozenset({"Lagerstroemia"})),
    "Little Spotty": ("Little Spotty", frozenset({"Eucalyptus"})),
    "Rosea": ("Rosea", frozenset({"Eucalyptus"})),
    "Nigra": ("Nigra", frozenset({"Prunus"})),
    "Crimson Spire": ("Crimson Spire", frozenset({"Prunus"})),
    "Santa Rosa": ("Santa Rosa", frozenset({"Prunus"})),
    "Pendula": ("Pendula", frozenset({"Prunus", "Morus"})),
    "Sunburst": ("Sunburst", frozenset({"Gleditsia"})),
    "Shade Master": ("Shademaster", frozenset({"Gleditsia"})),
    "Todd": ("Todd", frozenset({"Ulmus"})),
    "Inspire": ("Inspire", frozenset({"Ulmus"})),
    "Burnley Select": ("Burnley Select", frozenset({"Ulmus"})),
    "Sentuous": ("Scentuous", frozenset({"Corymbia"})),
    "Fastigiata": ("Fastigiata", frozenset({"Quercus"})),
    "Smaragd": ("Smaragd", frozenset({"Thuja"})),
    "Glauca": ("Glauca", frozenset({"Cupressus"})),
    "Aurea": ("Aurea", frozenset({"Cupressus"})),
    "Swanes Gold": ("Swane's Golden", frozenset({"Cupressus"})),
    "Jerilderie Red": ("Jerilderie Red", frozenset({"Brachychiton"})),
    "Griffith Pink": ("Griffith Pink", frozenset({"Brachychiton"})),
    "Bella Pink": ("Bella Pink", frozenset({"Brachychiton"})),
    "Green Spire": ("Greenspire", frozenset({"Tilia"})),
    "Frontyard": ("Frontyard", frozenset({"Tilia"})),
    "Wireless": ("Wireless", frozenset({"Zelkova"})),
    "Green Vase": ("Green Vase", frozenset({"Zelkova"})),
    "Swanhill": ("Swan Hill", frozenset({"Olea"})),
    "King's Park Special": ("Kings Park Special", frozenset({"Callistemon"})),
    "Burgundy": ("Burgundy", frozenset({"Agonis"})),
    "Digitata": ("Digitata", frozenset({"Platanus"})),
    "Elite (low fruiting)": ("Elite", frozenset({"Melia"})),
    "Copper Glow": ("Copper Glow", frozenset({"Leptospermum"})),
}

# Labels that say nothing about a selection.
NO_CULTIVAR = frozenset({"", "not specified", "unknown", "butia"})
# A seed provenance (the Banksia seed-collection program), not a selection.
PROVENANCE = re.compile(r"^banksia seed collection\b", re.IGNORECASE)

QUOTES = str.maketrans({"‘": "'", "’": "'", "\x91": "'", "\x92": "'", "`": "'"})
# The common-name separator: a dash followed by a space (`Betula nigra- River
# Birch` has none before it).
COMMON_NAME_SEP = re.compile(r"\s*-\s+")
QUOTED = re.compile(r"'([^']+)'")


def clean_text(value: str) -> str:
    return " ".join(value.translate(QUOTES).split())


def name_and_cultivar(value: str) -> tuple[str | None, str | None]:
    """`(species, cultivar parsed from the name)` for one `Species` value."""
    text = clean_text(value)
    parts = COMMON_NAME_SEP.split(text, maxsplit=1)
    head = parts[0]
    common = parts[1] if len(parts) > 1 else ""
    # A parenthesised common name written into the binomial.
    head = re.sub(r"\s*\([^)]*\)\s*$", "", head).strip()
    head = SOURCE_SPELLINGS.get(head, head)
    if not head or head.lower() in NOT_A_NAME:
        return None, None
    cultivar = None
    match = QUOTED.search(head)
    if match:
        cultivar = match.group(1).strip() or None
        head = (head[: match.start()] + head[match.end():]).strip()
    elif common:
        # A selection quoted in the common name: `Holly 'Lawsoniana'`, or the
        # whole common name `'nana'`.
        match = QUOTED.search(common)
        if match:
            cultivar = match.group(1).strip() or None
    if cultivar:
        cultivar = cultivar[0].upper() + cultivar[1:]
    species = normalize_species(head)
    return species, cultivar


class CultivarReader:
    """Reads the `Cultivar` picklist, counting labels it rejects."""

    def __init__(self):
        self.unknown: dict[str, int] = {}
        self.wrong_genus: dict[tuple[str, str], int] = {}
        self.provenance = 0

    def __call__(self, label: str, species: str | None) -> str | None:
        text = clean_text(label)
        if text.lower() in NO_CULTIVAR:
            return None
        if PROVENANCE.match(text):
            self.provenance += 1
            return None
        entry = CULTIVARS.get(text)
        if entry is None:
            self.unknown[text] = self.unknown.get(text, 0) + 1
            return None
        name, genera = entry
        genus = (species or "").split(" ")[0]
        if genus not in genera:
            key = (genus or "?", text)
            self.wrong_genus[key] = self.wrong_genus.get(key, 0) + 1
            return None
        return name

    def report(self) -> None:
        if self.provenance:
            print(
                f"Bendigo ingest: {self.provenance} `Cultivar` values are a seed "
                "provenance, not a selection; not published",
                file=sys.stderr,
            )
        if self.wrong_genus:
            total = sum(self.wrong_genus.values())
            print(
                f"Bendigo ingest: {total} `Cultivar` values name a selection of "
                f"another genus; not published: {sorted(self.wrong_genus.items(), key=lambda kv: -kv[1])[:10]}",
                file=sys.stderr,
            )
        if self.unknown:
            print(
                f"Bendigo ingest: {sum(self.unknown.values())} `Cultivar` values with "
                f"an unknown label; not published: {sorted(self.unknown.items(), key=lambda kv: -kv[1])[:10]}",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Shapefile reading (point .shp + dBase III .dbf)
# ---------------------------------------------------------------------------

def read_dbf(data: bytes, encoding: str = "cp1252") -> tuple[list[str], list[dict]]:
    """Every record of a dBase table as a dict of stripped strings."""
    if len(data) < 32:
        raise RuntimeError("Bendigo shapefile: the .dbf is truncated")
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
            raise RuntimeError(f"Bendigo shapefile: the .dbf ends at record {i} of {count}")
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
        raise RuntimeError("Bendigo shapefile: the .shp has no shapefile header")
    shape_type = struct.unpack("<i", data[32:36])[0]
    if shape_type != 1:
        raise RuntimeError(f"Bendigo shapefile: shape type {shape_type}, expected 1 (Point)")
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
            raise RuntimeError(f"Bendigo shapefile: record of shape type {record_type}")
        pos += 8 + content_words * 2
    return points


def download() -> tuple[list[str], list[dict], list[tuple[float | None, float | None]]]:
    response = get_with_retry(resource_download_url(RESOURCE), timeout=300)
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"Bendigo shapefile download is not a zip ({len(response.content)} bytes)"
        ) from exc
    members = {Path(n).name: n for n in archive.namelist()}
    for ext in (".shp", ".dbf", ".prj"):
        if f"{LAYER}{ext}" not in members:
            raise RuntimeError(
                f"Bendigo shapefile: no `{LAYER}{ext}` in the archive ({sorted(members)})"
            )
    prj = archive.read(members[f"{LAYER}.prj"]).decode("ascii", errors="replace")
    if "WGS_1984" not in prj or "PROJCS" in prj:
        raise RuntimeError(f"Bendigo shapefile is no longer WGS84 lon/lat: {prj[:200]}")
    names, rows = read_dbf(archive.read(members[f"{LAYER}.dbf"]))
    points = read_points(archive.read(members[f"{LAYER}.shp"]))
    if len(points) != len(rows):
        raise RuntimeError(
            f"Bendigo shapefile: {len(points)} shapes but {len(rows)} records"
        )
    for required in ("AssetID", "Species", "Cultivar"):
        if required not in names:
            raise RuntimeError(f"Bendigo shapefile has no `{required}` column ({names})")
    return names, rows, points


def transform(rows: list[dict], points: list[tuple[float | None, float | None]]) -> pa.Table:
    cultivar_reader = CultivarReader()
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []

    deleted = missing_id = 0
    for rec, (lon, lat) in zip(rows, points):
        if rec["_deleted"]:
            deleted += 1
            continue
        asset = rec.get("AssetID") or ""
        if not asset.isdigit():
            missing_id += 1
            continue
        name, parsed_cultivar = name_and_cultivar(rec.get("Species") or "")
        column_cultivar = cultivar_reader(rec.get("Cultivar") or "", name)

        tree_id.append(f"{ID_PREFIX}-{int(asset)}")
        species.append(name)
        cultivar.append(column_cultivar or (parsed_cultivar if name else None))
        latitude.append(lat)
        longitude.append(lon)

    if deleted:
        print(f"Bendigo ingest: skipped {deleted} deleted .dbf records", file=sys.stderr)
    if missing_id:
        print(
            f"Bendigo ingest: dropped {missing_id} rows with no numeric `AssetID`",
            file=sys.stderr,
        )
    cultivar_reader.report()

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["AUBXG"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    _, rows, points = download()
    print(f"Bendigo ingest: {len(rows)} records in the shapefile", file=sys.stderr)
    table = transform(rows, points)
    table = validate_coordinates(table, city="Bendigo", city_code="AUBXG")
    table = enforce_tree_schema(table, city="Bendigo", data_source="BENDIGO_OPENDATA")
    emit(table)
