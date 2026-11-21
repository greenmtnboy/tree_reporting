#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Red Deer's City tree inventory (no licence stated; displayed with
attribution only, not licensed for reuse).

Source: the City of Red Deer's own ArcGIS Enterprise layer
`CoRD_View_Layers/PARK_Trees_inservice` (ArcGIS Online items
4ede9512512a4d5187045a35e063a92d / 9414f00af7594ed9aadfacb7ef841558, owner
Administrator_CoRD_AGOL, public).  The layer is not listed on
data.reddeer.ca, whose "Open Government Licence - The City of Red Deer"
covers the datasets on that site; neither the service, the layer nor the
ArcGIS Online items state a licence or an attribution (all empty, checked
2026-11-21).  So the source is displayed with attribution only.

**Every row is a City tree in service** (`Status = 'in service'` and
`Owner = 'City'` on all 60.5k rows -- the view is pre-filtered).  What is not
a tree in the ground is dropped here, with logged counts:

  * `LivingStatus` is null on ~93% of rows (a tree with no condition note)
    and otherwise `living tree`, `tree suckers`, `dead standing`, `habitat
    tree`, `stump`, `stump pulled`.  The last four are dead wood or gone and
    are dropped (2026-11-21: 146 + 1 + 4 + 1 of 53,151 trees).
  * `DisposalYear` is set on 17 rows the City has disposed of while leaving
    them `in service`; they are dropped too.

**The view repeats rows.**  It is a join that fans out: 2,468 trees come back
four times (one twice), identical in every column including OBJECTID, so the
60,558 rows `returnCountOnly` reports are 53,151 trees.  The pages are read
in OBJECTID keyset order and a repeat is skipped (logged).

**The id is `COMPKEY`** (`red-{compkey}`), the asset key of the City's
Cityworks work-management system (`COMPTYPE` 36 = tree): non-null and
distinct over all 53,151 trees (checked 2026-11-21).  `OBJECTID` is not
used: here it is also the tag number `InventoryNum_Rule` (equal on every
row), which an attribute rule derives *from* the OID, so a reload would
re-key both; `UNITID` (`PEM_Tree_n` / `PARKTREE…`) is unique too but null on
one tree.  `enforce_tree_schema` still refuses a repeat or a null.

**Species is the binomial inside `CmnNameSpecies`**, the controlled list
(172 values) that the City writes as `Common (Qualifier) (Scientific
cultivar)`: `Spruce (Colorado Blue) (Picea pungens)`, `Elm (Brandon) (Ulmus
americana Brandon)`, `Poplar (Northwest) (Populus x jackii (Northwest))`.
The last parenthesised group is the scientific part; in it the City writes
the cultivar unquoted and capitalised after the epithet, which is quoted
here for `enforce_tree_schema` to move to `cultivar`.  The values the rule
would misread (a variety written capitalised, a hybrid formula, an
upper-case epithet) are in `SCIENTIFIC_OVERRIDES`.  The older free-text
`Species` column (`White Spruce`, `Colorado spruce`, null on 5.8k rows) is a
common name and is never mapped.  A parse-failure guard refuses the build if
more than 1% of named rows yield no taxon.

**No diameter**: `DBHLatest_SQLCalc` is filled on 3 of 53,151 trees, with no
stated unit, so it is not mapped.  No crown spread, no height.

**`InstallYear` is the planting year** (filled on ~5% of trees, mostly
2017 onwards: the Tree Replacement Program) -> 1 January of that year.
Stamped impossibilities (`1`, `2824`, years past the current one) are nulled.

Geometry is stored in NAD83 / Alberta 3TM 114 (EPSG:3776) and requested
reprojected to WGS84 server-side (`outSR=4326`).
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, max_record_count
from shared.ingest import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://arcgis.reddeer.ca/arcgis/rest/services/CoRD_View_Layers/"
    "PARK_Trees_inservice/FeatureServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,COMPKEY,LivingStatus,DisposalYear,InstallYear,CmnNameSpecies"

# `LivingStatus` values that are not a standing living tree.
NOT_LIVING = frozenset({"dead standing", "habitat tree", "stump", "stump pulled"})

EARLIEST_PLANT_YEAR = 1850

# Refuse to publish when more than this share of named rows parse to no taxon.
MAX_UNPARSED_SHARE = 0.01

# The scientific part of a `CmnNameSpecies` value -> (species, cultivar), for
# the values the general rule in `parse_scientific` would read wrongly.
SCIENTIFIC_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    # A botanical variety or form written capitalised, like a cultivar.
    "Pinus contorta Latifolia": ("Pinus contorta", None),
    "Picea glauca Densata": ("Picea glauca", None),
    "Salix alba Vitellina": ("Salix alba", None),
    "Ulmus davidiana Japonica": ("Ulmus davidiana", None),
    "Gleditsia triacanthos Inermis Harve": ("Gleditsia triacanthos", "Harve"),
    "Alnus incana sp. Tenuifolia": ("Alnus incana", None),
    # An epithet written with a capital.
    "Fraxinus Pennsylvanica Rugby": ("Fraxinus pennsylvanica", "Rugby"),
    "Crataegus Mordensis Toba": ("Crataegus x mordensis", "Toba"),
    # A cultivar written as a hybrid epithet ('Okanese' hybrid poplar).
    "Populus x okanese": ("Populus", "Okanese"),
    # Hybrid formulae and grafted combinations: the genus, or the nothospecies.
    "Populus deltoides x populus nigra": ("Populus x canadensis", None),
    "Malus melba x Malus rescue": ("Malus", "Parkland"),
    "Fraxinus nigra x Fraxinus mandshurica": ("Fraxinus", "Northern Gem"),
    "Pyrus ussuriensis x Paul": ("Pyrus", None),
    "Prunus PembinaOpataBrookred": ("Prunus", None),
    # "Cedar (Columnar)": no species Thuja columnaris exists; the columnar
    # cedar planted on the prairies is Thuja occidentalis 'Columnaris'.
    "Thuja columnaris": ("Thuja occidentalis", "Columnaris"),
    # A sex note, not a cultivar.
    "Acer negundo 705 Male Tree": ("Acer negundo", None),
    # "Pear (Combo)": the value has no scientific part at all.
    "Combo": ("Pyrus", None),
}

# Rank words inside the scientific part; a lowercase word after one is an
# infraspecific epithet and is dropped (species rank only).
RANK_WORDS = frozenset({"var", "var.", "subsp", "subsp.", "ssp", "ssp.", "f.", "sp", "sp.", "spp", "spp."})

# Parenthesised notes inside the scientific part that are not cultivars.
NOTE_PREFIXES = ("was ", "tree")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def trailing_group(text: str) -> str | None:
    """The contents of the balanced `(...)` group the text ends with."""
    if not text.endswith(")"):
        return None
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                return text[i + 1 : -1].strip() or None
    return None


def parse_scientific(sci: str) -> tuple[str | None, str | None]:
    """`(species, cultivar)` from the scientific part of `CmnNameSpecies`.

    Genus, an optional `x`, a lowercase epithet; a lowercase word after a
    rank word (or after the epithet) is infraspecific and dropped; every
    capitalised word that follows is the cultivar.  A nested `(Northwest)`
    is a cultivar, a nested `(Tree)` or `(was ...)` a note.
    """
    if sci in SCIENTIFIC_OVERRIDES:
        return SCIENTIFIC_OVERRIDES[sci]
    cultivar_parts: list[str] = []
    while "(" in sci:
        start = sci.index("(")
        end = sci.find(")", start)
        if end == -1:
            end = len(sci)
        inner = sci[start + 1 : end].strip()
        if inner and not inner.lower().startswith(NOTE_PREFIXES):
            cultivar_parts.append(inner)
        sci = (sci[:start] + " " + sci[end + 1 :]).strip()
    tokens = sci.split()
    if not tokens or not tokens[0].isalpha() or not tokens[0][0].isupper():
        return None, None
    genus = tokens[0]
    i = 1
    hybrid = False
    epithet = None
    if i < len(tokens) and tokens[i].lower() == "x":
        hybrid = True
        i += 1
    if (
        i < len(tokens)
        and tokens[i].isalpha()
        and tokens[i].islower()
        and tokens[i] not in RANK_WORDS
    ):
        epithet = tokens[i]
        i += 1
    rest = tokens[i:]
    named = [t for t in rest if not (t.lower() in RANK_WORDS or (t.isalpha() and t.islower()))]
    cultivar_parts = [" ".join(named)] + cultivar_parts if named else cultivar_parts
    species = genus
    if epithet:
        species = f"{genus} x {epithet}" if hybrid else f"{genus} {epithet}"
    cultivar = " ".join(cultivar_parts) or None
    return species, cultivar


def species_value(raw) -> tuple[str | None, bool]:
    """(species string for the schema, whether the value failed to parse)."""
    text = clean(raw)
    if text is None:
        return None, False
    if text.lower() == "unknown":
        return "Unknown", False
    sci = trailing_group(text)
    if sci is None:
        return None, True
    species, cultivar = parse_scientific(sci)
    if species is None:
        return None, True
    return (f"{species} '{cultivar}'" if cultivar else species), False


def plant_date_of(value) -> date | None:
    if value is None:
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if EARLIEST_PLANT_YEAR <= year <= date.today().year:
        return date(year, 1, 1)
    return None


class Counts:
    repeats = 0
    not_living = 0
    disposed = 0
    no_id = 0
    named = 0
    unparsed: dict[str, int] = {}


def iter_pages():
    """Pages in OBJECTID keyset order, the view's repeated rows skipped.

    Keyset paging rather than `resultOffset`: the view repeats an OBJECTID up
    to four times, and an offset over a non-unique sort can skip or repeat
    rows between pages.  A repeat cut off at a page boundary is lost, which
    is harmless because it is identical to the copy already read.
    """
    size = max_record_count(LAYER)
    seen: set[int] = set()
    last = -1
    while True:
        payload = get_json_with_retry(
            LAYER.query_url,
            params={
                "where": f"OBJECTID > {last}",
                "outFields": OUT_FIELDS,
                "returnGeometry": "true",
                "outSR": "4326",
                "geometryPrecision": "7",
                "orderByFields": "OBJECTID",
                "resultRecordCount": str(size),
                "f": "json",
            },
            timeout=LAYER.timeout,
        )
        features = payload.get("features") or []
        if not features:
            return
        page = []
        for feature in features:
            oid = (feature.get("attributes") or {}).get("OBJECTID")
            if oid in seen:
                Counts.repeats += 1
                continue
            seen.add(oid)
            page.append(feature)
        yield page
        last = max(f["attributes"]["OBJECTID"] for f in features)
        if len(features) < size and not payload.get("exceededTransferLimit"):
            return


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if rec.get("COMPKEY") is None:
        Counts.no_id += 1
        return False
    if (clean(rec.get("LivingStatus")) or "").lower() in NOT_LIVING:
        Counts.not_living += 1
        return False
    if clean(rec.get("DisposalYear")) is not None:
        Counts.disposed += 1
        return False
    return True


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, failed = species_value(rec.get("CmnNameSpecies"))
        if clean(rec.get("CmnNameSpecies")) is not None:
            Counts.named += 1
        if failed:
            raw = clean(rec.get("CmnNameSpecies")) or ""
            Counts.unparsed[raw] = Counts.unparsed.get(raw, 0) + 1
        tree_id.append(f"red-{int(rec['COMPKEY'])}")
        species.append(name)
        plant_date.append(plant_date_of(rec.get("InstallYear")))
        latitude.append(lat)
        longitude.append(lon)

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CARED"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        }
    )


def check_parse_failures() -> None:
    failed = sum(Counts.unparsed.values())
    if failed:
        print(
            f"Red Deer: {failed} row(s) with no readable scientific name: "
            + ", ".join(f"{k!r} x{v}" for k, v in sorted(Counts.unparsed.items())),
            file=sys.stderr,
        )
    if Counts.named and failed / Counts.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Red Deer: {failed} of {Counts.named} species values unreadable "
            f"(> {MAX_UNPARSED_SHARE:.0%}); has CmnNameSpecies changed shape?"
        )


if __name__ == "__main__":
    table = stream_to_table(iter_pages(), transform, keep=keep, label="Red Deer OpenData")
    print(
        f"Red Deer: skipped {Counts.repeats} repeated view row(s); dropped "
        f"{Counts.not_living} dead/stump, {Counts.disposed} disposed, "
        f"{Counts.no_id} without COMPKEY",
        file=sys.stderr,
    )
    check_parse_failures()
    table = validate_coordinates(table, city="Red Deer", city_code="CARED")
    table = enforce_tree_schema(
        table, city="Red Deer", data_source="RED_DEER_OPENDATA"
    )
    emit(table)
