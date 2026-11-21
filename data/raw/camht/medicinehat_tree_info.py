#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Medicine Hat's City tree inventory.

Source: "Park Tree Inventory" on the City of Medicine Hat's ArcGIS Online
organisation (item af05aa132ad24367a7d32d46af50bb38, owner MHArcGISOnline,
listed on opendata.medicinehat.ca): "trees located throughout the City of
Medicine Hat ... species, size, condition, and unique inventory identifier",
28,588 rows on 2026-11-21, edited live.  It is the City's only tree layer --
there is no separate street-tree layer in the organisation.

Licence: the item's licence links the City's "Disclaimer and Licence"
(https://www.medicinehat.ca/media/icmn0zwt/cmh_open-data-license.pdf), a
worldwide, royalty-free, perpetual, non-exclusive licence to "copy, modify,
publish, translate, adapt, distribute or otherwise use the GIS Data ...
provided that you acknowledge the source of the information ... and, where
possible, provide a link to this license."

**Not every row is a tree in the ground.**  `TREE_ALIVE_DEAD` (a coded-value
domain) holds `Alive - Go to DBH` (24,463), `Stump Removed` (1,513), `Stump -
Go to Condition Rating` (1,287), `Dead Standing - Go to Condition Rating`
(337) and, outside the domain, `Proposed Tree Planning` (966: planned sites,
no measurements and no `TREE_UID`).  Only the living trees are kept, plus the
22 rows with no status at all (not flagged as anything else); every other
status is dropped with a logged count, and an unseen status refuses the build.

**The id is `TREE_UID`**, the City's inventory tag (a 4-5 digit number,
`mht-{TREE_UID}`).  There is no GlobalID, and `OBJECTID` is a dense 1..N.
`TREE_UID` is null only on the planned sites and is repeated on 24 values
over the whole layer (53 kept rows, 2026-11-21): clerical re-uses of one tag
on neighbouring trees.  Repeated values are read up front with one `groupBy
... having` statistics query and those rows take `mht-{TREE_UID}-{OBJECTID}`
(Baltimore's and Columbus's rule).  The tags run in OBJECTID order on 91% of
consecutive rows: the layer is appended to and edited in place, not reloaded.

**Species is the `SPECIES` domain value**, `Scientific - Common` (`Picea
pungens 'Glauca' - Colorado Blue Spruce`); the part before ` - ` is the
taxon, the common name is not mapped.  Three values put the common name
first (`Starlite Crabapple - Malus x 'Jeflite'`) and are read from
`SPECIES_OVERRIDES`, as is `Populus x 'Northwest'` (Populus x jackii); a
bare hybrid mark before any other cultivar leaves the genus.  Curly quotes and a space inside a cultivar's quotes
(`Tilia x flavescens ' Dropmore'`) are normalised.  `Other - use Comment
Final field` (560; the comment field is not published) is the Unknown
sentinel.  A parse-failure guard refuses more than 1% unreadable values.

**DBH is `DBH_CM`, centimetres** (living trees: median 32, 95th percentile
60), converted to inches.  **`CANOPY_M` is the crown width in metres**
(median 7 for a 30-60 cm trunk, 1 for a sapling).  `HEIGHT_M` has no
canonical column.  No planting date is recorded.

Geometry is stored in Web Mercator and requested as WGS84 (`outSR=4326`).
"""

import json
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services3.arcgis.com/2gQ2Dv4Fb016qFVU/arcgis/rest/services/"
    "Park_Tree_Inventory/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,TREE_UID,SPECIES,TREE_ALIVE_DEAD,DBH_CM,CANOPY_M"

ALIVE = "alive - go to dbh"
NOT_A_TREE_STATUS = frozenset(
    {
        "stump removed",
        "stump - go to condition rating",
        "dead standing - go to condition rating",
        "proposed tree planning",
    }
)

# `SPECIES` values that are not `Scientific - Common`: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "starlite crabapple - malus x 'jeflite'": ("Malus", "Jeflite"),
    "gladiator crabapple - malus 'durleo'": ("Malus", "DurLeo"),
    "hot wings tatarian maple - acer tataricum 'garann'": ("Acer tataricum", "GarAnn"),
    "other - use comment final field": (UNKNOWN_SPECIES, None),
    # The domain writes a bare hybrid mark; the Northwest poplar is the
    # balsam x cottonwood cross Populus x jackii (Red Deer's list names it).
    "populus x 'northwest' - northwest poplar": ("Populus x jackii", "Northwest"),
}

# Refuse to publish when more than this share of named rows parse to no taxon.
MAX_UNPARSED_SHARE = 0.01

CM_PER_INCH = 2.54


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    return text or None


def parse_species(raw) -> tuple[str | None, bool]:
    """(species string for the schema, whether the value failed to parse)."""
    text = clean(raw)
    if text is None:
        return None, False
    override = SPECIES_OVERRIDES.get(text.lower())
    if override is not None:
        species, cultivar = override
        return (f"{species} '{cultivar}'" if cultivar else species), False
    sci = text.split(" - ", 1)[0].strip()
    head, _, rest = sci.partition("'")
    tokens = head.split()
    if not tokens or not tokens[0].isalpha() or not tokens[0][0].isupper():
        return None, True
    if any(not (t.isalpha() and (i == 0 or t.islower())) for i, t in enumerate(tokens)):
        return None, True
    species = " ".join(tokens)
    if rest:
        cultivar = rest.split("'", 1)[0].strip()
        if not cultivar:
            return None, True
        # A bare hybrid mark before a cultivar (`Malus x 'Thunderchild'`).
        if tokens[-1] == "x":
            species = " ".join(tokens[:-1])
        return f"{species} '{cultivar}'", False
    return species, False


@cache
def repeated_ids() -> frozenset[str]:
    """The `TREE_UID` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "TREE_UID IS NOT NULL",
            "groupByFieldsForStatistics": "TREE_UID",
            "outStatistics": json.dumps(
                [{"statisticType": "count", "onStatisticField": "OBJECTID",
                  "outStatisticFieldName": "n"}]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload or payload.get("exceededTransferLimit"):
        raise RuntimeError(
            f"Medicine Hat TREE_UID duplicate query was incomplete: {payload!r}"[:500]
        )
    return frozenset(str(f["attributes"]["TREE_UID"]).strip() for f in payload["features"])


class MedicineHat:
    def __init__(self) -> None:
        self.repeated = repeated_ids()
        self.dropped: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.named = 0
        self.suffixed = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = (clean(rec.get("TREE_ALIVE_DEAD")) or "").lower()
        if status in NOT_A_TREE_STATUS:
            self.dropped[status] += 1
            return False
        if status not in ("", ALIVE):
            raise RuntimeError(
                f"Medicine Hat: unseen TREE_ALIVE_DEAD value {rec.get('TREE_ALIVE_DEAD')!r}"
            )
        if clean(rec.get("TREE_UID")) is None:
            self.dropped["no TREE_UID"] += 1
            return False
        return True

    def transform(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            uid = clean(rec["TREE_UID"])
            if uid in self.repeated:
                self.suffixed += 1
                tree_id.append(f"mht-{uid}-{int(rec['OBJECTID'])}")
            else:
                tree_id.append(f"mht-{uid}")
            name, failed = parse_species(rec.get("SPECIES"))
            if clean(rec.get("SPECIES")) is not None:
                self.named += 1
            if failed:
                self.unparsed[clean(rec.get("SPECIES")) or ""] += 1
            species.append(name)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            cm = rec.get("DBH_CM")
            dbh.append(float(cm) / CM_PER_INCH if cm is not None else None)
            width = rec.get("CANOPY_M")
            crown.append(float(width) if width is not None and width > 0 else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CAMHT"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    def report(self) -> None:
        print(
            "Medicine Hat: dropped "
            + ", ".join(f"{v} {k!r}" for k, v in sorted(self.dropped.items()))
            + f"; {self.suffixed} row(s) on a repeated TREE_UID keyed with OBJECTID",
            file=sys.stderr,
        )
        failed = sum(self.unparsed.values())
        if failed:
            print(
                f"Medicine Hat: {failed} row(s) with no readable scientific name: "
                + ", ".join(f"{k!r} x{v}" for k, v in sorted(self.unparsed.items())),
                file=sys.stderr,
            )
        if self.named and failed / self.named > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Medicine Hat: {failed} of {self.named} SPECIES values unreadable "
                f"(> {MAX_UNPARSED_SHARE:.0%}); has the domain changed shape?"
            )


if __name__ == "__main__":
    city = MedicineHat()
    pages = iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )
    table = stream_to_table(pages, city.transform, keep=city.keep, label="Medicine Hat OpenData")
    city.report()
    table = validate_coordinates(table, city="Medicine Hat", city_code="CAMHT")
    table = enforce_tree_schema(
        table, city="Medicine Hat", data_source="MEDICINEHAT_OPENDATA"
    )
    emit(table)
