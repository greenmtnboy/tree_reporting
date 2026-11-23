#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bassendean's tree inventory, from the Town of Bassendean's ArcGIS Online.

No licence stated; displayed with attribution only, not licensed for reuse.
The item states no licence (`licenseInfo` and `accessInformation` empty), nor
do the public web map and app that show it ("Map of Trees within the Town",
item 0993b7a3f3eb4dde9e3625d98cdecd05), the organisation's portal, or the
Town's website (bassendean.wa.gov.au/disclaimer.aspx: an accuracy disclaimer
and "(c) Town of Bassendean").  data.wa.gov.au lists no Bassendean tree data.

Source: "Public_View_TOB_213_All_Trees_Corrected_July_2026_v1" (item
5973d2d7b04648a4bb000fe13cc4db74, owned by a Town of Bassendean staff account
on the Town's own organisation, "Removed fields for public consumption"),
layer 0, the "TOB Trees - Street Trees, Parks, Planned, Private Land" layer of
the Town's public tree map.  It supersedes the earlier public views on the
same organisation: `Public_TOB_208_Street_Trees_OldROAM_Jun20_2025_view`
(10,638 street trees from the retired ROAM asset system, frozen since
2026-05) and `Public_TOB_209_All_Trees_New_ESRI_Jul_2025_on_view` (453 trees
captured after the migration).  This layer merges both and renumbers them.

Measured 2026-11-22 (layer `dataLastEditDate` 2026-10-09): 13,894 points, all
with a geometry, spanning -31.9244 to -31.8873 and 115.9268 to 115.9626, well
inside AUBSD's CITY_BOUNDS and clear of Perth's (AUPER) box to the west.

**Rows kept.**  `tree_status` (a coded domain: Exists, Planned, Deceased,
Removed) decides: Exists 11,924, Planned 1,588, Deceased 193, Removed 189.
Planned sites (no tree yet) and Removed trees are dropped.  Deceased trees
are standing dead trees awaiting removal and stay, keeping their species
(the Fingal precedent).  44 Exists rows whose `tree_condition` is "Removed"
are dropped too: the Town recorded the tree as gone in the one column it
updates on inspection.  Everything dropped is counted in the log.

**The id is `GlobalID`** (`bsd-{guid}`): the layer's system GlobalID, non-null
and unique on all 13,894 rows.  `tree_number` was not used: it is unique but
for one value (13707: a Hakea and an Angophora 2 km apart) and, more to the
point, this layer renumbered every tree -- none of the ROAM layer's 10,638
`treenumber`s nor the TOB_209 layer's 453 `Tree_Number`s still sits at the
same place here.  OBJECTID has gaps (1..14,074 over 13,894 rows), so the
layer is edited in place and its GlobalIDs persist between edits.

Field choices:

  * `species` <- `botanical_name`, a coded domain whose codes are the names
    themselves: the scientific name, some with a common name in brackets
    ("Corymbia ficifolia (Red Flowering Gum)") which is cut, quoted cultivars
    (some unclosed: "Liquidambar formosa 'Red Ember", "Callistemon \"Little
    John"; closed here) and a trailing plant-variety-rights mark ("Agonis
    flexuosa 'After Dark' PVR"; cut).  `SPECIES_OVERRIDES` reads the names
    that are not a taxon as written; a formula hybrid naming two parents
    ("Brachychiton acer x bidwillii") is reduced to its genus as Perth does.
    `common_name` is not mapped (common names come from enrichment).
  * `diameter_at_breast_height` <- `trunk_diameter_DSH_mm` / 25.4:
    millimetres (median 60 among non-zero, 300 for a 4-6 m tree, 10-20 for a
    1-2 m sapling).  Zero is no measurement (nulled by the schema).  99 is a
    placeholder: 461 of its 494 rows come from one November 2023 inspection
    round in which it was entered for trees 3-6 m tall with 3-5 m crowns,
    while 98 and 101 occur twice and never (`PLACEHOLDER_DBH_MM`).  A few
    thousand-millimetre values on small trees are left to the shared guard.
  * `crown_width_m` <- `canopy_width_metre`, whole metres (median 2); zero
    is no measurement.  `height_metre` has no canonical column.
  * `plant_date` <- `date_planted` (a DateOnly field, "YYYY-MM-DD"), falling
    back to 1 January of `year_planted` where only the year is filled (no
    row today).  3,211 rows carry one, 2019-07-31 to 2026-07-22; each year's
    planting programme is recorded on 31 July of that year (943 trees on
    2020-07-31), which is year precision, not a load stamp, and is kept.
  * `latitude`/`longitude` from the point geometry, reprojected server-side
    from Web Mercator with `outSR=4326`.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    SHRUB_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

# Kept in step with bassendean_update_time.py.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/551UnqKK1GZeDKxQ/arcgis/rest/services/"
    "Public_View_TOB_213_All_Trees_Corrected_July_2026_v1/FeatureServer/0"
)
SOURCE_URL = LAYER.url

OUT_FIELDS = (
    "OBJECTID,GlobalID,tree_status,tree_condition,botanical_name,"
    "trunk_diameter_DSH_mm,canopy_width_metre,date_planted,year_planted"
)

ID_PREFIX = "bsd"

KEPT_STATUSES = frozenset({"Exists", "Deceased"})
REMOVED_CONDITIONS = frozenset({"removed"})

# The diameter entered for unmeasured trees in the November 2023 round.
PLACEHOLDER_DBH_MM = 99

# Refuse above this share of unreadable `date_planted` strings: the field
# changed format.
MAX_BAD_DATE_FRACTION = 0.01

# `botanical_name` values (after the bracket and PVR cleanup, lower-cased)
# that are not a taxon as written: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "tbc": (UNKNOWN_SPECIES, None),
    "other": (UNKNOWN_SPECIES, None),
    "various species": (UNKNOWN_SPECIES, None),
    "various native species": (UNKNOWN_SPECIES, None),
    # A grass, recorded as one "tree".
    "bamboo": (SHRUB_SPECIES, None),
    # Trade names written as an epithet.  'Torwood' is a garden hybrid
    # (E. torquata x E. woodwardii); 'Tuscarora' and 'Natchez' are
    # L. indica x L. fauriei crepe myrtles.
    "eucalyptus torwood": ("Eucalyptus", "Torwood"),
    "lagerstroemia tuscarora": ("Lagerstroemia", "Tuscarora"),
    "lagerstroemia natchez": ("Lagerstroemia", "Natchez"),
    # Subspecies written as cultivars.
    "eucalyptus camaldulensis 'camaldulensis'": ("Eucalyptus camaldulensis", None),
    "eucalyptus camaldulensis 'obtusa'": ("Eucalyptus camaldulensis", None),
    # Both words misspelt: the Chilean wine palm.
    "jubia chinensis": ("Jubaea chilensis", None),
}

_BRACKETS = re.compile(r"\s*\([^)]*\)")
_PVR = re.compile(r"\s+PVR$", re.IGNORECASE)

_counts = {
    "records": 0,
    "planned": 0,
    "removed": 0,
    "condition removed": 0,
    "other status": 0,
    "no id": 0,
    "dbh placeholder": 0,
    "dates": 0,
    "bad dates": 0,
}


def clean_name(value) -> str | None:
    """`botanical_name` without its bracketed common name or PVR mark, quotes closed."""
    if value is None:
        return None
    text = _BRACKETS.sub("", " ".join(str(value).split()))
    text = _PVR.sub("", text).replace('"', "'").strip()
    if text.count("'") % 2:
        text += "'"
    return text or None


def species_of(value) -> tuple[str | None, str | None]:
    """(species, cultivar) for one `botanical_name`; cultivar None = let the schema parse it."""
    text = clean_name(value)
    if text is None:
        return None, None
    override = SPECIES_OVERRIDES.get(text.lower())
    if override is not None:
        return override
    tokens = text.split()
    # A formula hybrid, Genus epithet x epithet: a cross of two named parents.
    if (
        len(tokens) == 4
        and tokens[2].lower() in ("x", "×")
        and tokens[1].islower()
        and tokens[3].islower()
    ):
        return tokens[0], None
    return text, None


def plant_date_of(rec: dict) -> date | None:
    raw = rec.get("date_planted")
    if raw:
        _counts["dates"] += 1
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            _counts["bad dates"] += 1
            return None
    year = rec.get("year_planted")
    return parse_plant_date_year(year) if year else None


def dbh_inches(value) -> float | None:
    if value is None:
        return None
    if int(value) == PLACEHOLDER_DBH_MM:
        _counts["dbh placeholder"] += 1
        return None
    return float(value) / 25.4


def keep_record(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    _counts["records"] += 1
    status = attrs.get("tree_status")
    if status not in KEPT_STATUSES:
        key = {"Planned": "planned", "Removed": "removed"}.get(status, "other status")
        _counts[key] += 1
        return False
    if str(attrs.get("tree_condition") or "").strip().lower() in REMOVED_CONDITIONS:
        _counts["condition removed"] += 1
        return False
    if not str(attrs.get("GlobalID") or "").strip():
        _counts["no id"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page at a time, with WGS84 point geometry."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    )


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in rows:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        guid = str(rec["GlobalID"]).strip().strip("{}").lower()
        name, selection = species_of(rec.get("botanical_name"))
        canopy = rec.get("canopy_width_metre")

        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(selection)
        plant_date.append(plant_date_of(rec))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_inches(rec.get("trunk_diameter_DSH_mm")))
        crown.append(float(canopy) if canopy else None)

    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUBSD"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


def report_dropped() -> None:
    for what in ("planned", "removed", "condition removed", "other status", "no id"):
        if _counts[what]:
            print(
                f"Bassendean ingest: dropped {_counts[what]} {what} record(s) "
                f"of {_counts['records']}",
                file=sys.stderr,
            )
    if _counts["dbh placeholder"]:
        print(
            f"Bassendean ingest: nulled {_counts['dbh placeholder']} placeholder "
            f"diameter(s) of {PLACEHOLDER_DBH_MM} mm",
            file=sys.stderr,
        )
    if _counts["dates"] and _counts["bad dates"] / _counts["dates"] > MAX_BAD_DATE_FRACTION:
        raise RuntimeError(
            f"Bassendean ingest: {_counts['bad dates']} of {_counts['dates']} "
            "date_planted values are unreadable; the field changed format"
        )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=keep_record, label="Bassendean OpenData"
    )
    report_dropped()
    table = validate_coordinates(table, city="Bassendean", city_code="AUBSD")
    table = enforce_tree_schema(
        table, city="Bassendean", data_source="BASSENDEAN_OPENDATA"
    )
    emit(table)
