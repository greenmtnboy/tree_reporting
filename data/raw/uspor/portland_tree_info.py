#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Portland, OR's street and park tree inventories, from the city's own ArcGIS Server.

Two layers of `COP_OpenData_Environment/MapServer` on portlandmaps.com, both
published by Portland Parks & Recreation's Urban Forestry division (credited
"Bureau of Parks and Recreation") and listed on the City of Portland's open
data Hub (gis-pdx.opendata.arcgis.com), which states no licence for either:

* **Street Tree Inventory - Active Records** (layer 1415, ArcGIS item
  c2b02440053f459db71e1109b9947e51): the second citywide street-tree census,
  inventoried 2022-05 to 2024-10.  252,205 rows on 2026-11-16.  It replaces
  layer 25 ("Street Trees", 218,602 rows inventoried 2010-2016), which is not
  read.
* **Parks Tree Inventory** (layer 220, item 15ae00ece1bf486a868c0f635d3acbfa):
  the park-tree census, inventoried 2017-05 to 2020-08 by staff and
  volunteers.  25,734 rows.

Both are emitted under the one `PORTLAND_OPENDATA` label; the id prefix says
which layer a row came from.  Both layers cap a page at 200 features, so the
street layer is ~1,260 requests (about 25 minutes, sequential).

**Ids.**  The street layer has no GlobalID and no asset number: its only
identifier is `OBJECTID`, which is the OID (unique and non-null by
construction) and runs 1..252,205 with no gaps -- the table is reloaded whole
when it is republished, so a future reload may renumber it.  So a street tree
is keyed on its rounded coordinate, `pdx-st-{lon:.7f}_{lat:.7f}` (EXTENDING.md,
step 1); until 2026-11-20 it was `pdx-st-{OBJECTID}`, which no check-in
referenced.  Two coordinates carried two different trees in November 2026
(dropped) and one an identical repeat (collapsed).  The park layer carries a `GlobalID` (unique over the
layer; its OBJECTIDs have gaps, so it is edited in place), published as
`pdx-pk-{GlobalID}` without the braces, lower-cased.

**Species.**  Street `SPECIES` is one string, `Binomial ['Cultivar'] - common
name` (`Acer platanoides - Norway maple`, `Acer grandidentatum 'Schmidt' -
Rocky Mountain Glow maple`, `Prunus (cherry species and cultivars) - cherry`);
the text before ` - ` is the scientific name, a quoted cultivar is left in for
the schema to move to `cultivar`, and a ` syn. ` alternative
(`Styphnolobium japonicum syn. Sophora japonica`) is cut.  The park layer's
`Species` is a code (`PSME`) whose domain names the common name;
`Genus_species` holds the binomial (`Pseudotsuga menziesii`) and is read
instead, with the same ` syn. ` cut.

**Dead trees.**  Every street row with `Condition` = Dead (2,875) has
`SPECIES` = Unknown and no functional type: a standing dead tree of no
recorded species, published as the `Dead` sentinel; the park layer's
`unknown (dead)` (264) likewise.  The street layer is "Active Records": it
holds no stumps, vacant sites or removed trees (`Site_Type` describes the
planting strip, not the site's status), so nothing else is dropped.

**DBH** is inches in both: street `DIAMETER` median 8.3, p99 42, park `DBH` median
19.3, max 99; zeros are nulled by the schema.  **Crown width** is recorded
only by the park layer, as `CrownWidthNS` / `CrownWidthEW` in feet; their
mean (or the one present) in metres is `crown_width_m`.  Neither layer has a
planting date (`Date_Inventoried` / `Inventory_Date` are survey dates), so
`plant_date` is null throughout.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    DEAD_SPECIES,
    UNKNOWN_SPECIES,
    coordinate_key,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features

SERVICE = "https://www.portlandmaps.com/od/rest/services/COP_OpenData_Environment/MapServer"
STREET = FeatureLayer(f"{SERVICE}/1415", timeout=180)
PARKS = FeatureLayer(f"{SERVICE}/220", timeout=180)

STREET_FIELDS = "SPECIES,DIAMETER,Condition"
PARK_FIELDS = "OBJECTID,GlobalID,Genus_species,DBH,Condition,CrownWidthNS,CrownWidthEW"

FEET_TO_M = 0.3048

# Where a street `SPECIES` string's scientific part ends: ` - ` as written,
# and the three slips the layer also holds -- `Thuja spp. -` (288, nothing
# after the dash), `Maackia chinensis- Chinese Maackia` and `Lindera
# obtusiloba, Japanese spicebush`.  A hyphen inside a name (`crus-galli`,
# `'JFS-Bieberich'`) has no space beside it and does not match.
STREET_SEPARATOR = re.compile(r"\s+-(?:\s+|$)|-\s+|,\s+")

# A street `SPECIES` string with no separator that is not one of these is a
# format change; more than this share of such rows raises.
STREET_BARE_VALUES = frozenset({"unknown"})
MAX_UNPARSED_SHARE = 0.01

PARK_DEAD = "unknown (dead)"


# The street layer glues a nothogenus's mark to it (`xChitalpa tashkentensis`,
# `xSycoparrotia semidecidua`, `xCupressocyparis leylandii`); the park layer
# and the rest of the fleet write `x Chitalpa`, which the schema publishes as
# `X chitalpa tashkentensis`.
_GLUED_NOTHOGENUS = re.compile(r"^[xX×](?=[A-Z])")


def scientific_part(text: str) -> str:
    """`Styphnolobium japonicum syn. Sophora japonica` -> `Styphnolobium japonicum`."""
    head, _, _ = text.partition(" syn. ")
    return _GLUED_NOTHOGENUS.sub("x ", head.strip())


def crown_width_m(ns, ew) -> float | None:
    widths = [float(w) for w in (ns, ew) if w is not None and float(w) > 0]
    if not widths:
        return None
    return sum(widths) / len(widths) * FEET_TO_M


class Portland:
    def __init__(self) -> None:
        self.rows: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.dropped: Counter[str] = Counter()

    # ---- street trees -------------------------------------------------

    def street_species(self, raw, condition) -> str | None:
        text = " ".join(str(raw or "").split())
        if not text:
            return None
        parts = STREET_SEPARATOR.split(text, maxsplit=1)
        if len(parts) == 2:
            return scientific_part(parts[0])
        if text.lower() not in STREET_BARE_VALUES:
            self.unparsed[text] += 1
        if (condition or "").strip().lower() == "dead":
            return DEAD_SPECIES
        return UNKNOWN_SPECIES if text.lower() == "unknown" else text

    def street(self, features: list[dict]) -> pa.Table:
        cols = _empty()
        for feature in features:
            rec = feature.get("attributes") or {}
            lat, lon = esri_point(feature.get("geometry"))
            if lat is None:
                self.dropped["street row with no location"] += 1
                continue
            lon, lat = coordinate_key(lon, lat)
            raw_dbh = rec.get("DIAMETER")
            cols["tree_id"].append(coordinate_tree_id("pdx-st", lon, lat))
            cols["species"].append(self.street_species(rec.get("SPECIES"), rec.get("Condition")))
            cols["latitude"].append(lat)
            cols["longitude"].append(lon)
            cols["diameter_at_breast_height"].append(
                float(raw_dbh) if raw_dbh is not None else None
            )
            cols["crown_width_m"].append(None)
        self.rows["street"] += len(features)
        return _table(cols)

    # ---- park trees ---------------------------------------------------

    def park_keep(self, feature: dict) -> bool:
        guid = (feature.get("attributes") or {}).get("GlobalID")
        if not guid:
            self.dropped["park row with no GlobalID"] += 1
            return False
        return True

    def parks(self, features: list[dict]) -> pa.Table:
        cols = _empty()
        for feature in features:
            rec = feature.get("attributes") or {}
            guid = str(rec["GlobalID"]).strip("{} ").lower()
            text = " ".join(str(rec.get("Genus_species") or "").split())
            if text.lower() == PARK_DEAD:
                species = DEAD_SPECIES
            else:
                species = scientific_part(text) or None
            lat, lon = esri_point(feature.get("geometry"))
            raw_dbh = rec.get("DBH")
            cols["tree_id"].append(f"pdx-pk-{guid}")
            cols["species"].append(species)
            cols["latitude"].append(lat)
            cols["longitude"].append(lon)
            cols["diameter_at_breast_height"].append(
                float(raw_dbh) if raw_dbh is not None else None
            )
            cols["crown_width_m"].append(
                crown_width_m(rec.get("CrownWidthNS"), rec.get("CrownWidthEW"))
            )
        self.rows["park"] += len(features)
        return _table(cols)

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Portland: dropped {n:,} rows: {reason}", file=sys.stderr)
        unparsed = sum(self.unparsed.values())
        if unparsed:
            print(
                f"Portland: {unparsed:,} street rows have a SPECIES with no ' - ' "
                f"separator: {dict(self.unparsed.most_common(10))}",
                file=sys.stderr,
            )
        street = self.rows["street"]
        if street and unparsed / street > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Portland: {unparsed:,} of {street:,} street SPECIES values are not "
                "'Binomial - common name'; the layer's format has changed"
            )


def _empty() -> dict[str, list]:
    return {
        k: []
        for k in (
            "tree_id",
            "species",
            "latitude",
            "longitude",
            "diameter_at_breast_height",
            "crown_width_m",
        )
    }


def _table(cols: dict[str, list]) -> pa.Table:
    n = len(cols["tree_id"])
    return pa.table(
        {
            "tree_id": pa.array(cols["tree_id"], type=pa.string()),
            "city": pa.array(["USPOR"] * n, type=pa.string()),
            "species": pa.array(cols["species"], type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(cols["latitude"], type=pa.float64()),
            "longitude": pa.array(cols["longitude"], type=pa.float64()),
            "diameter_at_breast_height": pa.array(
                cols["diameter_at_breast_height"], type=pa.float64()
            ),
            "crown_width_m": pa.array(cols["crown_width_m"], type=pa.float64()),
        }
    )


def iter_street_chunks():
    return iter_features(STREET, out_fields=STREET_FIELDS, return_geometry=True, out_sr=4326)


def iter_park_chunks():
    return iter_features(PARKS, out_fields=PARK_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    portland = Portland()
    parks = stream_to_table(
        iter_park_chunks(), portland.parks, keep=portland.park_keep, label="Portland parks"
    )
    street = stream_to_table(iter_street_chunks(), portland.street, label="Portland streets")
    street = drop_stacked_coordinates(
        street, label="Portland streets", identical=("species", "diameter_at_breast_height")
    )
    portland.report()
    table = pa.concat_tables([street, parks])
    table = validate_coordinates(table, city="Portland", city_code="USPOR")
    table = enforce_tree_schema(table, city="Portland", data_source="PORTLAND_OPENDATA")
    emit(table)
