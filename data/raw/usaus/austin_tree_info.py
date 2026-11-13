#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Austin, TX's public tree inventory, from the city's Socrata portal.

Source: "Tree Inventory" (`wrik-xasw`) on data.austintexas.gov, published by
the Urban Forest Program (Austin Climate Action and Resilience), public domain.
62,274 rows when the city was wired (2026-11-11).  It is a one-off
compilation, "public trees inventoried by the City of Austin as of March 13th,
2020", merged from the Development Services Department's Tree Division, AISD,
Parks and Recreation and Public Works' 2013 downtown inventory, and the city
says plainly that it "is not a complete comprehensive inventory of all trees.
Some errors and/or duplicate data may exist."  Its rows have not changed since
2020-03-13 (`rowsUpdatedAt`).  Paging, the watermark and the point shapes live
in `shared.platforms.socrata`.

The dataset has five columns -- `geometry`, `species`, `diameter`, `latitude`,
`longtitude` (sic) -- and the same five are an ArcGIS hosted layer
(`services.arcgis.com/0L95CJ0VTaxqcmED/.../Tree_Inventory/FeatureServer/0`,
the dataset's attribution link), which adds nothing but its `FID`.

**The id is Socrata's row identifier `:id`, because the source has no id of
its own.**  Neither copy carries an asset number, a GlobalID or anything that
sounds like one.  Of the two system ids, `:id` is the stable one: it is
62,274 distinct and non-null over the whole dataset (`count(distinct :id)`),
and the rows it names have not been rewritten since March 2020.  The ArcGIS
`FID` is a plain OID and was renumbered when that layer was overwritten in
October 2025 (`editingInfo.dataLastEditDate`), so keying on it would have
re-keyed every tree the next time the city republishes there.  If the city
ever replaces the Socrata rows wholesale, every `:id` changes with them; that
is a full re-key of a compilation the city does not maintain, and the
`@source_kept` guard does not fire on it because the row count is unchanged.

**Coordinates come from `geometry`, never from `latitude`/`longtitude`.**
7,295 rows carry Texas State Plane (ftUS) eastings and northings in those two
columns (latitude 10,072,645, longitude 3,116,131); `geometry` is WGS84 on
every row.

**`species` is an English common name, and nothing else.**  The column is
documented as "Common species name of the tree": 482 distinct spellings, in
three styles at once (`Cedar Elm`, `Elm, Cedar`, `Oak, Live (Southern)`),
with case variants and typos (`Monterrey Oak`, `Mexican Whie Oak`).  It
resolves through `shared.species.english` like Burlington ON's and
Mississauga's, with three local layers in front of it:

* `RAW_SPECIES` -- values the shared key cannot read: the parenthesised
  alternates (`Oak, Texas Live (Escarpment)`), the handful already written as
  a binomial (`Quercus stellata`, `Ashei juniperus`), the punctuation slips
  (`Sycamore. Mexican`) and the form markers (`Snag` is a standing dead tree,
  `Palm Species` a palm).
* `REGIONAL_KEYS` -- names whose shared answer is wrong in central Texas.
  Austin records `Southern Live Oak`, `Escarpment Live Oak`, `Texas Red Oak`,
  `Shumard Oak`, `Sugarberry`, `Hackberry, Common` and `Mexican Sycamore` as
  separate values, so its bare `Live Oak`, `Red Oak`, `Hackberry` and
  `Sycamore` are the genus and nothing finer; `Cedar` here is Ashe juniper,
  not *Thuja*; `Mountain Laurel` is the Texas mountain laurel, not *Kalmia*;
  `Arborvitae` is *Platycladus* as often as *Thuja* and stays Unknown.
* everything else is `species_from_common_name`; an unresolved name
  publishes as `Unknown` rather than as a guess.

`tree_name` is the published common name through `normalize_tree_name`, with
the parenthesised values given their plain name (`TREE_NAMES`).  One value
quotes a cultivar (`Indian Hawthorne 'Majestic Beauty'`); it goes to
`cultivar`.

**Rows dropped, and why** (counts as of 2026-11-11):

* empty sites, by the shared rule on the species value: `Stump`, the
  `Vacant Planting Site - *` / `Vacant Site*` family and `Vacant - replant`
  (2,459 rows after the two filters below);
* `NOT_A_TREE` here, every one of them with a diameter of 0 on every row:
  `tbd shade`, `tbd - shade`, `tbd ornamental`, `tbd - ornamental`, `tbd`
  (406 sites waiting for a tree of that kind), `Brush` and `Brush Pile` (12),
  `Cull` (16) and `Various` (1).  A new value in the `tbd`/`brush`/`cull`
  family raises (`NOT_A_TREE_FAMILY`): which side of the line it falls on is
  a decision, not a default;
* exact repeats: 222 rows share their point (to the source's full precision),
  species value and diameter with a row of lower `:id`.  These are the
  duplicates the city warns of -- the same tree compiled from two of its
  sources -- and the cross-source dedup never merges two municipal rows, so
  without this each would draw as two dots.  Rows at one point with a
  different species or diameter are kept;
* one row whose point lies outside `CITY_BOUNDS` (`validate_coordinates`).

That leaves 59,157 trees of the 62,274 rows.

**`diameter` is DBH in inches** ("measured at breast height 4.5-feet from the
ground.  Units are measured in inches.").  3,630 rows record 0, which
`enforce_tree_schema` nulls as unmeasured.  The column is numeric on the
portal, but arrives as a string like every Socrata value, so a parse failure
is counted and more than 1% of rows refuses the build.

No planting date (`plant_date` is null, typed `date32`) and no crown spread.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.socrata import SYSTEM_ID, SocrataDataset, iter_rows, point_lon_lat
from shared.species.english import common_name_key, species_from_common_name

DATASET = SocrataDataset("data.austintexas.gov", "wrik-xasw", timeout=180)

SELECT = f"{SYSTEM_ID},species,diameter,geometry"

# Sites with no tree on them, recorded in the species column.  All carry a
# diameter of 0.  `tbd *` is a site earmarked for a shade or ornamental tree
# that had not been chosen; `Cull`, `Brush`, `Brush Pile` and `Various` name
# no single standing tree.
NOT_A_TREE = frozenset({
    "tbd", "tbd shade", "tbd - shade", "tbd ornamental", "tbd - ornamental",
    "Brush", "Brush Pile", "Cull", "Various",
})
NOT_A_TREE_FAMILY = re.compile(r"^\s*(tbd|brush|cull)\b", re.IGNORECASE)

# Raw values the shared common-name key cannot read, resolved as written.
RAW_SPECIES: dict[str, str] = {
    # Parenthesised alternates: the key would fold both names into one string.
    "Oak, Live (Southern)": "Quercus virginiana",
    "Oak, Texas Live (Escarpment)": "Quercus fusiformis",
    "Oak, Mexican White (Netleaf)": "Quercus polymorpha",
    "Bean, Mescal (Texas Mountain Laurel)": "Dermatophyllum secundiflorum",
    "Ash, Velvet (Arizona Ash)": "Fraxinus velutina",
    "Elm, Chinese (Lacebark Elm)": "Ulmus parvifolia",
    "Orange, Japanese Bitter (Hardy orange)": "Citrus trifoliata",
    # "including hybrids" is L. indica x L. fauriei as much as L. indica.
    "Crape Myrtle (including hybrids)": "Lagerstroemia",
    # Punctuation slips that invert the wrong way or not at all.
    "Oak, Scarlet Oak": "Quercus coccinea",
    "Sycamore. Mexican": "Platanus mexicana",
    "Mulberry. Texas": "Morus microphylla",
    "Osage, Orange": "Maclura pomifera",
    "Cypress, Bald.r": "Taxodium distichum",
    "Chisos , Red oak": "Quercus gravesii",
    # Already a binomial, or nearly.
    "Quercus stellata": "Quercus stellata",
    "Quercus marilandica": "Quercus marilandica",
    "Ulmus crassifolia": "Ulmus crassifolia",
    "Quercus buckley": "Quercus buckleyi",
    "Ashei juniperus": "Juniperus ashei",
    # Form markers: a snag is a standing dead tree; a palm is a palm.
    "Snag": "Dead",
    "Palm Species": "Palm",
    "Palm spp.": "Palm",
}

# Common-name keys whose shared answer is wrong for central Texas.  `None`
# publishes Unknown.  See the module docstring.
REGIONAL_KEYS: dict[str, str | None] = {
    "live oak": "Quercus",
    "red oak": "Quercus",
    "hackberry": "Celtis",
    "sycamore": "Platanus",
    "cedar": "Juniperus",
    "mountain laurel": "Dermatophyllum secundiflorum",
    "mt laurel": "Dermatophyllum secundiflorum",
    "mountain laural": "Dermatophyllum secundiflorum",
    "arborvitae": None,
    # Texas readings: east of Texas, flameleaf sumac is Rhus copallinum.
    "flame leaf sumac": "Rhus lanceolata",
    "flameleaf sumac": "Rhus lanceolata",
    # Shin oak in central Texas is Q. sinuata var. breviloba; elsewhere in
    # Texas the name also covers Q. havardii and Q. mohriana.
    "shin oak": "Quercus sinuata",
}

# The card's common name for the values `normalize_tree_name` cannot un-invert.
TREE_NAMES: dict[str, str | None] = {
    "Oak, Live (Southern)": "Southern live oak",
    "Oak, Texas Live (Escarpment)": "Texas live oak",
    "Oak, Mexican White (Netleaf)": "Mexican white oak",
    "Bean, Mescal (Texas Mountain Laurel)": "Texas mountain laurel",
    "Ash, Velvet (Arizona Ash)": "Velvet ash",
    "Elm, Chinese (Lacebark Elm)": "Chinese elm",
    "Orange, Japanese Bitter (Hardy orange)": "Hardy orange",
    "Crape Myrtle (including hybrids)": "Crape myrtle",
    "Oak, Scarlet Oak": "Scarlet oak",
    "Sycamore. Mexican": "Mexican sycamore",
    "Mulberry. Texas": "Texas mulberry",
    "Osage, Orange": "Osage orange",
    "Cypress, Bald.r": "Bald cypress",
    "Chisos , Red oak": "Chisos red oak",
    "Quercus stellata": None,
    "Quercus marilandica": None,
    "Ulmus crassifolia": None,
    "Quercus buckley": None,
    "Ashei juniperus": None,
}

# A quoted selection with both quotes present.  `extract_cultivar` also reads
# a lone apostrophe as an opening quote, which turns "Eve's Necklace" into the
# cultivar "s Necklace".
_QUOTED_CULTIVAR = re.compile(r"['\"‘’]([^'\"‘’]+)['\"‘’]")

# Refuse the build when more than this share of non-empty diameters fail to parse.
MAX_DBH_PARSE_FAILURE_RATE = 0.01

_dbh_stats = {"seen": 0, "failed": 0}
_seen_points: set[tuple] = set()
_dropped = {"not_a_tree": 0, "duplicate": 0}


def clean(value) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


def species_of(raw: str | None) -> str | None:
    """The species value for one published common name.  See the docstring."""
    if raw is None:
        return None
    if raw in RAW_SPECIES:
        return RAW_SPECIES[raw]
    key = common_name_key(raw)
    if key in REGIONAL_KEYS:
        return REGIONAL_KEYS[key]
    return species_from_common_name(raw)


def tree_name_of(raw: str | None) -> str | None:
    if raw is None:
        return None
    if raw in TREE_NAMES:
        return TREE_NAMES[raw]
    return normalize_tree_name(_QUOTED_CULTIVAR.sub("", raw).strip())


def cultivar_of(raw: str | None) -> str | None:
    match = _QUOTED_CULTIVAR.search(raw or "")
    return clean(match.group(1)) if match else None


def parse_dbh(value) -> float | None:
    """Inches.  A failed parse is counted for the guard in `check_dbh_parse`."""
    if value is None or str(value).strip() == "":
        return None
    _dbh_stats["seen"] += 1
    try:
        return float(value)
    except (TypeError, ValueError):
        _dbh_stats["failed"] += 1
        return None


def check_dbh_parse() -> None:
    seen, failed = _dbh_stats["seen"], _dbh_stats["failed"]
    if seen and failed / seen > MAX_DBH_PARSE_FAILURE_RATE:
        raise RuntimeError(
            f"Austin diameter: {failed:,} of {seen:,} values did not parse as a "
            f"number; the portal's column has changed"
        )
    if failed:
        print(f"Austin diameter: {failed} unparseable value(s) nulled", file=sys.stderr)


def keep(rec: dict) -> bool:
    """False for an empty site or an exact repeat of an earlier row.

    Pages arrive ordered by `:id`, so the row kept from a set of repeats is the
    one with the lowest `:id`, whatever the paging.
    """
    raw = clean(rec.get("species"))
    if raw in NOT_A_TREE:
        _dropped["not_a_tree"] += 1
        return False
    if raw and NOT_A_TREE_FAMILY.match(raw):
        raise RuntimeError(
            f"Austin species value {raw!r} looks like a site marker but is new; "
            f"decide whether it is a tree and add it to NOT_A_TREE or RAW_SPECIES"
        )
    lon, lat = point_lon_lat(rec.get("geometry"))
    if lon is not None and lat is not None:
        fingerprint = (lon, lat, raw, clean(rec.get("diameter")))
        if fingerprint in _seen_points:
            _dropped["duplicate"] += 1
            return False
        _seen_points.add(fingerprint)
    return True


def iter_row_chunks():
    return iter_rows(DATASET, select=SELECT)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raw_id = clean(rec.get(SYSTEM_ID))
        if raw_id is None:
            raise RuntimeError("Austin row without a Socrata :id; the portal has changed")
        raw = clean(rec.get("species"))
        lon, lat = point_lon_lat(rec.get("geometry"))

        tree_id.append(f"aus-{raw_id}")
        species.append(species_of(raw))
        cultivar.append(cultivar_of(raw))
        tree_name.append(tree_name_of(raw))
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(rec.get("diameter")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USAUS"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=keep, label="Austin OpenData"
    )
    print(
        f"Austin OpenData: dropped {_dropped['not_a_tree']} site marker(s) and "
        f"{_dropped['duplicate']} exact duplicate(s)",
        file=sys.stderr,
    )
    check_dbh_parse()
    table = validate_coordinates(table, city="Austin", city_code="USAUS")
    table = enforce_tree_schema(
        table, city="Austin", data_source="AUSTIN_OPENDATA"
    )
    emit(table)
