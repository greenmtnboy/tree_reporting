#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Springfield, MO's tree inventory, from the City of Springfield's ArcGIS Online.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo` and `accessInformation` are both empty and
the Hub dataset's licence is "none".  The city's website use policy
(https://ecity.springfieldmo.gov/website-use-policy, "Use of Data") asks
anyone distributing data from its open-data systems to show its disclaimer
("This site provides data that has been modified for use from its original
source, www.springfieldmo.gov, the official website of the City of
Springfield, Missouri. ..."); that is a condition of use, not a licence.

Source: "Tree Inventory" (ArcGIS item 705ae4c71b014910a0617ba5c7b25b98, owner
`gis_cosmo`, "Point file of trees owned and maintained by the City of
Springfield"): the hosted layer `Tree_Inventory/FeatureServer/34`, stored in
Web Mercator and reprojected server-side.  26,375 rows on 2026-11-22.  Its
fields are `CONDITION`, `ADDRQUAL` (street address), `DIAM`, `COMMON_NAME` (a
bare small integer with no domain, not used) and `SCI_NAME`.

**The id is the rounded coordinate (`springfield-{lon:.7f}_{lat:.7f}`), as
for Spokane** (EXTENDING.md, step 1).  The layer has no GlobalID, no asset id
and no edit-date column; OBJECTID runs 1..26,375 without a gap, which is what
a wholesale reload looks like, so a reload may renumber every tree while a
position survives it.  9 points carried two kept rows each in November 2026
(18 rows); `drop_stacked_coordinates` drops every row at such a point,
because the id cannot tell them apart.

**`SCI_NAME` is a coded-value domain** (`QUERPALU` -> "Quercus palustris"),
read from the layer at run time.  The names are mostly binomials; some carry
a cultivar in plain words ("Acer rubrum october glory", "Malus spp.
prairefire", "Ilex x attenuata fosteri"), which `decode_species` splits into
`species` and `cultivar`.  `SPECIES_OVERRIDES` holds the codes whose name is
not a taxon as written.  A few names are misspelled ("Styrax japonicas",
"Tsuga candensis"); the shared misspelling table corrects those.  A code
outside the domain refuses the run past 1% of rows.  33 rows have no
`SCI_NAME` (all 1.5 in, condition Good: fresh plantings) and publish
`Unknown`.

**Stumps are dropped**: `CONDITION = S` (1,880 rows).  `CONDITION = D` (dead,
416) is a standing tree and stays.  The layer has no removed-tree or
planting-site rows.

**`DIAM` is DBH in inches** (median 7, 75th percentile 16, pin oaks and
silver maples in the 30s); zero is nulled by the schema.  There is no
planting date, crown spread or height.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    UNKNOWN_SPECIES,
    coordinate_key,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import (
    FeatureLayer,
    coded_value_domain,
    esri_point,
    iter_features,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/aOss8CrQf3pARS5q/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/34",
    timeout=180,
)

OUT_FIELDS = "CONDITION,DIAM,SCI_NAME"

# `CONDITION` codes that are not a tree.
STUMP_CONDITIONS = frozenset({"S"})

# Refuse a run where more than this share of rows carries a code outside the
# domain (a new code would otherwise publish as unknown trees).
MAX_UNDECODED_SHARE = 0.01

# `SCI_NAME` codes whose domain name is not a taxon as written:
# code -> (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str, str | None]] = {
    "UNKNTREE": (UNKNOWN_SPECIES, None),
    # Canopy-layer placeholders, not taxa (12 rows).
    "OVERSTORY": (UNKNOWN_SPECIES, None),
    "UNDERSTORY": (UNKNOWN_SPECIES, None),
    # Amur maple, filed under Tatarian maple as a subspecies.
    "ACERTATAGI": ("Acer ginnala", None),
    # Thornless honeylocust: a botanical variety, not a cultivar.
    "GLEDTRIAIN": ("Gleditsia triacanthos", None),
    # Red horsechestnut 'Briotii'.
    "AESCCARNBR": ("Aesculus x carnea", "Briotii"),
    # Hybrid holly and crabapple selections with no species epithet.
    "ILEXHYBRCJ": ("Ilex", "Christmas Jewel"),
    "MALUHYBRSS": ("Malus", "Spring Snow"),
    # Named "Taxus spp." under a Taxus x media code; the name is what we keep.
    "TAXUMEDIDE": ("Taxus", None),
    # 'Frontier' is a hybrid elm cultivar, not an epithet (0 rows today).
    "ULMUAMERFR": ("Ulmus", "Frontier"),
}


def split_name(name: str) -> tuple[str | None, str | None]:
    """A domain name as `(species, cultivar)`.

    Examples:
        "Quercus palustris"                -> ("Quercus palustris", None)
        "Acer rubrum october glory"        -> ("Acer rubrum", "October Glory")
        "Malus spp. prairefire"            -> ("Malus", "Prairefire")
        "Malus spp."                       -> ("Malus", None)
        "Ilex x attenuata fosteri"         -> ("Ilex x attenuata", "Fosteri")
        "Ulmus parvifolia  Allee"          -> ("Ulmus parvifolia", "Allee")
        "X Cupressocyparis leylandii"      -> ("X Cupressocyparis leylandii", None)
    """
    words = name.split()
    if not words:
        return None, None
    if len(words) >= 2 and words[1].lower() in ("spp.", "spp", "sp."):
        species_words, rest = words[:1], words[2:]
    elif words[0].lower() == "x":
        species_words, rest = words[:3], words[3:]
    elif len(words) >= 3 and words[1].lower() == "x":
        species_words, rest = words[:3], words[3:]
    else:
        species_words, rest = words[:2], words[2:]
    cultivar = " ".join(w.capitalize() for w in rest) or None
    return " ".join(species_words), cultivar


def decode_species(code, domain: dict[str, str]) -> tuple[str | None, str | None, bool]:
    """`(species, cultivar, decoded)` for one `SCI_NAME` code.

    A null code is an unidentified tree (decoded, species None -> Unknown);
    a code outside the domain is not decoded.
    """
    if code is None or not str(code).strip():
        return None, None, True
    key = str(code).strip()
    if key in SPECIES_OVERRIDES:
        species, cultivar = SPECIES_OVERRIDES[key]
        return species, cultivar, True
    name = domain.get(key)
    if name is None:
        return None, None, False
    species, cultivar = split_name(name)
    return species, cultivar, True


DOMAIN: dict[str, str] = {}
DROPPED = {"stump": 0, "no location": 0}
UNDECODED: dict[str, int] = {}


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if str(rec.get("CONDITION") or "").strip().upper() in STUMP_CONDITIONS:
        DROPPED["stump"] += 1
        return False
    if esri_point(feature.get("geometry")) == (None, None):
        DROPPED["no location"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page at a time, geometry reprojected to WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        lon, lat = coordinate_key(lon, lat)
        code = rec.get("SCI_NAME")
        name, cv, decoded = decode_species(code, DOMAIN)
        if not decoded:
            UNDECODED[str(code)] = UNDECODED.get(str(code), 0) + 1

        # The coordinate is the id; see the module docstring.
        tree_id.append(coordinate_tree_id("springfield", lon, lat))
        species.append(name or UNKNOWN_SPECIES)
        cultivar.append(cv)
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("DIAM")
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USSPF"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def check_decoding(table: pa.Table) -> None:
    undecoded = sum(UNDECODED.values())
    if undecoded:
        top = sorted(UNDECODED.items(), key=lambda kv: -kv[1])[:20]
        print(f"Springfield: {undecoded} rows with a code outside the domain: {top}", file=sys.stderr)
    if table.num_rows and undecoded / table.num_rows > MAX_UNDECODED_SHARE:
        raise RuntimeError(
            f"Springfield: {undecoded} of {table.num_rows} rows carry a SCI_NAME "
            f"code outside the layer's domain (> {MAX_UNDECODED_SHARE:.0%})"
        )


if __name__ == "__main__":
    DOMAIN.update(coded_value_domain(LAYER, "SCI_NAME"))
    if not DOMAIN:
        raise RuntimeError("Springfield: SCI_NAME lost its coded-value domain")
    table = stream_to_table(iter_row_chunks(), transform, keep=keep, label="Springfield OpenData")
    print(f"Springfield: dropped non-tree rows {DROPPED}", file=sys.stderr)
    table = drop_stacked_coordinates(table, label="Springfield OpenData")
    check_decoding(table)
    table = validate_coordinates(table, city="Springfield", city_code="USSPF")
    table = enforce_tree_schema(
        table, city="Springfield", data_source="SPRINGFIELD_MO_OPENDATA"
    )
    emit(table)
