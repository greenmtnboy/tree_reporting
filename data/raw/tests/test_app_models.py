"""The app's consumption models: tree_info.preql and landmark_info.preql.

These are what the browser sends the hosted resolver with every dashboard and
chat request (src/src/trilogyModels.ts), and the resolver parses all of it each
time. They read published parquets and build nothing, so they import no city
build model: tree_info.preql is written from the city models by
tools/sync_app_models.py, and these tests hold it to them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))
sys.path.insert(0, str(RAW_DIR / "tools"))

import sync_app_models  # noqa: E402
from shared.ingest import MUNICIPAL_DATA_SOURCES  # noqa: E402

CODES = sorted(MUNICIPAL_DATA_SOURCES)

# What a dashboard or the map chat imports (dashboardContextSource.ts,
# chatModelImports.ts), without the per-city constants the app adds.
APP_SCOPE = """import tree_enrichment;
import tree_info;
import tree_rollup_source;
import ecoregion_info;
import city_ecoregion;
import landmark_info as landmark;
"""


def parse(text: str):
    from trilogy import Environment

    env = Environment(working_path=RAW_DIR)
    env.parse(text)
    return env


@pytest.fixture(scope="module")
def targets():
    return sync_app_models.published_targets()


@pytest.fixture(scope="module")
def app():
    return parse("import tree_info;\n")


def location(datasource) -> str:
    return str(getattr(datasource.address, "location", datasource.address))


def bindings(datasource, code: str) -> set[tuple[str, str]]:
    """(column, concept) pairs, with the city's source enum read as `data_source`."""
    source = f"local.{code.lower()}_source"
    return {
        (c.alias, "local.data_source" if c.concept.address == source else c.concept.address)
        for c in datasource.columns
    }


def test_tree_info_is_in_sync():
    assert sync_app_models.planned() == sync_app_models.TREE_INFO.read_text(encoding="utf-8"), (
        "tree_info.preql is stale: run tools/sync_app_models.py"
    )


@pytest.mark.parametrize("code", CODES)
def test_each_city_reads_what_its_target_writes(code: str, targets, app):
    """Same file, same `complete where`, and only columns the target stores."""
    target = targets[code]
    datasource = app.datasources[target.name]
    assert datasource.is_root, f"{target.name}: the app's view must never be a build target"
    assert location(datasource) == location(target)
    assert sync_app_models.complete_where(datasource) == sync_app_models.complete_where(target)
    extra = bindings(datasource, code) - bindings(target, code)
    assert not extra, f"{code}: tree_info.preql binds columns its parquet does not have: {sorted(extra)}"
    assert ("tree_id", "local.tree_id") in bindings(datasource, code)
    assert ("data_source", "local.data_source") in bindings(datasource, code)


def test_tree_info_builds_nothing_and_merges_nothing(app):
    text = sync_app_models.TREE_INFO.read_text(encoding="utf-8")
    assert not re.search(r"^merge ", text, re.M)
    assert not re.search(r"^\s*freshness by", text, re.M)
    imports = re.findall(r"^import ([\w.]+)", text, re.M)
    assert not [name for name in imports if "." in name], f"tree_info.preql imports a city model: {imports}"
    managed = [name for name, ds in app.datasources.items() if not ds.is_root]
    assert not managed, f"tree_info.preql's scope declares build targets: {managed}"


def test_landmark_info_reads_the_published_union():
    landmarks = parse("import landmark_info;\n")
    publisher = parse((RAW_DIR / "full_landmark_publish.preql").read_text(encoding="utf-8"))
    (datasource,) = landmarks.datasources.values()
    assert datasource.is_root
    assert location(datasource) == location(publisher.datasources["full_landmark_info"])
    text = (RAW_DIR / "landmark_info.preql").read_text(encoding="utf-8")
    assert not [name for name in re.findall(r"^import ([\w.]+)", text, re.M) if "." in name and not name.startswith("std.")]


def _parquets(sql: str) -> list[str]:
    return sorted(set(re.findall(r"duckdb/(?:trees|landmarks)/([a-z_]+)_v\d+\.parquet", sql)))


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # A single city's tree rows come from that city's parquet, not the rollup.
        ("select data_source, count(tree_id) -> n where city = 'USSFO';", ["ussfo_tree_info"]),
        ("select tree_id, height_m where city = 'USBOS' and height_m > 30 limit 5;", ["usbos_tree_info"]),
        ("select borough, count(tree_id) -> n where city = 'GBLON';", ["gblon_tree_info"]),
        # A per-species count is answered by the summary.
        ("select species, count(tree_id) -> n where city = 'USSFO' order by n desc limit 5;", ["tree_rollup_species"]),
        # Landmarks, under their alias, from the union.
        ("select landmark.name, landmark.latitude where landmark.city = 'USSFO' limit 5;", ["full_landmark_info"]),
    ],
)
def test_the_app_scope_plans_against_published_files(query: str, expected: list[str]):
    from trilogy import Dialects

    env = parse(APP_SCOPE)
    sql = Dialects.DUCK_DB.default_executor(environment=env).generate_sql(query)[-1]
    assert _parquets(sql) == expected, sql
