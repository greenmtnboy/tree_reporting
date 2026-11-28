#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""St. Louis Park, Minnesota's city-managed trees.

Licence: no licence stated; displayed with attribution only, not licensed for
reuse.  The ArcGIS item (d8e68ae495d24f06a9adc233bf6e4c55, "Trees", owner
`SLP.GIS`, the City of St. Louis Park's ArcGIS Online organisation) has empty
`licenseInfo` and `accessInformation`; the organisation runs no open-data hub
and the City's tree page states no terms.  Its other items carry only an
"as-is ... no claim made to its accuracy" disclaimer, which is not a licence.

**Source: the "Trees" layer of the City's `AGO/Parks` MapServer** ("City
managed trees in the boulevard and in city parks"), 24,045 rows on
2026-11-27.  The City's own server (services.slpgis.org) answers that service
only with a token; the item's public path is the ArcGIS Online service proxy
(`utility.arcgis.com/usrsvcs/servers/{item}/...`), which is what is read.  The
service's other layer is community gardens.  The organisation's other tree
items are planting and pruning work lists (a season's contract, a grant's
removals), each a subset of this inventory, so they are not read.

**Removed trees are dropped.**  `RETIRED` is the date a tree was taken out
(153 rows, 2025-2026); the layer keeps them.  `LOCATION` (`BLVD` boulevard,
`PARK`, `NAT` natural area, `Private`: a tree the City records on private
land, 147 rows) is kept whatever it says, and a null too.

**The id is `OBJECTID`** (`slp-{OBJECTID}`), the runbook's last resort but the
right one here: the layer has no GlobalID (`globalIdField` is empty) and no
asset number, and it is a versioned enterprise geodatabase (`hasVersionedData`)
edited in place -- 24,045 rows with OBJECTIDs running to 1,329,520, the gaps
of years of edits left open (17.8k under 100,000 and the rest scattered), not
the dense 1..N of a reloaded layer.  It is unique and non-null over the whole
layer; `enforce_tree_schema` refuses a repeat.  188 positions hold two or more
rows (377 rows); they keep their own ids.

**Species is `GENUS` + `SPECIES`** (both coded-value domains whose codes are
their names): a genus and an epithet with any cultivar in quotes
(`americana 'Boulevard'`), split by the schema.  The epithet field is
hand-typed and needs cleaning before it is a name: the curly quotes of a
pasted cultivar arrive as U+FFFD (`americana �New Harmony�`), a
non-breaking space sits in two, a cultivar's opening quote is missing on one
(`regal'`), and two carry a note (`'Midwest' (RFM-38)`, `'Midwest', aka
Prairie Stature'`).  `Other other` is not a name.  A handful of rows weld a
genus to another tree's epithet, and the City's own common name (`NAME`)
says which tree it is (`Acer americana 'Boulevard'` is `Linden, Boulevard`,
`Celtis canadensis` is `Redbud`); `GENUS_SLIPS` corrects those, and only
those.  Spelling (`pensylvanica`, `davidana`, ...) is left to the shared
`SPECIES_MISSPELLINGS`.  Where `GENUS` is empty but `NAME` is set (81 rows)
the name resolves through the shared English table (`Oak, White` ->
`Quercus alba`); 334 rows have neither and publish no species.

**`plant_date` is `YRPLANT`**, a small integer on 9,092 rows, dated 1
January.  Codes that are not years (0, 1, 1111, 2826, 9000, 9996-9999: about
1,090 rows) are nulled with a logged count, as is anything before 1850 or
after the current year.

No diameter, height or crown spread is recorded.
"""

import re
import sys
from collections import Counter
from datetime import date
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
    "https://utility.arcgis.com/usrsvcs/servers/d8e68ae495d24f06a9adc233bf6e4c55/"
    "rest/services/AGO/Parks/MapServer/1",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,NAME,GENUS,SPECIES,LOCATION,YRPLANT,RETIRED"

EARLIEST_PLANT_YEAR = 1850

# (GENUS, epithet) pairs that weld one tree's genus to another's epithet,
# corrected to the tree the row's own common name (`NAME`) names.
GENUS_SLIPS = {
    ("acer", "americana"): "Tilia americana",          # Linden, Boulevard
    ("celtis", "canadensis"): "Cercis canadensis",     # Redbud
    ("cercidiphyllum", "canadensis"): "Cercis canadensis",  # Redbud
    ("cercis", "occidentalis"): "Celtis occidentalis",  # Hackberry
    ("acer", "nigra"): "Acer negundo",                 # Boxelder
    ("thuja", "canadensis"): "Thuja occidentalis",     # Cedar, Northern White
    ("ostrya", "americana"): "Ostrya virginiana",      # Ironwood
    ("ostrya", "pensylvanica"): "Ostrya virginiana",   # Ironwood
    ("quercus", "macrophyllum"): "Quercus macrocarpa",  # Oak, Bur
    ("ulmus", "saccharum"): "Ulmus",                   # Elm, Accolade
}

NOT_A_NAME = frozenset({"other", "other other"})

QUOTE_JUNK = str.maketrans({"�": "'", "‘": "'", "’": "'", "\xa0": " "})
NOTE = re.compile(r"\s*(\([^)]*\)|,\s*aka\b.*)$", re.IGNORECASE)


def clean(value) -> str:
    return " ".join(str(value).translate(QUOTE_JUNK).split()) if value is not None else ""


def plant_year(value) -> int | None:
    if value is None:
        return None
    year = int(value)
    if EARLIEST_PLANT_YEAR <= year <= date.today().year:
        return year
    return None


def tidy_epithet(epithet: str) -> tuple[str, str]:
    """(epithet without cultivar, cultivar or '')."""
    epithet = NOTE.sub("", epithet).strip()
    if "'" not in epithet:
        return epithet, ""
    head, _, tail = epithet.partition("'")
    head, tail = head.strip(), tail.strip()
    if not tail:
        # `regal'`: the opening quote lost; the whole value is the cultivar.
        return "", head
    return head, tail.replace("'", "").strip()


def title_cultivar(cultivar: str) -> str:
    # `st. croix`, `valley forge`: typed in lower case; cultivar names are capitalised.
    if cultivar and cultivar == cultivar.lower():
        return " ".join(w[:1].upper() + w[1:] for w in cultivar.split())
    return cultivar


class StLouisPark:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.species_from: Counter[str] = Counter()
        self.slips: Counter[str] = Counter()
        self.bad_years: Counter[int] = Counter()

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        if rec.get("RETIRED") is not None:
            self.dropped["RETIRED (tree removed)"] += 1
            return False
        if rec.get("OBJECTID") is None:
            self.dropped["no OBJECTID"] += 1
            return False
        return True

    def species(self, rec: dict) -> str | None:
        genus = clean(rec.get("GENUS"))
        epithet = clean(rec.get("SPECIES"))
        if genus and f"{genus} {epithet}".strip().lower() not in NOT_A_NAME:
            name, cultivar = tidy_epithet(epithet)
            key = (genus.lower(), name.split(" ")[0].lower() if name else "")
            if key in GENUS_SLIPS:
                self.slips[f"{genus} {name} -> {GENUS_SLIPS[key]}"] += 1
                base = GENUS_SLIPS[key]
            else:
                base = " ".join(p for p in (genus.capitalize(), name.lower()) if p)
            self.species_from["GENUS + SPECIES"] += 1
            cultivar = title_cultivar(cultivar)
            return f"{base} '{cultivar}'" if cultivar else base
        common = clean(rec.get("NAME"))
        resolved = species_from_common_name(common) if common else None
        self.species_from[
            "NAME (shared English table)" if resolved else "none / unresolved"
        ] += 1
        return resolved

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"slp-{int(rec['OBJECTID'])}")
            species.append(self.species(rec))
            year = plant_year(rec.get("YRPLANT"))
            if year is None and rec.get("YRPLANT") is not None:
                self.bad_years[int(rec["YRPLANT"])] += 1
            plant_date.append(date(year, 1, 1) if year else None)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USSLP"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"St. Louis Park: dropped {n:,} rows: {reason}", file=sys.stderr)
        for source, n in sorted(self.species_from.items()):
            print(f"St. Louis Park: species from {source}: {n:,}", file=sys.stderr)
        for slip, n in sorted(self.slips.items()):
            print(f"St. Louis Park: genus slip {slip}: {n:,}", file=sys.stderr)
        if self.bad_years:
            print(
                f"St. Louis Park: nulled {sum(self.bad_years.values()):,} YRPLANT codes "
                f"that are not a year: {dict(sorted(self.bad_years.items()))}",
                file=sys.stderr,
            )


def iter_row_chunks():
    """One ArcGIS page at a time, geometry in WGS84."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by="OBJECTID"
    )


if __name__ == "__main__":
    slp = StLouisPark()
    table = stream_to_table(iter_row_chunks(), slp, keep=slp.keep, label="St. Louis Park OpenData")
    slp.report()
    table = validate_coordinates(table, city="St. Louis Park", city_code="USSLP")
    table = enforce_tree_schema(
        table, city="St. Louis Park", data_source="ST_LOUIS_PARK_OPENDATA"
    )
    emit(table)
