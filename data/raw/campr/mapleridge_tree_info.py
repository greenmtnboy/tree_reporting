#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Maple Ridge's street-tree inventory, from the City of Maple Ridge.

Source: "Street Tree" (`DataCatalog/Environment/MapServer/5`) on the City's
own ArcGIS Server (geoservices.mapleridge.ca), catalogued on the City's Open
Government portal (opengov.mapleridge.ca, item
290505c676d64cf09989eca1320aedd3): "a listing of public trees on boulevards
in Maple Ridge ... Park trees and private trees are not included in the
inventory", 15,863 rows on 2026-11-22.  It is the City's only tree inventory:
no other DataCatalog service carries a tree layer (`Tree Canopy`, layer 9 of
the same service, is canopy polygons, and `RidgeViewData/1000TreesProject` is
a 119-row subset of these same street trees).

Licence: the item's licence field links the "Open Government Licence - Maple
Ridge" (https://opengov.mapleridge.ca/pages/open-government-licence), version
2.0, based on the Open Government Licence - British Columbia: a "worldwide,
royalty-free, perpetual, non-exclusive licence to use the Information,
including for commercial purposes" to "copy, modify, publish, translate,
adapt, distribute or otherwise use the Information", acknowledging the source
with "Contains information licensed under the Open Government Licence - Maple
Ridge" and, where possible, a link to the licence.

**The id is `GlobalID`** (`mpr-{guid}`): the layer's system GlobalID, non-null
and unique over all 15,863 rows (2026-11-22).  `OBJECTID` runs 2..47,694 with
gaps -- a versioned layer edited in place, not reloaded.  The City's own
`TreeID` is unique too, but null on 7 rows.  A row without a GlobalID is
dropped with a count.

**Not every row is a tree.**  `Status` is a domain (Existing, Future, Missing,
Removed, Developer, Planting Location) but the published layer carries only
`Existing`; anything else is dropped, and an unseen value refuses the build.
`Condition` (also a domain) records the site state the status does not:
`Stump Grinding` (186: a removed tree awaiting grinding -- the comments read
"Removed 2024") and `PossibleLocation` (160: a planting site, most with a
2026 planned species) are dropped; `Dead` (2) stays, as a tree.

**Species is `Genus` + `Species` + `Variety`**, separate Latin fields (the
`Type` field is their concatenation; `CommonName` is not mapped).  The
epithet field carries the hybrid mark (`× yedoensis`) and infraspecific ranks
(`triacanthos var. inermis`), which the schema folds.  A hybrid *formula*
(`truncatum × platanoides`, `nuttallii × florida`, `sargentii ×
subhirtella`, `acuminata × denudata`) is published at genus: the schema would
cut it to its first parent, which names the wrong species.  `Variety` holds
the quoted cultivar (`'Franksred'`) and goes to the `cultivar` column itself,
so a cultivar with an apostrophe (`'Inge's Ruby Vase'`) survives; a
`var.`/`ssp.` there is an infraspecific rank and is dropped.  A parse-failure
guard refuses more than 1% unreadable names.

**DBH is `TrunkDBH`, centimetres** (measured on 1,398 rows only: 2020s
plantings median 7, 1990s 30, the remnant conifers 135-180), converted to
inches; zero is "not measured".  **`YearPlanted`** becomes 1 January of that
year, except **1970**, which is a stamp, not a planting: 296 rows, against 33
for 1969 and none for 1971, nearly all of them in parks and the cemetery, and
they include the remnant native Douglas-firs, hemlocks and redcedars of
135-180 cm that no 1970 planting could have grown into.  2026 (1,612 rows) is
real: this season's development and replacement plantings.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://geoservices.mapleridge.ca/server/rest/services/"
    "DataCatalog/Environment/MapServer/5",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,Status,Condition,Genus,Species,Variety,TrunkDBH,YearPlanted"

STATUS_KEPT = "existing"
STATUS_DROPPED = frozenset({"future", "missing", "removed", "developer", "planting location"})
CONDITION_NOT_A_TREE = frozenset({"stump grinding", "possiblelocation", "removal"})

# The planting year the inventory stamps on trees it did not see planted.
PLACEHOLDER_PLANT_YEAR = 1970

# Refuse to publish when more than this share of named rows parse to no taxon.
MAX_UNPARSED_SHARE = 0.01

HYBRID_MARK = "×"
INFRASPECIFIC = re.compile(r"^(var|ssp|subsp|f)\.?\s", re.IGNORECASE)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    return text or None


def is_word(token: str) -> bool:
    return token.isalpha() or (token.replace("-", "").isalpha() and "-" in token)


def scientific_name(genus, epithet) -> tuple[str | None, bool]:
    """(species for the schema, whether the value failed to parse)."""
    g = clean(genus)
    e = clean(epithet)
    if g is None:
        return None, e is not None
    if not g.isalpha() or not g[0].isupper():
        return None, True
    if e is None:
        return g, False
    tokens = e.replace(HYBRID_MARK, f" {HYBRID_MARK} ").replace(" x ", f" {HYBRID_MARK} ").split()
    marks = [i for i, t in enumerate(tokens) if t == HYBRID_MARK]
    # `× yedoensis` is a nothospecies; `truncatum × platanoides` is a formula.
    if any(i > 0 for i in marks):
        return g, False
    words = [t for t in tokens if t != HYBRID_MARK]
    if not words or not all(is_word(t.rstrip(".")) for t in words):
        return None, True
    return f"{g} {' '.join(tokens)}", False


def cultivar_of(variety) -> str | None:
    text = clean(variety)
    if text is None or INFRASPECIFIC.match(text):
        return None
    text = text.strip("'\"").strip()
    return text or None


class MapleRidge:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.named = 0
        self.placeholder_years = 0
        self.genus_only_formulae = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = (clean(rec.get("Status")) or "").lower()
        if status in STATUS_DROPPED:
            self.dropped[f"Status {status}"] += 1
            return False
        if status != STATUS_KEPT:
            raise RuntimeError(f"Maple Ridge: unseen Status value {rec.get('Status')!r}")
        condition = (clean(rec.get("Condition")) or "").lower()
        if condition in CONDITION_NOT_A_TREE:
            self.dropped[f"Condition {condition}"] += 1
            return False
        if clean(rec.get("GlobalID")) is None:
            self.dropped["no GlobalID"] += 1
            return False
        return True

    def transform(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            guid = clean(rec["GlobalID"]).strip("{}").lower()
            tree_id.append(f"mpr-{guid}")
            name, failed = scientific_name(rec.get("Genus"), rec.get("Species"))
            raw = " ".join(x for x in (clean(rec.get("Genus")), clean(rec.get("Species"))) if x)
            if raw:
                self.named += 1
            if failed:
                self.unparsed[raw] += 1
            if name is not None and clean(rec.get("Species")) and name == clean(rec.get("Genus")):
                self.genus_only_formulae += 1
            species.append(name)
            cultivar.append(cultivar_of(rec.get("Variety")) if name is not None else None)
            year = rec.get("YearPlanted")
            if year == PLACEHOLDER_PLANT_YEAR:
                self.placeholder_years += 1
                year = None
            plant_date.append(parse_plant_date_year(year))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            cm = rec.get("TrunkDBH")
            dbh.append(cm_to_inches(cm) if cm is not None and cm > 0 else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CAMPR"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        print(
            "Maple Ridge: dropped "
            + (", ".join(f"{v} {k!r}" for k, v in sorted(self.dropped.items())) or "nothing")
            + f"; nulled {self.placeholder_years} placeholder YearPlanted {PLACEHOLDER_PLANT_YEAR}"
            + f"; {self.genus_only_formulae} hybrid formula(e) published at genus",
            file=sys.stderr,
        )
        failed = sum(self.unparsed.values())
        if failed:
            print(
                f"Maple Ridge: {failed} row(s) with no readable scientific name: "
                + ", ".join(f"{k!r} x{v}" for k, v in sorted(self.unparsed.items())),
                file=sys.stderr,
            )
        if self.named and failed / self.named > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Maple Ridge: {failed} of {self.named} Genus/Species values unreadable "
                f"(> {MAX_UNPARSED_SHARE:.0%}); has the layer changed shape?"
            )


if __name__ == "__main__":
    city = MapleRidge()
    pages = iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )
    table = stream_to_table(
        pages, city.transform, keep=city.keep, label="Maple Ridge OpenData"
    )
    city.report()
    table = validate_coordinates(table, city="Maple Ridge", city_code="CAMPR")
    table = enforce_tree_schema(
        table, city="Maple Ridge", data_source="MAPLERIDGE_OPENDATA"
    )
    emit(table)
