#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Canberra's public trees: the ACT Government's tree asset register.

**Source**: "ACTGOV Tree Assets" (ArcGIS Online item
d401a7266ba74dc18c1902ae6e71fe90, owner `cityoperationsactgov`,
`ACTGOV_Tree_Assets/FeatureServer/0`), "Trees in the Australian Capital
Territory ... owned or managed by City Services, City and Environment
Directorate (CED) and Parks and Conservation Service".  Trees on Commonwealth
(National Capital Authority) land, other directorates' land and private land
are not in it.  The same layer is mirrored monthly on data.act.gov.au
(`9qch-rvqr`); this reads the ArcGIS layer, which is the master and is edited
daily.  Licence on the item: "Creative Commons By Attribution 4.0 (Australian
Capital Territory)", with the actmapi data terms
(https://www.actmapi.act.gov.au/terms-and-conditions).

786,310 rows on 2026-11-16, every one `ASSET_TYPE` = `TREE`: street (454,592),
open space (295,698), median (21,653), laneway (12,531) and reserve (1,836)
trees.  The layer carries no removed or planned assets: `OPERATIONAL_STATE` is
`ACTIVE` (785,662), null (388) or `IN CONSOLIDATION` (260) -- a tree a
developer has planted and is still maintaining before handover to the
Territory, which is a standing tree and is kept.  A non-`TREE` asset type
would be a new kind of row and is refused rather than guessed at.

**Id: `GlobalID`** (`cbr-{globalid}`), unique and non-null over the whole
layer (`groupBy ... having COUNT > 1` returns no rows).  The asset system's own
`ASSET_ID` (`TREE46`) is also unique and non-null and is the fallback should
the GlobalIDs ever be regenerated; 200 sampled rows had the same GlobalID and
OBJECTID on the data.act.gov.au copy of 2026-10-20 as on the live layer, and
the OBJECTIDs (841,491 to 2,780,815) are carried over from the directorate's
own geodatabase rather than assigned by the hosted layer.

**Species is `BOTANICAL_NAME`**, a scientific name throughout (the `GENUS` and
`SPECIES` columns are the same name split; `MIS25_OP` is a species code from
the municipal infrastructure standard; the layer has no common-name column).
225,573 rows (29%) have no name at all and publish as `Unknown`: the register
records the asset but no identification.  Cultivars are quoted (`Pyrus
calleryana 'Chanticleer'`) and are split here rather than by the schema,
because several carry a possessive (`'King's Park Special'`, `'Paul's
Scarlet'`) that the shared quote rule would cut, and some carry a trade name
after the quote (`'Jeffersred' Autumn Blaze`, `'Cimmzam' (Cimmaron)`), which is
dropped.  A cross between two named species (`Lagerstroemia x L. fauriei`,
`Acer truncatum x platanoides`) is a hybrid of no one species and publishes as
the genus.  A few names were stored double-encoded (`'AureaÂ Pendula'`) and
are repaired.  `Standard`, `Male Form` and `Grey Form` describe how a tree was
grown or which sex it is, not a selection, and are not cultivars.

**No diameter, planting date, height or crown spread**: the layer publishes
none.  `HERITAGE_LISTED` marks a tree inside a heritage-registered place and
`REGISTERED_TREE` one on the ACT Tree Register (Tree Protection Act 2005);
neither is mapped (see EXTENDING "Notable registers").
"""

import re
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/E5n4f1VY84i0xSjy/arcgis/rest/services/"
    "ACTGOV_Tree_Assets/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,ASSET_TYPE,BOTANICAL_NAME"
TREE_ASSET_TYPE = "TREE"

# Raw names (after the encoding repair) the rules below would misread.
NAME_FIXES = {
    # `Prunus 'Elvins'`, a flowering plum cultivar written as an epithet.
    "Prunus elvins": "Prunus 'Elvins'",
    # The hawthorn selection 'Smithiana', which the register also writes
    # `Crataegus 'Smithiana'`; there is no species of that name.
    "Crataegus smithiana": "Crataegus 'Smithiana'",
    # 'All-in-One' is an almond; there is no Pyrus amygdalus.
    "Pyrus amygdalus 'All In One'": "Prunus dulcis 'All In One'",
}

# Quoted words that are a growing form or a sex, not a cultivar.
NOT_CULTIVARS = frozenset({"Standard", "Male Form", "Grey Form"})

QUOTES = "'\"‘’“”"
PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
# `Genus epithet x ...` or `Genus x G. epithet`: a cross between two named
# species, not a nothospecies like `Acer x freemanii`.
CROSS = re.compile(r"^([A-Z][a-z]+)\s+(?:[a-z]+\s+[xX×]\s+\S|[xX×]\s+[A-Z][a-z]?\.)")


def repair_encoding(text: str) -> str:
    """`AureaÂ\\xa0Pendula` -> `Aurea Pendula`: UTF-8 that was read as cp1252."""
    if "Ã" in text or "Â" in text or "â€" in text:
        try:
            text = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return " ".join(text.replace("\xa0", " ").split())


def _is_possessive(s: str, i: int) -> bool:
    """`King's`: an apostrophe between a letter and a closing `s`."""
    return (
        s[i] in "'’"
        and i > 0
        and s[i - 1].isalpha()
        and s[i + 1 : i + 2] == "s"
        and (i + 2 == len(s) or not s[i + 2].isalpha())
    )


@cache
def parse_name(raw: str | None) -> tuple[str | None, str | None]:
    """`(species text, cultivar)` from one `BOTANICAL_NAME`.

    The species text still goes through `enforce_tree_schema`'s sanitiser,
    which truncates `subsp.`/`var.` and folds synonyms; this only separates
    what the sanitiser would misread.
    """
    if raw is None:
        return None, None
    s = repair_encoding(raw)
    if not s:
        return None, None
    s = NAME_FIXES.get(s, s)

    cultivar = None
    open_at = next((i for i, ch in enumerate(s) if ch in QUOTES), -1)
    if open_at == -1:
        head = s
    else:
        head = s[:open_at]
        rest = s[open_at + 1 :]
        close_at = next(
            (i for i, ch in enumerate(rest) if ch in QUOTES and not _is_possessive(rest, i)),
            -1,
        )
        cultivar = rest[:close_at] if close_at != -1 else rest
        cultivar = " ".join(cultivar.split()) or None
        if cultivar in NOT_CULTIVARS:
            cultivar = None
    head = " ".join(PARENTHETICAL.sub(" ", head).split())
    cross = CROSS.match(head)
    if cross:
        head = cross.group(1)
    return head or None, cultivar


class Canberra:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        if rec.get("ASSET_TYPE") != TREE_ASSET_TYPE:
            raise RuntimeError(
                f"Canberra: ASSET_TYPE {rec.get('ASSET_TYPE')!r} is new; decide "
                f"whether it is a tree: {rec!r}"
            )
        if not str(rec.get("GlobalID") or "").strip("{} "):
            self.dropped["no GlobalID"] += 1
            return False
        return True

    def transform(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        for feature in features:
            rec = feature.get("attributes") or {}
            name, cv = parse_name(rec.get("BOTANICAL_NAME"))
            lat, lon = esri_point(feature.get("geometry"))
            tree_id.append(f"cbr-{str(rec['GlobalID']).strip('{} ').lower()}")
            species.append(name)
            cultivar.append(cv)
            latitude.append(lat)
            longitude.append(lon)
        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["AUCBR"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Canberra: dropped {n:,} rows: {reason}", file=sys.stderr)


if __name__ == "__main__":
    canberra = Canberra()
    table = stream_to_table(
        iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326),
        canberra.transform,
        keep=canberra.keep,
        label="Canberra ACTGOV Tree Assets",
    )
    canberra.report()
    table = validate_coordinates(table, city="Canberra", city_code="AUCBR")
    table = enforce_tree_schema(table, city="Canberra", data_source="CANBERRA_OPENDATA")
    emit(table)
