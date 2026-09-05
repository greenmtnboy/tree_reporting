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

    Only `root` datasources count as claims.  A city whose enum ever shrinks
    to a single value must additionally pin that value on the *published*
    target's `complete where`, because a lone partial source is never a union
    candidate — the materialisation query has to imply the source's
    completeness clause directly.  GRMLO shipped that way before its OSM
    partition existed (see the historical note in grmlo/milos_tree_info.preql);
    counting only root claims keeps such a pin from reading as a duplicate.
    """
    path = city_models()[code]
    text = path.read_text(encoding="utf-8")
    root_blocks = re.findall(
        r"^root\b.*?;", text, flags=re.S | re.M
    )
    claimed = [
        source
        for block in root_blocks
        for city, _key, source in COMPLETE_RE.findall(block)
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
    if MUNICIPAL_DATA_SOURCES[code]:
        # Prefix match: cities with an OSM staging source append a third argument.
        assert f"greatest({code.lower()}_data_updated_through, {column}" in text
    else:
        # A city with no municipal source (GRMLO) has no municipal probe, so
        # its watermark must not include a municipal column nothing feeds —
        # the community column leads instead, either alone or as the first
        # arm of a greatest() with the OSM staging column.
        assert (
            f"_published_data_updated_through <- {column}" in text
            or f"_published_data_updated_through <- greatest({column}" in text
        )
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
    cluster merge publishes overlapping rows with no flag for the frontend
    to filter on.
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
    # A city whose municipal portal is down cannot be allowed to demand a
    # rebuild it cannot complete.  The published watermark is a greatest()
    # across sources, so one fresh source marks the Parquet stale and the
    # rebuild then needs *every* partition -- which is how wiring Berlin's OSM
    # source made it permanently stale against a dead gdi.berlin.de and took
    # full_tree_info down with it on every tick.  Dropping the OSM term is the
    # documented escape hatch, and it costs the city its "re-extraction rebuilds
    # the Parquet" property, so it is listed here rather than left implicit.
    WATERMARK_EXEMPT = {
        "DEBER": "gdi.berlin.de has been in maintenance for days; see the "
                 "comment in deber/berlin_tree_info.preql",
    }
    if code in WATERMARK_EXEMPT:
        assert column not in text.split("greatest(", 1)[1].split(")", 1)[0], (
            f"{code} is watermark-exempt ({WATERMARK_EXEMPT[code]}) but "
            f"{column} is back in greatest(); remove the exemption instead"
        )
    else:
        assert column in text.split("greatest(", 1)[1].split(")", 1)[0], (
            f"{code}'s published watermark must include {column} or a "
            "re-extraction never rebuilds the Parquet"
        )
    assert "import ..tree_dedup;" in text, (
        f"{code} has an OSM partition but does not import the shared cluster "
        "merge, so its overlap with the inventory is never resolved"
    )


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_every_city_prunes_absorbed_rows(code: str):
    """Every city's published target drops the rows it absorbed.

    `where tree_id = cluster_id` is what makes the Parquet one row per tree.
    Without it the absorbed rows are published and the map double-renders every
    tree that OSM and the inventory both know about -- which is what the old
    `is_duplicate` flag and the worker's `AND NOT COALESCE(...)` existed to
    hide.  Nothing else reports its absence: the city builds and its counts are
    simply high, so this is the only thing between a new city and a silently
    duplicated map.
    """
    text = city_models()[code].read_text(encoding="utf-8")
    assert "import ..tree_dedup;" in text
    assert '\nwhere tree_id = cluster_id\n' in text, (
        f"{code}'s published target has no `where tree_id = cluster_id`, so it "
        "publishes the rows its clusters absorbed"
    )


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_no_city_publishes_the_old_duplicate_flag(code: str):
    """The flag is gone, and a copy-paste must not bring it back.

    It was the workaround for a planner that could not apply a row gate when
    materialising a target, fixed in pytrilogy 0.3.348.  A city still
    publishing it would name a concept the shared model no longer derives, so
    the build fails -- but pointing at the missing concept is a long way from
    saying the prune replaced it.
    """
    text = city_models()[code].read_text(encoding="utf-8")
    assert "is_duplicate" not in text, (
        f"{code} still publishes is_duplicate; the absorbed rows are pruned "
        "now, so the flag has no rows left to mark"
    )


RAW_ATTRIBUTES = {
    "species": "raw_species",
    "tree_name": "raw_tree_name",
    "plant_date": "raw_plant_date",
    "latitude": "raw_latitude",
    "longitude": "raw_longitude",
    "diameter_at_breast_height": "raw_dbh",
    "submission_photo_url": "raw_photo_url",
}


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_city_feeds_the_shared_cluster_merge(code: str):
    """Every raw source maps the shared raw_* concepts, never the canonical ones.

    A raw source that maps `species: species` directly hands the planner a
    second, unmerged path to the concept, so the cluster merge in
    tree_dedup.preql silently stops applying to that attribute.  The city also
    has to feed `source_label` (the merge classifies rows by its prefix) and
    merge dbh itself -- the one attribute the shared file leaves to the city,
    because Boston imputes it first.
    """
    lower = code.lower()
    text = city_models()[code].read_text(encoding="utf-8")
    assert f"merge {lower}_source_label into source_label;" in text, (
        f"{code} does not feed source_label; the shared merge cannot classify its rows"
    )
    for attr, raw in RAW_ATTRIBUTES.items():
        assert re.search(rf"^\s+\w+: \??{attr},$", text, re.M) is None, (
            f"{code} maps {attr} straight onto the canonical concept; map "
            f"{raw} and let tree_dedup.preql derive it"
        )
        assert re.search(rf"^\s+\w+: \??{raw},$", text, re.M), (
            f"{code} never maps {raw}, so the merge has nothing to pick from"
        )
    dbh_merge = "processed_dbh" if code == "USBOS" else "merged_dbh"
    assert f"merge {dbh_merge} into diameter_at_breast_height;" in text, (
        f"{code} does not merge {dbh_merge} into diameter_at_breast_height"
    )
    for column in ("    merged_sources,", "    ?merged_tree_ids,"):
        assert column in text, f"{code} does not publish {column.strip()}"


def test_shared_dedup_merges_every_attribute_but_dbh():
    text = (RAW_DIR / "tree_dedup.preql").read_text(encoding="utf-8")
    for attr in RAW_ATTRIBUTES:
        if attr == "diameter_at_breast_height":
            assert re.search(r"^merge merged_dbh into", text, re.M) is None, (
                "dbh is merged per city (Boston imputes it first); a second "
                "merge into diameter_at_breast_height would conflict"
            )
            continue
        merged = "merged_photo_url" if attr == "submission_photo_url" else f"merged_{attr}"
        assert f"merge {merged} into {attr};" in text


def test_cross_city_model_does_not_redefine_the_dedup():
    """The cross-city union takes the dedup columns from the published parquets.

    `merged_sources` and `merged_tree_ids` are one shared
    derivation in tree_dedup.preql, computed inside each city's own build over
    that city's rows.  tree_info.preql must not re-derive or merge anything of
    its own for them: the published parquets carry the columns, and asking the
    planner to resolve the cluster aggregate over the union of every city fails
    with a keyless join.
    """
    text = (RAW_DIR / "tree_info.preql").read_text(encoding="utf-8")
    for token in ("cluster_id", "merged_sources", "merged_tree_ids"):
        assert f"merge {token}" not in text and f"auto {token}" not in text, (
            f"tree_info.preql redefines {token}; the city parquets already carry it"
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
