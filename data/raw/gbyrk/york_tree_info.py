#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""York's tree inventory: City of York Council's council-owned and private trees.

Source: two layers of the council's on-prem ArcGIS Server map service
`Public/EnvPlan/MapServer` (stored in British National Grid, read
reprojected to WGS84):

* 3 `Council trees` -- trees the council owns and maintains (streets, parks,
  schools, housing land, parish land): 20,064 rows on 2026-11-21;
* 18 `Private trees` -- privately owned trees the council records, mostly
  under Tree Preservation Orders: 1,172 rows.

Both are published on York Open Data (CKAN, `council-owned-trees` and
`private-trees`) and on the council's ArcGIS Hub (`data-cyc`) as "a live API
link to CYC's GIS server", under the Open Government Licence v3.0
(https://data.yorkopendata.org/dataset/council-owned-trees); the ArcGIS item
(30f38f358843467daa2d93074a03b8d5) adds "(c) Crown copyright and database
rights 2017 Ordnance Survey 100020818".  The two layers share one schema
(`TREEID`, `SITE_NAME`, `SPECIES`, `BOTANICAL`, `OWNER`, `Planted`) and one
OBJECTID sequence, so they are read as one source under one label.

**The id is the rounded coordinate (`york-{lon:.7f}_{lat:.7f}`).**  There is
no GlobalID.  `TREEID` (the council's tag number, `U02296`, `L06030`, a TPO
reference) is blank on 4,761 rows, and 366 values repeat across 1,032
more (`L06030` on 49 rows), so it is not an id.  `OBJECTID` is not one
either: the two layers interleave within 774,562..798,561, a sequence that
starts three-quarters of a million rows in, the mark of a table that is
truncated and reloaded whole.  Five positions hold more than one row on
2026-11-21 (one shared by a council and a private row);
`drop_stacked_coordinates` keeps one row where they agree on species (four
points) and drops all four rows at the one where they do not.

**Species is `BOTANICAL`**, a scientific name in title case (`Acer
Pseudoplatanus`, `Tilia X Europaea`; the schema lower-cases the epithet),
with the cultivar quoted (`Tilia Cordata 'green Spire'`, sometimes with the
closing quote cut off by the column's width) or as a trailing capitalised
word (`Pyrus Calleryana Chanticleer`, `Carpinus Betulus Fastigiata`), both
split into `cultivar` here.  `REWRITES` maps values that put a cultivar in
the epithet's place (`Prunus Kanzan`, `Malus Profusion`), the few that are
a common name in the botanical column (`Cherry`, 988 rows, is the genus;
`Leyland Cypress`, `Liquid Amber`) and the column's own placeholders:
`Unclassified` carries what it is in `SPECIES` (`Conifer`, `Dead Trees`,
`Hedgerow`), `Various` (23 rows) is a mixed group.  `SPECIES`, the common
name, is not mapped.  Misspellings (`Crateagus`, `Jugans Regia`, `Acer
Saccarhinum`) are left to the shared misspelling map.

**Not every row is a tree**: `Unclassified`/`Hedgerow` (6 rows) is a hedge,
dropped with a count.

**`Planted` is a year as text**; `0` (all but 213 rows) means unrecorded and
is null.  A year is published as 1 January.  Values that are not a
four-digit year (`2`, `201`: 3 rows) are nulled, and the build refuses past
1% of present values (or ten, whichever is more: only ~200 rows have one).  There is no diameter, height or crown column.
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
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

SERVICE = "https://maps.york.gov.uk/arcgis/rest/services/Public/EnvPlan/MapServer"
LAYERS = (
    FeatureLayer(f"{SERVICE}/3", timeout=120),  # Council trees
    FeatureLayer(f"{SERVICE}/18", timeout=120),  # Private trees
)
OUT_FIELDS = "OBJECTID,BOTANICAL,SPECIES,Planted"

ID_PREFIX = "york"

# `BOTANICAL` values (whitespace collapsed, case folded) that name a cultivar
# in the epithet's place, a common name, or a typo the shared parser cannot
# place; the value is the scientific name (with any cultivar quoted).
REWRITES = {
    "cherry": "Prunus",
    "leyland cypress": "Cupressus x leylandii",
    "liquid amber": "Liquidambar",
    "prunus kanzan": "Prunus 'Kanzan'",
    "prunus amanogawa": "Prunus 'Amanogawa'",
    "prunus umineko": "Prunus 'Umineko'",
    "prunus pandora": "Prunus 'Pandora'",
    "prunus autumnalis rosea": "Prunus x subhirtella 'Autumnalis Rosea'",
    "prunus lauraceae": "Prunus laurocerasus",
    # `SPECIES` says Cockspur Thorn: Crataegus x prunifolia, whose accepted
    # name is Crataegus persimilis.
    "prunus prunifolia": "Crataegus persimilis 'Prunifolia'",
    "prunus domestica insititia": "Prunus domestica",
    "malus profusion": "Malus 'Profusion'",
    "carpinus 'frans fontaine'": "Carpinus betulus 'Frans Fontaine'",
    "laburnum vossii": "Laburnum x watereri 'Vossii'",
    "betula jacquemontii": "Betula utilis 'Jacquemontii'",
    "acer drummondii": "Acer platanoides 'Drummondii'",
    "acer platanus 'crimson king'": "Acer platanoides 'Crimson King'",
    "fraxinus excel 'jaspidea aurea'": "Fraxinus excelsior 'Jaspidea'",
    "chamaecyparis pisifera 'bouleva": "Chamaecyparis pisifera 'Boulevard'",
    "ulmus sapporo ' autumn gold'": "Ulmus 'Sapporo Autumn Gold'",
    "salix x crysocoma": "Salix x sepulcralis 'Chrysocoma'",
    "sorbus commixta embly": "Sorbus commixta 'Embley'",
    "various": UNKNOWN_SPECIES,
}

# `Unclassified` in `BOTANICAL` defers to `SPECIES`; None drops the row.
UNCLASSIFIED = {
    "conifer": UNKNOWN_CONIFER_SPECIES,
    "dead trees": DEAD_SPECIES,
    "hedgerow": None,
}

_QUOTES = "'\"‘’“”"
_RANK_WORDS = frozenset({"x", "var", "var.", "subsp", "subsp.", "f", "f.", "sp", "sp.", "spp", "spp.", "species"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _title(cultivar: str) -> str | None:
    words = cultivar.strip(_QUOTES + " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or None


def split_species(botanical, common) -> tuple[str | None, str | None, bool]:
    """`(species, cultivar, is_tree)` from `BOTANICAL` (and `SPECIES` where it defers)."""
    text = clean(botanical)
    if text is None:
        return None, None, True
    key = text.casefold()
    if key == "unclassified":
        what = (clean(common) or "").casefold()
        if what in UNCLASSIFIED:
            name = UNCLASSIFIED[what]
            return name, None, name is not None
        return UNKNOWN_SPECIES, None, True
    text = REWRITES.get(key, text)
    quote = next((i for i, ch in enumerate(text) if ch in _QUOTES), None)
    if quote is not None:
        # Everything after the first quote, closed or not (the column cuts
        # `'emerald Queen` short).
        return clean(text[:quote]), _title(text[quote:]), True
    words = text.split()
    if len(words) >= 3 and words[1].lower() not in _RANK_WORDS and words[2][:1].isupper() and words[2].isalpha():
        # `Pyrus Calleryana Chanticleer`, `Betula Pendula Youngii`.
        return " ".join(words[:2]), _title(" ".join(words[2:])), True
    return text, None, True


_YEAR = re.compile(r"^\d{4}$")

# Unreadable `Planted` values tolerated before the 1% rule takes over.
UNREADABLE_FLOOR = 10


class PlantedYears:
    """`Planted` to a date, counting present values that are not a year."""

    def __init__(self) -> None:
        self.present = 0
        self.unreadable: dict[str, int] = {}

    def __call__(self, value) -> date | None:
        text = clean(value)
        if text is None or text == "0":
            return None
        self.present += 1
        if not _YEAR.match(text):
            self.unreadable[text] = self.unreadable.get(text, 0) + 1
            return None
        return date(int(text), 1, 1)

    def check(self) -> None:
        bad = sum(self.unreadable.values())
        if bad:
            print(f"York: nulled {bad} unreadable Planted values {sorted(self.unreadable)}", file=sys.stderr)
        # 1% of the ~200 present years is two values, and three slips were
        # already there when the city was wired; a changed format would make
        # nearly every present value unreadable.
        if bad > max(UNREADABLE_FLOOR, 0.01 * self.present):
            raise RuntimeError(
                f"York: {bad} of {self.present} Planted values are not years "
                f"(e.g. {sorted(self.unreadable)[:5]}); the format changed"
            )


_years = PlantedYears()
_counts = {"no_geometry": 0, "not_a_tree": 0}


def iter_row_chunks():
    """One ArcGIS page (1,000 features) at a time, both layers, geometry in WGS84."""
    for layer in LAYERS:
        yield from iter_features(layer, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        if lat is None or lon is None:
            _counts["no_geometry"] += 1
            continue
        name, selection, is_tree = split_species(rec.get("BOTANICAL"), rec.get("SPECIES"))
        if not is_tree:
            _counts["not_a_tree"] += 1
            continue
        tree_id.append(coordinate_tree_id(ID_PREFIX, lon, lat))
        species.append(name)
        cultivar.append(selection)
        plant_date.append(_years(rec.get("Planted")))
        latitude.append(lat)
        longitude.append(lon)

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["GBYRK"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="York OpenData")
    if _counts["no_geometry"]:
        print(f"York: dropped {_counts['no_geometry']} rows with no position", file=sys.stderr)
    if _counts["not_a_tree"]:
        print(f"York: dropped {_counts['not_a_tree']} hedgerow rows", file=sys.stderr)
    _years.check()
    table = drop_stacked_coordinates(table, label="York OpenData", identical=("species",))
    table = validate_coordinates(table, city="York", city_code="GBYRK")
    table = enforce_tree_schema(table, city="York", data_source="YORK_OPENDATA")
    emit(table)
