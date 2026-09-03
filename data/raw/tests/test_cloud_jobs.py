"""`data/trilogy.toml`'s job table is the deploy, and nothing else checks it.

Since the per-city split, adding a city means three job entries rather than an
import line in a shared model, and the failure mode of forgetting one is
silence: the city's parquet simply never rebuilds, or its OSM extract never
runs and the staging object it reads goes stale for ever.  There is no error
anywhere, on any tick, because a job that does not exist cannot fail.

The old shape had one guard for this -- a city missing from
`raw/tree_cities.preql` never refreshed -- and it was an import list, so it
only ever caught the trees lane.  These tests are the replacement, and they
check the schedule for the two properties that are load-bearing rather than
cosmetic: that Overpass extract jobs never fire together (two concurrent
requests from one IP fail each other), and that every entrypoint names a file
that exists (a typo is a job that 404s on a provisioned VM twenty minutes into
its first run).
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
CONFIG = DATA_DIR / "trilogy.toml"

sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import MUNICIPAL_DATA_SOURCES, OSM_DATA_SOURCES  # noqa: E402


def jobs() -> list[dict]:
    parsed = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    return parsed["cloud"]["job"]


def statements(path: Path) -> str:
    """A preql file with its comments stripped.

    These models carry more prose than preql -- the reason a merge or an import
    is absent is often a paragraph explaining what went wrong when it was
    present -- so a bare substring search over the raw text finds the warning
    rather than the code.
    """
    return "\n".join(
        line.split("#", 1)[0]
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def jobs_by_key() -> dict[str, dict]:
    return {job["key"]: job for job in jobs()}


CITY_CODES = sorted(MUNICIPAL_DATA_SOURCES)


def test_job_keys_are_unique():
    """Identity is `path::key`, so a duplicate silently deploys one job."""
    keys = [job["key"] for job in jobs()]
    assert len(keys) == len(set(keys)), "duplicate [[cloud.job]] key"


@pytest.mark.parametrize("code", CITY_CODES)
def test_every_city_has_a_refresh_job(code: str):
    job = jobs_by_key().get(f"city-{code.lower()}")
    assert job is not None, (
        f"{code} has no `city-{code.lower()}` [[cloud.job]]; its parquet would "
        "never rebuild on any schedule, with no error anywhere"
    )
    assert job["operation"] == "refresh"
    assert job.get("schedule"), f"{code}'s refresh job has no cron"


@pytest.mark.parametrize("code", sorted(OSM_DATA_SOURCES))
def test_every_osm_city_has_an_extract_job(code: str):
    """A staged OSM source with no extract job is a permanently stale partition.

    The staging parquet is the only thing the city model reads; nothing else
    writes it now that extraction is scheduled rather than hand-run.
    """
    job = jobs_by_key().get(f"osm-{code.lower()}")
    assert job is not None, (
        f"{code} is in OSM_DATA_SOURCES but has no `osm-{code.lower()}` "
        "[[cloud.job]]; nothing would ever publish its staging parquet"
    )
    assert job["operation"] == "refresh"
    assert job.get("schedule"), f"{code}'s OSM extract job has no cron"


@pytest.mark.parametrize("job", jobs(), ids=lambda job: job["key"])
def test_entrypoint_exists(job: dict):
    """A typo here fails on a provisioned VM, minutes into the job's first run."""
    entrypoint = DATA_DIR / job["entrypoint"]
    assert entrypoint.is_file(), f"{job['key']} names a missing entrypoint"


@pytest.mark.parametrize("job", jobs(), ids=lambda job: job["key"])
def test_schedules_are_six_field(job: dict):
    """The platform's cron parser is seconds-first.

    A five-field expression is not rejected -- it is misread, fires once and
    then sticks with "Failed to advance", so the job stops silently after one
    tick.
    """
    cron = job.get("schedule")
    if cron is None:
        return  # ad-hoc job, fired by hand
    assert len(cron.split()) == 6, (
        f"{job['key']}'s cron {cron!r} has {len(cron.split())} fields; the "
        "platform wants six (seconds first)"
    )


def test_osm_extract_jobs_never_fire_together():
    """Overpass allows two concurrent slots per client IP.

    Worse, it answers an over-budget request with HTTP 200 carrying an error
    remark rather than a 4xx, so a collision does not look like a failure --
    it looks like a city with no trees in OSM.  Distinct (weekday, hour,
    minute) is the cheap way to guarantee it, and it is checked rather than
    left to the reviewer because these entries are copy-pasted by definition.
    """
    slots: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for job in jobs():
        if not job["key"].startswith("osm-") or not job.get("schedule"):
            continue
        _sec, minute, hour, _dom, _month, dow = job["schedule"].split()
        slots[(dow, hour, minute)].append(job["key"])
    clashes = {slot: keys for slot, keys in slots.items() if len(keys) > 1}
    assert not clashes, f"OSM extract jobs share a firing slot: {clashes}"


def test_landmark_staging_jobs_have_no_cron():
    """These republish unconditionally; a cron would rebuild for nothing.

    `copy into` under `run` copies on every execution, and the copy moves the
    staging object's Last-Modified, which is exactly the watermark the city's
    landmark probe reads.  On a cron the city's landmark parquet would rebuild
    every firing whether or not the CSV changed.
    """
    scheduled = [
        job["key"]
        for job in jobs()
        if job["key"].startswith("landmarks-") and job.get("schedule")
    ]
    assert not scheduled, (
        f"{scheduled} have a cron; a landmark staging publish is unconditional "
        "and belongs on manual firing only"
    )


def test_city_refresh_entrypoints_are_the_city_models():
    """One city per job, and never an umbrella.

    A city model imports `tree_common` and `community_tree_info` and nothing
    else, so its bundle is exactly one city.  Pointing a refresh at a file that
    also brings the cross-city `data_source` merge into scope makes pytrilogy
    plan the city's parquet from sources other than its own partition union and
    publish it incomplete, silently -- Athens shipped OSM-only that way.

    Only *that* merge is forbidden.  A city is free to merge its own concepts
    (Berlin merges a derived `processed_dbh` into the canonical diameter); what
    must not happen is the seventeen per-city `{code}_source` enums collapsing
    into one key, which is what destroys the partition-completeness proof.
    """
    for code in CITY_CODES:
        entrypoint = jobs_by_key()[f"city-{code.lower()}"]["entrypoint"]
        assert entrypoint.startswith(f"raw/{code.lower()}/"), (
            f"city-{code.lower()} does not point at {code}'s own directory"
        )
        assert entrypoint.endswith("_tree_info.preql")
        text = statements(DATA_DIR / entrypoint)
        assert "into data_source" not in text, (
            f"{entrypoint} merges a source enum into data_source; that merge "
            "belongs only in tree_info.preql, and in a city model it makes the "
            "city's parquet plan from the wrong sources"
        )
        for other in CITY_CODES:
            if other == code:
                continue
            assert f"import ..{other.lower()}." not in text, (
                f"{entrypoint} imports {other}'s model; a city refresh job "
                "would then adopt another city's parquet"
            )


STAGING_DIR = DATA_DIR / "osm_staging"


def staging_model(code: str) -> Path:
    """The staging model for a city, from the upper-case code the tests use.

    Lower-casing here rather than at each call site is not tidiness: the codes
    in `OSM_DATA_SOURCES` are upper-case and the filenames are lower-case, so
    an f-string built straight from the code resolves on a case-insensitive
    filesystem (Windows, macOS) and raises `FileNotFoundError` on Linux CI.
    That is exactly how this shipped the first time.
    """
    return STAGING_DIR / f"{code.lower()}_osm_staging.preql"


@pytest.mark.parametrize("code", sorted(OSM_DATA_SOURCES))
def test_every_staging_model_pushes_down_its_city(code: str):
    """The `where` is what tells the shared extract script which city to fetch.

    One `osm_staging/osm_rows.py` serves all seventeen, and it learns the city
    from the `--filter 'city=<CODE>'` Trilogy compiles out of the datasource's
    `where` clause.  Elsewhere a pushdown filter is an optimisation -- the SQL
    predicate applies the same restriction if the argument never arrives -- but
    here it decides what leaves the network, so a model without one would ask
    Overpass for every city's bounding box on every firing.

    The script refuses to run without a filter rather than defaulting to all
    cities, so the real failure is loud; this keeps it from happening at all.
    """
    model = staging_model(code)
    text = statements(model)
    assert "file `./osm_rows.py`" in text, (
        f"{model.name} should read the shared extract script, not a per-city shim"
    )
    assert f"where city = '{code}';" in text, (
        f"{model.name} does not push its city down; the shared extract script "
        "has no way to know which city it is fetching"
    )


@pytest.mark.parametrize("code", sorted(OSM_DATA_SOURCES))
def test_extra_osm_columns_are_declared_in_the_model(code: str):
    """A column added to the OSM rows must also be declared on the partition.

    `OSM_EXTRA_NULL_COLUMNS` makes the *script* emit the column; the model has
    to declare it too, or the source cannot supply what the datasource asks for,
    drops out of the union, and the remaining partitions stop covering the
    source enum.  Trilogy reports that as `complete where` clauses "not provably
    exhaustive over that type", which is a long way from "you forgot a column".
    """
    sys.path.insert(0, str(RAW_DIR))
    from _osm_shared import OSM_CITY_NAMES, OSM_EXTRA_NULL_COLUMNS

    assert code in OSM_CITY_NAMES, (
        f"{code} has no OSM_CITY_NAMES entry, so the shared extract script "
        "would refuse to fetch it"
    )
    text = statements(staging_model(code))
    for column in OSM_EXTRA_NULL_COLUMNS.get(code, {}):
        assert f"property tree_id.{column} " in text, (
            f"{code} emits {column} but its staging model does not declare it"
        )
        assert f"{column}: ?{column}," in text
        assert f"?{column}," in text


def test_rollup_reads_every_city():
    """A city missing from the publisher never appears on the cross-city map.

    Its own pipeline can be perfectly healthy -- portal probed, parquet built,
    job green -- and it is simply absent from `full_tree_info`, with nothing
    anywhere reporting it.  That was true when the publisher held seventeen stub
    datasources and it is still true now that it holds one list; the list is
    just a great deal easier to leave a city out of.
    """
    text = statements(DATA_DIR / "raw/full_tree_publish.preql")
    listed = set(re.findall(r"trees/([a-z]{5})_tree_info_v", text))
    expected = {code.lower() for code in MUNICIPAL_DATA_SOURCES}
    assert listed == expected, (
        f"the publisher reads {sorted(listed)} but the cities are "
        f"{sorted(expected)}; missing: {sorted(expected - listed)}"
    )
    assert "{data_version}" in text, (
        "the publisher's paths must interpolate data_version, not hardcode a "
        "version; the list form takes f-strings"
    )


def test_the_rollup_producer_and_consumer_share_a_datasource_name():
    """The core's ordering is a derived edge, and a rename would delete it.

    trilogy-cloud reads a job's outputs from its managed datasources and its
    inputs from its root ones, keyed by physical address -- except that an
    address built from an f-string is not a join key, so a templated address
    keys on the datasource's *name*.  Both of ours are templated (the parquet
    carries `_v{data_version}`), so the edge exists purely because
    `full_tree_publish.preql` and `full_tree_info_source.preql` happen to call
    the rollup the same thing.

    Rename either and nothing errors: the two jobs simply stop being ordered,
    land in the same tick unordered, and enrichment reads the rollup while it
    is being rewritten.  That is the failure this asserts against.
    """
    producer = statements(DATA_DIR / "raw/full_tree_publish.preql")
    consumer = statements(DATA_DIR / "raw/full_tree_info_source.preql")

    # Managed (no `root`) in the producer, root in the consumer.
    assert re.search(r"^datasource full_tree_info \(", producer, re.M), (
        "full_tree_publish.preql must declare `full_tree_info` as a managed "
        "datasource; a `copy into` or a renamed target is not a derivable "
        "output and the core loses its ordering"
    )
    assert re.search(r"^root datasource full_tree_info \(", consumer, re.M), (
        "full_tree_info_source.preql must declare `full_tree_info` as a *root* "
        "datasource; managed would make every consumer try to rebuild it"
    )
    entrypoint = jobs_by_key()["refresh-enrichment"]["entrypoint"]
    assert "import full_tree_info_source;" in statements(DATA_DIR / entrypoint), (
        f"{entrypoint} must import the rollup source, or the enrichment job "
        "declares no input and nothing orders it after the publisher"
    )
    # And it must stay OUT of the model the app resolves against: a second tree
    # source in the planner's scope changed a chart's answer (2 where the
    # fixtures say 3), which `pnpm test:queries` caught and the smoke tests did
    # not.
    assert "import full_tree_info_source;" not in statements(
        DATA_DIR / "raw/tree_enrichment.preql"
    ), (
        "tree_enrichment.preql must not import the rollup source; it is in the "
        "frontend's model bundle, and a second way to answer a tree question "
        "changes what the dashboard charts return"
    )


def test_the_core_shares_one_cron():
    """One cron per toml is one schedule row, and a schedule row is one tick.

    `trilogy cloud sync` groups the jobs a toml declares on one cron into a
    single schedule, and the platform orders a tick's jobs by dependency.  Split
    the core across two crons and the derived edge above has nothing to act on:
    the two jobs are in different firings with nothing between them but wall
    clock, which is the arrangement this replaced.
    """
    crons = {
        key: jobs_by_key()[key]["schedule"]
        for key in ("publish-full", "refresh-enrichment", "refresh-ecoregions")
    }
    assert len(set(crons.values())) == 1, (
        f"the core jobs are on different crons ({crons}); they must share one "
        "so the platform can order them as a single tick"
    )


def test_the_core_reads_only_published_parquets():
    """The core's whole reason for a single cadence.

    `publish-full`, `refresh-enrichment` and `refresh-ecoregions` run daily
    regardless of what any city is doing, which is only safe while none of
    them can reach a municipal portal, Overpass, or a staging object.  The
    rollup and enrichment both do it by declaring the published parquets as
    root datasources instead of importing the city models; this asserts they
    have not quietly re-acquired the import.
    """
    for key in ("publish-full", "refresh-enrichment"):
        entrypoint = jobs_by_key()[key]["entrypoint"]
        text = statements(DATA_DIR / entrypoint)
        for code in CITY_CODES:
            assert f"import {code.lower()}." not in text, (
                f"{entrypoint} imports {code}'s model; the core would then "
                "probe that city's portal daily and could rebuild its parquet "
                "in the core's container rather than the city's"
            )
        assert "import tree_info" not in text, (
            f"{entrypoint} imports tree_info, which imports every city model"
        )
