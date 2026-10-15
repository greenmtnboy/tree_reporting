"""Socrata paging stops on a short page, and a short page is not proof the data
ran out.

From 2026-10-08 to 10-13 New York's published parquet held exactly 750,000 of
its 1,123,163 trees -- three full 250k pages and nothing after -- with no error
anywhere, and the rollup published that.  Both Socrata pagers now read
`count(*)` first and refuse a run that fetched fewer rows.  The HTTP layer is
faked.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))
sys.path.insert(0, str(RAW_DIR / "usnyc"))

import shared.platforms.socrata as socrata  # noqa: E402
from shared.ingest import UpstreamUnavailable  # noqa: E402
from shared.platforms.socrata import SocrataDataset, iter_rows  # noqa: E402

DATASET = SocrataDataset("data.example.test", "abcd-1234")


def fake_portal(monkeypatch, count: int, pages: list[int]) -> list[dict]:
    """`count(*)` answers `count`; the paged requests answer `pages` rows each."""
    requests: list[dict] = []
    remaining = list(pages)

    def get(url, params=None, **_):
        requests.append(dict(params or {}))
        if params and params.get("$select") == "count(*) as n":
            return [{"n": str(count)}]
        size = remaining.pop(0) if remaining else 0
        return [{"id": str(i)} for i in range(size)]

    monkeypatch.setattr(socrata, "get_json_with_retry", get)
    return requests


def rows(pages) -> int:
    return sum(len(page) for page in pages)


@pytest.mark.parametrize(
    "count,pages",
    [
        (5, [2, 2, 1]),  # ends on a short page
        (4, [2, 2, 0]),  # an exact multiple ends on an empty one
        (0, [0]),  # an empty dataset is not an error
    ],
)
def test_a_complete_dataset_pages_through(monkeypatch, count, pages):
    fake_portal(monkeypatch, count, pages)
    assert rows(iter_rows(DATASET, page_size=2)) == count


def test_an_empty_page_before_the_end_fails(monkeypatch):
    """The October shape: full pages, then an empty one, well short of the count."""
    fake_portal(monkeypatch, 7, [2, 2, 0])
    with pytest.raises(UpstreamUnavailable, match="4 rows of the 7"):
        rows(iter_rows(DATASET, page_size=2))


def test_a_short_page_before_the_end_fails(monkeypatch):
    fake_portal(monkeypatch, 7, [2, 1])
    with pytest.raises(UpstreamUnavailable, match="3 rows of the 7"):
        rows(iter_rows(DATASET, page_size=2))


def test_the_count_carries_the_where(monkeypatch):
    requests = fake_portal(monkeypatch, 1, [1])
    rows(iter_rows(DATASET, where="status = 'Alive'", page_size=2))
    assert requests[0] == {"$select": "count(*) as n", "$where": "status = 'Alive'"}
    assert requests[1]["$where"] == "status = 'Alive'"


def test_a_dataset_that_grows_mid_run_passes(monkeypatch):
    fake_portal(monkeypatch, 3, [2, 2, 0])
    assert rows(iter_rows(DATASET, page_size=2)) == 4


CSV_HEADER = "objectid,genusspecies,dbh,planteddate,location\n"


def nyc_page(start: int, size: int) -> io.BytesIO:
    lines = [
        f"{i},Acer rubrum,10,,POINT (-73.98 40.75)\n" for i in range(start, start + size)
    ]
    return io.BytesIO((CSV_HEADER + "".join(lines)).encode())


def run_nyc(monkeypatch, count: int, pages: list[int]) -> int:
    import nyc_tree_info

    sizes = list(pages)
    monkeypatch.setattr(nyc_tree_info, "PAGE_SIZE", 2)
    monkeypatch.setattr(nyc_tree_info, "EMIT_ROWS", 2)
    monkeypatch.setattr(nyc_tree_info, "row_count", lambda dataset: count)
    monkeypatch.setattr(
        nyc_tree_info,
        "download_csv_page",
        lambda offset=0: nyc_page(offset, sizes.pop(0) if sizes else 0),
    )
    return sum(t.num_rows for t in nyc_tree_info.iter_pages())


def test_nyc_pages_the_whole_city(monkeypatch):
    assert run_nyc(monkeypatch, 5, [2, 2, 1]) == 5


def test_nyc_pages_a_city_that_is_an_exact_multiple(monkeypatch):
    """The page after the last full one is empty; that is the end, not a failure."""
    assert run_nyc(monkeypatch, 4, [2, 2, 0]) == 4


def test_nyc_refuses_a_city_cut_short(monkeypatch):
    with pytest.raises(UpstreamUnavailable, match="4 rows of the 7"):
        run_nyc(monkeypatch, 7, [2, 2, 0])
