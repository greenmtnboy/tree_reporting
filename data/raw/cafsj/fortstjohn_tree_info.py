#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fort St. John's City tree inventory.

Source: "Tree Inventory" (item c4ee1a5aa7c7496b8a70544a8fcf4e1a, owner
`gis_fortstjohn`), the City of Fort St. John's hosted feature layer, listed on
the City's open data site data-fortstjohn.opendata.arcgis.com: "Completed in
2020, the tree inventory point dataset contains a spatial representation of
City-owned and maintained trees."  8,956 rows on 2026-11-22: street trees and
the trees of the City's parks and cemeteries (`LOC_NOTE`), including the
Charlie Lake boat launch just west of the municipal boundary.

Licence: the item's own licence field is empty; the open data site's home
page links its "Open Government License" page
(https://data-fortstjohn.opendata.arcgis.com/pages/city-of-fort-st-john-open-government-data-license),
version 2.0 of the Open Government Licence - Fort St. John (OGL-BC shaped):
a "worldwide, royalty-free, perpetual, non-exclusive license" to "Copy,
modify, publish, translate, adapt, distribute or otherwise use the
Information in any medium, mode or format for any lawful purpose", with the
attribution "Contains information licensed under the Open Government License
- City of Fort St. John."

**The id is `FACILITYID`** (`fsj-TRE00000001`), the City's asset id: non-null
and unique over the whole layer on 2026-11-22 (a `groupBy FACILITYID having
COUNT > 1` statistics query returns nothing).  There is no GlobalID, and
`OBJECTID` runs densely from 28631 to 37586 -- a layer reloaded whole, so it is
not an id.  A row without a `FACILITYID` is dropped with a logged count; a
repeat would be refused by `enforce_tree_schema`.

**Species** is `SPECIES`, a scientific name with any cultivar quoted in it
(`Fraxinus nigra 'Fallgold'`, `Picea glauca 'Densata'` in curly quotes), which
the shared schema moves to `cultivar`.  `Genus spp` is the genus.  One value
writes its cultivar unquoted (`Malus profusion`) and is quoted here.
`COMMONNAME` is not mapped.  A parse-failure guard refuses more than 1% of
named rows whose value does not start with a Latin genus.

**DBH is `DBH`, in centimetres**, read from the values: an integer 0..130
(median about 10), Amur maple and serviceberry averaging 3-4 and white spruce
24 -- inches would make the average street spruce 60 cm and the largest
balsam poplar 3.3 m.  Converted to inches; zero is no measurement.
`MULTISTEM` (a yes/no domain) has no canonical column.  No planting date,
height or crown spread is published.

Geometry is stored in NAD83 / UTM 10N and requested as WGS84 (`outSR=4326`).
"""

import re
import sys
from collections import Counter
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
    "https://services.arcgis.com/uFSCv839a7S1MRb7/arcgis/rest/services/"
    "tree_inventory/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,FACILITYID,SPECIES,DBH"
ID_PREFIX = "fsj"

CM_PER_INCH = 2.54

# Source values whose cultivar is written unquoted, keyed case-folded.
SPECIES_FIXES: dict[str, str] = {
    "malus profusion": "Malus 'Profusion'",
}

# A value naming a taxon starts with a capitalised Latin genus (any case in
# the source: `Aesculus Glabra`), optionally a hybrid mark.
LATIN_RE = re.compile(r"^[A-Za-z][a-z]+(\s|$)")

# Refuse to publish when more than this share of named rows parse to no taxon.
MAX_UNPARSED_SHARE = 0.01


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(raw) -> tuple[str | None, bool]:
    """(species for the schema, whether the value failed to parse)."""
    text = clean(raw)
    if text is None:
        return None, False
    text = SPECIES_FIXES.get(text.lower(), text)
    if not LATIN_RE.match(text):
        return None, True
    return text, False


class FortStJohn:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.named = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        if clean(rec.get("FACILITYID")) is None:
            self.dropped["no FACILITYID"] += 1
            return False
        return True

    def transform(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"{ID_PREFIX}-{clean(rec['FACILITYID'])}")
            raw = rec.get("SPECIES")
            if clean(raw) is not None:
                self.named += 1
            name, failed = parse_species(raw)
            if failed:
                self.unparsed[clean(raw)] += 1
            species.append(name)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            cm = rec.get("DBH")
            dbh.append(float(cm) / CM_PER_INCH if cm is not None and cm > 0 else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CAFSJ"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        print(
            "Fort St. John: dropped "
            + (", ".join(f"{v} {k}" for k, v in sorted(self.dropped.items())) or "nothing"),
            file=sys.stderr,
        )
        failed = sum(self.unparsed.values())
        if failed:
            print(
                f"Fort St. John: {failed} row(s) with no readable scientific name: "
                + ", ".join(f"{k!r} x{v}" for k, v in sorted(self.unparsed.items())),
                file=sys.stderr,
            )
        if self.named and failed / self.named > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Fort St. John: {failed} of {self.named} SPECIES values unreadable "
                f"(> {MAX_UNPARSED_SHARE:.0%}); has the layer changed shape?"
            )


if __name__ == "__main__":
    city = FortStJohn()
    pages = iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )
    table = stream_to_table(
        pages, city.transform, keep=city.keep, label="Fort St. John OpenData"
    )
    city.report()
    table = validate_coordinates(table, city="Fort St. John", city_code="CAFSJ")
    table = enforce_tree_schema(
        table, city="Fort St. John", data_source="FORT_ST_JOHN_OPENDATA"
    )
    emit(table)
