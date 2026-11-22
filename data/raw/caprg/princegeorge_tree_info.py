#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Prince George's tree inventory, from the City of Prince George.

Source: "Trees" (`OpenData_ParkData/FeatureServer/0`) on the City's ArcGIS
Online organisation, catalogued as item 382244b24c054f0ab92ca9d76facd44c
("Tree locations within the City of Prince George ... based on the ongoing
GPS data collection done by the Parks department", owner
`opendatapg_cityofpg`) inside the service item
035b9e8a045c4da984009cb53e6be642 (owner `opendatascripts_cityofpg`, the
account that republishes the City's open data): 5,502 rows on 2026-11-22.
It is the City's only tree inventory: the service's other layers are park
assets (bins, playgrounds, turf, beds), and `Trees_3d` is a token-only scene
of the same layer.  Street and park trees are in the one layer (`Location` is
a park name or a block range such as `1500 - 1700 Dogwood St`).

Licence: the layer item's licence field links the "Open Government License -
City of Prince George"
(https://pgmapinfo.princegeorge.ca/opendata/CityofPrinceGeorge_Open_Government_License_Open_Data.pdf),
version 2.0, based on the Open Government License - British Columbia: a
"worldwide, royalty-free, perpetual, non-exclusive license to use the
Information, including for commercial purposes", free to "copy, modify,
publish, translate, adapt, distribute or otherwise use the Information",
with the attribution "Contains information licensed under the Open
Government License - City of Prince George".

**The id is the City's `AssetID`** (`pg-{AssetID}`), not the GlobalID.  The
hosted layer is a script copy: every row's `created_date` is the same instant
(2026-11-14, user `opendatascripts_cityofpg`), so the whole layer is
re-inserted on each publish and the hosted GlobalIDs cannot be shown to
survive one.  `AssetID` is the asset-management key the City's own records
carry (it equals `FacilityID` on 5,392 rows), unique and non-null over all
5,502 rows (2026-11-22), sparse over 1..23,189.  A row without one is dropped
with a count.  (`barcode`/`gisuid`, the `PRK-` tag, is unique too, and kept as
a fallback should `AssetID` ever go.)

**Not every row is a tree.**  `LifeCycleStatus` is a domain (Proposed,
Active, Abandoned, Removed, Under Construction); only Active is kept, and an
unseen code refuses the build.  A row with a `TreeRemoveDate` or a
`DateSpaceRetired` is dropped whatever its status.  On 2026-11-22 every row
was Active with neither date, so nothing was dropped.  The 1,514 rows with no
genus (no species, no DBH; most `Type = Unknown`) are kept: they are GPS-
located park trees not yet identified, several with a photograph filed under
`Photo\\Tree`, not planting sites -- the layer has no vacant-site value.

**Species is `GenusName` + `SpeciesName` + `VarietyName`**, three coded
Latin fields (`CommonName` is not mapped; 84 unidentified rows carry only a
common name and stay unidentified).  The epithet field is 12 characters
wide, so two names arrive cut (`hippocastanu`, `ellipsoidali`) and are
completed here; `spp` and `hybrid` mean the genus.  `tremula 'Erecta'` carries
its cultivar in the epithet field, which the schema splits.  `VarietyName`
holds cultivars (`Brandon`, `Schubert`, `DurLawrence`, `Dalecarlica`, ...)
which go to `cultivar`, except botanical varieties (`Commutata`,
`Vittellina`, `Douglasii`), which are dropped, and `Arnoldiana` on a bare
`Aesculus`, which is the nothospecies Aesculus x arnoldiana (the Autumn
Splendor buckeye).  A parse-failure guard refuses more than 1% unreadable
names.

**DBH is `DBH`, centimetres** (American elm median 45, green ash 13, blue
spruce 6; the big black cottonwoods 130-155), converted to inches; zero (247
rows) is "not measured".  A few plantings were typed in metres: 96 rows below
0.1 (`0.035` on 92 trees planted on Ospika Blvd and Massey Dr in 2016-17,
2.5 m tall: a 35 mm caliper; `0.07`/`0.08` on 3 m oaks and lindens) are read
as metres, and the two `0.8` (a 3 m ash: neither 8 mm nor 80 cm) are
nulled.  **Plant date** is `TreePlantDate`, else
`YearPlanted` (1 January), else `InstallDate`.  Dates are stored at UTC
midnight (`dateFieldsTimeReference` UTC) and read as UTC dates.
`TreeHeight_m` (182 rows) has no column in the schema and is not read.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services2.arcgis.com/CnkB6jCzAsyli34z/arcgis/rest/services/"
    "OpenData_ParkData/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = (
    "OBJECTID,AssetID,LifeCycleStatus,TreeRemoveDate,DateSpaceRetired,"
    "GenusName,SpeciesName,VarietyName,DBH,TreePlantDate,YearPlanted,InstallDate"
)

STATUS_KEPT = "ACT"
STATUS_DROPPED = {"PRP": "proposed", "ABN": "abandoned", "RMV": "removed", "UC": "under construction"}

# The coded epithet field is 12 characters wide.
EPITHET_TRUNCATED = {"hippocastanu": "hippocastanum", "ellipsoidali": "ellipsoidalis"}
# Epithet values that name no species: the tree is known to genus.
GENUS_ONLY = {"spp", "sp", "hybrid"}
# VarietyName values that are botanical varieties, not cultivars.
VARIETY_RANK = {"commutata", "vittellina", "vitellina", "douglasii"}
# VarietyName values that, on a bare genus, name a nothospecies.
NOTHOSPECIES = {("Aesculus", "arnoldiana"): "Aesculus × arnoldiana"}
NO_VARIETY = {"none", ""}

# A DBH below this is metres: the 2016-17 plantings' 35 mm caliper is `0.035`.
METRES_BELOW = 0.1

# Refuse to publish when more than this share of named rows parse to no taxon.
MAX_UNPARSED_SHARE = 0.01


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    return text or None


def scientific_name(genus, epithet, variety) -> tuple[str | None, str | None, bool]:
    """(species for the schema, cultivar, whether the value failed to parse)."""
    g = clean(genus)
    e = clean(epithet)
    v = clean(variety)
    if v is not None and v.lower() in NO_VARIETY:
        v = None
    if g is None:
        return None, None, e is not None
    if not g.isalpha() or not g[0].isupper():
        return None, None, True
    cultivar = None
    if v is not None:
        if e is None and (g, v.lower()) in NOTHOSPECIES:
            return NOTHOSPECIES[(g, v.lower())], None, False
        if v.lower() not in VARIETY_RANK:
            cultivar = v.strip("'\"").strip()
            if cultivar.islower():
                cultivar = cultivar.title()
    if e is None:
        return g, cultivar, False
    # A quoted cultivar inside the epithet field (`tremula 'Erecta'`).
    if "'" in e:
        head, _, tail = e.partition("'")
        cultivar = cultivar or tail.strip("'").strip() or None
        e = head.strip()
    word = e.lower()
    if word in GENUS_ONLY:
        return g, cultivar, False
    word = EPITHET_TRUNCATED.get(word, word)
    if not word.replace("-", "").isalpha():
        return None, None, True
    return f"{g} {word}", cultivar, False


def plant_date_of(rec: dict):
    planted = esri_ms_to_date(rec.get("TreePlantDate"))
    if planted is not None:
        return planted
    year = parse_plant_date_year(rec.get("YearPlanted"))
    if year is not None:
        return year
    return esri_ms_to_date(rec.get("InstallDate"))


class PrinceGeorge:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.named = 0
        self.dbh_units: Counter[str] = Counter()

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = clean(rec.get("LifeCycleStatus"))
        if status in STATUS_DROPPED:
            self.dropped[f"LifeCycleStatus {STATUS_DROPPED[status]}"] += 1
            return False
        if status != STATUS_KEPT:
            raise RuntimeError(
                f"Prince George: unseen LifeCycleStatus {rec.get('LifeCycleStatus')!r}"
            )
        if rec.get("TreeRemoveDate") is not None:
            self.dropped["TreeRemoveDate set"] += 1
            return False
        if rec.get("DateSpaceRetired") is not None:
            self.dropped["DateSpaceRetired set"] += 1
            return False
        if clean(rec.get("AssetID")) is None:
            self.dropped["no AssetID"] += 1
            return False
        return True

    def dbh_inches(self, value) -> float | None:
        if value is None or value <= 0:
            return None
        if value < METRES_BELOW:
            self.dbh_units["metres"] += 1
            return cm_to_inches(value * 100)
        if value < 1:
            self.dbh_units["unreadable"] += 1
            return None
        return cm_to_inches(value)

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
            tree_id.append(f"pg-{clean(rec['AssetID'])}")
            name, cv, failed = scientific_name(
                rec.get("GenusName"), rec.get("SpeciesName"), rec.get("VarietyName")
            )
            raw = " ".join(
                x for x in (clean(rec.get("GenusName")), clean(rec.get("SpeciesName"))) if x
            )
            if raw:
                self.named += 1
            if failed:
                self.unparsed[raw] += 1
            species.append(name)
            cultivar.append(cv if name is not None else None)
            plant_date.append(plant_date_of(rec))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(self.dbh_inches(rec.get("DBH")))

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CAPRG"] * n, type=pa.string()),
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
            "Prince George: dropped "
            + (", ".join(f"{v} {k!r}" for k, v in sorted(self.dropped.items())) or "nothing")
            + f"; DBH read as metres on {self.dbh_units['metres']} row(s), "
            + f"nulled as unreadable on {self.dbh_units['unreadable']}",
            file=sys.stderr,
        )
        failed = sum(self.unparsed.values())
        if failed:
            print(
                f"Prince George: {failed} row(s) with no readable scientific name: "
                + ", ".join(f"{k!r} x{v}" for k, v in sorted(self.unparsed.items())),
                file=sys.stderr,
            )
        if self.named and failed / self.named > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Prince George: {failed} of {self.named} GenusName/SpeciesName values "
                f"unreadable (> {MAX_UNPARSED_SHARE:.0%}); has the layer changed shape?"
            )


if __name__ == "__main__":
    city = PrinceGeorge()
    pages = iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)
    table = stream_to_table(pages, city.transform, keep=city.keep, label="Prince George OpenData")
    city.report()
    table = validate_coordinates(table, city="Prince George", city_code="CAPRG")
    table = enforce_tree_schema(
        table, city="Prince George", data_source="PRINCE_GEORGE_OPENDATA"
    )
    emit(table)
