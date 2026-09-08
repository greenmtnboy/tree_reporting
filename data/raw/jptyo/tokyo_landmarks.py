#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Tokyo's metropolitan-designated cultural properties and historic sites.

Source: two CSV registers published by the Tokyo Metropolitan Board of
Education (東京都教育庁) on the same catalog as the trees, under CC BY 4.0:

| package              | resource                        | rows |
|----------------------|---------------------------------|-----:|
| `t000021d0000000017` | 文化財一覧 (cultural properties)  |  245 |
| `t000021d0000000025` | 東京都指定史跡データ一覧 (sites)  |   42 |

This is the runbook's first-preference landmark source -- an official
designation registry, on the same portal as the trees, read live.  No Nominatim
geocoding, no committed CSV, no staging object.

**Both registers, because they are mostly disjoint.**  The obvious reading is
that the historic-sites file is a subset: its 34 distinct names include 史跡 and
the cultural-property file has a `種類` of 史跡 on 47 rows.  Measured, only 12
names are shared, so taking the larger file alone would drop 22 designated
sites.  They are unioned and deduplicated on the name, with the cultural
property winning a tie -- it carries an official English name and a `種類`
classification the sites file does not have.

**`NO` is unique within a register and restarts across them**, so the id
carries which register it came from.  Both files start at 1.

**The name is the register's own English one where it has it**, which is all
245 cultural properties (`名称_英語`, "Yushima-tenmangu Omote Torii (Front
Shrine Gate of Yushima-tenmangu Shrine)").  The sites file publishes no English
column at all, so those 42 keep the Japanese name -- better than dropping a
designated site, and the map renders it fine.

**One row has a shifted column and is dropped.**  `0000000246` (下宅部遺跡)
publishes `緯度` as `035.766250` and `経度` as the literal `, 139.451301`: a
comma inside an unquoted latitude cell pushed the rest of the row one field
right.  The longitude is readable in the debris and is deliberately *not*
recovered -- repairing a column shift by string surgery is how a parser starts
guessing, and this is one row of 287.  It is dropped with a logged count.

**Eighteen designated properties are on the Izu Islands and are out of scope.**
Hachijōjima and Aogashima are administratively part of Tokyo Metropolis, and
this register covers them: 八丈島湯浜遺跡出土品 and the rest sit at 32.5-33.1 N,
some 290 km south of the mainland.  `CITY_BOUNDS['JPTYO']` is the mainland
metropolis and nothing else, because that is where every one of the 228,051
trees is and because the bounds are what the Overpass extraction asks for -- an
island-inclusive box would sweep 1,700 km of ocean.  So those rows are filtered
by the city's *own* bounds rather than by a second copy of them, and they are
counted separately from the parse failure above: "outside the city" and "this
file has a column-order bug" are different facts and a single reject count
would hide the second behind the first.

**The designation year is western where the register writes one.**  245 of the
cultural properties date as `1970-08-03` or `1969/3/27`; 15 of the sites use
Japanese era names instead (`昭和46年3月29日史跡指定　平成19年3月15日追加指定`)
and several record more than one date, a designation followed by an extension.
A four-digit western year is extracted where one is present and left null
otherwise, rather than converting eras -- the field is context on a pin, and a
half-converted date that silently lands 25 years out would be worse than a null.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, read_csv_rows
from _ingest_shared import CITY_BOUNDS, emit

CULTURAL_PROPERTIES = CkanResource(
    "catalog.data.metro.tokyo.lg.jp", "25c5e6f7-0f8d-44d8-ac37-127e008f7a69"
)
HISTORIC_SITES = CkanResource(
    "catalog.data.metro.tokyo.lg.jp", "6fb22ee3-5138-4fee-b611-e041f2e47351"
)

# The city's own bounds, not a second copy: this is what decides that the Izu
# Islands are out of scope, and it is the same box the trees are validated
# against and the OSM extraction asks Overpass for.
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = CITY_BOUNDS["JPTYO"]

_WESTERN_YEAR = re.compile(r"(1[89]\d\d|20\d\d)")


def clean(value) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def parse_year(value) -> int | None:
    """The first four-digit western year in a designation date, or None."""
    match = _WESTERN_YEAR.search(str(value or ""))
    return int(match.group(1)) if match else None


def parse_point(row: dict) -> tuple[float, float] | None:
    """`(lon, lat)`, or None when the register's cells do not parse."""
    try:
        lat = float((row.get("緯度") or "").strip())
        lon = float((row.get("経度") or "").strip())
    except (TypeError, ValueError):
        return None
    return lon, lat


def in_city(point: tuple[float, float]) -> bool:
    lon, lat = point
    return LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX


def read_register(resource: CkanResource, prefix: str, label: str) -> list[dict]:
    rows = read_csv_rows(resource)
    kept: list[dict] = []
    unreadable = offshore = 0
    for row in rows:
        name = clean(row.get("名称_英語")) or clean(row.get("名称"))
        point = parse_point(row)
        raw_id = clean(row.get("NO"))
        if name is None or raw_id is None or point is None:
            unreadable += 1
            continue
        if not in_city(point):
            offshore += 1
            continue
        kept.append(
            {
                "landmark_id": f"tyo-{prefix}-{raw_id}",
                "name": name,
                "japanese_name": clean(row.get("名称")),
                "lon": point[0],
                "lat": point[1],
                "address": clean(row.get("住所")),
                # 種類 is the finer classification (建造物, 天然記念物, 史跡) and
                # only the cultural-property register has it; 文化財分類 is the
                # designation itself (都指定文化財 / 都指定史跡), which both do.
                "property_type": clean(row.get("種類")) or clean(row.get("文化財分類")),
                "year_designated": parse_year(row.get("文化財指定日")),
            }
        )
    print(
        f"{label}: {len(rows)} row(s); dropped {unreadable} with no name or no "
        f"readable coordinate and {offshore} outside the mainland metropolis "
        f"(the Izu Islands), leaving {len(kept)}",
        file=sys.stderr,
    )
    return kept


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["JPTYO"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array(
                [f"POINT({r['lon']} {r['lat']})" for r in records], type=pa.string()
            ),
            "address": pa.array([r["address"] for r in records], type=pa.string()),
            "property_type": pa.array(
                [r["property_type"] for r in records], type=pa.string()
            ),
            "year_designated": pa.array(
                [r["year_designated"] for r in records], type=pa.int64()
            ),
        }
    )


def main() -> None:
    records = read_register(CULTURAL_PROPERTIES, "cp", "Tokyo cultural properties")
    sites = read_register(HISTORIC_SITES, "hs", "Tokyo designated historic sites")

    # The cultural-property register wins a shared name: it carries the English
    # name and the finer classification.
    seen = {r["japanese_name"] for r in records if r["japanese_name"]}
    duplicates = 0
    for record in sites:
        if record["japanese_name"] and record["japanese_name"] in seen:
            duplicates += 1
            continue
        seen.add(record["japanese_name"])
        records.append(record)
    print(
        f"Tokyo landmarks: {len(records)} designated place(s) after dropping "
        f"{duplicates} the two registers share",
        file=sys.stderr,
    )
    emit(transform(records))


if __name__ == "__main__":
    main()
