#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Los Angeles's street-tree inventory, from the Bureau of Street Services.

Source: "Trees (Bureau of Street Services)" on the LA GeoHub, ArcGIS item
266c6255b1fc4ae8b8f100d8696e1fa4, a hosted feature layer of 635,558 points.

**Why this layer and not data.lacity.org's `vt5t-mscf`.**  The Socrata dataset
this ingest used to read is "Street Tree Inventory - 1990s", converted from an
Access database in 2016, and its `x`/`y` columns are truncated: at most three
decimals of longitude (~90 m) on the `original` half and as few as one or two
on the `bayarea` half (~1 km).  Its 545,396 in-bounds rows sat on only 161,439
distinct points, so most of LA's trees were stacked on a coarse grid.  This
layer has one distinct survey-grade point per feature (635,558 of 635,558), is
twenty years newer (last edited 2020-11-19) and drops every placeholder
coordinate the old one carried.  Its IDs are a different numbering -- old
`trees_id` 224036 and new `TreeID` 224036 are different trees a city apart --
so the ids take a new `laxb-` prefix rather than reusing `lax-`: an old deep
link or report must not resolve to an unrelated tree.

**What the layer does not carry.**  No diameter and no date: the attribute
table is `TreeID`, `Type` and a `TOOLTIP` string.  The old source's DBH ranges
and its 1981-1999 inventory dates (which this ingest published as
`plant_date`) are gone; both were a quarter-century stale.

**Species lives in the tooltip.**  `TOOLTIP` is
`Tree ID: n\\nLocation: ...\\nSpecies: COMMON\\nBotanical Name: Binomial`, with
a literal backslash-n between lines.  The botanical name is read; the common
name is not needed (common names come from enrichment).

**`Type` and the botanical name decide what is a tree.**  Type 1 `Tree`
(544,739) and 2 `Tree in Tree Well` (79,654) are read; 3 `Tree Well Only`
(11,157) and 4 `Tree Planting Location` (8) are not trees and are filtered
server-side.  Within the trees, the vacant-site and stump placeholders
(`Vacant - OK to plant`, `VACANT/INADEQUATE SPACING`, `Vacant well`, `Stump`,
... ~37k rows) are dropped, while `Dead tree` (~1.4k) stays as a standing tree
of unknown species, as it does for Burlington and Halifax.  `Not Specified`
(~183k) is a tree whose species was not recorded and takes the sentinel.

The layer is marked "historical, not actively maintained", so the freshness
probe reads `editingInfo` and will simply never move; that is the honest
answer for a frozen inventory.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services/"
    "Trees_Data_Bureau_of_Street_Services/FeatureServer/0",
    timeout=300,
)

# 1 = Tree, 2 = Tree in Tree Well.  3 (Tree Well Only) and 4 (Tree Planting
# Location) are sites, not trees.
TREE_TYPES = (1, 2)
WHERE = f"Type IN ({', '.join(str(t) for t in TREE_TYPES)})"

# Casefolded prefixes of a botanical name that means "no tree stands here".
NOT_A_TREE_PREFIXES = ("vacant", "stump", "for unknown street name")
# A tree stands here but its species is not known.
UNKNOWN_SPECIES_NAMES = {"not specified", "dead tree"}
# Cultivar rows abbreviate the epithet (`Lagerstroemia i. 'Rosea'`, 2,837
# rows), which the central cleanup would cut back to the genus.  These are the
# only abbreviated forms in the layer.
ABBREVIATED_EPITHETS = {
    "lagerstroemia i.": "Lagerstroemia indica",
    "podocarpus m.": "Podocarpus macrophyllus",
}


def parse_tooltip(tooltip: str | None) -> dict[str, str]:
    """The tooltip's `Label: value` lines as a dict.

    The layer stores the line break as a literal backslash-n; a real newline is
    accepted too in case the layer is ever republished with one.
    """
    fields: dict[str, str] = {}
    if not tooltip:
        return fields
    for line in tooltip.replace("\\n", "\n").split("\n"):
        label, sep, value = line.partition(":")
        if sep:
            fields[label.strip()] = value.strip()
    return fields


def botanical_name(tooltip: str | None) -> str | None:
    return parse_tooltip(tooltip).get("Botanical Name") or None


def is_tree(tooltip: str | None) -> bool:
    name = (botanical_name(tooltip) or "").casefold()
    return not name.startswith(NOT_A_TREE_PREFIXES)


def normalize_botanical(value: str | None) -> str | None:
    if not value or value.casefold() in UNKNOWN_SPECIES_NAMES:
        return None
    for abbreviated, full in ABBREVIATED_EPITHETS.items():
        if value.casefold().startswith(abbreviated + " "):
            value = full + value[len(abbreviated):]
            break
    return normalize_species(value)


def iter_row_chunks():
    """One ArcGIS page at a time; the layer's maxRecordCount is 1000."""
    return iter_features(
        LAYER,
        out_fields="TreeID,Type,TOOLTIP",
        where=WHERE,
        return_geometry=True,
        out_sr=4326,
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = rec.get("TreeID")
        tree_id.append(f"laxb-{raw_id}" if raw_id not in (None, "") else None)
        species.append(normalize_botanical(botanical_name(rec.get("TOOLTIP"))))
        lat, lon = esri_point(feature.get("geometry"))
        latitude.append(lat)
        longitude.append(lon)

    n = len(tree_id)
    return pa.table({
        'tree_id': pa.array(tree_id, type=pa.string()),
        'city': pa.array(['USLAX'] * n, type=pa.string()),
        'species': pa.array(species, type=pa.string()),
        'latitude': pa.array(latitude, type=pa.float64()),
        'longitude': pa.array(longitude, type=pa.float64()),
    })


if __name__ == '__main__':
    table = stream_to_table(
        iter_row_chunks(),
        transform,
        keep=lambda f: is_tree((f.get("attributes") or {}).get("TOOLTIP")),
        label='Los Angeles ingest',
    )
    table = validate_coordinates(table, city='Los Angeles', city_code='USLAX')
    table = enforce_tree_schema(table, city='Los Angeles', data_source="LOSANGELES_OPENDATA")
    emit(table)
