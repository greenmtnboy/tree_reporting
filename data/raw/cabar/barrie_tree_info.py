#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Barrie's (Ontario) tree inventory, from the City of Barrie.

Source: "Tree" (ArcGIS item d053284adf6341d7939486d13bfd6911, owner
`BarrieGIS`, "Point Feature contains records of all tree locations in the City
of Barrie"), listed on the City's open data site opendata.barrie.ca and served
from the City's own ArcGIS Server as
`Open_Data/FacilitiesStreets/MapServer/37`, stored in UTM 17N (EPSG 26917) and
reprojected server-side.  46,499 rows on 2026-11-22: street trees and park
trees (`PARKID` filled on ~12k) in one layer.

LICENCE: Open Government Licence - Barrie, v1.0 (based on OGL-Ontario).  The
item's own `licenseInfo` is only "(c) Copyright City of Barrie, The City of
Barrie does not warrant ..."; the open data site's home page says "By
downloading data from this site you are agreeing to the terms of the Open Data
License", linking
https://www.barrie.ca/Online%20Services/PublishingImages/OpenData_Images/COB_DataLicense.pdf,
which grants a worldwide, royalty-free licence to copy, modify, publish and
distribute, with the attribution statement "Contains information licensed
under the Open Government Licence - Barrie."

**The id is the rounded coordinate (`barrie-{lon:.7f}_{lat:.7f}`)**, as for
Spokane and Philadelphia (EXTENDING.md, step 1).  The layer has no GlobalID.
`ASSETID` is the City's asset number but not a key: 2,318 rows have none and
hundreds of values repeat (one appears five times), checked over the whole
layer with a grouped count.  `OBJECTID` runs 1..46,499 without a gap, which is
a layer reloaded whole, so it would re-key on the next reload.  Rows sharing a
7 dp coordinate are dropped (one kept where they are identical); a row without
geometry has no id and is dropped.

**Species is `GENUS` + `SPECIES`, two coded-value domains whose codes are the
Latin genus and epithet** (`Acer` + `platanoides 'Crimson King'`).  The
domains are not enforced: rows carry free-typed values too, and the cleanup
here is for those --

  * the whole binomial typed into `GENUS` with `SPECIES` empty
    (`Acer saccharum`, `Syringa vulgaris 'Katherine Havenmeyer'`) is read as
    the name; `Malus sp.` is a genus;
  * a cultivar typed as the epithet, unquoted or half-quoted
    (`Ulmus pioneer`, `Ulmus morton stalwart`, `Ginkgo biloba Magyar`,
    `Acer platanoides'Deborah'`, `Acer x freemanii 'Jeffersred`) is quoted so
    the schema moves it to `cultivar` instead of publishing an invented
    species (`CULTIVAR_EPITHETS`, `trailing_cultivar`);
  * a stray quote on the genus (`Syringa'`) and curly quotes are cleaned.

Misspelt names (`Gleditisia`, `tricanthos`, `Gymnocladus diocia`, `Saphora`,
`Cladastis`) are left for the shared misspelling table.

**About a third of the rows (16k) have no `GENUS`, only `COMMONNAME`** -- the
newer collection writes the common name alone.  Those resolve through the
shared English table (`shared.species.english`); a common name not in it
publishes `Unknown`.  Where `GENUS` is filled the City's Latin name wins.
Where both are empty and `TREE_COMMENT` is exactly a common name the table
knows (`White Fir`, `Douglas Fir`, ~120 rows of a park survey), that is read.
**Where the genus and the common name disagree at genus level** the domain
was picked from the wrong row of a drop-down (`Celtis reticulata 'Ivory
Silk'`, `Sorbus saccharum 'Green Mountain'`, `Gleditisia dioica`): the
epithet and the common name agree and the genus does not, so the common
name's binomial is used, with a count in the log.  Where only the epithet
is from another genus's row (`Acer pensylvanica` on Freeman and silver
maples, `Ulmus saccharum`, `Ginkgo campestre`: about 25 rows,
`MISPICKED_EPITHETS`) the row publishes the common name's binomial if it is
in the same genus, else the genus alone -- never the chimera, and never
`Acer pensylvanicum`, the striped maple these are not.

**DBH is a band in centimetres** (`<10 cm`, `10 - 19 cm` ... `> 100 cm`, the
`TreeDiameterAtBreastHeight` domain, written with a hyphen or an en dash).
Each band publishes its midpoint, the open `> 100 cm` its bound plus half the
25 cm width of the band below it (112.5 cm), Basingstoke's and Halifax's rule.
About 400 rows hold a free-typed measurement instead: a number of centimetres
(`28`, `~33`, `2 cm`), several stems (`~17, 12`, combined as the square root
of the sum of squares, the usual multi-stem equivalent), or a range (`~8-12`,
its midpoint).  A value in metres (`4 m`, `2.5 m tall`) is a height typed in
the wrong column and is nulled.  A value matching none of these is counted,
and the run refuses above 1%.

**`plant_date` comes from `TREE_COMMENT`**, where the City writes
`planted 2017` (`Planted 2023`, `planted 2018 June with BioForest`,
`planted 2017, replanted 2018` -- the replanting wins): ~5,800 rows, the
year's 1 January.  A bare `2026` in the comment is the inspection, not a
planting, and is not read.

**Rows that are not trees are dropped.**  `TREE_STATUS` is `ACTIVE` or empty
on every row (no removed, stump or planting-site rows are published; the
empty ones are ordinary trees and stay).  The surveyors' own notes mark a few
points as not a tree -- "Please delete, mistakenly collected", "this point is
a mistake, please remove", "Delete double points", "Removed", "Stump" -- and
those are dropped.  `CONDITION = DEAD` trees stay (`Dead` is a tree).

`HEIGHT` and `AGE` are bands with no canonical column; `COMMONNAME` is used
only to resolve rows with no Latin name.
"""

import math
import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    cm_to_inches,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import species_from_common_name

LAYER = FeatureLayer(
    "https://gispublic.barrie.ca/arcgis/rest/services/Open_Data/"
    "FacilitiesStreets/MapServer/37",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,GENUS,SPECIES,COMMONNAME,TREE_STATUS,DBH,TREE_COMMENT"

ID_PREFIX = "barrie"

# Refuse a run where more than this share of non-empty DBH values is unreadable.
MAX_UNREADABLE_DBH_SHARE = 0.01

# `DBH` band (dash normalised, whitespace collapsed) -> diameter midpoint, cm.
DBH_BAND_CM: dict[str, float] = {
    "<10 cm": 5.0,
    "10-19 cm": 15.0,
    "20-29 cm": 25.0,
    "30-39 cm": 35.0,
    "40-49 cm": 45.0,
    "50-74 cm": 62.5,
    "75-100 cm": 87.5,
    ">100 cm": 112.5,
}
DBH_NOT_RECORDED = {"", "unknown", "other"}

# An epithet that is really a cultivar of a hybrid or a cultivar typed bare:
# (genus, epithet as typed, lowercased) -> the name to publish.
CULTIVAR_EPITHETS: dict[tuple[str, str], str] = {
    ("ulmus", "pioneer"): "Ulmus 'Pioneer'",
    ("ulmus", "morton stalwart"): "Ulmus 'Morton Stalwart'",
    ("ulmus", "morton glossy"): "Ulmus 'Morton Glossy'",
    ("malus", "royalty"): "Malus 'Royalty'",
}

# Genus + epithet pairs that are no taxon: the epithet drop-down was picked
# from another genus's row (checked against each row's COMMONNAME on
# 2026-11-22 -- `Acer pensylvanica` is a Freeman, Karpick or silver maple, not
# the striped maple `Acer pensylvanicum`).  The row publishes the common
# name's binomial when that is in the same genus, else the genus alone.
MISPICKED_EPITHETS: frozenset[tuple[str, str]] = frozenset(
    {
        ("acer", "pensylvanica"),
        ("acer", "x flavescens"),
        ("amelanchier", "pensylvanica"),
        ("amelanchier", "x freemanii"),
        ("celtis", "palustris"),
        ("celtis", "rubra"),
        ("cercidiphyllum", "robur"),
        ("cladastis", "pensylvanica"),
        ("cladastis", "tataricum"),
        ("ginkgo", "campestre"),
        ("ginkgo", "triacanthos"),
        ("gleditisia", "aucuparia"),
        ("gleditisia", "tataricum"),
        ("gymnocladus", "dentata"),
        ("gymnocladus", "occidentalis"),
        ("quercus", "platanoides"),
        ("sorbus", "saccharum"),
        ("syringa", "robur"),
        ("tilia", "dioica"),
        ("ulmus", "platanoides"),
        ("ulmus", "saccharum"),
    }
)

# Words that may follow an epithet without being a cultivar.
_RANK_WORDS = {"x", "var", "var.", "subsp", "subsp.", "ssp", "ssp.", "f.", "cv", "cv."}

# Free-typed TREE_COMMENT values that say the point is not a tree.
NOT_A_TREE_COMMENT = re.compile(
    r"^\s*(?:please,?\s+delete|this\s+point\s+is\s+a\s+mistake|"
    r"this\s+was\s+a\s+mistake|delete\s+double\s+points|(?:tree\s+)?removed\s*$|"
    r"stump(?:\s+\d+\s*inches)?\s*$)",
    re.IGNORECASE,
)
PLANTED = re.compile(r"\b(re)?planted\b\D{0,20}?((?:19|20)\d{2})\b", re.IGNORECASE)

DROPPED: dict[str, int] = {"not a tree (surveyor note)": 0, "no geometry": 0}
CLEANED: dict[str, int] = {
    "binomial typed in GENUS": 0,
    "cultivar typed as epithet": 0,
    "genus contradicted by common name": 0,
    "epithet from another genus": 0,
    "resolved from COMMONNAME": 0,
    "COMMONNAME not in table": 0,
    "resolved from TREE_COMMENT": 0,
}
DBH_COUNTS: dict[str, int] = {"band": 0, "measured": 0, "height in metres": 0, "unreadable": 0}
UNRESOLVED_COMMON: dict[str, int] = {}
UNREADABLE_DBH: dict[str, int] = {}


def _clean(value) -> str:
    text = str(value or "").replace("‘", "'").replace("’", "'")
    return " ".join(text.split())


def trailing_cultivar(epithet: str) -> str:
    """Quote a cultivar typed after the epithet without quotes.

    `biloba Magyar` -> `biloba 'Magyar'`; `platanoides'Deborah'` ->
    `platanoides 'Deborah'`; `x freemanii 'Jeffersred` -> closed quote.
    A rank word (`var.`, `x`) and what it qualifies are left alone.
    """
    text = re.sub(r"(\w)'(\w)", r"\1 '\2", epithet)  # platanoides'Deborah'
    if text.count("'") == 1:
        text = text + "'" if not text.endswith("'") else text
    if "'" in text:
        return text
    words = text.split()
    if len(words) < 2:
        return text
    # Keep the epithet and any rank word + the name it qualifies.
    keep = [words[0]]
    i = 1
    if words[0].lower() == "x" and len(words) > 1:
        keep.append(words[1])
        i = 2
    while i < len(words) and words[i].lower() in _RANK_WORDS and i + 1 < len(words):
        keep.extend(words[i : i + 2])
        i += 2
    rest = words[i:]
    if not rest:
        return text
    return " ".join(keep) + " '" + " ".join(w.capitalize() for w in rest) + "'"


def latin_name(genus_raw, species_raw) -> str | None:
    genus = _clean(genus_raw).strip("'").strip()
    epithet = _clean(species_raw)
    if not genus:
        return None
    if " " in genus and not epithet:
        CLEANED["binomial typed in GENUS"] += 1
        head, _, tail = genus.partition(" ")
        return f"{head} {trailing_cultivar(tail)}"
    genus = genus.capitalize()
    if not epithet:
        return genus
    fixed = CULTIVAR_EPITHETS.get((genus.lower(), epithet.lower().strip("'")))
    if fixed:
        CLEANED["cultivar typed as epithet"] += 1
        return fixed
    cleaned = trailing_cultivar(epithet)
    if cleaned != epithet and "'" in cleaned and "'" not in epithet:
        CLEANED["cultivar typed as epithet"] += 1
    return f"{genus} {cleaned}"


def _genus_of(name: str | None) -> str | None:
    if not name:
        return None
    return name.split()[0].strip("'").lower()


def _epithet_of(name: str | None) -> str | None:
    if not name:
        return None
    words = [w for w in name.split() if w.lower() != "x" and not w.startswith("'")]
    return words[1].lower() if len(words) > 1 else None


def scientific_name(rec: dict) -> str | None:
    latin = latin_name(rec.get("GENUS"), rec.get("SPECIES"))
    common = _clean(rec.get("COMMONNAME"))
    from_common = species_from_common_name(common) if common else None
    if latin:
        # A genus contradicted by the common name, where the common name's
        # binomial shares the epithet: the genus is the wrong drop-down row.
        if (
            from_common
            and " " in from_common
            and _genus_of(from_common) != _genus_of(latin)
            and _epithet_of(latin)
            and _epithet_of(latin)[:5] == _epithet_of(from_common)[:5]
        ):
            CLEANED["genus contradicted by common name"] += 1
            cultivar = re.search(r"'[^']*'", latin)
            return from_common + (f" {cultivar.group(0)}" if cultivar else "")
        genus, _, rest = latin.partition(" ")
        base = rest.split("'")[0].strip().lower()
        if (genus.lower(), base) in MISPICKED_EPITHETS:
            CLEANED["epithet from another genus"] += 1
            if from_common and _genus_of(from_common) == genus.lower():
                return from_common
            return genus
        return latin
    if common and common.lower() not in {"other", "unknown"}:
        if from_common:
            CLEANED["resolved from COMMONNAME"] += 1
            return from_common
        CLEANED["COMMONNAME not in table"] += 1
        UNRESOLVED_COMMON[common] = UNRESOLVED_COMMON.get(common, 0) + 1
        return None
    comment = _clean(rec.get("TREE_COMMENT")).rstrip(".")
    if comment:
        from_comment = species_from_common_name(comment)
        if from_comment:
            CLEANED["resolved from TREE_COMMENT"] += 1
            return from_comment
    return common or None


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def diameter_inches(value) -> float | None:
    raw = _clean(value)
    text = raw.lower().replace("–", "-").replace("—", "-")
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text in DBH_NOT_RECORDED:
        return None
    squashed = text.replace(" ", "").replace("cm", " cm")
    if squashed in DBH_BAND_CM:
        DBH_COUNTS["band"] += 1
        return cm_to_inches(DBH_BAND_CM[squashed])
    if re.search(r"\d\s*m\b|\bm\s*tall|mtall", text):
        DBH_COUNTS["height in metres"] += 1
        return None
    body = text.lstrip("~").replace("cm", "").strip().rstrip(",").strip()
    # `25, 17. 20`: a full stop typed for a comma between stems.
    body = re.sub(r"\.\s", ", ", body).replace("~", "")
    stems = [s.strip() for s in re.split(r"[,\s]+", body) if s.strip()]
    values: list[float] = []
    for stem in stems:
        if "-" in stem:
            lo, _, hi = stem.partition("-")
            a, b = _number(lo), _number(hi)
            if a is None or b is None:
                values = []
                break
            values.append((a + b) / 2)
        else:
            n = _number(stem)
            if n is None:
                values = []
                break
            values.append(n)
    if not values:
        DBH_COUNTS["unreadable"] += 1
        UNREADABLE_DBH[raw] = UNREADABLE_DBH.get(raw, 0) + 1
        return None
    DBH_COUNTS["measured"] += 1
    return cm_to_inches(math.sqrt(sum(v * v for v in values)))


def plant_date(comment) -> date | None:
    matches = PLANTED.findall(str(comment or ""))
    if not matches:
        return None
    replanted = [year for re_, year in matches if re_]
    year = int(replanted[-1] if replanted else matches[0][1])
    return date(year, 1, 1)


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if NOT_A_TREE_COMMENT.search(str(rec.get("TREE_COMMENT") or "")):
        DROPPED["not a tree (surveyor note)"] += 1
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
        tree_id.append(coordinate_tree_id(ID_PREFIX, lon, lat))
        species.append(scientific_name(rec))
        planted.append(plant_date(rec.get("TREE_COMMENT")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("DBH")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CABAR"] * n, type=pa.string()),
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
    attempted = sum(DBH_COUNTS.values())
    unreadable = DBH_COUNTS["unreadable"]
    if attempted and unreadable / attempted > MAX_UNREADABLE_DBH_SHARE:
        worst = sorted(UNREADABLE_DBH.items(), key=lambda kv: -kv[1])[:10]
        raise RuntimeError(
            f"Barrie: {unreadable} of {attempted} DBH values unreadable "
            f"(over {MAX_UNREADABLE_DBH_SHARE:.0%}); the bands changed? {worst}"
        )


if __name__ == "__main__":
    table = stream_to_table(iter_pages(), transform, keep=keep, label="Barrie OpenData")
    check_dbh_readable()
    unresolved = sorted(UNRESOLVED_COMMON.items(), key=lambda kv: -kv[1])
    print(
        f"Barrie: dropped {DROPPED}; species {CLEANED}; DBH {DBH_COUNTS}; "
        f"common names not in the English table: {unresolved[:25]}",
        file=sys.stderr,
    )
    table = drop_stacked_coordinates(
        table, label="Barrie", identical=("species", "diameter_at_breast_height")
    )
    table = table.filter(pc.is_valid(table["latitude"]))
    table = validate_coordinates(table, city="Barrie", city_code="CABAR")
    table = enforce_tree_schema(table, city="Barrie", data_source="BARRIE_OPENDATA")
    emit(table)
