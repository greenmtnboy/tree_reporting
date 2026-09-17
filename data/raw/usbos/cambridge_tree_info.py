#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Cambridge MA street and park trees, from the city's Socrata portal.

`82zb-7qc9` is several inventories in one feed: the city's record, a Harvard
campus survey under `H`-prefixed ids, and rows for planting sites that outlive
their tree. Three stages reduce it to one row per living tree, in this order
because each shrinks the next: empty sites, then replanted wells, then the
Harvard re-survey. `raw/tree_dedup.preql` cannot do this: every municipal row
is its own cluster there, and `sitetype`/`treewellid` never reach the model.
"""

import math
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    validate_coordinates,
)

DATASET_ID = "82zb-7qc9"
# Only current (non-removed) trees; request all fields we need
BASE_URL = f"https://data.cambridgema.gov/resource/{DATASET_ID}.json"
PAGE_SIZE = 50_000
# Socrata truncates the layer's field names to ten characters: `cartegraph` is
# CARTEGRAPHRETIREDATE, `cartegra_1` CARTEGRAPHPLANTDATE, `siteretire`
# SITERETIREDREASON.
SELECT = (
    "treeid,scientific,commonname,cultivar,plantdate,cartegra_1,the_geom,diameter,"
    "sitetype,treewellid,siteretire,cartegraph"
)
WHERE = "removaldat IS NULL"

# The publisher defines SITETYPE as whether a tree occupies the site, with
# `Retired` "paved over or otherwise empty and no longer available for
# planting", and says it supersedes conflicting data in the record. These rows
# carry a real binomial, so `shared.ingest.is_not_a_tree` (which reads the
# species field) does not catch them. `Spar` (a standing dead trunk),
# `Unknown` and a blank may describe a tree, and stay.
SITE_TYPES_WITHOUT_A_TREE = frozenset(
    {
        "Retired",
        "Proposed Tree",
        "Refused Not Planted",
        "Not Installed Issue",
        "Damaged Tree Well",
        "Planting Site",
        "Stump",
    }
)
SITE_TYPES_WITH_A_TREE = frozenset({"Tree", "Spar", "Unknown", ""})

# Measured against a control, not borrowed from the cross-source merge's 5m.
# Distinct city trees are mutual species-compatible nearest neighbours ~75% of
# the time at every band out to 15m, so mutual-NN says nothing here; d1/d2
# does. Harvard-to-city pairs score 0.09/0.22/0.38 in the 0-1/1-2/2-3m bands
# against 0.31/0.67/0.65 for the control, and the two are level by 3-5m.
MATCH_METRES = 3.0

# 79% of multi-row wells span under 2m; a well id reused across the city (up
# to 4,967m apart) is two wells, not a replanting.
WELL_SPAN_METRES = 5.0

CAMBRIDGE_LAT = 42.3736


def parse_plant_date(raw: str | None) -> date | None:
    """Parse ISO 8601 date string from Socrata calendar_date field."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def fetch_page(offset: int) -> list[dict]:
    params = {
        "$select": SELECT,
        "$where": WHERE,
        "$limit": PAGE_SIZE,
        "$offset": offset,
        "$order": "treeid ASC",
    }
    r = requests.get(BASE_URL, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_all() -> list[dict]:
    records: list[dict] = []
    offset = 0
    while True:
        page = fetch_page(offset)
        records.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return records


def _text(rec: dict, key: str) -> str:
    return (rec.get(key) or "").strip()


def _point(rec: dict) -> tuple[float, float] | None:
    geom = rec.get("the_geom")
    if not geom or geom.get("type") != "Point":
        return None
    coords = geom.get("coordinates") or [None, None]
    if coords[0] is None or coords[1] is None:
        return None
    return float(coords[1]), float(coords[0])


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular separation, exact enough within one city."""
    return math.hypot(
        (a[0] - b[0]) * 111_320.0,
        (a[1] - b[1]) * 111_320.0 * math.cos(math.radians(CAMBRIDGE_LAT)),
    )


def _record_completeness(rec: dict) -> int:
    return sum(
        1
        for key in ("scientific", "commonname", "plantdate", "the_geom", "diameter")
        if rec.get(key) not in (None, "")
    )


def canonicalize_records(records: list[dict]) -> tuple[list[dict], int, int]:
    deduped: dict[str, dict] = {}
    dropped_missing_id = 0
    duplicate_rows = 0

    for rec in records:
        raw_id = rec.get("treeid")
        if not raw_id:
            dropped_missing_id += 1
            continue

        existing = deduped.get(raw_id)
        if existing is None:
            deduped[raw_id] = rec
            continue

        duplicate_rows += 1
        if _record_completeness(rec) > _record_completeness(existing):
            deduped[raw_id] = rec

    return list(deduped.values()), dropped_missing_id, duplicate_rows


def drop_sites_without_a_tree(
    records: list[dict],
) -> tuple[list[dict], dict[str, int], set[str]]:
    """Remove rows whose `sitetype` says the planting site is empty."""
    kept: list[dict] = []
    dropped: dict[str, int] = defaultdict(int)
    unrecognised: set[str] = set()

    for rec in records:
        site_type = _text(rec, "sitetype")
        if site_type in SITE_TYPES_WITHOUT_A_TREE:
            dropped[site_type] += 1
            continue
        if site_type not in SITE_TYPES_WITH_A_TREE:
            unrecognised.add(site_type)
        kept.append(rec)

    return kept, dict(dropped), unrecognised


def _plant_date(rec: dict) -> str:
    """PLANTDATE, else CARTEGRAPHPLANTDATE.

    The two agree on every row that carries both; the Cartegraph date alone
    covers ~900 recent plantings (all 400 stamped 2025-09-01 are the "Fall
    2025" season, median 1.4in).
    """
    return _text(rec, "plantdate") or _text(rec, "cartegra_1")


def _is_retired(rec: dict) -> bool:
    """A retirement reason or a Cartegraph retire date on the record.

    Not SITEREPLANTED: 243 live trees carry it, most planted since 2020.
    """
    return bool(_text(rec, "siteretire")) or bool(_text(rec, "cartegraph"))


def _current_tree_in_well(group: list[dict]) -> dict | None:
    """The tree standing in a shared well now, or None to keep every row.

    A retirement mark first, then the latest planting date, then the highest
    id (which matches planting order 94% of the time where both are dated).
    """
    standing = [r for r in group if not _is_retired(r)]
    if len(standing) == 1:
        return standing[0]
    candidates = standing or group

    dated = [r for r in candidates if _plant_date(r)]
    if dated:
        newest = max(_plant_date(r) for r in dated)
        latest = [r for r in dated if _plant_date(r) == newest]
        if len(latest) == 1:
            return latest[0]

    if all(r["treeid"].isdigit() for r in candidates):
        return max(candidates, key=lambda r: int(r["treeid"]))

    return None


def collapse_replanted_wells(records: list[dict]) -> tuple[list[dict], int, int]:
    """Keep one row per tree well: a replanting gets a new `treeid` and the
    old record is left open, so well 10153 held an 8in maple and a 1.9in
    redbud 1cm apart."""
    wells: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        well = _text(rec, "treewellid")
        if well and well != "0":
            wells[well].append(rec)

    absorbed: set[int] = set()
    collapsed = 0
    unresolved = 0

    for well in sorted(wells):
        group = wells[well]
        if len(group) < 2:
            continue
        survivor = _current_tree_in_well(group)
        if survivor is None:
            unresolved += 1
            continue
        anchor = _point(survivor)
        for rec in group:
            if rec is survivor:
                continue
            here = _point(rec)
            if anchor is None or here is None or _metres(anchor, here) > WELL_SPAN_METRES:
                continue
            absorbed.add(id(rec))
            collapsed += 1

    return [r for r in records if id(r) not in absorbed], collapsed, unresolved


def _genus(rec: dict) -> str:
    name = _text(rec, "scientific")
    return name.split()[0].lower() if name else ""


def _species_contradict(a: dict, b: dict) -> bool:
    """Both rows name a genus and the genera differ. Not the binomial: the two
    surveys disagree on the epithet of the same tree often enough to refuse
    real duplicates."""
    genus_a, genus_b = _genus(a), _genus(b)
    return bool(genus_a) and bool(genus_b) and genus_a != genus_b


def collapse_harvard_resurveys(records: list[dict]) -> tuple[list[dict], int, int]:
    """Absorb Harvard rows that re-survey a city tree; the city row survives.

    The `H` layer is 89% binomial but 0.6% carry a diameter and none a
    planting date, address or inspector, and 96% of it is campus interior the
    city never surveyed. On 155 high-confidence duplicates it agrees with the
    city on the species only 77% of the time, so it fills a blank species and
    never overwrites one.

    Greedy 1:1, shortest pair first, so a loose pair cannot steal a tight one
    and one city tree cannot absorb a run. Contradicting genera never match:
    without that, `H1544191`, an Amelanchier 2.3m from the honeylocust `5512`,
    was absorbed in place of the honeylocust beside it.
    """
    harvard = [r for r in records if r["treeid"].startswith("H")]
    city = [r for r in records if not r["treeid"].startswith("H")]
    if not harvard or not city:
        return records, 0, 0

    cell_lat = MATCH_METRES / 111_320.0
    cell_lon = MATCH_METRES / (111_320.0 * math.cos(math.radians(CAMBRIDGE_LAT)))

    def cell(point: tuple[float, float]) -> tuple[int, int]:
        return int(math.floor(point[0] / cell_lat)), int(math.floor(point[1] / cell_lon))

    buckets: dict[tuple[int, int], list[tuple[dict, tuple[float, float]]]] = defaultdict(list)
    for rec in city:
        point = _point(rec)
        if point is not None:
            buckets[cell(point)].append((rec, point))

    candidates: list[tuple[float, str, str, dict, dict]] = []
    for rec in harvard:
        point = _point(rec)
        if point is None:
            continue
        row, col = cell(point)
        for drow in (-1, 0, 1):
            for dcol in (-1, 0, 1):
                for other, other_point in buckets.get((row + drow, col + dcol), ()):
                    if _species_contradict(rec, other):
                        continue
                    distance = _metres(point, other_point)
                    if distance <= MATCH_METRES:
                        candidates.append(
                            (distance, rec["treeid"], other["treeid"], rec, other)
                        )

    # The ids break distance ties, so page order cannot change the survivor.
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    used_harvard: set[str] = set()
    used_city: set[str] = set()
    species_filled = 0

    for _distance, harvard_id, city_id, harvard_rec, city_rec in candidates:
        if harvard_id in used_harvard or city_id in used_city:
            continue
        used_harvard.add(harvard_id)
        used_city.add(city_id)
        if not _text(city_rec, "scientific") and _text(harvard_rec, "scientific"):
            city_rec["scientific"] = harvard_rec["scientific"]
            species_filled += 1

    return (
        [r for r in records if r["treeid"] not in used_harvard],
        len(used_harvard),
        species_filled,
    )


def build_table(records: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    cities: list[str] = []
    species_list: list[str | None] = []
    cultivars: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for rec in records:
        species = normalize_species(rec.get("scientific"))
        if species is None:
            continue

        raw_id = rec.get("treeid")
        tree_ids.append(f"cam-{raw_id}" if raw_id else None)
        cities.append("USBOS")
        species_list.append(species)
        cultivars.append(_text(rec, "cultivar") or None)

        tree_names.append(normalize_tree_name(rec.get("commonname")))
        plant_dates.append(parse_plant_date(_plant_date(rec)))

        point = _point(rec)
        latitudes.append(point[0] if point else None)
        longitudes.append(point[1] if point else None)

        raw_dbh = rec.get("diameter")
        dbhs.append(float(raw_dbh) if raw_dbh is not None else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "cultivar": pa.array(cultivars, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "plant_date": pa.array(plant_dates, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    records = fetch_all()
    records, dropped_missing_id, duplicate_rows = canonicalize_records(records)
    if dropped_missing_id:
        print(
            f"Cambridge ingest: dropped {dropped_missing_id} rows with null tree_id",
            file=sys.stderr,
        )
    if duplicate_rows:
        print(
            f"Cambridge ingest: collapsed {duplicate_rows} duplicate tree_id rows",
            file=sys.stderr,
        )

    records, dropped_sites, unrecognised_site_types = drop_sites_without_a_tree(records)
    if dropped_sites:
        detail = ", ".join(f"{k} {v}" for k, v in sorted(dropped_sites.items()))
        print(
            f"Cambridge ingest: dropped {sum(dropped_sites.values())} empty planting "
            f"sites ({detail})",
            file=sys.stderr,
        )
    if unrecognised_site_types:
        # The drop matches exact strings, so a renamed value fails open quietly.
        sample = ", ".join(repr(s) for s in sorted(unrecognised_site_types)[:5])
        print(
            f"Cambridge ingest: unrecognised sitetype value(s) {sample}; check whether "
            f"they describe a site with no tree and update SITE_TYPES_WITHOUT_A_TREE",
            file=sys.stderr,
        )

    records, replanted, unresolved_wells = collapse_replanted_wells(records)
    if replanted:
        print(
            f"Cambridge ingest: collapsed {replanted} superseded rows in replanted "
            f"tree wells ({unresolved_wells} wells left intact as unresolvable)",
            file=sys.stderr,
        )

    records, resurveyed, species_filled = collapse_harvard_resurveys(records)
    if resurveyed:
        print(
            f"Cambridge ingest: absorbed {resurveyed} Harvard rows re-surveying a city "
            f"tree within {MATCH_METRES:.0f}m ({species_filled} donated a missing species)",
            file=sys.stderr,
        )

    table = build_table(records)
    dropped_null_species = len(records) - table.num_rows
    if dropped_null_species:
        print(
            f"Cambridge ingest: dropped {dropped_null_species} rows with null species",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city="Cambridge", city_code="USBOS")
    table = enforce_tree_schema(table, city="Cambridge", data_source="CAMBRIDGE")
    emit(table)
