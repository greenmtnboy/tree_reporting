#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Cockburn's street tree inventory, from the City of Cockburn's ArcGIS Online.

No licence stated; displayed with attribution only, not licensed for reuse.
The item states no licence (`licenseInfo` empty), the Cockburn Mapping Hub
(maps-cityofcockburn.hub.arcgis.com) states none, and the council's website
copyright notice (cockburn.wa.gov.au/Disclaimer) covers its website material,
which it allows to be reproduced unaltered for personal, non-commercial use.

Source: "Street_Trees_3857" (item 4e25763b23fe47c1bb928920152789a9, owner
`gisservices_cityofcockburn`: "Existing street trees found in the City of
Cockburn area"), layer 0 of `Street_Trees_3857_1de94/FeatureServer`, the layer
behind the Mapping Hub's Street Trees theme.  Measured 2026-11-21 (layer
`dataLastEditDate` 2026-11-20): 76,058 points spanning -32.181 to -32.074 and
115.753 to 115.915, inside AUCKB's CITY_BOUNDS.  The council publishes street
(verge) trees only: its parks and reserves layers are polygons, and no park
tree layer is public.  `Street-Trees/FeatureServer/0` ("Street_Trees_v2 -
Public Maps View") is an older public view of the same register, 990 rows
behind this one and with no row this one lacks.

**Removed trees.**  The public layer has no status column.  The council's
earlier asset-register copy on the same organisation (`Cockurn_Street_Trees/
FeatureServer/0`, last edited 2026-02-03, inspections to 2021) carries
`Tree_Status` (Existing 50,611 / Removed 7,460), and 7,270 of the Removed
asset numbers are still in the public layer with the same species and
diameter (a 300-row sample: 289 identical, 1 different).  Those are dropped
here: a tree the council records as removed is not published.  A row whose
asset number was Removed but whose `Botanical_Name` differs from the removed
record is a replanting under the same number and is kept.  The status read
fails loud: if it went silently empty those trees would come back.

**The id is `Tree_Number`, the council's asset number** (`ckb-{Tree_Number}`).
There is no `GlobalID` in the public layer.  It is non-null on every row and
unique except for one value (171934: an olive at 20 and a pear at 31 Santena
Street, distinct trees); it is the same number the asset-register copy
carries for the same tree, so it survives the layer's reloads.  `OBJECTID` is
dense 1..76,058, a whole-layer reload, and is not used.  A repeated number is
read up front with one `groupBy ... having` statistics query and those rows
take `ckb-{Tree_Number}-{lon}_{lat}` (the shared coordinate id appended).

**`Quantity_of_Trees`** is 1 on every row but one (171812, two young Agonis
at 5 Longtail Rise, Beeliar, one point).  The grain is the point, so that row
publishes as one tree; the count is logged.

Field choices:

  * `species` <- `Botanical_Name`, already the scientific name with quoted
    cultivars (`Magnolia grandiflora 'Little Gem'`; Swane's Gold cypress is
    opened with a backtick, and its apostrophe would end the shared quote
    rule early, so it is an override).  `Genus`/`Species`/
    `Common_Name` are 8-letter field codes and `Full_Name_CommonName` is the
    common name; none is mapped.  `SPECIES_OVERRIDES` reads the names that are
    not a taxon as written: unquoted cultivars of a genus (`Callistemon Kings
    Park Special`, 4,476 trees, the city's second commonest), `Pyrus
    Bradford`, `Fraxinus raywoodii` (the Raywood ash), `Oleaceae europaea`
    (a family name for the olive's genus), `Pomegranate species`, and the
    common-name citrus (`Citrus orange`, `Citrus Lime`).  "Conifer species" is the
    `Unknown conifer` sentinel; "Unknown species", "Unknown evergreen",
    "Unknown deciduous", "Palm" and blanks reach the shared sentinels.
  * `diameter_at_breast_height` <- `Diameter_mm` / 25.4: millimetres (median
    ~130, mean 180; zeros nulled by the schema).
  * `crown_width_m` <- `Canopy_Spread_m`, metres (mean 3.2); zero is no
    measurement.  `Approx_Height_m` has no canonical column.
  * `plant_date` is null: the public layer records none.
  * `latitude`/`longitude` from the point geometry, reprojected server-side
    from Web Mercator with `outSR=4326`.
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
    UNKNOWN_CONIFER_SPECIES,
    coordinate_tree_id,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

# Kept in step with cockburn_update_time.py.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/pvAgeoXPDsVDgOQ0/arcgis/rest/services/"
    "Street_Trees_3857_1de94/FeatureServer/0",
    timeout=180,
)
SOURCE_URL = LAYER.url

# The council's asset-register copy, read only for `Tree_Status` (see above).
STATUS_LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/pvAgeoXPDsVDgOQ0/arcgis/rest/services/"
    "Cockurn_Street_Trees/FeatureServer/0",
    timeout=180,
)

OUT_FIELDS = "OBJECTID,Tree_Number,Quantity_of_Trees,Botanical_Name,Diameter_mm,Canopy_Spread_m"

ID_PREFIX = "ckb"

# `Botanical_Name` values that are not a taxon as written: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "callistemon kings park special": ("Callistemon", "Kings Park Special"),
    "callistemon dawson river weeper": ("Callistemon", "Dawson River Weeper"),
    # Backtick-quoted, and the apostrophe inside ends the shared quote rule early.
    "cupressus sempervirens `swane's gold'": ("Cupressus sempervirens", "Swane's Gold"),
    "pyrus bradford":("Pyrus calleryana", "Bradford"),
    "pyrus 'bradford'": ("Pyrus calleryana", "Bradford"),
    "fraxinus raywoodii": ("Fraxinus angustifolia", "Raywood"),
    "oleaceae europaea 'no fruit'": ("Olea europaea", None),
    "pomegranate species": ("Punica granatum", None),
    "citrus orange": ("Citrus sinensis", None),
    "citrus lime": ("Citrus", None),
    "conifer species": (UNKNOWN_CONIFER_SPECIES, None),
}

# Refuse when the removed-tree read comes back far smaller than the 7,460
# the register held: a status layer that changed meaning or emptied.
MIN_REMOVED = 5_000
# Refuse when more than this share of the public layer is dropped as removed
# (7,270 of 76,058, 9.6%, in November 2026).
MAX_REMOVED_SHARE = 0.15


def name_key(value) -> str:
    return " ".join(str(value or "").split()).lower()


@cache
def repeated_numbers() -> frozenset[int]:
    """The `Tree_Number` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "Tree_Number IS NOT NULL",
            "groupByFieldsForStatistics": "Tree_Number",
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
        raise RuntimeError(f"Cockburn Tree_Number duplicate query was incomplete: {payload!r}"[:500])
    return frozenset(int(f["attributes"]["Tree_Number"]) for f in payload["features"])


def removed_trees() -> dict[int, set[str]]:
    """`Tree_Number` -> the species names the register records as Removed."""
    removed: dict[int, set[str]] = {}
    for page in iter_features(
        STATUS_LAYER,
        out_fields="OBJECTID,Tree_Number,Botanical_Name",
        where="Tree_Status = 'Removed'",
    ):
        for feature in page:
            rec = feature.get("attributes") or {}
            if rec.get("Tree_Number") is None:
                continue
            removed.setdefault(int(rec["Tree_Number"]), set()).add(name_key(rec.get("Botanical_Name")))
    if len(removed) < MIN_REMOVED:
        raise RuntimeError(
            f"Cockburn: the asset register lists only {len(removed):,} removed trees; "
            "its Tree_Status has changed meaning"
        )
    return removed


def positive(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if value > 0 else None


class Cockburn:
    def __init__(self) -> None:
        self.repeated = repeated_numbers()
        self.removed = removed_trees()
        self.dropped: Counter[str] = Counter()
        self.notes: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        number = rec.get("Tree_Number")
        if number is None:
            self.dropped["no Tree_Number"] += 1
            return False
        names = self.removed.get(int(number))
        if names is not None:
            if name_key(rec.get("Botanical_Name")) in names:
                self.dropped["recorded Removed in the asset register"] += 1
                return False
            self.notes["Removed number reused by a different species (kept)"] += 1
        lat, lon = esri_point(feature.get("geometry"))
        if lat is None or lon is None:
            self.dropped["no geometry"] += 1
            return False
        if (rec.get("Quantity_of_Trees") or 1) > 1:
            self.notes["rows recording more than one tree (kept as one)"] += 1
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            lat, lon = esri_point(feature.get("geometry"))
            number = int(rec["Tree_Number"])
            if number in self.repeated:
                tree_id.append(coordinate_tree_id(f"{ID_PREFIX}-{number}", lon, lat))
            else:
                tree_id.append(f"{ID_PREFIX}-{number}")
            key = name_key(rec.get("Botanical_Name"))
            if key in SPECIES_OVERRIDES:
                sp, cv = SPECIES_OVERRIDES[key]
            else:
                sp, cv = normalize_species(rec.get("Botanical_Name")), None
            species.append(sp)
            cultivar.append(cv)
            latitude.append(lat)
            longitude.append(lon)
            mm = positive(rec.get("Diameter_mm"))
            dbh.append(mm / 25.4 if mm is not None else None)
            crown.append(positive(rec.get("Canopy_Spread_m")))

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["AUCKB"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Cockburn: dropped {n:,} rows: {reason}", file=sys.stderr)
        for note, n in sorted(self.notes.items()):
            print(f"Cockburn: {n:,} {note}", file=sys.stderr)
        removed = self.dropped["recorded Removed in the asset register"]
        if self.seen and removed / self.seen > MAX_REMOVED_SHARE:
            raise RuntimeError(
                f"Cockburn: {removed:,} of {self.seen:,} rows match removed trees; "
                "the asset register and the public layer have diverged"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    cockburn = Cockburn()
    table = stream_to_table(
        iter_row_chunks(), cockburn, keep=cockburn.keep, label="Cockburn OpenData"
    )
    cockburn.report()
    table = validate_coordinates(table, city="Cockburn", city_code="AUCKB")
    table = enforce_tree_schema(table, city="Cockburn", data_source="COCKBURN_OPENDATA")
    emit(table)
