#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fareham Borough Council's tree inventory, from the council's map server.

Source: `Corporate/Trees/MapServer/1` (`Council Trees`) on
maps.fareham.gov.uk, "Council Trees in Fareham, captured by FBC Tree Team,
based on OS MasterMap Topography Layer".  Published under the Open
Government Licence v3.0: the council's ArcGIS Online item for the service
(5f3ea634a36e4f3cb3c9a9e322512211, owner `htaylor_FarehamCouncil`, the
council's GIS account) gives its licence as "Open Government License (OGL)"
linking nationalarchives.gov.uk/doc/open-government-licence/version/3/, and
its credit as "FBC Trees Team, Contains OS data (c) Crown copyright and
database right".  23,195 rows on 2026-11-21, every one `TREETYPE` `Single
tree`.  Stored in British National Grid and read reprojected to WGS84 by the
server.

The service's other two layers are not read.  `Council Tree Groups` (2) is
polygons, one per group.  `TPOs` (0) is the Tree Preservation Order register:
6,434 polygons -- individual protected trees drawn as crown outlines, group
members, group boundaries, woodlands and areas, mostly privately owned trees
-- whose `Species_Description` is an English common name only (`Oak`,
`English Oak`, `Sycamore`), so it could not publish a scientific name.

**The layer is reloaded whole**: `OBJECTID` is a dense 22,904..46,098 run.
**`TREECODE` is the key** (`fareham-{TREECODE}`, leading zeros dropped): the
Ezytreev tree code, six digits (252..189,011), non-null and distinct on all
23,195 rows (checked over the whole layer on 2026-11-21).  `FEATREF` is
distinct too but is a drawing-feature number; `TREENO` is the number within
a site and repeats.  The layer has no GlobalID.  A row without a readable
code is dropped with a logged count.

The layer has no status column: `VITALITY`, `AGE`, `LASTVISIT` and
`VISITDATE` are null on every row and nothing marks a felled tree.  The five
`'Marker - sign' (NO INSPECTION)` rows are site markers, not trees, and are
dropped.  `Dead Tree - Unknown Species` publishes as Dead.

**Species is `SPECIES`**, a botanical name (`COMMONNAME` is a short English
name and is not mapped).  It is the Ezytreev species list Basingstoke
(`gbbsk/`) also uses, so `TAXA` -- the closed table of the names that are
not a clean binomial with an optional quoted cultivar -- shares most of its
entries.  Quoted cultivars are split by `enforce_tree_schema`; misspellings
(`Quercus petrea`, `Gleditsia tricanthos`, `Rhus typhrina`) are left to the
shared species map.

**DBH is `TRUNK`, centimetres of diameter** (`45cm`; 9 rows blank).
Diameter, not girth: median recorded height rises from 5 m under 20 cm to
13 m at 40-60 cm and 18 m from 100 cm, the same allometry as Basingstoke's
diameter bands.  `TRUNK / 2.54` publishes; the central check nulls the few
implausible values (a 538 cm entry).  An unreadable non-blank value counts
as unreadable and above 1% the ingest refuses.

**Crown width is twice `SPREAD`**: `SPREAD` (`" 2.5m"`, mirrored numerically
in `spreadr`, the r for radius) is a crown radius -- median 1.4 m under 20 cm
DBH, 3.8 m at 40-60 cm and 7 m at 100-120 cm, exactly half Basingstoke's
crown spreads at the same diameters -- so the published width is double it,
as Belfast (`gbbfs/`) does.  `HEIGHT` (metres) has no canonical column.
There is no planting date, so `plant_date` is null on every row.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    DEAD_SPECIES,
    UNKNOWN_CONIFER_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.fareham.gov.uk/server/rest/services/Corporate/Trees/MapServer/1",
    timeout=180,
)

OUT_FIELDS = "OBJECTID,TREECODE,SPECIES,TRUNK,SPREAD"

NOT_A_TREE = frozenset({"'Marker - sign' (NO INSPECTION)"})

# Refuse when more than this share of `TRUNK` or `SPREAD` values cannot be read.
MAX_UNREADABLE_SHARE = 0.01

_TRUNK = re.compile(r"(\d+(?:\.\d+)?)\s*cm")
_SPREAD = re.compile(r"(\d+(?:\.\d+)?)\s*m")

# `SPECIES` (whitespace collapsed) -> (species, cultivar) for every name that
# is not a clean binomial with an optional quoted cultivar.
TAXA: dict[str, tuple[str, str | None]] = {
    # Placeholders.
    "'Species not known'": (UNKNOWN_SPECIES, None),
    "Unknown Species - Broadleaf": (UNKNOWN_SPECIES, None),
    "Unknown Species - Conifer": (UNKNOWN_CONIFER_SPECIES, None),
    "Dead Tree - Unknown Species": (DEAD_SPECIES, None),
    "Chamaecyparis (unknown)": ("Chamaecyparis", None),
    "Cupressus unknown species": ("Cupressus", None),
    # A cultivar note with no cultivar named.
    "Acer platanoides Purple Variety": ("Acer platanoides", None),
    "Acer pseudoplantanus cultivar": ("Acer pseudoplatanus", None),
    # Hybrids written without their mark.
    "Ulmus hollandica": ("Ulmus x hollandica", None),
    "Alnus Spaethii": ("Alnus x spaethii", None),
    "Laburnum vossii": ("Laburnum x watereri", "Vossii"),
    "Prunus x 'Schmittii'": ("Prunus x schmittii", None),
    # Unquoted cultivars after the genus.
    "Prunus Tai Haku": ("Prunus", "Tai Haku"),
    "Prunus Umineko 'Snow Goose'": ("Prunus", "Umineko"),
    "Malus Rudolph": ("Malus", "Rudolph"),
    "Ulmus Clusius": ("Ulmus", "Clusius"),
    "Ulmus Dodoens": ("Ulmus", "Dodoens"),
    "Ulmus Lobel": ("Ulmus", "Lobel"),
    "Ulmus lutece": ("Ulmus", "Lutece"),
    'Fraxinus "Jaspidea"': ("Fraxinus excelsior", "Jaspidea"),
    "Fagus pendula": ("Fagus sylvatica", "Pendula"),
    # Unquoted (or double-quoted) cultivars after the species.
    "Acer campestre Elegant": ("Acer campestre", "Elegant"),
    "Acer campestre Louisa Red Shine": ("Acer campestre", "Louisa Red Shine"),
    "Acer campestre Queen Elizabeth": ("Acer campestre", "Queen Elizabeth"),
    "Acer platanoides Deborah": ("Acer platanoides", "Deborah"),
    "Acer platanoides Emerald Queen": ("Acer platanoides", "Emerald Queen"),
    "Acer pseudoplatanus Spaethii": ("Acer pseudoplatanus", "Spaethii"),
    "Acer pseudoplatanus Variegatum": ("Acer pseudoplatanus", "Variegatum"),
    "Betula pendula youngii": ("Betula pendula", "Youngii"),
    "Cedrus atlantica glauca": ("Cedrus atlantica", "Glauca"),
    "Cotinus coggyria Royal Purple": ("Cotinus coggygria", "Royal Purple"),
    "Fagus sylvatica Asplenifolia": ("Fagus sylvatica", "Asplenifolia"),
    "Fagus sylvatica purpurea": ("Fagus sylvatica", "Purpurea"),
    "Fraxinus oxycarpa Raywood": ("Fraxinus oxycarpa", "Raywood"),
    "Ilex aquifolium argentea marginata": ("Ilex aquifolium", "Argentea Marginata"),
    'Magnolia grandiflora "Exmouth"': ("Magnolia grandiflora", "Exmouth"),
    "Platanus orientalis digitata": ("Platanus orientalis", "Digitata"),
    "Prunus padus Albertii": ("Prunus padus", "Albertii"),
    "Prunus x hillieri Spire": ("Prunus x hillieri", "Spire"),
    "Quercus robur fastigiata Koster": ("Quercus robur", "Fastigiata Koster"),
    "Sorbus aria Magnifica": ("Sorbus aria", "Magnifica"),
    "Sorbus intermedia Brouwers": ("Sorbus intermedia", "Brouwers"),
    "Sorbus latifolia Henk Vink": ("Sorbus latifolia", "Henk Vink"),
    # Austrian pine: a subspecies note, not a cultivar.
    "Pinus nigra Austriaca": ("Pinus nigra", None),
    # Kenai birch, written as a variety of the paper birch.
    "Betula papyrifera kenaica": ("Betula papyrifera", None),
    # An apostrophe inside the quoted cultivar.
    "Fraxinus excelsior 'Westof's Glory'": ("Fraxinus excelsior", "Westhof's Glory"),
    # 'Paul's Scarlet' is the Midland hawthorn's cultivar; `oxycantha` is no
    # name at all (WCVP), and `oxyacantha` would read as a different taxon.
    "Crataegus oxycantha 'Paul Scarlet'": ("Crataegus laevigata", "Paul's Scarlet"),
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def species_of(raw) -> tuple[str, str | None]:
    """`(species string, cultivar)` from `SPECIES`; quoted cultivars are left
    in the string for `enforce_tree_schema` to split."""
    text = clean(raw)
    if text is None:
        return UNKNOWN_SPECIES, None
    return TAXA.get(text, (text, None))


_counts: dict[str, int] = {
    "no TREECODE": 0,
    "not a tree (marker sign)": 0,
}
_unreadable: dict[str, dict[str, int]] = {"TRUNK": {}, "SPREAD": {}}
_rows_read = 0


def measure(value, pattern: re.Pattern, field: str) -> float | None:
    """A `45cm` / `2.5m` string as a number; blank is null, anything else unreadable."""
    text = clean(value)
    if text is None:
        return None
    match = pattern.fullmatch(text)
    if match is None:
        _unreadable[field][text] = _unreadable[field].get(text, 0) + 1
        return None
    number = float(match.group(1))
    return number if number > 0 else None


def tree_code(value) -> int | None:
    text = clean(value)
    if text is None or not text.isdigit():
        return None
    return int(text)


def is_tree(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if clean(rec.get("SPECIES")) in NOT_A_TREE:
        _counts["not a tree (marker sign)"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    global _rows_read
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        _rows_read += 1
        rec = feature.get("attributes") or {}
        code = tree_code(rec.get("TREECODE"))
        if code is None:
            _counts["no TREECODE"] += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))
        name, selection = species_of(rec.get("SPECIES"))
        diameter_cm = measure(rec.get("TRUNK"), _TRUNK, "TRUNK")
        radius_m = measure(rec.get("SPREAD"), _SPREAD, "SPREAD")

        tree_id.append(f"fareham-{code}")
        species.append(name)
        cultivar.append(selection)
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_cm / 2.54 if diameter_cm else None)
        crown.append(2 * radius_m if radius_m else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["GBFAR"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def report() -> None:
    for reason, n in _counts.items():
        if n:
            print(f"Fareham: dropped {n:,} rows: {reason}", file=sys.stderr)
    for field, labels in _unreadable.items():
        bad = sum(labels.values())
        if not bad:
            continue
        print(
            f"Fareham: {bad:,} unreadable {field} values nulled: "
            f"{sorted(labels.items(), key=lambda kv: -kv[1])[:10]}",
            file=sys.stderr,
        )
        if _rows_read and bad / _rows_read > MAX_UNREADABLE_SHARE:
            raise RuntimeError(
                f"Fareham: {bad:,} of {_rows_read:,} {field} values are unreadable; "
                "the format changed"
            )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=is_tree, label="Fareham OpenData")
    report()
    table = validate_coordinates(table, city="Fareham", city_code="GBFAR")
    table = enforce_tree_schema(table, city="Fareham", data_source="FAREHAM_OPENDATA")
    emit(table)
