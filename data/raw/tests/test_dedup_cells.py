"""The dedup grid cell table that tree_dedup.preql carries as an inline VALUES block.

The model matches rows across sources by grid cell, and the cell size is a
per-city calibration (see DEDUP_CELL_METRES).  Two failure modes are silent:
a city missing from the table joins to no cell, so its cluster ids never
form and nothing is ever deduplicated; and a hand-copied degree constant
drifts from the metres written next to it.  The table is keyed by city,
converted to degrees in one place, and generated into the model by
dedup_cells.py -- the check here is what catches an edit to the metres that
was not followed by `uv run dedup_cells.py --write`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    CITY_BOUNDS,
    DEDUP_CELL_METRES,
    MUNICIPAL_DATA_SOURCES,
    dedup_cell_degrees,
)
from dedup_cells import MODEL, build_table, current_block, render_block  # noqa: E402


def test_every_city_has_a_cell():
    assert set(DEDUP_CELL_METRES) == set(MUNICIPAL_DATA_SOURCES)
    assert set(DEDUP_CELL_METRES) <= set(CITY_BOUNDS)


@pytest.mark.parametrize("code", sorted(DEDUP_CELL_METRES))
def test_cell_is_a_calibrated_size(code: str):
    """10 m (a 5 m guarantee) or 20 m (10 m); anything else is a typo."""
    assert DEDUP_CELL_METRES[code] in (10, 20)


@pytest.mark.parametrize("code", sorted(DEDUP_CELL_METRES))
def test_degrees_follow_the_metres(code: str):
    lat_deg, lon_deg = dedup_cell_degrees(code)
    metres = DEDUP_CELL_METRES[code]
    assert lat_deg == pytest.approx(metres / 111320.0)
    # A degree of longitude is shorter than a degree of latitude everywhere
    # off the equator, so the cell is wider in longitude degrees.
    assert lon_deg > lat_deg
    lat_min, lat_max, _, _ = CITY_BOUNDS[code]
    assert lon_deg == pytest.approx(lat_deg / math.cos(math.radians((lat_min + lat_max) / 2)))


def test_tempe_matches_the_hand_calibrated_constants():
    """The constants the models carried before the table existed."""
    lat_deg, lon_deg = dedup_cell_degrees("USTEM")
    assert lat_deg == pytest.approx(0.00008983, abs=1e-8)
    assert lon_deg == pytest.approx(0.00010759, abs=1e-8)


def test_model_block_is_generated_from_the_table():
    """tree_dedup.preql's VALUES block must match DEDUP_CELL_METRES exactly.

    It is an inline `query` datasource rather than a python one so that the
    resolver service, which has the .preql text and none of the scripts, still
    binds the cell size -- an unbound root concept there made the planner
    error out instead of reading the published parquet.
    """
    text = MODEL.read_text(encoding="utf-8")
    assert current_block(text) == render_block(), (
        "tree_dedup.preql is stale; run `cd data/raw && uv run dedup_cells.py --write`"
    )
    for code in DEDUP_CELL_METRES:
        assert f"('{code}', " in text


def test_table_has_one_row_per_city():
    table = build_table()
    assert table.num_rows == len(DEDUP_CELL_METRES)
    assert table.schema.field("cell_lat_deg").type == "double"
    assert build_table("USTEM").column("city").to_pylist() == ["USTEM"]
