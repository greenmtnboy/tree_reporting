#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bend, Oregon's trees, from the City of Bend's Volunteer Tree Inventory.

Licence: no licence stated; displayed with attribution only, not licensed for
reuse.  The ArcGIS item (3a518917e1fc407cbc5d72ab4260101b, owner
`slayne@bendoregon.gov_bendoregon`, the City of Bend's ArcGIS Online
organisation) has empty `licenseInfo` and `accessInformation`, and the
City's open-data hub (https://data.bendoregon.gov) states no licence for any
dataset -- its items carry only a "reference purposes only ... AS IS"
disclaimer, which is not a licence.

**Source: the "Volunteer Tree Inventory" layer**
(`service_b8c085f8c6f543658e06d452bf211869/FeatureServer/0`), the hosted
layer behind the City's Survey123 form: the 2006 street-tree inventory, a
2022 LiDAR pass and the 2026 Bend Tree Project, re-surveyed by City staff and
volunteers and still being edited daily.  40,385 rows on 2026-11-23.  It is
the City's only published tree inventory (nothing on data.bendoregon.gov; the
`_form` and `_results` items are Survey123 views of this same service).

**Only trees are kept.**  `InventoryStatus` (a coded domain) is `Planted`
(28,543) or `Planted - Private` (145, a tree the survey found on private land
beside the right of way) for a tree; `Pending` (7,132: a point still to be
surveyed, no species and no DBH), `Vacant` (3,999, planting sites), `Stump`
(564), `BadData_Delete` (a point the City has flagged for deletion) and a null
status (2) are dropped with a logged count.  An unseen status raises.

**The id is `globalid`** (`bnd-{globalid}`): Survey123's own row key,
non-null and unique over the whole layer, and kept through edits.  `TreeID`
(`TREE-427`) is set on 11,917 rows only; `objectid` (2..118,986 for 40k
rows) has the gaps of a layer that has been edited and reloaded.

**Species is `BotanicalName`**, a scientific name with any cultivar in quotes
(`Acer x freemanii 'Jeffersred'`, split by the schema); `Unknown`, `Stump`
and the common name typed into it once (`Ponderosa Pine`) are not names.
About a fifth of the trees (5,369, mostly the older records) have only the
`CommonName` domain value; those resolve through the shared English table
(`shared.species.english`), and a name it does not know publishes no
species.  Where both are filled the City's Latin name wins.

**DBH is `DBH`, inches** (alias "DBH (Inches)"; median 7.5, 95th percentile
24.0); zeros and the few typed-in values over the schema's ceiling (1042,
747, ...) are nulled by the schema.  No plant date is recorded
(`CreationDate` is the survey, not the planting).  `Height` is a LiDAR
height on 301 rows and the canonical schema has no column for it; no crown
spread is recorded.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import species_from_common_name  # noqa: E402

LAYER = FeatureLayer(
    "https://services5.arcgis.com/JisFYcK2mIVg9ueP/arcgis/rest/services/"
    "service_b8c085f8c6f543658e06d452bf211869/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "objectid,globalid,InventoryStatus,CommonName,BotanicalName,DBH"

TREE_STATUSES = frozenset({"Planted", "Planted - Private"})
NOT_TREE_STATUSES = frozenset({"Pending", "Vacant", "Stump", "BadData_Delete", ""})

# `BotanicalName` values that are not a name.
NOT_A_NAME = frozenset({"unknown", "stump", "vacant planting"})


def clean(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


class Bend:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.species_from: Counter[str] = Counter()

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = clean(rec.get("InventoryStatus"))
        if status not in TREE_STATUSES:
            if status not in NOT_TREE_STATUSES:
                raise RuntimeError(
                    f"Bend: unseen InventoryStatus {status!r}; read what it "
                    "means before deciding whether it is a tree"
                )
            self.dropped[f"InventoryStatus {status or 'null'}"] += 1
            return False
        if not clean(rec.get("globalid")):
            self.dropped["no globalid"] += 1
            return False
        return True

    def species(self, rec: dict) -> str | None:
        latin = clean(rec.get("BotanicalName"))
        if latin and latin.lower() not in NOT_A_NAME:
            # A common name typed into the botanical field (`Ponderosa Pine`).
            if " " in latin and latin.split()[1][:1].isupper() and "'" not in latin:
                resolved = species_from_common_name(latin)
                if resolved:
                    self.species_from["common name in BotanicalName"] += 1
                    return resolved
            self.species_from["BotanicalName"] += 1
            return latin
        common = clean(rec.get("CommonName"))
        resolved = species_from_common_name(common) if common else None
        self.species_from[
            "CommonName (shared table)" if resolved else "none / unresolved"
        ] += 1
        return resolved

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"bnd-{clean(rec['globalid']).strip('{}').lower()}")
            species.append(self.species(rec))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw = rec.get("DBH")
            dbh.append(float(raw) if raw is not None else None)
        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USBND"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Bend: dropped {n:,} rows: {reason}", file=sys.stderr)
        for source, n in sorted(self.species_from.items()):
            print(f"Bend: species from {source}: {n:,}", file=sys.stderr)


def iter_row_chunks():
    """One ArcGIS page at a time, geometry in WGS84."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by="objectid"
    )


if __name__ == "__main__":
    bend = Bend()
    table = stream_to_table(iter_row_chunks(), bend, keep=bend.keep, label="Bend OpenData")
    bend.report()
    table = validate_coordinates(table, city="Bend", city_code="USBND")
    table = enforce_tree_schema(table, city="Bend", data_source="BEND_OPENDATA")
    emit(table)
