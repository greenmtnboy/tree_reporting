#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Seoul's protected and veteran trees (보호수 및 노거수), as landmarks.

Source: Seoul Open Data Plaza dataset OA-1323, "서울시 보호수 및 노거수
위치정보 (좌표계: WGS1984)", published by the Seoul Metropolitan Government
(provided by the Garden City Bureau, Landscape Division, 정원도시국 조경과).
Licence: "이용허락범위 제한 없음" (no restriction on permitted use) on
https://data.seoul.go.kr/dataList/OA-1323/S/1/datasetView.do; the national
portal's copy (data.go.kr 15125822) is KOGL Type 1 (공공누리 제1유형, source
attribution).  346 trees, last refreshed 2022-10-14.

The register is read live from the portal's sheet service -- the endpoint the
dataset page's grid calls (`/dataList/dataView.do`), which needs no API key and
returns every row in one page.  Its body is a JavaScript object literal with
bare keys (`{result:"ok",page:{...},list:[{UNQ_NO:"19",...}]}`), not JSON, so
`js_literal_to_json` quotes the keys outside string values before parsing.

**Three kinds of tree, and `lm_type` says which.**  The register mixes trees
with a designation date (보호수, designated protected trees, 187), candidates
for designation (`지정품목` = 후보목, 113) and veteran trees on the register
with neither (노거수, 46).  The name is the tree's Korean species name plus
that kind (`느티나무 보호수`), which is how the register itself talks about
them; the address is the register's 소재지, prefixed with the district when the
cell omits it.

**`UNQ_NO` is the id**, unique and non-null over all 346 rows.  Coordinates are
WGS84 (126.80-127.18 E, 37.44-37.69 N), inside the city's bounds.

**The designation date is parsed leniently and only for its year**: the register
writes `1981.10.27`, `72.10.12` (a two-digit year, 1900s) and `19740420`, plus
one `19891.10.27`.  A year outside 1900-2030 is left null rather than guessed.
"""

import json
import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    CITY_BOUNDS,
    UpstreamUnavailable,
    emit,
    post_with_retry,
)

DATASET_ID = "OA-1323"
SHEET_URL = (
    "https://data.seoul.go.kr/dataList/dataView.do?onepagerow=1000&srvType=S"
    f"&infId={DATASET_ID}&serviceKind=0&pageNo=1&ssUserId=SAMPLE_VIEW&strWhere=&strOrderby="
)

LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = CITY_BOUNDS["KRSEL"]

_BARE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def js_literal_to_json(text: str) -> str:
    """Quote bare object keys and drop trailing commas outside string values."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif ch.isalpha() or ch == "_":
            word = _BARE.match(text, i).group(0)
            out.append(word if word in ("true", "false", "null") else f'"{word}"')
            i += len(word)
        elif ch == ",":
            k = i + 1
            while k < n and text[k].isspace():
                k += 1
            if k >= n or text[k] not in "]}":
                out.append(ch)
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def fetch_register() -> list[dict]:
    response = post_with_retry(SHEET_URL, data={"browser1": "chrome", "version": "1"}, timeout=120, backoff=5.0)
    text = response.text.strip()
    if not text.startswith("{result:"):
        raise UpstreamUnavailable(f"{SHEET_URL} did not return the sheet payload")
    payload = json.loads(js_literal_to_json(text))
    if payload.get("result") != "ok":
        raise UpstreamUnavailable(f"{SHEET_URL} answered result={payload.get('result')!r}")
    rows = payload["list"]
    total = payload["page"]["totalCount"]
    if len(rows) != total:
        raise RuntimeError(f"Seoul protected trees: got {len(rows)} of {total} rows in one page")
    return rows


def clean(value) -> str | None:
    text = " ".join(str(value or "").split())
    return text if text and text not in ("0", "null") else None


def parse_year(value) -> int | None:
    text = clean(value)
    if text is None:
        return None
    digits = re.match(r"\d+", text)
    if digits is None:
        return None
    raw = digits.group(0)
    if len(raw) == 8:          # 19740420
        year = int(raw[:4])
    elif len(raw) == 2:        # 72.10.12
        year = 1900 + int(raw)
    elif len(raw) == 4:        # 1981.10.27
        year = int(raw)
    else:                      # 19891.10.27 and anything else
        return None
    return year if 1900 <= year <= 2030 else None


def kind_of(row: dict) -> str:
    if clean(row.get("DSGN_ITEM")) == "후보목":
        return "보호수 후보목"
    if clean(row.get("DSGN_YMD")):
        return "보호수"
    return "노거수"


def address_of(row: dict) -> str | None:
    location = clean(row.get("LCTN"))
    district = clean(row.get("GU_NM"))
    if location and district and not location.startswith(district):
        return f"{district} {location}"
    return location or district


def transform(rows: list[dict]) -> pa.Table:
    records = []
    unreadable = outside = 0
    for row in rows:
        raw_id = clean(row.get("UNQ_NO"))
        try:
            lon, lat = float(row.get("LOT")), float(row.get("LAT"))
        except (TypeError, ValueError):
            unreadable += 1
            continue
        if raw_id is None:
            unreadable += 1
            continue
        if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
            outside += 1
            continue
        kind = kind_of(row)
        species = clean(row.get("TRSPC_KORN"))
        records.append({
            "landmark_id": f"sel-bh-{raw_id}",
            "name": f"{species} {kind}" if species else kind,
            "geometry_raw": f"POINT({lon} {lat})",
            "address": address_of(row),
            "borough": clean(row.get("GU_NM")),
            "lm_type": kind,
            "year_designated": parse_year(row.get("DSGN_YMD")),
        })
    print(
        f"Seoul protected trees: {len(rows)} row(s); dropped {unreadable} with no id "
        f"or coordinate and {outside} outside the city, leaving {len(records)}",
        file=sys.stderr,
    )
    return pa.table({
        "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
        "city": pa.array(["KRSEL"] * len(records), type=pa.string()),
        "name": pa.array([r["name"] for r in records], type=pa.string()),
        "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
        "address": pa.array([r["address"] for r in records], type=pa.string()),
        "borough": pa.array([r["borough"] for r in records], type=pa.string()),
        "lm_type": pa.array([r["lm_type"] for r in records], type=pa.string()),
        "year_designated": pa.array([r["year_designated"] for r in records], type=pa.int64()),
    })


def main() -> None:
    emit(transform(fetch_register()))


if __name__ == "__main__":
    main()
