"""The `data_source` picklist has to agree in three places at once.

`_ingest_shared.DATA_SOURCES` is what the ingest scripts validate against, the
per-city `{code}_source` enums in the preql models are what Trilogy validates
against, and the raw datasources' `complete where` clauses are what make the
municipal/community union resolve.  If any of the three drifts, the failure is
either a Trilogy resolution error a long way from the cause, or — worse —
community rows silently vanishing from a city's Parquet.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    CITY_BOUNDS,
    COMMUNITY_DATA_SOURCES,
    DATA_SOURCES,
    MUNICIPAL_DATA_SOURCES,
    community_source_for,
)

ENUM_RE = re.compile(r"key (\w+_source) enum<string>\[(.*?)\];", re.S)
COMPLETE_RE = re.compile(r"complete where city = '(\w+)' and (\w+_source) = '(\w+)'")


def city_models() -> dict[str, Path]:
    """City code -> its tree model, discovered from the `complete where` clauses."""
    models: dict[str, Path] = {}
    for path in sorted(RAW_DIR.glob("*/[a-z]*_tree_info.preql")):
        text = path.read_text(encoding="utf-8")
        match = ENUM_RE.search(text)
        if not match:
            continue
        code = match.group(1)[: -len("_source")].upper()
        models[code] = path
    return models


def enum_values(path: Path) -> list[str]:
    match = ENUM_RE.search(path.read_text(encoding="utf-8"))
    assert match, f"{path.name} declares no `{{code}}_source` enum"
    return re.findall(r"'([^']+)'", match.group(2))


def test_every_city_has_a_source_enum():
    assert set(city_models()) == set(MUNICIPAL_DATA_SOURCES)


def test_every_city_bounds_entry_has_a_community_source():
    # The community ingest drops any row whose city is missing from either map.
    assert set(CITY_BOUNDS) == set(COMMUNITY_DATA_SOURCES)


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_preql_enum_matches_python_picklist(code: str):
    expected = [*MUNICIPAL_DATA_SOURCES[code], community_source_for(code)]
    assert sorted(enum_values(city_models()[code])) == sorted(expected)


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_every_enum_value_is_claimed_by_exactly_one_raw_source(code: str):
    """Trilogy only unions sources that partition the enum, one value each.

    A value with no source leaves the city's Parquet unprovable and the model
    unresolvable; a value claimed twice makes the union ambiguous.
    """
    path = city_models()[code]
    claimed = [
        source
        for city, _key, source in COMPLETE_RE.findall(path.read_text(encoding="utf-8"))
        if city == code
    ]
    assert sorted(claimed) == sorted(enum_values(path))


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_every_city_declares_a_community_partition(code: str):
    """The whole point of the feature: approved trees must reach each city."""
    text = city_models()[code].read_text(encoding="utf-8")
    assert f"'{community_source_for(code)}'" in text
    assert "community_tree_info.py" in text, (
        f"{code} declares a community source label but no datasource reading "
        "the community ingest, so no approved tree would ever land in its Parquet"
    )


def test_data_source_labels_are_globally_unique():
    assert len(DATA_SOURCES) == len(set(DATA_SOURCES))


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_city_freshness_uses_its_own_community_column(code: str):
    """Community freshness must be per city, or one approval rebuilds all 14.

    Trilogy pushes a datasource's `complete where` into row queries but *not*
    into the watermark probe, so isolating cities by filtering rows does not
    work — it stays `SELECT MAX(col) FROM uv_run(...)` over every row. The
    isolation has to come from each city probing its own column.
    """
    text = city_models()[code].read_text(encoding="utf-8")
    column = f"{code.lower()}_community_data_updated_through"
    assert f"greatest({code.lower()}_data_updated_through, {column})" in text
    assert f"{column}: {column}" in text
    # The bare shared name would silently re-couple every city.
    assert ", community_data_updated_through)" not in text


def test_probe_emits_one_column_per_city():
    from community_update_time import column_for, fetch_published_at_by_city

    expected = {column_for(code) for code in MUNICIPAL_DATA_SOURCES}
    declared = set(
        re.findall(
            r"property <\*>\.(\w+_community_data_updated_through) datetime;",
            (RAW_DIR / "community_tree_info.preql").read_text(encoding="utf-8"),
        )
    )
    assert declared == expected
    assert {column_for(c) for c in fetch_published_at_by_city()} == expected
