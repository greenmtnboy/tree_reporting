#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Brimbank City Council street and open-space trees, from data.gov.au.

Source: two datasets on data.gov.au's CKAN, both published by Brimbank City
Council under Creative Commons Attribution 2.5 Australia (each package's
licence field): "Brimbank Street Trees" (`brimbank-street-trees`, shapefile
resource `0072de55`) and "Brimbank Open Space Trees"
(`brimbank-open-space-trees`, shapefile resource `0df2776c`).  Both are a
zipped point shapefile on GDA94 lon/lat (EPSG:4283, within centimetres of
WGS84), files dated 2019-01-29, resources last modified 2019-03-12: 133,373
street and 59,922 open-space records.  The council has no open-data portal of
its own.  Brimbank (Sunshine, St Albans, Keilor, Deer Park) is west of the
City of Melbourne, shares no boundary with it, and is inside AUMEL's
territory, so it is a partition of Melbourne rather than a city.

**Read from the shapefiles, not the WFS.**  Each dataset lists a GeoServer
WFS/GeoJSON resource, but on 2026-11-23 both feature types were gone
("Feature type ... unknown"), as Bendigo's was (`aubxg/bendigo_tree_info.py`,
whose standard-library shapefile reader this copies; the .dbf's language
driver is 3, Windows ANSI).

**The id is the coordinate, because the source has none.**  The columns are
`Name` (the street or reserve), `Location` (an address), `Type`, `Comment`,
`Status`, `Genus`, `Species`, `dbh`, `Long`/`Lat` (equal to the geometry on
every record), `height`, `canopy`, `overhead_w` (overhead wires) and
`program`: no asset number, and the record order is a file position.  So the
id is `coordinate_tree_id("brimbank", lon, lat)`, Spokane's rule
(`usgeg/spokane_tree_info.py`), over both files together.  Rows that share a
point are dropped by `drop_stacked_coordinates` unless they name the same
taxon (one tree entered twice, the first kept): 737 points in the street file,
47 in the open-space file, and 11 points that both files record.

* `Status` is `Existing` or `Removed`; the 34,641 removed trees are dropped.
  `Type` is `TR-Street Tree`, `TR-Open Space Tree` or `TR-Street Tree Pla`
  (the field is 18 characters wide): the last is a tree recorded at planting
  (all 2018 programs, a named species, measured as a sapling in the
  open-space file), not an empty site, so it stays.  The inventory records no
  vacant sites or stumps.
* `Genus` + `Species` are the scientific name, but `Species` sometimes holds
  the whole name (`Corymbia maculata`), a hybrid with its mark welded on
  (`Xacerifolia`), a bare cultivar (`Harkness`, `Raywood`), or a name cut off
  at the field's 30 characters (`angustifolia subsp. angustifol`).
  `name_of` assembles the binomial; `SOURCE_SPELLINGS` restores the truncated
  and structurally odd names keyed on the exact (genus, species) pair.
  Misspellings proper (`Faxinus`, `europea`, `Nichollii`) are left for the
  shared misspelling map.  `Not Specified` in `Genus` is the sentinel; an
  epithet with no genus publishes `Unknown` unless the epithet names one genus
  in this inventory (`NO_GENUS`).
* **`dbh` is a band in centimetres** (`<10`, `10-30` ... `70-90`, `>90`):
  71,508 street trees under 10 cm, a council planting young trees for
  fifteen years.  Each band is its midpoint and the open-topped `>90` its
  bound plus half the common 20 cm width (100 cm), Denver's rule as Ballarat
  (`aubal/`) reads it; `<10` is 5 cm.  **`canopy` is the crown spread in
  metres, banded the same way** (`00-02` ... `15-20`, `>20` read as 22.5 m),
  published as `crown_width_m`.  `Not Specified` or blank is null; an unknown
  band label counts as unreadable and more than 1% refuses the build.
  `height` is a height band and is not published.
* **`program` is the planting program**, the year in its name: `STIP2013`
  (the Street Tree Improvement Program of that year), `RRP2018`, `UD2012`,
  `MRP2008`.  Its year is published as the planting date (1 January of it,
  Melbourne's own year-only rule); the trees under a 2016-2018 program are all
  in the under-10 cm band.  `Not Specified` and `OS-Open Space` give no date.
  `Comment` (Yes/No) is unexplained and not published.
"""

import io
import re
import struct
import sys
import zipfile
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    get_with_retry,
    normalize_species,
    validate_coordinates,
)
from shared.platforms.ckan import CkanResource, resource_download_url  # noqa: E402

# (resource, the layer name inside the zip, the crown column's spelling).
LAYERS: tuple[tuple[CkanResource, str, str], ...] = (
    (
        CkanResource("data.gov.au/data", "0072de55-3e43-4d0e-9a5e-7a9449b828fc", timeout=300),
        "Brimbank Street Trees",
        "canopy",
    ),
    (
        CkanResource("data.gov.au/data", "0df2776c-4432-4dcc-9cfd-1ea3efad2cfc", timeout=300),
        "Brimbank Open Space Trees",
        "Canopy",
    ),
)

ID_PREFIX = "brimbank"
REQUIRED = ("Status", "Type", "Genus", "Species", "dbh", "program")
KNOWN_TYPES = frozenset({"TR-Street Tree", "TR-Open Space Tree", "TR-Street Tree Pla"})
KNOWN_STATUS = frozenset({"Existing", "Removed"})

NOT_SPECIFIED = frozenset({"", "not specified"})
GENUS_ONLY = frozenset({"sp.", "sp", "spp.", "spp"})

# (Genus, Species) exactly as published -> the whole name.  Names cut off at
# the field width, cultivars written unquoted or as the epithet, and a genus
# column that contradicts the name in `Species`.
SOURCE_SPELLINGS: dict[tuple[str, str], str] = {
    ("Callistemon", "Callistemon 'Kings Park Specia"): "Callistemon 'Kings Park Special'",
    ("Eucalyptus", "Eucalyptus leucoxylon megaloca"): "Eucalyptus leucoxylon subsp. megalocarpa",
    ("Eucalyptus", "Eucalyptus mannifera little sp"): "Eucalyptus mannifera 'Little Spotty'",
    ("Eucalyptus", "Eucalyptus pauciflora subsp pa"): "Eucalyptus pauciflora",
    ("Eucalyptus", "Eucalyptus leucoxylon Rosea"): "Eucalyptus leucoxylon 'Rosea'",
    ("Eucalyptus", "Eucalyptus sideroxylon Rosea"): "Eucalyptus sideroxylon 'Rosea'",
    # `Canata` is no published selection of the species.
    ("Eucalyptus", "Eucalyptus leucoxylon Canata"): "Eucalyptus leucoxylon",
    ("Fraxinus", "Fraxinus 'claret ash'"): "Fraxinus angustifolia 'Raywood'",
    ("Fraxinus", "Fraxinus Urbanite"): "Fraxinus pennsylvanica 'Urbanite'",
    ("Fraxinus", "Fraxinus oxycarpa Raywoodii"): "Fraxinus angustifolia 'Raywood'",
    ("Fraxinus", "Raywood"): "Fraxinus angustifolia 'Raywood'",
    ("Fraxinus", "Urbanite"): "Fraxinus pennsylvanica 'Urbanite'",
    ("Fraxinus", "angustifolia subsp. angustifol"): "Fraxinus angustifolia subsp. angustifolia",
    # `Honey Lo` is the common name, honey locust.
    ("Gleditsia", "Gleditsia triacanthos Honey Lo"): "Gleditsia triacanthos",
    ("Gleditsia", "Gleditsia trican Shademaster"): "Gleditsia triacanthos 'Shademaster'",
    ("Lagerstroemia", "Lagerstroemia indica x L. faur"): "Lagerstroemia indica x fauriei",
    ("Prunus", "Prunus cerasifera Nigra"): "Prunus cerasifera 'Nigra'",
    ("Prunus", "Sato-zakura Group"): "Prunus serrulata",
    ("Prunus", "Shimidsu Sakura"): "Prunus serrulata 'Shimidsu Sakura'",
    ("Pyrus", "Pyrus betulaefolia 'southwood"): "Pyrus betulaefolia 'Southworth'",
    ("Pyrus", "Pyrus calleryana Capital"): "Pyrus calleryana 'Capital'",
    ("Robinia", "Robinia pseudoacacia Frisia"): "Robinia pseudoacacia 'Frisia'",
    ("Salix", "babylonica var. pekinensis 'To"): "Salix babylonica var. pekinensis 'Tortuosa'",
    ("Salix", "babylonica var. perkinensis 'T"): "Salix babylonica var. pekinensis 'Tortuosa'",
    ("Salix", "X sepulcralis var. chyrysocoma"): "Salix x sepulcralis var. chrysocoma",
    ("Citrus", "Xsoulangeana"): "Magnolia x soulangeana",
    ("Garrya", "Geijera parviflora"): "Geijera parviflora",
}

# An epithet with `Not Specified` as its genus, where the epithet belongs to
# one genus among this inventory's trees.  `indica` (Lagerstroemia or Melia)
# does not, and publishes `Unknown`.
NO_GENUS: dict[str, str] = {
    "camaldulensis": "Eucalyptus camaldulensis",
    "melanoxylon": "Acacia melanoxylon",
    "leylandii": "Cupressus x leylandii",
    "patersonia": "Lagunaria patersonia",
}

# Band label -> midpoint.  DBH in centimetres, crown spread in metres.
DBH_BAND_CM: dict[str, float] = {
    "<10": 5.0,
    "10-30": 20.0,
    "30-50": 40.0,
    "50-70": 60.0,
    "70-90": 80.0,
    ">90": 100.0,
}
CROWN_BAND_M: dict[str, float] = {
    "00-02": 1.0,
    "02-06": 4.0,
    "06-10": 8.0,
    "10-15": 12.5,
    "15-20": 17.5,
    ">20": 22.5,
}
MAX_BAND_PARSE_FAILURES = 0.01

PROGRAM_YEAR = re.compile(r"^(?:STIP|RRP|UD|MRP)\s*((?:19|20)\d\d)$")
NO_PROGRAM = frozenset({"not specified", "os-open space"})


class BandReader:
    """Reads one banded column, counting the labels it does not know."""

    def __init__(self, name: str, bands: dict[str, float]):
        self.name = name
        self.bands = bands
        self.read = 0
        self.failures: dict[str, int] = {}

    def __call__(self, value: str | None) -> float | None:
        self.read += 1
        text = (value or "").strip()
        if text.lower() in NOT_SPECIFIED:
            return None
        mid = self.bands.get(text)
        if mid is None:
            self.failures[text] = self.failures.get(text, 0) + 1
        return mid

    def check(self) -> None:
        bad = sum(self.failures.values())
        if not bad:
            return
        share = bad / max(self.read, 1)
        print(
            f"Brimbank: {bad} unreadable `{self.name}` values ({share:.2%}): "
            f"{sorted(self.failures.items(), key=lambda kv: -kv[1])[:10]}",
            file=sys.stderr,
        )
        if share > MAX_BAND_PARSE_FAILURES:
            raise RuntimeError(
                f"Brimbank `{self.name}`: {share:.2%} of rows carry a band label "
                "this ingest does not know; the council has changed its bands"
            )


class ProgramReader:
    """The planting year a `program` code names, counting codes it cannot read."""

    def __init__(self) -> None:
        self.read = 0
        self.failures: dict[str, int] = {}

    def __call__(self, value: str | None) -> date | None:
        self.read += 1
        text = (value or "").strip()
        if text.lower() in NO_PROGRAM or not text:
            return None
        match = PROGRAM_YEAR.match(text)
        if match is None:
            self.failures[text] = self.failures.get(text, 0) + 1
            return None
        return date(int(match.group(1)), 1, 1)

    def check(self) -> None:
        bad = sum(self.failures.values())
        if not bad:
            return
        share = bad / max(self.read, 1)
        print(
            f"Brimbank: {bad} unreadable `program` values ({share:.2%}): "
            f"{sorted(self.failures.items(), key=lambda kv: -kv[1])[:10]}",
            file=sys.stderr,
        )
        if share > MAX_BAND_PARSE_FAILURES:
            raise RuntimeError(
                f"Brimbank `program`: {share:.2%} of rows carry a program code "
                "this ingest does not know"
            )


def tidy(name: str) -> str | None:
    """`normalize_species` on the taxon, the quoted cultivar kept as written."""
    quote = name.find("'")
    if quote == -1:
        return normalize_species(name)
    taxon = normalize_species(name[:quote])
    return f"{taxon} {name[quote:]}" if taxon else None


def name_of(genus: str, epithet: str) -> str | None:
    """The scientific name from `Genus` and `Species`; None publishes `Unknown`."""
    g = " ".join(genus.split())
    s = " ".join(epithet.split())
    whole = SOURCE_SPELLINGS.get((g, s))
    if whole is not None:
        return tidy(whole)
    if g.lower() in NOT_SPECIFIED:
        if s.lower() in NOT_SPECIFIED:
            return None
        whole = NO_GENUS.get(s.lower())
        if whole is not None:
            return whole
        # A whole name written into `Species` with the genus left blank.
        if len(s.split()) >= 2 and s[:1].isupper() and not s.lower().startswith("x"):
            return tidy(s)
        return None
    if s.lower() in NOT_SPECIFIED or s.lower() in GENUS_ONLY:
        return normalize_species(g)
    # A trailing ` cv` ("a cultivar of") names no selection.
    s = re.sub(r"\s+cv$", "", s)
    # The whole name, genus included.
    if s.lower().startswith(g.lower() + " "):
        return tidy(s)
    # A hybrid with its mark: `X acerifolia`, `Xacerifolia`.
    hybrid = re.match(r"^[Xx]\s*([a-z].*)$", s)
    if hybrid:
        return tidy(f"{g} x {hybrid.group(1)}")
    # A bare cultivar: `Harkness`, `Kings Park Special`.
    if s[:1].isupper():
        return normalize_species(g) + f" '{s}'"
    return tidy(f"{g} {s}")


# ---------------------------------------------------------------------------
# Shapefile reading (point .shp + dBase III .dbf), as aubxg/bendigo_tree_info.py
# ---------------------------------------------------------------------------

def read_dbf(data: bytes, encoding: str = "cp1252") -> tuple[list[str], list[dict]]:
    """Every record of a dBase table as a dict of stripped strings."""
    if len(data) < 32:
        raise RuntimeError("Brimbank shapefile: the .dbf is truncated")
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
            raise RuntimeError(f"Brimbank shapefile: the .dbf ends at record {i} of {count}")
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
        raise RuntimeError("Brimbank shapefile: the .shp has no shapefile header")
    shape_type = struct.unpack("<i", data[32:36])[0]
    if shape_type != 1:
        raise RuntimeError(f"Brimbank shapefile: shape type {shape_type}, expected 1 (Point)")
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
            raise RuntimeError(f"Brimbank shapefile: record of shape type {record_type}")
        pos += 8 + content_words * 2
    return points


def download(resource: CkanResource, layer: str) -> tuple[list[dict], list[tuple[float | None, float | None]]]:
    response = get_with_retry(resource_download_url(resource), timeout=resource.timeout)
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"Brimbank `{layer}` download is not a zip ({len(response.content)} bytes)"
        ) from exc
    members = {Path(n).name: n for n in archive.namelist()}
    for ext in (".shp", ".dbf", ".prj"):
        if f"{layer}{ext}" not in members:
            raise RuntimeError(
                f"Brimbank shapefile: no `{layer}{ext}` in the archive ({sorted(members)})"
            )
    prj = archive.read(members[f"{layer}.prj"]).decode("ascii", errors="replace")
    if "PROJCS" in prj or not ("GDA_1994" in prj or "WGS_1984" in prj):
        raise RuntimeError(f"Brimbank `{layer}` is no longer GDA94/WGS84 lon/lat: {prj[:200]}")
    names, rows = read_dbf(archive.read(members[f"{layer}.dbf"]))
    points = read_points(archive.read(members[f"{layer}.shp"]))
    if len(points) != len(rows):
        raise RuntimeError(f"Brimbank `{layer}`: {len(points)} shapes but {len(rows)} records")
    for required in REQUIRED:
        if required not in names:
            raise RuntimeError(f"Brimbank `{layer}` has no `{required}` column ({names})")
    return rows, points


def transform(layers: list[tuple[list[dict], list, str]]) -> pa.Table:
    dbh_reader = BandReader("dbh", DBH_BAND_CM)
    crown_reader = BandReader("canopy", CROWN_BAND_M)
    program_reader = ProgramReader()

    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    removed = deleted = no_point = 0
    for rows, points, crown_column in layers:
        for rec, (lon, lat) in zip(rows, points):
            if rec["_deleted"]:
                deleted += 1
                continue
            status, kind = rec.get("Status") or "", rec.get("Type") or ""
            if status not in KNOWN_STATUS or kind not in KNOWN_TYPES:
                raise RuntimeError(
                    f"Brimbank: unknown Status/Type {status!r}/{kind!r}; the coding has changed"
                )
            if status == "Removed":
                removed += 1
                continue
            if lon is None or lat is None:
                no_point += 1
                continue
            cm = dbh_reader(rec.get("dbh"))

            tree_id.append(coordinate_tree_id(ID_PREFIX, lon, lat))
            species.append(name_of(rec.get("Genus") or "", rec.get("Species") or ""))
            plant_date.append(program_reader(rec.get("program")))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(cm / 2.54 if cm is not None else None)
            crown.append(crown_reader(rec.get(crown_column)))

    print(
        f"Brimbank: dropped {removed} removed tree(s), {deleted} deleted record(s), "
        f"{no_point} with no point",
        file=sys.stderr,
    )
    dbh_reader.check()
    crown_reader.check()
    program_reader.check()

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUMEL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


if __name__ == "__main__":
    layers = []
    for resource, layer, crown_column in LAYERS:
        rows, points = download(resource, layer)
        print(f"Brimbank: {len(rows)} records in `{layer}`", file=sys.stderr)
        layers.append((rows, points, crown_column))
    table = transform(layers)
    table = drop_stacked_coordinates(table, label="Brimbank", identical=("species",))
    table = validate_coordinates(table, city="Melbourne", city_code="AUMEL")
    table = enforce_tree_schema(table, city="Melbourne", data_source="BRIMBANK")
    emit(table)
