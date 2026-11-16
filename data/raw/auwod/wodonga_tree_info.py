#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Wodonga, VIC's street and reserve trees.

Source: the "Street and reserve trees" feature service (ArcGIS Online item
f6e5ca95559a4253bf7d25897bf8ed15, owner GIS_CityOfWodonga, CC BY 4.0), layer
0 `Trees` on services-ap1.arcgis.com.  The item describes itself as
"reprocessed data for the tree layer, updated daily ... only street trees or
trees within a reserve".  94,652 rows on 2026-11-16 (layer
`dataLastEditDate` 2026-11-15), every one a point and every one `spacestatus
= 'Existing tree'`.  Paging and the reprojection to WGS84 live in
`shared.platforms.arcgis`.

The council also publishes "Open trees" (item 9639883214ca483ab9f09d318059ca43,
430,936 rows), which adds trees "identified from LiDAR or other spatial
sources that may be located on land not managed by Council" and warns of
"duplicate features, vegetation that is not a single tree".  That is canopy
detection, not an inventory, so it is not read.

**`assetglobalid` is the key.**  The layer has no GlobalID of its own
(`globalIdField` is empty) and its OID, `tree_OBJECTID`, is renumbered by the
daily reprocessing at the publisher's discretion.  `assetglobalid` is the
GlobalID of the tree in the council's asset layer, carried through: 94,652
distinct lowercase GUIDs over 94,652 rows, none null.  The asset number
`assetid` is also unique, but one row carries `Eucalyptus ` in it, so it is a
typed-in field rather than a guaranteed one.  Prefixed `wod-`.

**`genus` + `species` are the scientific name**, the epithet capitalised
(`Eucalyptus` / `Camaldulensis`), combined with `normalize_species_parts`.
About 160 rows carry the whole name in `genus` and nothing in `species`
(`Eucalyptus spp\r`), which the same call reads.  Many cultivars are written
unquoted after the epithet (`Freemanii jeffersred`, `Indica natchez`,
`Parvifolia burnley select`); the shared rules would truncate those to the
species and lose the selection, so the trade names in `UNQUOTED_CULTIVARS`
are quoted first and survive as `cultivar`.  A handful of source errors are
corrected in `SOURCE_SPELLINGS` -- above all `Callistemon` / `Kings park
special` (1,469 rows), the cultivar 'Kings Park Special', which left alone
publishes as the chimera `Callistemon kings`.  30,499 rows (32%) record no
genus or species at all and 3,073 say `Unknown`/`Unknown`; both publish as
`Unknown`.  `commonname` is not mapped.

**`diameter` is not published.**  The field is aliased "Diameter (mm)" and is
filled on 18,253 rows, but 18,218 of those are integers spread flat across
0-50 (about 1,800 in every five-unit bin), with the same distribution for a
crape myrtle as for a river red gum and no correlation with height or canopy
(r = 0.003 and 0.009).  That is not a trunk measurement in any unit; the 35
values above 50 (red gums at 120-200, plausibly cm) are too few to stand on
their own.  `height` is `1.0` or `1.3` on 42,972 rows (1.3 m is breast
height, not a tree's height) and is not published either.  `canopy_cover` is
a LiDAR canopy *area* in m^2 on 13,324 rows, not a crown spread, so
`crown_width_m` is null.  The layer records no planting date (`inspectdate`
is an inspection; `age` is a class), so `plant_date` is an all-null
`date32`.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/w6r4LlwgJu8O0neQ/arcgis/rest/services/"
    "Street_and_reserve_trees/FeatureServer/0",
    timeout=180,
)

OUT_FIELDS = "assetglobalid,genus,species,spacestatus"
ORDER_BY = "tree_OBJECTID"

# The one site status the layer publishes today.  Anything else (a vacant
# site, a stump) is not a tree; the keep filter drops it with a count.
EXISTING_TREE = "existing tree"

# Exact source spellings (genus and species joined, whitespace collapsed),
# corrected before the shared rules see them.
SOURCE_SPELLINGS: dict[str, str] = {
    "Callistemon Kings park special": "Callistemon 'Kings Park Special'",
    "Callistemon ‘Kings park special’": "Callistemon 'Kings Park Special'",
    "Pyrau calleryana'chanticleer'": "Pyrus calleryana 'Chanticleer'",
    # A genus slip: the epithet and cultivar are the maple's.
    "Acacia Freemanii jeffersred": "Acer x freemanii 'Jeffersred'",
    "Fraxinus Pennsylvanica Çimmzam": "Fraxinus pennsylvanica 'Cimmzam'",
}

# Trade names the layer writes unquoted after the epithet.  Each is a
# registered cultivar of the species it follows in this layer; quoting it
# lets `enforce_tree_schema` move it to `cultivar` rather than drop it.
# Botanical ranks written the same way (`Eximia nana`, `Robur fastigiata`,
# `Citrinus x viminalis`) are not listed and are left to the shared rules.
UNQUOTED_CULTIVARS = frozenset(
    {
        "jeffersred", "natchez", "sioux", "tuscarora", "lipan", "zuni",
        "todd", "burnley select", "inspire", "sensation", "urbanite",
        "hannah ray", "winter glow", "luscious", "southworth dancer",
        "crimson sentry", "oakville highlight", "little gem", "teddy bear",
        "pringreen", "raywood", "scentuous", "korean sun", "swan hill",
        "elsrijk", "wildfire", "lutescens", "winter light",
    }
)


def raw_name(genus: str | None, species: str | None) -> str:
    """Genus and species joined, with the layer's stray CR/LF collapsed."""
    return " ".join(" ".join(part.split()) for part in (genus, species) if part)


def species_of(genus: str | None, species: str | None) -> str | None:
    """The scientific name, with unquoted cultivars quoted; None is `Unknown`."""
    text = raw_name(genus, species)
    if not text:
        return None
    text = SOURCE_SPELLINGS.get(text, text)
    tokens = text.split(" ")
    if len(tokens) > 2 and not any(q in text for q in "'‘’\""):
        tail = " ".join(tokens[2:])
        if tail.lower() in UNQUOTED_CULTIVARS:
            text = f"{tokens[0]} {tokens[1]} '{tail.title()}'"
    parts = text.split(" ", 1)
    return normalize_species_parts(parts[0], parts[1] if len(parts) > 1 else None)


def is_tree(feature: dict) -> bool:
    status = ((feature.get("attributes") or {}).get("spacestatus") or "").strip()
    return not status or status.lower() == EXISTING_TREE


def iter_row_chunks():
    """One ArcGIS page (1,000 features) at a time."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by=ORDER_BY
    )


MISSING_ID = 0


def transform(features: list[dict]) -> pa.Table:
    global MISSING_ID
    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        guid = (attrs.get("assetglobalid") or "").strip().strip("{}").lower()
        if not guid:
            MISSING_ID += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))

        tree_id.append(f"wod-{guid}")
        species.append(species_of(attrs.get("genus"), attrs.get("species")))
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["AUWOD"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=is_tree, label="Wodonga OpenData"
    )
    if MISSING_ID:
        print(f"Wodonga ingest: dropped {MISSING_ID} rows with no assetglobalid", file=sys.stderr)
    table = validate_coordinates(table, city="Wodonga", city_code="AUWOD")
    table = enforce_tree_schema(
        table, city="Wodonga", data_source="WODONGA_OPENDATA"
    )
    emit(table)
