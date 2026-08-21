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
    OSM_DATA_SOURCES,
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
    if code in OSM_DATA_SOURCES:
        expected.append(OSM_DATA_SOURCES[code])
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
    # Prefix match: cities with an OSM staging source append a third argument.
    assert f"greatest({code.lower()}_data_updated_through, {column}" in text
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


@pytest.mark.parametrize("code", sorted(OSM_DATA_SOURCES))
def test_osm_city_is_fully_wired(code: str):
    """A city in OSM_DATA_SOURCES must have every half of the OSM wiring.

    The failure modes mirror the community ones: a missing staging datasource
    silently emits zero OSM rows, a shared/missing freshness column either
    re-couples cities or never marks the Parquet stale, and a dropped
    is_duplicate derivation publishes overlapping rows with no flag for the
    frontend to filter on.
    """
    lower = code.lower()
    text = city_models()[code].read_text(encoding="utf-8")
    assert f"'{OSM_DATA_SOURCES[code]}'" in text
    assert f"{lower}_osm_staging.parquet" in text, (
        f"{code} declares an OSM source label but no staging datasource"
    )
    # Staged in GCS, never a repo-relative path.  The probe's watermark is the
    # object's Last-Modified; a committed copy would be read at its mtime, which
    # git does not preserve, so every fresh clone would look like new data and
    # the city would rebuild on every tick.
    assert f"file `./{lower}_osm_staging.parquet`" not in text, (
        f"{code} reads its OSM staging parquet from the repo; it belongs in GCS"
    )
    assert f"staging/{lower}_osm_staging.parquet`" in text, (
        f"{code}'s OSM staging datasource must point at the GCS staging prefix"
    )
    column = f"{lower}_osm_data_updated_through"
    assert f"data_updated_through: {column}" in text
    assert column in text.split("greatest(", 1)[1].split(")", 1)[0], (
        f"{code}'s published watermark must include {column} or a re-extraction "
        "never rebuilds the Parquet"
    )
    assert f"{lower}_is_duplicate" in text
    assert f"is_duplicate: {lower}_is_duplicate" in text, (
        f"{code} derives is_duplicate but does not materialize it as a column"
    )


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_every_city_materializes_is_duplicate(code: str):
    """`is_duplicate` must be a column in every city's Parquet, OSM or not.

    The browser worker filters flagged rows out with a single
    `AND NOT COALESCE(is_duplicate, false)` for whichever city is loaded.  A
    city that omits the column does not degrade -- DuckDB raises a binder error
    and that city's map fails to load -- so the twelve cities with no OSM
    partition still emit a constant false rather than nothing.
    """
    lower = code.lower()
    text = city_models()[code].read_text(encoding="utf-8")
    assert f"is_duplicate: {lower}_is_duplicate" in text, (
        f"{code} does not materialize an is_duplicate column; the worker "
        "selects it for every city"
    )
    if code in OSM_DATA_SOURCES:
        # Derived from the staggered-grid anchor counts, not a constant.
        assert f"auto {lower}_is_duplicate <- {lower}_source =" in text
    else:
        assert f"auto {lower}_is_duplicate <- False;" in text, (
            f"{code} has no OSM partition, so its is_duplicate must be the "
            "constant false"
        )


def test_cross_city_model_does_not_merge_is_duplicate():
    """The opposite of data_source: this merge must stay out of tree_info.preql.

    `is_duplicate` is derived from a per-city grid aggregate over that city's
    own rows.  Merging the fourteen keys into one makes the planner resolve
    that aggregate over the union of all fourteen cities and it fails to plan
    at all -- `UnresolvableQueryException: Planner emitted a keyless join
    between row-bearing sources`.  Measured, not assumed; the comment in
    tree_info.preql carries the error.
    """
    text = (RAW_DIR / "tree_info.preql").read_text(encoding="utf-8")
    merged = [c for c in MUNICIPAL_DATA_SOURCES
              if f"merge {c.lower()}_is_duplicate into" in text]
    assert not merged, (
        f"{merged} merge is_duplicate in tree_info.preql; this breaks planning "
        "for every city model that derives the flag"
    )


def test_no_staging_parquet_is_committed():
    """Staged sources live in GCS, not the repo.

    They were committed at first and it worked, but the probes read the file's
    mtime as the freshness watermark and **git does not preserve mtime**: every
    fresh clone -- which is every cloud job run -- stamped the checkout time, so
    the watermark advanced on every tick and Boston and Tempe rebuilt three
    times a day.  That is the same every-tick thrash staging was introduced to
    avoid, just sourced from a checkout instead of Overpass's clock.
    """
    committed = sorted(p.name for p in RAW_DIR.glob("*/*_staging.parquet"))
    assert not committed, (
        f"{committed} are in the working tree; publish them with "
        "_ingest_shared.upload_staging and let .gitignore keep them out"
    )
