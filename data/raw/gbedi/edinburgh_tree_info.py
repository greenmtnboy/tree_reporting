#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Edinburgh's council-owned trees, from the City of Edinburgh Council.

Source: "Trees" (ArcGIS Online item 6c224ada230b4d349cfd739015c7846d, owner
`cec_arnabsaha` in the City of Edinburgh Council organisation, listed on the
council's open data hub data.edinburghcouncilmaps.info), the hosted layer
`Trees/FeatureServer/41`: "Trees within the Edinburgh Council boundary
area".  The council's own map server publishes the same table as
`Misc/INSPIRE/MapServer/41` on edinburghcouncilmaps.info (same 50,400
`PrimaryKey`s, every attribute identical, checked 2026-11-20); the hosted copy
is read because it carries the item, its licence and an `editingInfo` stamp.
The item's licence: "This information is supplied under the Open Government
License v3.0 ... requires the following attribution statement: Copyright City
of Edinburgh Council, contains Ordnance Survey data (c) Crown copyright and
database right".

**The inventory is an old snapshot.**  50,400 rows on 2026-11-20, the same
count Esri UK's 2019 demo copy holds; the council re-hosted it in June 2024
(the layer's `lastEditDate`).  `Ward` uses the 2007-2017 ward names
(`Meadows/Morningside`), so the survey predates the 2017 ward review.  There
is no date column.

**`PrimaryKey` is the key** (`edinburgh-{PrimaryKey}`, e.g. `Sgl/18 (046468)`:
record type, site sequence, asset number), non-null and distinct on all 50,400
rows (also case-insensitively; checked over the whole layer on 2026-11-20).
The layer has no GlobalID and `OBJECTID` is a load counter.  A row without one
is dropped with a logged count.

**Location is the geometry**, stored in British National Grid and read
reprojected to WGS84 by the server (within 0.7 m of pyproj's EPSG:27700
transform of `Easting`/`Northing`, which are whole metres, over a 101-row
sample).  389 rows share a point with another row; each is its own record.

**Not every row is a tree.**  `LatinName` `'Vacant` (34, "Vacant tree pit")
and `'New tree site` (24, "Suitable location") are sites, and the ten `Hdg/`
keys ("hedge" records) are all stands of invasive weeds -- Japanese
knotweed, giant hogweed, Himalayan balsam -- dropped both by name and by
key, so a future hedge is dropped too.  Rows with no species and nothing else
recorded (no height, spread, diameter or age class; 210) are dropped as
empty.  `Grp/` keys (7) are tree groups, published as one point each.

**Species is `LatinName`**, scientific (`Acer pseudoplatanus`, `Prunus
spp.`), with a quoted cultivar (`Prunus 'Kanzan'`, `Pinus nigra var
'Nigra'`) split by `split_species`; `TAXA` fixes the source spellings that
would otherwise mis-parse (`Prunus kanzan`, `Acer psuedoplatanus
'Atropurpureum'`, `Crataegus oxycantha 'Paul Scarlet'`).  The placeholders,
whose values begin with a stray quote, are `'Unknown conifer'` (Unknown
conifer), `'Unlisted` ("Species not in list") and `'Mix Broad` ("Mixed
Broadleaves"), both Unknown.  `CommonName` is not mapped.

**DBH is `DiameterAtBreastHeight`, a 10 cm band of diameter** (`10 - 20` ...
`80 - 90`, `90 +`).  Diameter, not girth: the median band rises from 10-20 cm
on juvenile trees to 30-40 on middle-aged and 40-50 on mature ones.  Each
band publishes its midpoint and the open-topped `90 +` its bound plus half a
band (95 cm), converted to inches.  The `60 - 70` band is overrepresented on
trees under 5 m (217 rows, against 39 at `50 - 60` and 7 at `70 - 80`): a
miscoded small band, nulled on those rows.  More than 1% of labels unreadable
refuses the build (three rows carry an age class in the column).

**Crown width is `Spread`**, a 3 m band (`Up to 3 meters` ... `Over 12
meters`), published as its midpoint, `Over 12` as 13.5 m, under the same
guard.  `Height` is a band with no canonical column and `AgeGroup` a life
stage, not a planting date; there is no planting date.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_CONIFER_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services-eu1.arcgis.com/FgpikkYuSUOuITxp/arcgis/rest/services/"
    "Trees/FeatureServer/41",
    timeout=180,
)

OUT_FIELDS = "OBJECTID,PrimaryKey,LatinName,Height,Spread,AgeGroup,DiameterAtBreastHeight"

# `LatinName` values (whitespace collapsed) that record a site or a weed.
NOT_A_TREE = frozenset(
    {
        "'Vacant",
        "'New tree site",
        "Fallopia japonica",
        "Heracleum mantegazzianum",
        "Impatiens glandulifera",
    }
)

# `PrimaryKey` record type for a hedge.
HEDGE_PREFIX = "Hdg/"

# `LatinName` (whitespace collapsed) -> (species, cultivar): placeholders and
# source spellings the general split would get wrong.
TAXA: dict[str, tuple[str, str | None]] = {
    "'Unknown conifer'": (UNKNOWN_CONIFER_SPECIES, None),
    "'Unlisted": (UNKNOWN_SPECIES, None),
    "'Mix Broad": (UNKNOWN_SPECIES, None),
    # Cultivars written in the epithet's place.
    "Prunus kanzan": ("Prunus", "Kanzan"),
    "Prunus shirofugen": ("Prunus", "Shirofugen"),
    "Prunus umineko": ("Prunus", "Umineko"),
    "Prunus schmitti": ("Prunus x schmittii", None),
    # `var 'Nigra'` is the variety, not a cultivar.
    "Pinus nigra var 'Nigra'": ("Pinus nigra", None),
    "Chamaecyparis lawsoniana Elwoods Pillar": ("Chamaecyparis lawsoniana", "Ellwood's Pillar"),
    # Misspelt binomials carrying a cultivar.
    "Acer psuedoplatanus 'Atropurpureum'": ("Acer pseudoplatanus", "Atropurpureum"),
    "Gleditisia triacanthos 'Sunburst'": ("Gleditsia triacanthos", "Sunburst"),
    # `oxyacantha` is a rejected name; these are C. laevigata's cultivars.
    "Crataegus oxycantha 'Paul Scarlet'": ("Crataegus laevigata", "Paul's Scarlet"),
    "Crataegus oxycantha 'Rosea Flore Pleno'": ("Crataegus laevigata", "Rosea Flore Pleno"),
}

_QUOTES = "'\"‘’“”"
_QUOTED = re.compile(rf"^(?P<taxon>[^{_QUOTES}]*?)\s*[{_QUOTES}](?P<cultivar>.+?)[{_QUOTES}]?\s*$")
_HYBRID = frozenset({"x", "X", "×"})

# Diameter bands, cm -> midpoint.  `90 +` is open-topped: bound + half a band.
DBH_BAND_CM: dict[str, float] = {
    "10 - 20": 15.0,
    "20 - 30": 25.0,
    "30 - 40": 35.0,
    "40 - 50": 45.0,
    "50 - 60": 55.0,
    "60 - 70": 65.0,
    "70 - 80": 75.0,
    "80 - 90": 85.0,
    "90 +": 95.0,
}

# Crown-spread bands, metres -> midpoint.  `Over 12 meters` is open-topped.
CROWN_BAND_M: dict[str, float] = {
    "Up to 3 meters": 1.5,
    "3 to 6 meters": 4.5,
    "6 to 9 meters": 7.5,
    "9 to 12 meters": 10.5,
    "Over 12 meters": 13.5,
}

# The miscoded band on small trees (see the module docstring).
SMALL_TREE_HEIGHT = "Up to 5 meters"
MISCODED_SMALL_BAND = "60 - 70"

# Columns whose presence says a row with no species is still a recorded tree.
DESCRIPTIVE_FIELDS = ("Height", "Spread", "AgeGroup", "DiameterAtBreastHeight")

MAX_UNREADABLE_SHARE = 0.01


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def split_species(raw) -> tuple[str | None, str | None]:
    """`(species string, cultivar)` from the layer's `LatinName`."""
    text = clean(raw)
    if text is None:
        return UNKNOWN_SPECIES, None
    if text in TAXA:
        return TAXA[text]
    match = _QUOTED.match(text)
    if match and match["taxon"]:
        return clean(match["taxon"]), clean(match["cultivar"].strip(_QUOTES))
    words = text.split()
    # `Fagus sylvatica Asplenifolia`: an unquoted capitalised name after the binomial.
    epithet = 2 if len(words) >= 3 and words[1] in _HYBRID else 1
    if (
        len(words) > epithet + 1
        and words[epithet].islower()
        and words[epithet + 1][:1].isupper()
        and words[epithet + 1].replace("-", "").isalpha()
    ):
        return " ".join(words[: epithet + 1]), " ".join(words[epithet + 1:])
    return text, None


_counts: dict[str, int] = {
    "no PrimaryKey": 0,
    "not a tree (vacant pit, new tree site, invasive weed)": 0,
    "hedge": 0,
    "empty record (no species, no measurements)": 0,
    "unreadable DiameterAtBreastHeight": 0,
    "unreadable Spread": 0,
    "miscoded 60-70 cm band on a tree under 5 m": 0,
}
_rows_read = 0


def band(value, bands: dict[str, float], field: str) -> float | None:
    text = clean(value)
    if text is None:
        return None
    mid = bands.get(text)
    if mid is None:
        _counts[f"unreadable {field}"] += 1
    return mid


def is_tree(feature: dict) -> bool:
    global _rows_read
    _rows_read += 1
    rec = feature.get("attributes") or {}
    name = clean(rec.get("LatinName"))
    if name in NOT_A_TREE:
        _counts["not a tree (vacant pit, new tree site, invasive weed)"] += 1
        return False
    if (clean(rec.get("PrimaryKey")) or "").startswith(HEDGE_PREFIX):
        _counts["hedge"] += 1
        return False
    if name is None and not any(clean(rec.get(f)) for f in DESCRIPTIVE_FIELDS):
        _counts["empty record (no species, no measurements)"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = clean(rec.get("PrimaryKey"))
        if raw_id is None:
            _counts["no PrimaryKey"] += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))
        name, selection = split_species(rec.get("LatinName"))

        diameter_cm = band(rec.get("DiameterAtBreastHeight"), DBH_BAND_CM, "DiameterAtBreastHeight")
        if (
            clean(rec.get("DiameterAtBreastHeight")) == MISCODED_SMALL_BAND
            and clean(rec.get("Height")) == SMALL_TREE_HEIGHT
        ):
            _counts["miscoded 60-70 cm band on a tree under 5 m"] += 1
            diameter_cm = None

        tree_id.append(f"edinburgh-{raw_id}")
        species.append(name)
        cultivar.append(selection)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_cm / 2.54 if diameter_cm else None)
        crown.append(band(rec.get("Spread"), CROWN_BAND_M, "Spread"))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["GBEDI"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * len(tree_id), type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def report() -> None:
    for reason, n in _counts.items():
        if n and not reason.startswith("unreadable"):
            verb = "nulled DBH on" if reason.startswith("miscoded") else "dropped"
            print(f"Edinburgh: {verb} {n:,} rows: {reason}", file=sys.stderr)
    for field in ("DiameterAtBreastHeight", "Spread"):
        bad = _counts[f"unreadable {field}"]
        if bad:
            print(f"Edinburgh: {bad:,} unreadable {field} values nulled", file=sys.stderr)
        if _rows_read and bad / _rows_read > MAX_UNREADABLE_SHARE:
            raise RuntimeError(
                f"Edinburgh: {bad:,} of {_rows_read:,} {field} values are unreadable; "
                "the bands changed"
            )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=is_tree, label="Edinburgh OpenData")
    report()
    table = validate_coordinates(table, city="Edinburgh", city_code="GBEDI")
    table = enforce_tree_schema(table, city="Edinburgh", data_source="EDINBURGH_OPENDATA")
    emit(table)
