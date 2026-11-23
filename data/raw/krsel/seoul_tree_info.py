#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["openpyxl", "pyarrow", "requests", "pytrilogy"]
# ///
"""Seoul's street trees, from Seoul Open Data Plaza (서울 열린데이터광장).

Source: dataset OA-23040, "서울시 가로수 위치정보" (Seoul street-tree
locations), published by the Seoul Metropolitan Government, Garden City Bureau,
Landscape Division (정원도시국 조경과).  Licence: "이용허락범위 제한 없음" (no
restriction on permitted use) on the dataset page,
https://data.seoul.go.kr/dataList/OA-23040/F/1/datasetView.do, and on the
national portal's listing of the same dataset (data.go.kr 277368).

**One xlsx, one sheet per managing authority.**  The file is
"<year> 서울시 가로수 위치정보.xlsx" -- 287,636 rows in 27 sheets: the 25
autonomous districts (자치구) and two city agencies, the Central Park & Leisure
Center (중부공원여가센터) and the Seoul Facilities Corporation (서울시설공단).
Every sheet has the same shape: a title row, a `관리청: <authority>` row, then
the header `자치구, 노선, 수종, 도로명 주소, 지번 주소, 좌표(경도), 좌표(위도)`.
The header is found by name, and a sheet without it raises.

**The filename is year-stamped, so it is discovered, never hard-coded.**
`current_data_file()` reads the dataset page's file table (the same table a
browser shows) and picks the `데이터` row; the download is the POST the page's
`downloadFile()` makes.  The freshness probe reads the same row's 수정일, so
the two cannot disagree about which file is current.  The same rows are also
served by the keyed Open API `aiStreetTree`; the file needs no key.

**No size, no date.**  The file has no DBH (흉고직경), no root-collar
diameter, no height, crown or planting date -- position and species only.  The
2012 inventory (OA-1325, data.go.kr 15046229) had all of those plus a tree id,
but it is fourteen years stale; this is the live one.

**The source publishes no tree id, and this file synthesises one from the
coordinate** (`coordinate_tree_id`, EXTENDING.md step 1), as Tokyo does.
Coordinates are WGS84 decimal degrees (checked by value: 126.80-127.18 E,
37.43-37.69 N), published as text to 2-9 decimal places.  At the helper's 7 dp,
58 points carry two rows each; a pair that agrees on species is one tree
recorded twice and keeps one row, any other pair is dropped
(`drop_stacked_coordinates`).

**Species is a Korean vernacular name and nothing else** -- 73 values.
`shared.species.korean` resolves them; read its docstring before touching a
mapping.  Two values are not trees and their rows are dropped with a count:
`공분` (an empty tree pit, 5,881 rows) and a heat-wave shade canopy.

**`borough` is the district the tree stands in**, read from the address
(`서울특별시 <구> ...`), not the sheet's `자치구` column: that column names the
*managing* authority, which is an agency on two sheets and is not always the
district of the address (Yongsan manages trees on Mapo's side of a boundary
road).  Where neither address names one of the 25 districts, a district sheet
falls back to its own district and an agency sheet to null.
"""

import io
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    UpstreamUnavailable,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    get_with_retry,
    post_with_retry,
    validate_coordinates,
)
from shared.species.korean import (  # noqa: E402
    cultivar_from_korean_name,
    is_korean_non_tree,
    species_from_korean_name,
)

DATASET_ID = "OA-23040"
DATASET_PAGE = f"https://data.seoul.go.kr/dataList/{DATASET_ID}/F/1/datasetView.do"
DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"

# Korea Standard Time; the portal's dates are local calendar days.
KST = timezone(timedelta(hours=9))

HEADER = ("자치구", "노선", "수종", "도로명 주소", "지번 주소", "좌표(경도)", "좌표(위도)")

DISTRICTS = frozenset({
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구",
    "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구",
    "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구",
    "강동구",
})
_ADDRESS_DISTRICT = re.compile(r"서울(?:특별시|시)?\s+(\S+구)(?:\s|$)")

# Seoul spans roughly 126.76-127.19 E, 37.41-37.72 N; anything outside this
# wider box is a swapped or truncated cell, not a tree in a neighbouring city.
LON_RANGE = (126.0, 128.0)
LAT_RANGE = (37.0, 38.0)

# A coordinate cell that does not parse; above this share the file changed shape.
MAX_UNREADABLE = 0.01

_FILE_ROW = re.compile(r'<tr id="fileTr_(\d+)">(.*?)</tr>', re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")
_INF_SEQ = re.compile(r'<form name="frmFile".*?name="infSeq" value="(\d+)"', re.S)
_PAGE_DATE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")


def _text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())


def current_data_file() -> dict:
    """The dataset page's current data file: `{seq, inf_seq, name, modified}`.

    `modified` is the row's 수정일 as midnight KST.  Raises UpstreamUnavailable
    when the page is not the dataset page at all (a maintenance or error page);
    raises RuntimeError when it is, but holds no xlsx data row or an unreadable
    date -- the portal changed its layout, which must stay loud.
    """
    page = get_with_retry(DATASET_PAGE, timeout=60).text
    if f'value="{DATASET_ID}"' not in page or 'id="fileDownList"' not in page:
        raise UpstreamUnavailable(f"{DATASET_PAGE} is not the {DATASET_ID} dataset page")
    inf_seq = _INF_SEQ.search(page)
    if inf_seq is None:
        raise RuntimeError(f"{DATASET_PAGE}: no infSeq on the file download form")
    candidates = []
    for seq, row in _FILE_ROW.findall(page):
        cells = [_text(c) for c in _CELL.findall(row)]
        # NO, 항목, 파일명, 용량 (MB), 수정일, 내려받기
        if len(cells) < 5 or cells[1] != "데이터" or not cells[2].lower().endswith(".xlsx"):
            continue
        stamp = _PAGE_DATE.search(cells[4])
        if stamp is None:
            raise RuntimeError(f"{DATASET_PAGE}: unreadable 수정일 {cells[4]!r} for {cells[2]!r}")
        modified = datetime(*map(int, stamp.groups()), tzinfo=KST).astimezone(timezone.utc)
        candidates.append({"seq": seq, "inf_seq": inf_seq.group(1), "name": cells[2], "modified": modified})
    if not candidates:
        raise RuntimeError(f"{DATASET_PAGE}: no xlsx 데이터 row in the file table")
    # One today; if the portal ever lists two years side by side, the newest
    # posting is the current inventory.
    return max(candidates, key=lambda c: (c["modified"], c["name"]))


def download(file: dict) -> bytes:
    response = post_with_retry(
        DOWNLOAD_URL,
        data={"infId": DATASET_ID, "seqNo": "", "seq": file["seq"], "infSeq": file["inf_seq"]},
        timeout=300,
        backoff=5.0,
    )
    body = response.content
    # An xlsx is a zip; anything else is the portal's error page.
    if not body.startswith(b"PK"):
        raise UpstreamUnavailable(f"{DOWNLOAD_URL} did not return an xlsx for {file['name']!r}")
    print(f"Seoul OpenData: downloaded {file['name']!r} ({len(body):,} bytes)", file=sys.stderr)
    return body


def read_rows(body: bytes):
    """Yield `(sheet, row dict)` for every data row of every sheet."""
    import openpyxl  # only the ingest needs it; the probe imports this module

    workbook = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            header = None
            for values in sheet.iter_rows(values_only=True):
                cells = tuple("" if v is None else str(v).strip() for v in values)
                if header is None:
                    if cells[: len(HEADER)] == HEADER:
                        header = HEADER
                    continue
                if not any(cells):
                    continue
                yield sheet.title, dict(zip(HEADER, cells))
            if header is None:
                raise RuntimeError(f"Seoul OpenData: sheet {sheet.title!r} has no {HEADER} header")
    finally:
        workbook.close()


def parse_point(row: dict) -> tuple[float, float] | None:
    try:
        lon = float(row["좌표(경도)"])
        lat = float(row["좌표(위도)"])
    except (TypeError, ValueError):
        return None
    if not (LON_RANGE[0] < lon < LON_RANGE[1] and LAT_RANGE[0] < lat < LAT_RANGE[1]):
        return None
    return lon, lat


def district_of(row: dict, sheet: str) -> str | None:
    for column in ("지번 주소", "도로명 주소"):
        match = _ADDRESS_DISTRICT.search(row.get(column) or "")
        if match and match.group(1) in DISTRICTS:
            return match.group(1)
    return sheet if sheet in DISTRICTS else None


def build_table(rows) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    borough: list[str | None] = []
    latitude: list[float] = []
    longitude: list[float] = []

    total = unreadable = not_trees = 0
    for sheet, row in rows:
        total += 1
        label = row["수종"]
        if is_korean_non_tree(label):
            not_trees += 1
            continue
        point = parse_point(row)
        if point is None:
            unreadable += 1
            continue
        lon, lat = point
        tree_id.append(coordinate_tree_id("krsel", lon, lat))
        species.append(species_from_korean_name(label))
        cultivar.append(cultivar_from_korean_name(label))
        borough.append(district_of(row, sheet))
        longitude.append(lon)
        latitude.append(lat)

    if total == 0:
        raise RuntimeError("Seoul OpenData: the workbook has no data rows")
    if unreadable / total > MAX_UNREADABLE:
        raise RuntimeError(
            f"Seoul OpenData: {unreadable} of {total} rows have no readable coordinate; "
            f"the file changed shape"
        )
    print(
        f"Seoul OpenData: {total} row(s); dropped {not_trees} that are not trees "
        f"(empty pits 공분, shade canopies) and {unreadable} with no readable "
        f"coordinate, leaving {len(tree_id)}",
        file=sys.stderr,
    )
    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["KRSEL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "borough": pa.array(borough, type=pa.string()),
        # No planting date, no size.  Still typed: an untyped pa.null() lands
        # in the parquet as INT32 and breaks year().
        "plant_date": pa.array([None] * n, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
    })


def main() -> None:
    file = current_data_file()
    table = build_table(read_rows(download(file)))
    table = drop_stacked_coordinates(table, label="Seoul OpenData", identical=("species",))
    table = validate_coordinates(table, city="Seoul", city_code="KRSEL")
    table = enforce_tree_schema(table, city="Seoul", data_source="SEOUL_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
