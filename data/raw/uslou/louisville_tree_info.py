#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Louisville, KY's Urban Forestry trees: the trees Louisville Metro planted.

LICENCE: none stated.  The ArcGIS item (a04b388a30d84f8baeba9c9fa67b639e,
"URBAN FORESTRY TREES (PUBLIC DATA VIEW)", owner `megan.hale_Metro` in the
Louisville Metro Government organisation, a public view created 2026-02-06)
has no description, no `licenseInfo` and no `accessInformation`, and it is
not listed in the Louisville Metro Open Data catalogue
(data.louisvilleky.gov), whose Terms of Use dedicate *its* datasets under the
ODC Public Domain Dedication and License.  The PDDL is therefore not claimed
for this layer: it is displayed with attribution only, not licensed for
reuse.

**What it is.**  Metro Urban Forestry's planting tracker, not a full street
tree inventory: it holds the trees Metro and its partners (Trees Louisville,
neighbourhood associations, contractors, volunteers) planted since 2013,
some inventoried park and street trees, and every *proposed* site.  42,976
rows on 2026-11-18.  No metro-wide inventory of existing street trees is
published anywhere (searched the Open Data catalogue and ArcGIS Online); the
only other tree layers are the 271 "Trees of Significance" and nonprofit
project layers.

**`Status` decides what is a tree** (coded-value domain, read at run time):

* `StreetTree` (11,258) and `ParkTree` (2,873) are trees and are kept;
* `Prop Site` (26,513, "Proposed Planting Location"), `BADLocation_Site` /
  `BADLocation_Home` ("Do Not Plant"), `Stump` and `Illegal_removal` are
  places or removed trees, and are dropped, as is a null `Status` (5 empty
  rows).  A code new to the domain raises.

`Condition` = Dead (about 500 kept rows) is a standing dead tree and stays
under its species; a dead tree with no species publishes as `Dead`.

**The id is `globalid`** (non-null and unique over all 42,976 rows, checked
again at run time by `enforce_tree_schema`), published as
`lou-{globalid}`.  The layer carries no asset number of its own.  Every
`created_date` on the existing rows is 2023-08-31: the table was loaded
then, and a reload would regenerate the GlobalIDs.

**Species is `Tree_Name_Genus_Species`**, a coded field whose values are
`Common, Name - Binomial` (`Maple, Red - Acer rubrum`).  The binomial after
the last ` - ` is read; the common name is not.  A few values are outside
the domain but in the same shape (`Apple species - Malus sp.`).
`Tree, unknown` (no binomial) is Unknown.  An interspecific cross written
`Genus a x b` (`Quercus robur x bicolor`, Regal Prince oak) is a hybrid of
no one species and publishes as the genus, as Columbus and Seattle do;
so do `Ulmus hybrid` and `Ulmus x`, a hybrid elm of no named species.
`cultivar` (a coded field of upper-case cultivar names) is title-cased.

**DBH is `diameter`, inches** (domain range 0-100): mode 2 (new 2 in
caliper stock), 1.5 and 1.75 next, a tail of inventoried trees to 40+.

**`INSTALL_DATE` is `plant_date`** on about 8,800 kept rows, 2013-11 to
2026.  Older rows are stamped at 00:00 UTC, newer ones at local midnight
(04:00/05:00 UTC); both read as the same calendar date in UTC.  The largest
single date, 2023-12-01 (445 rows), is the FY24 replacement programme's
planting date (`FY25_HISTORICAL` names it), young trees of many species, so a
real record and kept.
"""

import re
import sys
from collections import Counter
from datetime import date
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    coded_value_domain,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.ingest import (
    DEAD_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/79kfd2K6fskCAkyg/arcgis/rest/services/"
    "URBAN_FORESTRY_TREES_(PUBLIC_DATA_VIEW)/FeatureServer/0",
    timeout=180,
)

OUT_FIELDS = (
    "OBJECTID,globalid,Status,Condition,Tree_Name_Genus_Species,cultivar,"
    "diameter,INSTALL_DATE"
)

# `Status` codes that are a tree in the ground.
TREE_STATUSES = frozenset({"StreetTree", "ParkTree"})
# Codes that are a site or a removed tree, by code; the domain is checked
# against TREE_STATUSES | SITE_STATUSES at run time.
SITE_STATUSES = frozenset(
    {"Prop Site", "BADLocation_Home", "BADLocation_Site", "Stump", "Illegal_removal"}
)

DEAD_CONDITION = "dead"

# `Genus a x b`: a cross between two named species.
CROSS = re.compile(r"^([A-Z][a-z]+)\s+[a-z]+\.?\s+[xX×]\s+[a-z]")

# `Ulmus hybrid`, `Ulmus x`: a hybrid of the genus, no species named.
HYBRID_OF_GENUS = re.compile(r"^[A-Z][a-z]+\s+(?:hybrid|[xX×])$")

# Above this share of kept rows carrying a species value with no ` - `
# binomial, the column has changed shape.
MAX_UNPARSED_SHARE = 0.01


@cache
def status_codes() -> frozenset[str]:
    codes = frozenset(coded_value_domain(LAYER, "Status"))
    unknown = codes - TREE_STATUSES - SITE_STATUSES
    if unknown:
        raise RuntimeError(
            f"Louisville Status domain has new codes {sorted(unknown)}; "
            "decide whether they are trees"
        )
    if not TREE_STATUSES <= codes:
        raise RuntimeError(f"Louisville Status domain lost a tree code: {sorted(codes)}")
    return codes


def binomial(raw: str | None) -> tuple[str | None, bool]:
    """(`species` text, parsed) from one `Tree_Name_Genus_Species` value."""
    if raw is None:
        return None, True
    text = " ".join(str(raw).split())
    if not text:
        return None, True
    if " - " not in text:
        # `Tree, unknown`: a common-name-only value.
        return (UNKNOWN_SPECIES, True) if "unknown" in text.lower() else (None, False)
    name = text.rsplit(" - ", 1)[1].strip()
    cross = CROSS.match(name)
    if cross:
        return cross.group(1), True
    if HYBRID_OF_GENUS.match(name):
        return name.split()[0], True
    return name or None, True


def clean_cultivar(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip("'\"‘’ ")
    if not text:
        return None
    if text.isupper():
        text = text.title()
    return text


class Louisville:
    def __init__(self) -> None:
        status_codes()
        self.dropped: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.rows = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = rec.get("Status")
        if status in TREE_STATUSES:
            if not rec.get("globalid"):
                self.dropped["no globalid"] += 1
                return False
            return True
        if status is not None and status not in SITE_STATUSES:
            raise RuntimeError(f"Louisville Status {status!r} is new; decide whether it is a tree")
        self.dropped[f"Status {status!r} (not a tree)"] += 1
        return False

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"lou-{str(rec['globalid']).strip('{}').lower()}")
            raw = rec.get("Tree_Name_Genus_Species")
            name, parsed = binomial(raw)
            if not parsed:
                self.unparsed[str(raw)] += 1
            if name is None and (rec.get("Condition") or "").strip().lower() == DEAD_CONDITION:
                name = DEAD_SPECIES
            species.append(name)
            cultivar.append(clean_cultivar(rec.get("cultivar")))
            plant_date.append(esri_ms_to_date(rec.get("INSTALL_DATE")))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("diameter")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)
        self.rows += len(tree_id)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USLOU"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Louisville: dropped {n:,} rows: {reason}", file=sys.stderr)
        unparsed = sum(self.unparsed.values())
        if unparsed:
            print(
                f"Louisville: {unparsed:,} rows with a species value that has no "
                f"binomial (published as null): {dict(self.unparsed.most_common(10))}",
                file=sys.stderr,
            )
        if self.rows and unparsed / self.rows > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Louisville: {unparsed:,} of {self.rows:,} species values have no "
                "' - ' binomial; Tree_Name_Genus_Species changed shape"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    louisville = Louisville()
    table = stream_to_table(
        iter_row_chunks(), louisville, keep=louisville.keep, label="Louisville OpenData"
    )
    louisville.report()
    table = validate_coordinates(table, city="Louisville", city_code="USLOU")
    table = enforce_tree_schema(
        table, city="Louisville", data_source="LOUISVILLE_OPENDATA"
    )
    emit(table)
