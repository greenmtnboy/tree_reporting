"""A city with a satellite partition must have every half of the wiring.

The failure modes mirror the community and OSM ones: a missing datasource
silently emits zero satellite rows, a shared or missing freshness column
either re-couples cities or never marks the Parquet stale, and a shared merge
that does not know the label classes every satellite row as municipal -- at
which point a detection can *own* a cluster over the city's own inventory row.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    DATA_SOURCES,
    MUNICIPAL_DATA_SOURCES,
    SATELLITE_DATA_SOURCES,
    satellite_source_for,
)
from test_data_sources import city_models, enum_values  # noqa: E402


def test_every_satellite_city_is_a_city():
    assert set(SATELLITE_DATA_SOURCES) <= set(MUNICIPAL_DATA_SOURCES)
    for code, label in SATELLITE_DATA_SOURCES.items():
        assert label == satellite_source_for(code) == f"SATELLITE_{code}"
        assert label in DATA_SOURCES


@pytest.mark.parametrize("code", sorted(SATELLITE_DATA_SOURCES))
def test_satellite_city_is_fully_wired(code: str):
    lower = code.lower()
    text = city_models()[code].read_text(encoding="utf-8")
    label = SATELLITE_DATA_SOURCES[code]
    assert label in enum_values(city_models()[code])
    assert "import ..satellite_tree_info;" in text, (
        f"{code} does not import the shared satellite model, so its freshness "
        "column is undeclared"
    )
    assert "file `../satellite_tree_info.py`" in text, (
        f"{code} declares {label} but no datasource reads the satellite ingest, "
        "so no reviewed detection would ever land in its Parquet"
    )
    assert f"complete where city = '{code}' and {lower}_source = '{label}'" in text
    # The ingest emits every wired city's rows; the `where` is what narrows
    # them (and what Trilogy compiles into the --filter pushdown).
    assert re.search(
        rf"file `\.\./satellite_tree_info\.py`\s*\nwhere city = '{code}';", text
    ), f"{code}'s satellite datasource has no `where city = '{code}'`"
    column = f"{lower}_satellite_data_updated_through"
    assert f"{column}: {column}" in text, f"{code} does not probe its own satellite column"
    assert "file `../satellite_update_time.py`" in text
    assert column in text.split("greatest(", 1)[1].split(")", 1)[0], (
        f"{code}'s published watermark must include {column} or a publish "
        "from the reviewer never rebuilds the Parquet"
    )


@pytest.mark.parametrize("code", sorted(set(MUNICIPAL_DATA_SOURCES) - set(SATELLITE_DATA_SOURCES)))
def test_a_city_without_imagery_declares_no_satellite_partition(code: str):
    """The registry is the authority: a city that gains a SATELLITE_ enum
    value by copy-paste, without imagery behind it, would fail resolution with
    `no complete sources found` a long way from the cause."""
    text = city_models()[code].read_text(encoding="utf-8")
    assert "SATELLITE_" not in text, (
        f"{code} mentions a satellite partition but is not in SATELLITE_DATA_SOURCES"
    )


def test_probe_emits_one_column_per_wired_city():
    from satellite_update_time import column_for, fetch_published_at_by_city

    expected = {column_for(code) for code in SATELLITE_DATA_SOURCES}
    declared = set(
        re.findall(
            r"property <\*>\.(\w+_satellite_data_updated_through) datetime;",
            (RAW_DIR / "satellite_tree_info.preql").read_text(encoding="utf-8"),
        )
    )
    assert declared == expected
    assert {column_for(c) for c in fetch_published_at_by_city()} == expected


def test_the_shared_merge_classes_the_satellite_partition():
    """Without its own class a satellite row reads as municipal, owns its
    cluster, and can absorb the city's real inventory row."""
    text = (RAW_DIR / "tree_dedup.preql").read_text(encoding="utf-8")
    assert "= 'SATELLITE_' then 'satellite'" in text
    assert "(max(value ? source_class = 'satellite') by cluster_id)" in text
    # Below municipal and community, above OSM: the order of the coalesce arms
    # is the precedence.
    arms = re.findall(r"max\(value \? source_class = '(\w+)'\) by cluster_id", text)
    assert arms == ["community", "municipal", "satellite", "osm"]
