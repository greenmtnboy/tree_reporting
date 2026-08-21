"""The streaming ingest helpers.

A city ingest that accumulates every source record before transforming holds
the dataset in the most expensive representation available.  Amsterdam's 325k
records peaked at 882 MB of Python heap and failed *every* cloud refresh that
actually rebuilt it, while passing locally every time -- the failure is latent
rather than absent for the others, because a city is only rebuilt when its
source updates.

These helpers are the shared fix, so they are worth testing directly: a bug in
the pagination termination rule silently truncates a city's data, which looks
exactly like a portal that returned less.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    iter_offset_pages,
    stream_to_table,
)


def _transform(rows: list[dict]) -> pa.Table:
    return pa.table({"i": pa.array([r["i"] for r in rows], type=pa.int64())})


class TestIterOffsetPages:
    def test_stops_on_a_short_page(self):
        pages = {0: [{"i": 0}, {"i": 1}], 2: [{"i": 2}]}
        got = list(iter_offset_pages(lambda o: pages.get(o, []), page_size=2))
        assert got == [[{"i": 0}, {"i": 1}], [{"i": 2}]]

    def test_stops_on_an_empty_page(self):
        """A source whose last full page is exactly page_size must still stop:
        the next request returns nothing, and yielding it would append an empty
        chunk (harmless) but looping forever would not."""
        pages = {0: [{"i": 0}], 1: []}
        assert list(iter_offset_pages(lambda o: pages.get(o, []), page_size=1)) == [
            [{"i": 0}]
        ]

    def test_empty_source_yields_nothing(self):
        assert list(iter_offset_pages(lambda o: [], page_size=10)) == []

    def test_requests_ascending_offsets(self):
        seen: list[int] = []

        def fetch(offset: int) -> list[dict]:
            seen.append(offset)
            return [{"i": offset}] * 2 if offset < 4 else []

        list(iter_offset_pages(fetch, page_size=2))
        assert seen == [0, 2, 4]


class TestStreamToTable:
    def test_concatenates_chunks(self):
        chunks = [[{"i": 1}, {"i": 2}], [{"i": 3}]]
        table = stream_to_table(iter(chunks), _transform, label="t")
        assert table.column("i").to_pylist() == [1, 2, 3]

    def test_keep_filters_before_transforming(self):
        """Filtering here rather than after the concat means a dropped record
        never occupies a column -- which is the point, for a source that
        carries rows that are not trees at all."""
        chunks = [[{"i": 1}, {"i": 2}], [{"i": 3}]]
        table = stream_to_table(
            iter(chunks), _transform, keep=lambda r: r["i"] != 2, label="t"
        )
        assert table.column("i").to_pylist() == [1, 3]

    def test_a_chunk_emptied_by_the_filter_is_skipped(self):
        chunks = [[{"i": 2}], [{"i": 3}]]
        table = stream_to_table(
            iter(chunks), _transform, keep=lambda r: r["i"] != 2, label="t"
        )
        assert table.column("i").to_pylist() == [3]

    def test_raises_when_the_source_is_empty(self):
        """Silently emitting zero rows would publish an empty Parquet and read
        as a city that lost its trees."""
        with pytest.raises(RuntimeError, match="no rows"):
            stream_to_table(iter([]), _transform, label="empty city")

    def test_reports_what_it_streamed(self, capsys):
        stream_to_table(iter([[{"i": 1}], [{"i": 2}]]), _transform, label="demo")
        err = capsys.readouterr().err
        assert "streamed 2 record(s) in 2 chunk(s)" in err

    def test_reports_filtered_count(self, capsys):
        stream_to_table(
            iter([[{"i": 1}, {"i": 2}]]),
            _transform,
            keep=lambda r: r["i"] != 2,
            label="demo",
        )
        assert "1 filtered out" in capsys.readouterr().err
