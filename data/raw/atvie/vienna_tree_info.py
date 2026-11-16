#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Vienna's tree register (Baumkataster), from the city's WFS.

Source: `ogdwien:BAUMKATOGD` on `data.wien.gv.at/daten/geo`, Stadt Wien's
GeoServer, catalogued on data.gv.at as "Baumkataster bzw. Bäume Standorte
Wien" (dataset c91a4635-8b7d-43fe-9b27-d95dec8392a7) under CC BY 4.0,
attribution "Datenquelle: Stadt Wien - https://data.wien.gv.at".  The register
is kept by MA 42 (Wiener Stadtgärten): every street tree, and the park and
woodland trees surveyed so far -- **233,411 rows** when this was written.

**Keyed on `BAUM_ID`, not `OBJECTID`.**  Both are unique and non-null over the
whole layer, but `OBJECTID` is the load sequence, not the tree: the layer's
ids ran 836,360,976 to 836,600,581 -- 233k rows in a 240k-wide window that
far up the sequence -- which is a table reloaded wholesale and renumbered each
time.  `BAUM_ID` is the register's own tree number and is what this publishes.

**Species is `GATTUNG_ART`, a Latin name with the German common name in
parentheses** (`Acer platanoides 'Globosum' (Kugelspitzahorn)`).  The
parenthetical is stripped here; a quoted cultivar is left in for
`enforce_tree_schema` to move to `cultivar`.  Three habits of the register's:

* `Tilia spec.` is a tree identified to genus; the shared hygiene reads `spec.`
  as the rank marker it is and truncates to the genus.
* `Malus hybride 'John Downie'` writes the hybrid mark before a cultivar of
  the genus; it is dropped (`Malus 'John Downie'`), as Copenhagen's `hybr.`.
* Infraspecific names without a rank (`Pinus nigra nigra`, `Acer cappadocicum
  lobelii`) are truncated to the species by the shared hygiene.

`Jungbaum wird gepflanzt` ("young tree being planted", 3,722 rows, nearly
all with no planting year, girth or height) is a planting the city has
scheduled, not a tree at the point yet, and is dropped with a count.
`nicht bekannt`, `unbekannt`, `Laubbaum` (broadleaf), `Obstbaum` (fruit
tree) and `Baumgruppe` (a group) are trees nobody named and publish as
`Unknown`.  The layer has no status column, and no removed-tree records.

**`STAMMUMFANG` is trunk circumference in whole centimetres** (median 83 cm,
`STAMMUMFANG_TXT` reads `83 cm`), measured at 1 m as Austrian practice is --
close enough to breast height to publish as one.  0 is "nicht bekannt" and is
null.  A few hundred-metre typos (34,593 cm) are nulled by the shared 200-inch
guard.

**`PFLANZJAHR` is the planting year**, 0 ("nicht definiert") on 65,343 rows,
which is null; published as January 1 of the year.

**`KRONENDURCHMESSER` is a crown-diameter class**, coded 0-8 with the bands
in `KRONENDURCHMESSER_TXT` (`0-3 m`, `4-6 m`, ... `19-21 m`, `>21 m`).  It is
read from the code, as the class midpoint, into `crown_width_m` -- Ballarat's
rule for a banded crown spread: wrong by up to a metre and a half per tree and
right in aggregate, which is what the map's crown sizing needs.  The open top
class takes 22.5 m.  0 ("nicht bekannt") is null; an unknown code counts as
unreadable and more than 1% unreadable refuses the build.  The code is read
rather than the text because the two disagree on a few dozen rows (`4` with
`>21 m`), and the code is the column the register types.  `BAUMHOEHE` is a
height class and is not published (height comes from LIDAR).

`BEZIRK` is the district number (1-23; null on 595 rows of the waterworks'
and Danube authority's estates) and rides the shared `borough` column as the
district's name (`Innere Stadt`, `Leopoldstadt`, ...).  54 rows lie in Purkersdorf, outside the city, on the waterworks'
(MA 31) estate there; `validate_coordinates` drops them with the city box.
"""

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
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.wfs import WfsLayer, iter_wfs_features

# Kept in step with `vienna_update_time.py`.
LAYER = WfsLayer("https://data.wien.gv.at/daten/geo", "ogdwien:BAUMKATOGD", timeout=300)

PROPERTIES = [
    "BAUM_ID", "BEZIRK", "GATTUNG_ART", "PFLANZJAHR", "STAMMUMFANG",
    "KRONENDURCHMESSER", "SHAPE",
]

# `GATTUNG_ART` values that record a scheduled planting rather than a tree.
NOT_A_TREE = frozenset({"jungbaum wird gepflanzt"})

# Trees the register did not name; published as Unknown.
UNNAMED = frozenset({"nicht bekannt", "unbekannt", "laubbaum", "obstbaum", "baumgruppe"})

# KRONENDURCHMESSER code -> class midpoint in metres (the `_TXT` band in the
# comment).  0 is "nicht bekannt".
CROWN_CLASS_M: dict[int, float] = {
    1: 1.5,    # 0-3 m
    2: 5.0,    # 4-6 m
    3: 8.0,    # 7-9 m
    4: 11.0,   # 10-12 m
    5: 14.0,   # 13-15 m
    6: 17.0,   # 16-18 m
    7: 20.0,   # 19-21 m
    8: 22.5,   # >21 m
}
MAX_PARSE_FAILURES = 0.01

# Vienna's 23 Gemeindebezirke, by number.
DISTRICTS: dict[int, str] = {
    1: "Innere Stadt", 2: "Leopoldstadt", 3: "Landstraße", 4: "Wieden",
    5: "Margareten", 6: "Mariahilf", 7: "Neubau", 8: "Josefstadt",
    9: "Alsergrund", 10: "Favoriten", 11: "Simmering", 12: "Meidling",
    13: "Hietzing", 14: "Penzing", 15: "Rudolfsheim-Fünfhaus", 16: "Ottakring",
    17: "Hernals", 18: "Währing", 19: "Döbling", 20: "Brigittenau",
    21: "Floridsdorf", 22: "Donaustadt", 23: "Liesing",
}

_COMMON_NAME = re.compile(r"\s*\([^()]*\)\s*$")
_HYBRID_BEFORE_SELECTION = re.compile(r"\bhybride\s+(?=['‘’])")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(value) -> str | None:
    """The Latin part of `GATTUNG_ART`, or None for an unnamed tree."""
    text = clean(value)
    if text is None:
        return None
    text = _COMMON_NAME.sub("", text).strip()
    if not text or text.lower() in UNNAMED:
        return None
    return _HYBRID_BEFORE_SELECTION.sub("", text)


class Counts:
    def __init__(self) -> None:
        self.read = 0
        self.no_id = 0
        self.no_point = 0
        self.not_a_tree = 0
        self.duplicate = 0
        self.crown_unreadable: dict[object, int] = {}
        self.girth_unreadable: dict[object, int] = {}


def _int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def make_transform(seen: set[str], counts: Counts):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        borough: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            counts.read += 1
            props = feature.get("properties") or {}
            baum_id = _int(props.get("BAUM_ID"))
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            if baum_id is None:
                counts.no_id += 1
                continue
            if not coords:
                counts.no_point += 1
                continue
            raw_species = clean(props.get("GATTUNG_ART"))
            if raw_species is not None and raw_species.lower() in NOT_A_TREE:
                counts.not_a_tree += 1
                continue
            key = f"vie-{baum_id}"
            if key in seen:
                counts.duplicate += 1
                continue
            seen.add(key)

            girth_raw = props.get("STAMMUMFANG")
            girth = _int(girth_raw)
            if girth is None and girth_raw is not None:
                counts.girth_unreadable[girth_raw] = counts.girth_unreadable.get(girth_raw, 0) + 1
            crown_raw = props.get("KRONENDURCHMESSER")
            crown_code = _int(crown_raw)
            crown_m = None
            if crown_code is None:
                if crown_raw is not None:
                    counts.crown_unreadable[crown_raw] = counts.crown_unreadable.get(crown_raw, 0) + 1
            elif crown_code != 0:
                crown_m = CROWN_CLASS_M.get(crown_code)
                if crown_m is None:
                    counts.crown_unreadable[crown_code] = counts.crown_unreadable.get(crown_code, 0) + 1
            district = _int(props.get("BEZIRK"))

            tree_id.append(key)
            species.append(parse_species(raw_species))
            borough.append(DISTRICTS.get(district) if district else None)
            plant_date.append(parse_plant_date_year(_int(props.get("PFLANZJAHR"))))
            longitude.append(float(coords[0]))
            latitude.append(float(coords[1]))
            dbh.append(circumference_cm_to_dbh_inches(girth) if girth and girth > 0 else None)
            crown.append(crown_m)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["ATVIE"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "borough": pa.array(borough, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    return transform


def check_parse_failures(counts: Counts) -> None:
    for name, failures in (("KRONENDURCHMESSER", counts.crown_unreadable),
                           ("STAMMUMFANG", counts.girth_unreadable)):
        bad = sum(failures.values())
        if not bad:
            continue
        share = bad / max(counts.read, 1)
        print(
            f"Vienna OpenData: {bad} unreadable `{name}` values ({share:.2%}): "
            f"{sorted(failures.items(), key=lambda kv: -kv[1])[:10]}",
            file=sys.stderr,
        )
        if share > MAX_PARSE_FAILURES:
            raise RuntimeError(
                f"Vienna `{name}`: {share:.2%} of rows unreadable; the register "
                "has changed its coding"
            )


def main() -> None:
    seen: set[str] = set()
    counts = Counts()
    table = stream_to_table(
        iter_wfs_features(LAYER, sort_by="BAUM_ID", properties=PROPERTIES, page_size=20000),
        make_transform(seen, counts),
        label="Vienna OpenData",
    )
    check_parse_failures(counts)
    print(
        f"Vienna OpenData: read {counts.read}; dropped {counts.not_a_tree} scheduled "
        f"planting(s) (`Jungbaum wird gepflanzt`), {counts.no_id} row(s) with no "
        f"BAUM_ID, {counts.no_point} with no point and {counts.duplicate} repeated "
        f"BAUM_ID(s), leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Vienna", city_code="ATVIE")
    table = enforce_tree_schema(table, city="Vienna", data_source="VIENNA_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
