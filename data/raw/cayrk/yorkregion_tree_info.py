#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""York Region's (Ontario) street trees, from the Regional Municipality of York.

Source: "Street Trees" (ArcGIS item 293d80c24bf54a4f8ab66bddaeaab184, owner
`YorkMunicipalGovt`), listed on the Region's open data hub
(insights-york.opendata.arcgis.com, linked from york.ca/opendata) and served
from the Region's own ArcGIS Server as `OpenData/Biodiversity/MapServer/0`,
stored in UTM 17N (EPSG 26917) and reprojected server-side.  82,444 rows on
2026-11-23: "street trees within regional road right-of-ways that the Region
owns and/or maintains" -- all Regional trees in urban areas, partial in rural
ones -- across all nine local municipalities (Vaughan 20k, Markham 17k,
Richmond Hill 9.5k, Whitchurch-Stouffville 8.3k, East Gwillimbury, King,
Aurora, Georgina, Newmarket).  The local municipalities' own street trees are
not in it.  A FeatureServer twin (`.../Biodiversity/FeatureServer/0`) serves
the same rows; neither publishes `editingInfo`.  maxRecordCount 2000, with
pagination.

LICENCE: York Region Open Data Licence v1.0 (adapted from the UK OGL).  The
item's licence field reads 'Please refer to "York Region Open Data Licence",
searchable on this website'; the licence is the Region's own item
78cc02388af248c0b7a30eda6adfade0 ("York Region Open Data Licence",
https://www.arcgis.com/sharing/rest/content/items/78cc02388af248c0b7a30eda6adfade0/data),
which grants "a worldwide, royalty-free, perpetual, non-exclusive licence" to
"Copy, publish, distribute and transmit the Information", adapt it and use it
commercially.  Credit is optional; the statement it gives is "Contains public
sector information made available under The Regional Municipality of York's
Open Data Licence".

**The id is `TREEID`** (`yorkregion-{TREEID}`), the Region's asset number:
non-null on all 82,444 rows and unique (a grouped count `HAVING COUNT > 1`
returns nothing), sparse over 1..9,030,579.  There is no GlobalID, and
`OBJECTID` runs 1..82,444 without a gap -- a layer reloaded whole -- so it is
not used.

**`SPECIES` is the Latin name with the cultivar quoted** (`Syringa reticulata
'Ivory Silk'`; `SPECIESID` is its code, `COMMONNAME` the common name, which
is not mapped).  Clean-ups here are typing, not taxonomy: curly and double
quotes, a cultivar quote glued to the epithet (`grandifolia'robin hill'`), and
a handful of values in `SPECIES_FIXES` (a cultivar typed as the epithet,
`Ulmus Triumph`; `Amelanchier grandifolia'robin hill'`, the hybrid
serviceberry; `Virginiana Shubert`, two Schubert chokecherries; `Cornus
'pagoda'`, the pagoda dogwood).  The Accolade-group elms the Region files
under one parent (`Ulmus japonica 'Morton'`, `Ulmus carpinifolia 'Accolade'`,
`Ulmus Japonica x Wilsoniana 'Morton'`, `Ulmus carpinifolia 'Frontier'`) are
interspecific crosses and publish as the genus with the cultivar, as Oakville
and the shared English table file them.  Misspellings (`Gleditisia`,
`triocanthos`, `Cercidiphylum`, `gradidentata`, `Poulus`, `bignonoides`) are
left for the shared misspelling table.

**`CURRENTDBH` is a whole number of centimetres, stored as text** (median
~12; 5 on 19k rows, the caliper of a new planting; max 150).  A value that
does not parse is counted, and the run refuses above 1%.

**`YEARPLANTED` is a year** (published as 1 January).  `9999` (208 rows) is
the not-recorded code and is nulled; future years are nulled by the schema.
2001 is the commonest year (4.5k rows, with a wider DBH spread than its
neighbours), which may be a default for trees of unknown age at the first
inventory, but it is not a single stamped value and is kept.

**Every row is a tree**: `STATUS` is `Active` on all of them, and there are
no stump, vacant or planting-site rows.  `CURRENTTREEHEALTH = 6 - Dead`
(1.5k) stays (`Dead` is a tree; the species is kept).  `TREEHEIGHT` is a band
and `OWNERSHIP` / `RDSECTYPE` have no canonical column.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features

LAYER = FeatureLayer(
    "https://ww8.yorkmaps.ca/arcgis/rest/services/OpenData/Biodiversity/MapServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,TREEID,SPECIES,YEARPLANTED,CURRENTDBH"

ID_PREFIX = "yorkregion"

# `YEARPLANTED` code for "not recorded".
PLACEHOLDER_YEAR = 9999

# Refuse a run where more than this share of non-empty DBH values is unreadable.
MAX_UNREADABLE_DBH_SHARE = 0.01

# Whole values (after quote clean-up, lowercased) -> the name to publish.
SPECIES_FIXES: dict[str, str] = {
    # A cultivar typed as the epithet; the Triumph elm is a hybrid.
    "ulmus triumph": "Ulmus 'Triumph'",
    # Genus missing, cultivar typed bare (COMMONNAME: Hedge Maple, a mispick;
    # `virginiana` + `Shubert` is the Schubert chokecherry).
    "virginiana shubert": "Prunus virginiana 'Schubert'",
    # The pagoda dogwood (COMMONNAME `Pagoda Dogwood Tree`).
    "cornus 'pagoda'": "Cornus alternifolia",
    # The Accolade-group elms: interspecific crosses (U. davidiana var.
    # japonica x U. wilsoniana; Frontier is U. minor x U. parvifolia), filed
    # under one parent.  The genus with the cultivar, as Oakville does.
    "ulmus japonica 'morton'": "Ulmus 'Morton'",
    "ulmus japonica x wilsoniana 'morton'": "Ulmus 'Morton'",
    "ulmus carpinifolia 'accolade'": "Ulmus 'Accolade'",
    "ulmus carpinifolia 'frontier'": "Ulmus 'Frontier'",
    # Robin Hill is a cultivar of the hybrid serviceberry (A. x grandiflora);
    # `grandifolia` is a typo for the epithet and the hybrid sign is dropped.
    "amelanchier grandifolia 'robin hill'": "Amelanchier x grandiflora 'Robin Hill'",
    # A cultivar written after the epithet with a botanical rank word.
    "salix alba var. vitellina cv. pendula": "Salix alba 'Pendula'",
}

DROPPED: dict[str, int] = {"no TREEID": 0, "no geometry": 0}
COUNTS: dict[str, int] = {
    "species fixed": 0,
    "placeholder year nulled": 0,
    "dbh read": 0,
    "dbh unreadable": 0,
}
UNREADABLE_DBH: dict[str, int] = {}


def scientific_name(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("‘", "'").replace("’", "'").replace('"', "'")
    text = " ".join(text.split())
    # `grandifolia'robin hill'`: a cultivar quote glued to the epithet.
    text = re.sub(r"(\w)'(\w)", r"\1 '\2", text)
    if not text:
        return None
    fixed = SPECIES_FIXES.get(text.lower())
    if fixed:
        COUNTS["species fixed"] += 1
        return fixed
    # `Acer freemanii 'Firefall'`: the hybrid sign dropped.
    if text.lower().startswith("acer freemanii"):
        COUNTS["species fixed"] += 1
        text = "Acer x freemanii" + text[len("acer freemanii"):]
    return text


def diameter_inches(value) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        cm = float(text)
    except ValueError:
        COUNTS["dbh unreadable"] += 1
        UNREADABLE_DBH[text] = UNREADABLE_DBH.get(text, 0) + 1
        return None
    COUNTS["dbh read"] += 1
    return cm_to_inches(cm) if cm > 0 else None


def plant_date(value) -> date | None:
    if value is None:
        return None
    year = int(value)
    if year == PLACEHOLDER_YEAR:
        COUNTS["placeholder year nulled"] += 1
        return None
    return date(year, 1, 1)


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if rec.get("TREEID") is None:
        DROPPED["no TREEID"] += 1
        return False
    lat, _ = esri_point(feature.get("geometry"))
    if lat is None:
        DROPPED["no geometry"] += 1
        return False
    return True


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"{ID_PREFIX}-{int(rec['TREEID'])}")
        species.append(scientific_name(rec.get("SPECIES")))
        planted.append(plant_date(rec.get("YEARPLANTED")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("CURRENTDBH")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAYRK"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def iter_pages():
    yield from iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def check_dbh_readable() -> None:
    attempted = COUNTS["dbh read"] + COUNTS["dbh unreadable"]
    unreadable = COUNTS["dbh unreadable"]
    if attempted and unreadable / attempted > MAX_UNREADABLE_DBH_SHARE:
        worst = sorted(UNREADABLE_DBH.items(), key=lambda kv: -kv[1])[:10]
        raise RuntimeError(
            f"York Region: {unreadable} of {attempted} DBH values unreadable "
            f"(over {MAX_UNREADABLE_DBH_SHARE:.0%}); CURRENTDBH changed? {worst}"
        )


if __name__ == "__main__":
    table = stream_to_table(
        iter_pages(), transform, keep=keep, label="York Region OpenData"
    )
    check_dbh_readable()
    print(f"York Region: dropped {DROPPED}; {COUNTS}", file=sys.stderr)
    table = validate_coordinates(table, city="York Region", city_code="CAYRK")
    table = enforce_tree_schema(
        table, city="York Region", data_source="YORK_REGION_OPENDATA"
    )
    emit(table)
