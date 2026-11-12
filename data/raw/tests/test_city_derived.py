"""Every city's derived job (raw/city_derived.preql): written, scheduled, ordered.

A city without one keeps the map's fallback for ever: no predictions file, no
browser projection, and nothing errors. tools/sync_city_derived.py writes
the model, the probe, the job and the dependency; these tests fail while it
would write anything, and pin the properties the generator exists for.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
sys.path.insert(0, str(RAW_DIR))
sys.path.insert(0, str(RAW_DIR / "tools"))

from shared.ingest import MUNICIPAL_DATA_SOURCES  # noqa: E402
import sync_city_derived  # noqa: E402

CODES = sorted(MUNICIPAL_DATA_SOURCES)


def config() -> dict:
    return tomllib.loads((DATA_DIR / "trilogy.toml").read_text(encoding="utf-8"))


def jobs_by_key() -> dict[str, dict]:
    return {job["key"]: job for job in config()["cloud"]["job"]}


def derived_model(code: str) -> Path:
    city = sync_city_derived.city_model(code)
    return city.with_name(city.name.replace("_tree_info.preql", "_tree_derived.preql"))


def refresh_sql(code: str, target: str) -> str:
    """A target's update SQL as the refresh plans it: the targets after it
    hidden, the ones before it in scope (test_tree_predictions.py does the same)."""
    from trilogy import Dialects, Environment
    from trilogy.dialect.config import DuckDBConfig
    from trilogy.execution.state.isolation import hidden_datasources

    path = derived_model(code)
    env = Environment(working_path=path.parent)
    env.parse(path.read_text(encoding="utf-8"))
    lc = code.lower()
    order = [f"{lc}_tree_predictions", f"{lc}_tree_browser", f"{lc}_tree_vocab"]
    pending = set(order[order.index(f"{lc}_{target}"):])
    executor = Dialects.DUCK_DB.default_executor(environment=env, conf=DuckDBConfig(enable_python_datasources=True))
    datasource = env.datasources[f"{lc}_{target}"]
    with hidden_datasources(env, pending):
        statement = datasource.create_update_statement(env, None, line_no=None)
        return "\n".join(executor.generator.compile_statement(q)
                         for q in executor.generator.generate_queries(env, [statement]))


def test_the_generator_has_nothing_to_write():
    stale = [p.relative_to(DATA_DIR).as_posix() for p, text in sync_city_derived.planned().items()
             if not p.exists() or p.read_text(encoding="utf-8") != text]
    assert not stale, f"run tools/sync_city_derived.py; it would write {stale}"


@pytest.mark.parametrize("code", CODES)
def test_every_city_has_a_derived_job_on_its_city_jobs_cron(code: str):
    jobs = jobs_by_key()
    lc = code.lower()
    job, city = jobs[f"derived-{lc}"], jobs[f"city-{lc}"]
    assert job["operation"] == "refresh"
    assert job["entrypoint"] == derived_model(code).relative_to(DATA_DIR).as_posix()
    assert job["schedule"] == city["schedule"], "one cron, so the platform orders it after the city in one tick"
    assert (DATA_DIR / job["entrypoint"]).exists()


@pytest.mark.parametrize("code", CODES)
def test_the_derived_job_reads_its_city_by_the_city_jobs_output_name(code: str):
    """The name match is the derived edge: the platform runs the derived job
    after its city job in the tick they share."""
    text = derived_model(code).read_text(encoding="utf-8")
    output = sync_city_derived.city_output(code)
    assert re.search(rf"^root datasource {output} extends published_city_trees", text, re.M), output
    assert f"trees/{code.lower()}_tree_info_v{{data_version}}.parquet`" in text
    city = sync_city_derived.city_model(code).read_text(encoding="utf-8")
    assert re.search(rf"^partial datasource {output} extends published_tree_info", city, re.M)
    assert f"trees/{code.lower()}_tree_info_v{{data_version}}.parquet`" in city


def test_no_dependency_entry_stands_in_for_the_edge():
    entrypoints = {jobs_by_key()[f"derived-{code.lower()}"]["entrypoint"] for code in CODES}
    assert not entrypoints & set(config().get("dependencies", {}))


@pytest.mark.parametrize("code", CODES)
def test_heights_come_from_the_citys_own_parquet_or_are_null(code: str):
    text = derived_model(code).read_text(encoding="utf-8")
    surveyed = sync_city_derived.has_heights(code)
    assert ("    height_m: ?height_m," in text) == surveyed, code
    assert ("cast(null as float)'''): ?height_m" in text) == (not surveyed), code


def test_heights_are_read_where_a_city_publishes_them():
    """tree_height_source.preql lists the cities with a height column; each
    of them, and only they, read one in their derived model."""
    listed = set(re.findall(r"trees/([a-z]{5})_tree_info_v",
                            (RAW_DIR / "tree_height_source.preql").read_text(encoding="utf-8")))
    surveyed = {code.lower() for code in CODES if sync_city_derived.has_heights(code)}
    assert surveyed == listed


@pytest.mark.parametrize("code", ["USBOS", "USSFO"])
def test_a_derived_model_plans_its_three_tables_from_its_own_city(code: str):
    """Boston with heights, San Francisco without: three targets, and no read
    of the rollup or of another city's file."""
    from trilogy import Environment

    path = derived_model(code)
    env = Environment(working_path=path.parent)
    env.parse(path.read_text(encoding="utf-8"))
    lc = code.lower()
    targets = {name for name, ds in env.datasources.items() if not ds.is_root and ds.address}
    assert {f"{lc}_tree_predictions", f"{lc}_tree_browser", f"{lc}_tree_vocab"} <= targets, targets
    sql = refresh_sql(code, "tree_predictions")
    read = set(re.findall(r"trees/([a-z_]+)_v\d+\.parquet", sql))
    assert "full_tree_info" not in read, read
    cities = {name.removesuffix("_tree_info") for name in read if name.endswith("_tree_info")}
    assert cities == {lc}, read


@pytest.mark.parametrize("code", CODES)
def test_the_predictions_are_written_before_the_projection_reads_them(code: str):
    """The projection reads the predictions file back (city_derived.preql);
    the refresh takes a file's targets in the order it declares them."""
    text = derived_model(code).read_text(encoding="utf-8")
    lc = code.lower()
    order = [text.index(f"datasource {lc}_{name} ") for name in ("tree_predictions", "tree_browser", "tree_vocab")]
    assert order == sorted(order)


@pytest.mark.parametrize("code", ["USBOS", "USSFO"])
def test_the_projection_reads_the_published_sizes_and_keeps_every_tree(code: str):
    """No fit table or crown CSV: the projection takes the sizes from the
    predictions file, through a padded join, and never sizes a tree itself."""
    lc = code.lower()
    sql = refresh_sql(code, "tree_browser")
    assert "_fits_v" not in sql and "read_csv" not in sql
    assert f"{lc}_tree_predictions_v" in sql
    assert "INNER JOIN" not in sql or not re.search(r"INNER JOIN \S+ on \S+\.\"tree_id\"", sql), "a join that drops trees"


def test_a_citys_watermark_covers_its_parquet_the_enrichment_and_every_fit_table():
    from shared.city_derived import FIT_TABLES, city_inputs

    urls = city_inputs("USBOS")
    assert urls["city"].endswith("/usbos_tree_info_v2.parquet")
    assert urls["enrichment"].endswith("/tree_enrichment_v2.parquet")
    for table in FIT_TABLES:
        assert urls[table].endswith(f"/{table}_v2.parquet")
    fit_files = set(re.findall(r"trees/(\w+)_v\{data_version\}\.parquet",
                               (RAW_DIR / "allometry_fits_source.preql").read_text(encoding="utf-8")))
    assert fit_files == set(FIT_TABLES), "the probe must watch every fit table the predictions read"


@pytest.mark.parametrize("moment, monday", [
    ("2026-11-12T13:45:00+00:00", "2026-11-09T00:00:00+00:00"),   # a Thursday
    ("2026-11-09T00:00:00+00:00", "2026-11-09T00:00:00+00:00"),   # the Monday itself
    ("2026-11-15T23:59:59+00:00", "2026-11-09T00:00:00+00:00"),   # the Sunday after
    ("2026-11-16T01:00:00+00:00", "2026-11-16T00:00:00+00:00"),   # the next week
])
def test_the_fits_watermark_is_floored_to_its_monday(moment: str, monday: str):
    """The fits refit at most weekly: a week of daily rollup changes is one value."""
    from datetime import datetime

    import prediction_inputs_probe

    assert prediction_inputs_probe.week_of(datetime.fromisoformat(moment)) == datetime.fromisoformat(monday)
