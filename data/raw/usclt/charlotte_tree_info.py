#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Charlotte, NC's street and landscape tree inventory.

Source: "Landscape Management Trees" on the City of Charlotte's open data hub
(data.charlottenc.gov, also listed on Data.gov), served from the city's
on-prem ArcGIS Server as `ENG/LSM_Trees/MapServer/0`: the trees the city's
Landscape Management division maintains on streets, medians and city
property.  191,781 rows when the city was wired (2026-11-11); the Hub
catalogue's `modified` read 2026-10-16.  Paging and Esri's geometry live in
`shared.platforms.arcgis`.

**The schema is thin**: `OBJECTID`, `Tree_ID`, `Common`, `DBH`, `Active`,
`Location` (a street address, blank on 50,171 rows) and the point.  No
botanical name, no planting date, no condition, no field has a coded-value
domain (checked: the layer's `fields` carry none), and the layer publishes no
`editingInfo` and no edit-date column.

**`Active` is the status column**, and it reads 1 on every row: the layer
publishes only standing assets.  1 is kept; any other value raises, because a
second status is a schema change and guessing which side of the line it falls
on would publish the wrong trees.  The empty sites the layer does carry are
written into `Common` -- `STUMP` (366) and `Vacant Site Medium` (3) -- and the
shared rule drops them (`is_not_a_tree`, inside `enforce_tree_schema`).

**The id is `Tree_ID`, made unique where the publisher repeated it.**  There
is no `GlobalID`.  `Tree_ID` is the city's asset number (4,065 to
110,655,627), non-null on every row and unique on all but two values:
110590338 on 20 rows and 110590339 on 3, all of them DBH-1/DBH-2 plantings
along one street a few metres apart -- a batch of new trees that was given one
asset number, not a duplicated record.  Those rows take `clt-{Tree_ID}-{OBJECTID}`
and every other row `clt-{Tree_ID}`.  The repeated values are read up front
with one `groupBy ... having` statistics query, so the rule does not depend
on which page a row arrives in.  `OBJECTID` alone was the alternative and is
worse: it runs to 2.3 million over 191k rows, i.e. the table is reloaded and
its OIDs reassigned, whereas `Tree_ID` survives a reload.

**The species is a common name only**, in two spellings of one vocabulary:
`MAPLE: RED` (inverted on a colon) and `MAPLE RED` (inverted on nothing), with
a long tail of natural-order and mixed-case entries (`Willow Oak`, `Okame
Cherry`, `Privet, glossy`).  `charlotte_name` turns a colon into the comma
`shared.species.english` already inverts on; a value with no separator is
tried as written, then with its first word moved to the end (`OAK NORTHERN
RED` -> `NORTHERN RED OAK`), then its last word to the front (`MOUNTAIN ASH
EUROPEAN`) -- the first reading the table resolves wins, and no value in the
layer resolves to two different taxa under two readings.  `SPP` (`CRAPEMYRTLE:
SPP`, 31,568 trees) is a genus-level identification, so the answer is cut to
its genus even where the table resolves the bare common name to a species.
Two keys are refused here: bare `Cedar` (the shared table's Ontario reading,
*Thuja occidentalis*, is not what "cedar" means in the Carolinas, where it is
usually eastern redcedar; the layer spells that one out) and the generic
`Broadleaf Deciduous/Evergreen ...` forms, which are `Unknown`.  `PALM SPP`
is the `Palm` sentinel and `UNKNOWN SHRUB` the `Shrub` one.

`tree_name` is the un-inverted reading that resolved.  A name that resolved
under no reading is published as written only when it is not in the
all-capitals inverted convention; `OAK TURKEY` publishes no `tree_name`
rather than "Oak turkey".

A quoted selection (`CRAPEMYRTLE 'CAROLINA BEAUTY'`, `GINKGO 'LAKEVIEW'`) is
the `cultivar`, title-cased from the source's capitals; an unterminated quote
(`CHOKECHERRY: COMMON 'SHUB`) is not a cultivar.  `DBH` is inches;
`enforce_tree_schema` nulls the zeros and the
values over 200 (182 rows, 999 among them).  There is no
planting date, so `plant_date` is an all-null `date32`.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import common_name_key, species_from_common_name

LAYER = FeatureLayer(
    "https://gis.charlottenc.gov/arcgis/rest/services/ENG/LSM_Trees/MapServer/0"
)
OUT_FIELDS = "OBJECTID,Tree_ID,Common,DBH,Active"

ACTIVE = 1

# Keys whose shared-table answer is wrong for Charlotte, or that name a
# growth form rather than a taxon.  `None` publishes `Unknown`.
LOCAL_KEYS: dict[str, str | None] = {
    "cedar": None,
    "palm": "Palm",
    "unknown shrub": "Shrub",
}
_GENERIC_FORM = re.compile(r"^broadleaf (deciduous|evergreen)\b")
_SPP = re.compile(r"\bspp?\.?$", re.IGNORECASE)
_CULTIVAR = re.compile(r"'([^']+)'")

# Refuse to publish when more than this share of DBH values will not parse.
MAX_DBH_PARSE_FAILURE = 0.01


def check_active(value) -> None:
    """Raise on an `Active` value the ingest has not seen."""
    if value != ACTIVE:
        raise RuntimeError(
            f"Charlotte Active {value!r} is new; decide whether it is a tree"
        )


def repeated_tree_ids() -> frozenset[int]:
    """The `Tree_ID` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "1=1",
            "groupByFieldsForStatistics": "Tree_ID",
            "outStatistics": json.dumps(
                [
                    {
                        "statisticType": "count",
                        "onStatisticField": "OBJECTID",
                        "outStatisticFieldName": "n",
                    }
                ]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload:
        raise RuntimeError(f"Tree_ID duplicate query gave no features: {payload!r}")
    return frozenset(
        int(f["attributes"]["Tree_ID"])
        for f in payload["features"]
        if f.get("attributes", {}).get("Tree_ID") is not None
    )


def readings(text: str) -> list[str]:
    """The orders a Charlotte common name may be read in, most literal first."""
    if ":" in text:
        head, _, tail = text.partition(":")
        return [f"{head.strip()}, {tail.strip()}"]
    if "," in text:
        return [text]
    words = text.split(" ")
    out = [text]
    if len(words) > 1:
        out.append(" ".join(words[1:] + words[:1]))
    if len(words) > 2:
        out.append(" ".join(words[-1:] + words[:-1]))
    return out


def charlotte_name(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """`(species, tree_name, cultivar)` for one `Common` value."""
    text = " ".join((raw or "").split())
    if not any(ch.isalpha() for ch in text):
        return None, None, None
    match = _CULTIVAR.search(text)
    cultivar = None
    if match:
        inner = " ".join(match.group(1).split())
        cultivar = inner.title() if inner.isupper() else inner
        text = " ".join((text[: match.start()] + text[match.end() :]).split())

    # `HEAD: SPP` / `HEAD SPP`: a genus-level identification.
    genus_only = False
    bare = text.replace(":", " ").split()
    if len(bare) > 1 and _SPP.fullmatch(bare[-1]):
        genus_only = True
        text = " ".join(bare[:-1])

    key = common_name_key(text)
    if key in LOCAL_KEYS:
        return LOCAL_KEYS[key], normalize_tree_name(text), cultivar
    if _GENERIC_FORM.match(key):
        return None, normalize_tree_name(text), cultivar

    for reading in readings(text):
        species = species_from_common_name(reading)
        if species is not None:
            if genus_only and " " in species:
                species = species.split(" ")[0]
            return species, normalize_tree_name(reading), cultivar

    inverted_convention = (
        text.isupper() and " " in text and ":" not in raw and "," not in text
    )
    name = None if inverted_convention else normalize_tree_name(readings(text)[0])
    return None, name, cultivar


def iter_row_chunks():
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


class Transformer:
    def __init__(self, repeated: frozenset[int]):
        self.repeated = repeated
        self.dbh_seen = 0
        self.dbh_failed = 0

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str | None] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        tree_name: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            check_active(rec.get("Active"))
            raw_id = rec.get("Tree_ID")
            if raw_id is None:
                tree_id.append(None)
            elif int(raw_id) in self.repeated:
                tree_id.append(f"clt-{int(raw_id)}-{rec['OBJECTID']}")
            else:
                tree_id.append(f"clt-{int(raw_id)}")
            sp, name, cv = charlotte_name(rec.get("Common"))
            species.append(sp)
            tree_name.append(name)
            cultivar.append(cv)
            plant_date.append(None)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(self.parse_dbh(rec.get("DBH")))

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USCLT"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "tree_name": pa.array(tree_name, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def parse_dbh(self, value) -> float | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        self.dbh_seen += 1
        try:
            return float(value)
        except (TypeError, ValueError):
            self.dbh_failed += 1
            return None

    def check_parse_failures(self) -> None:
        if self.dbh_seen and self.dbh_failed / self.dbh_seen > MAX_DBH_PARSE_FAILURE:
            raise RuntimeError(
                f"Charlotte DBH: {self.dbh_failed} of {self.dbh_seen} values "
                "did not parse as numbers; the field's format has changed"
            )


if __name__ == "__main__":
    transform = Transformer(repeated_tree_ids())
    table = stream_to_table(iter_row_chunks(), transform, label="Charlotte OpenData")
    transform.check_parse_failures()
    missing_id = table.column("tree_id").null_count
    if missing_id:
        print(f"Charlotte: dropping {missing_id} row(s) with no Tree_ID", file=sys.stderr)
        table = table.filter(table.column("tree_id").is_valid())
    table = validate_coordinates(table, city="Charlotte", city_code="USCLT")
    table = enforce_tree_schema(
        table, city="Charlotte", data_source="CHARLOTTE_OPENDATA"
    )
    emit(table)
