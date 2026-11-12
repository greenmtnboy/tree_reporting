"""The browser projection: the per-city parquet the map opens with one scan.

Each city's derived job (city_derived.preql) resolves the joins the map's
worker used to run on every visit: the display name and growth form from the
enrichment table, the sizes from the predictions the same job just wrote.
What the model gets wrong here is what every phone would show, so San
Francisco's own derived model is planned and its SQL run over fixture
parquets, and the rows are checked column by column. The job's wiring is
tests/test_city_derived.py's.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
REPO_DIR = DATA_DIR.parent

sys.path.insert(0, str(RAW_DIR))

MODEL = RAW_DIR / "ussfo" / "sf_tree_derived.preql"
ECOREGION_MODEL = RAW_DIR / "ecoregion_browser.preql"


def statements(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )


# --- Wiring -----------------------------------------------------------------


def test_the_projection_reads_the_predictions_its_own_job_wrote():
    """No second predictions file, and no import of a model that builds one."""
    model = statements(RAW_DIR / "city_derived.preql")
    assert "import tree_predictions;" in model
    assert "import tree_predictions_source;" not in model
    assert not (RAW_DIR / "tree_predictions_source.preql").exists()
    for managed in ("tree_enrichment", "full_tree_publish"):
        assert f"import {managed};" not in model, managed


def test_the_ecoregion_projection_consumes_the_dimension_by_name():
    model = statements(ECOREGION_MODEL)
    source = statements(RAW_DIR / "ecoregion_info_source.preql")
    assert "import ecoregion_info_source;" in model
    assert re.search(r"^root datasource ecoregion_info \(", source, re.M)
    assert re.search(r"^datasource ecoregion_browser \(", model, re.M)
    for text in (model, source):
        assert "geometry_geojson" not in text, "the geometry is the 5 MB the browser does not want"
        assert "import ecoregion_info;" not in text


def test_the_projections_have_jobs_and_the_slices_are_gone():
    import tomllib

    jobs = tomllib.loads((DATA_DIR / "trilogy.toml").read_text(encoding="utf-8"))["cloud"]["job"]
    entrypoints = {job["entrypoint"]: job for job in jobs}
    assert "raw/ussfo/sf_tree_derived.preql" in entrypoints
    assert "raw/ecoregion_browser.preql" in entrypoints
    # One job per city builds it; the cross-city jobs are gone.
    for gone in ("raw/tree_browser_city.preql", "raw/tree_predictions.preql", "raw/tree_predictions_city.preql"):
        assert gone not in entrypoints, gone
    assert not (RAW_DIR / "tree_browser_city.preql").exists()
    assert entrypoints["raw/ecoregion_browser.preql"]["schedule"] == entrypoints["raw/ecoregion_info.preql"]["schedule"]


def test_the_models_parse():
    """A parse failure is otherwise found on a provisioned VM at 06:00 UTC."""
    from trilogy import Environment

    sys.path.insert(0, str(RAW_DIR))
    from shared.ingest import MUNICIPAL_DATA_SOURCES

    for code in MUNICIPAL_DATA_SOURCES:
        (city,) = (RAW_DIR / code.lower()).glob("*_tree_derived.preql")
        env = Environment(working_path=city.parent)
        env.parse(city.read_text(encoding="utf-8"))
        names = {d.name for d in env.datasources.values()}
        lc = code.lower()
        city = re.search(r"^root datasource (\w+) extends published_city_trees", city.read_text(encoding="utf-8"), re.M).group(1)
        for name in (city, "tree_enrichment", f"{lc}_tree_predictions",
                     f"{lc}_tree_browser", f"{lc}_tree_vocab"):
            assert name in names, (code, name)

    env = Environment(working_path=RAW_DIR)
    env.parse(ECOREGION_MODEL.read_text(encoding="utf-8"))
    assert {d.name for d in env.datasources.values()} == {"ecoregion_info", "ecoregion_browser"}


# --- The rows ----------------------------------------------------------------

# tree_id, city, species, tree_name, dbh, has a prediction row
FIXTURE_TREES = [
    ("sf-1", "USSFO", "Acer platanoides", "Norway Maple", 30.0, True),   # enriched, named by the source
    ("sf-2", "USSFO", "Acer platanoides", None, 12.0, True),             # enriched, unnamed
    ("sf-3", "USSFO", "Newtaxon alba", "Whitewood", None, True),         # no enrichment row
    ("sf-4", "USSFO", "Newtaxon alba", None, None, False),               # no enrichment row, no name, no prediction
    ("sf-5", "USSFO", "Unknown", None, 20.0, True),                      # sentinel: an authored enrichment row
    ("sf-6", "USSFO", "Palm", "Palm", 40.0, True),                       # sentinel with a growth form
    ("sf-7", "USSFO", "Ailanthus altissima", "  ", 15.0, True),          # blank source name, blank first common name
    ("pred-1", "USSFO", "Unknown deciduous", None, None, False),         # an imagery detection: no prediction row
]


@pytest.fixture(scope="module")
def fixture_files(tmp_path_factory):
    """SF's parquet, the enrichment table and SF's predictions, as small local parquets."""
    import duckdb

    tmp = tmp_path_factory.mktemp("browser")
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE rollup (
            tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR, cultivar VARCHAR,
            crown_width_m DOUBLE, tree_name VARCHAR, plant_date DATE, diameter_at_breast_height DOUBLE,
            latitude DOUBLE, longitude DOUBLE, submission_photo_url VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO rollup VALUES (?, ?, ?, ?, NULL, NULL, ?, DATE '2001-05-01', ?, 37.77, -122.42, NULL)",
        [(tree_id, city, "PREDICTED_USSFO" if tree_id.startswith("pred-") else "FIXTURE", species, name, dbh)
         for tree_id, city, species, name, dbh, _ in FIXTURE_TREES],
    )
    conn.execute(
        """
        CREATE TABLE enrichment AS SELECT * FROM (VALUES
            ('Acer platanoides',    'Acer',      ['Norway maple', 'Harlequin maple'], 'broadleaf'),
            ('Ailanthus altissima', 'Ailanthus', [' ', 'Tree of heaven'],            'broadleaf'),
            ('Unknown',             NULL,        ['Species not recorded'],            'default'),
            ('Palm',                NULL,        ['Palm (species not recorded)'],     'palm')
        ) AS t(species, genus, common_names, tree_form)
        """
    )
    conn.execute(
        """
        CREATE TABLE predictions (
            tree_id VARCHAR, city VARCHAR, data_source VARCHAR, genus VARCHAR, height_m FLOAT,
            predicted_crown_width_m DOUBLE, predicted_height_m FLOAT, accepted_height_m FLOAT,
            prediction_inputs_published_through TIMESTAMPTZ
        )
        """
    )
    # SF's predictions file as its job writes it: every tree but the
    # detection, which has no LIDAR height (tree_predictions.preql).
    conn.executemany(
        "INSERT INTO predictions VALUES (?, ?, 'FIXTURE', NULL, NULL, ?, ?, ?, TIMESTAMPTZ '2026-10-01 06:00:00+00')",
        [
            (tree_id, city, 4.5 if dbh else None, 11.0 if dbh else None, 13.5 if tree_id == "sf-1" else None)
            for tree_id, city, _, _, dbh, predicted in FIXTURE_TREES
            if predicted
        ],
    )
    files = {}
    for table, pattern in (("rollup", "ussfo_tree_info_v"), ("enrichment", "tree_enrichment_v"),
                           ("predictions", "ussfo_tree_predictions_v")):
        path = tmp / f"{table}.parquet"
        conn.execute(f"COPY {table} TO '{path.as_posix()}' (FORMAT PARQUET)")
        files[pattern] = path
    return conn, files


def run_model(fixture_files, query: str, one_scan: bool = True) -> list[dict]:
    """The model's own SQL, run by DuckDB over the fixture parquets.

    The planner is asked for the query in SF's derived model with its
    predictions table in scope, as the job's refresh has it once that table
    is written; the `read_parquet` URLs it names are pointed at the local
    parquets, and what comes back is what SF's published file would hold.
    """
    from trilogy import Dialects, Environment

    conn, files = fixture_files
    env = Environment(working_path=MODEL.parent)
    env.parse(MODEL.read_text(encoding="utf-8"))
    # Ask for the derivation, not a published file: with the targets in scope
    # the planner would read ussfo_tree_browser_v2.parquet back instead.
    for key in [k for k, d in env.datasources.items() if d.name.endswith(("_tree_browser", "_tree_vocab"))]:
        del env.datasources[key]
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    (sql,) = executor.generate_sql(query)
    for pattern, path in files.items():
        sql, n = re.subn(rf"read_parquet\('[^']*{pattern}[^']*'\)", f"read_parquet('{path.as_posix()}')", sql)
        assert n <= 1 if one_scan else True, f"{pattern}: expected at most one scan, found {n}"
    assert "read_parquet('https" not in sql, "a published file survived the substitution"
    assert "_fits_v" not in sql and "read_csv" not in sql, "the projection sized its trees itself"
    columns = [d[0] for d in conn.execute(sql).description]
    return [dict(zip(columns, row)) for row in conn.execute(sql).fetchall()]


@pytest.fixture(scope="module")
def rows(fixture_files) -> list[dict]:
    out = run_model(
        fixture_files,
        """
        select
            tree_id, city, data_source, species, cultivar, tree_name, display_name,
            browser_tree_form, plant_date, diameter_at_breast_height, crown_width_m,
            published_predicted_crown_width_m, published_accepted_height_m,
            published_predicted_height_m, latitude, longitude, submission_photo_url
        where city = 'USSFO';
        """,
    )
    # Under the projection's column names (city_derived.preql's city_tree_browser).
    names = {"published_predicted_crown_width_m": "predicted_crown_width_m",
             "published_accepted_height_m": "height_m",
             "published_predicted_height_m": "predicted_height_m"}
    return [{names.get(k, k): v for k, v in row.items()} for row in out]


@pytest.fixture(scope="module")
def vocab(fixture_files) -> list[dict]:
    return run_model(
        fixture_files,
        """
        select
            city, data_source, species, cultivar, tree_name, display_name,
            vocab_tree_count
        where city = 'USSFO';
        """,
        # The plan counts and names in two reads of the rollup; a few
        # thousand output rows, so this is planner cost and not a phone's.
        one_scan=False,
    )


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["tree_id"]: row for row in rows}


def test_one_row_per_tree_of_the_city(rows):
    ids = [row["tree_id"] for row in rows]
    assert sorted(ids) == sorted(t[0] for t in FIXTURE_TREES if t[1] == "USSFO")
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("tree_id", ["sf-4", "pred-1"])
def test_a_tree_with_no_prediction_row_keeps_its_row(rows, tree_id):
    """The predictions table holds no row for an imagery detection without a
    LIDAR height (tree_predictions.preql), and those are most of Boston's map:
    the projection must keep every tree of the city's parquet whatever the
    predictions hold."""
    row = by_id(rows)[tree_id]
    assert row["predicted_crown_width_m"] is None and row["predicted_height_m"] is None
    assert row["species"] == dict((t[0], t[2]) for t in FIXTURE_TREES)[tree_id]


def test_display_name_prefers_the_first_common_name(rows):
    trees = by_id(rows)
    assert trees["sf-1"]["display_name"] == "Norway maple"
    assert trees["sf-2"]["display_name"] == "Norway maple"
    # The source's own name is kept beside it, unchanged.
    assert trees["sf-1"]["tree_name"] == "Norway Maple"
    assert trees["sf-2"]["tree_name"] is None


def test_display_name_falls_back_to_the_source_name_then_the_species(rows):
    trees = by_id(rows)
    assert trees["sf-3"]["display_name"] == "Whitewood"
    assert trees["sf-4"]["display_name"] == "Newtaxon alba"


def test_a_blank_first_common_name_is_skipped(rows):
    """Blank is not a name: the source's blank is skipped too, down to the species."""
    assert by_id(rows)["sf-7"]["display_name"] == "Ailanthus altissima"


def test_sentinels_label_themselves_from_their_authored_rows(rows):
    """`Unknown` and `Palm` are not taxa; ingest.py authors their enrichment rows."""
    trees = by_id(rows)
    assert trees["sf-5"]["display_name"] == "Species not recorded"
    assert trees["sf-5"]["browser_tree_form"] == "default"
    assert trees["sf-6"]["display_name"] == "Palm (species not recorded)"
    assert trees["sf-6"]["browser_tree_form"] == "palm"


def test_tree_form_is_never_null(rows):
    trees = by_id(rows)
    assert trees["sf-1"]["browser_tree_form"] == "broadleaf"
    assert trees["sf-3"]["browser_tree_form"] == "default"
    assert all(row["browser_tree_form"] for row in rows)


def test_the_prediction_is_folded_in(rows):
    """As the city's predictions table published it, not derived again."""
    trees = by_id(rows)
    assert trees["sf-1"]["predicted_crown_width_m"] == pytest.approx(4.5)
    assert trees["sf-3"]["predicted_crown_width_m"] is None, "a prediction row with nothing to size from"


def test_heights_are_folded_in(rows):
    """Measured and predicted, side by side, from the predictions file."""
    trees = by_id(rows)
    assert trees["sf-1"]["height_m"] == pytest.approx(13.5)
    assert trees["sf-1"]["predicted_height_m"] == pytest.approx(11.0)
    assert trees["sf-2"]["height_m"] is None
    assert trees["sf-3"]["predicted_height_m"] is None
    assert trees["sf-4"]["height_m"] is None and trees["sf-4"]["predicted_height_m"] is None, "no prediction row"


# --- The vocabulary ------------------------------------------------------------

VOCAB_KEY = ("city", "data_source", "species", "cultivar", "tree_name", "display_name")


def test_the_vocabulary_is_the_projection_grouped(rows, vocab):
    """Every string tuple the projection holds, once, with its tree count.

    The worker builds the projection's ENUMs from this file alone, so a value
    it lacks would fail the cast of every window holding that tree.
    """
    from collections import Counter

    expected = Counter(tuple(row[c] for c in VOCAB_KEY) for row in rows)
    got = {tuple(row[c] for c in VOCAB_KEY): row["vocab_tree_count"] for row in vocab}
    assert len(got) == len(vocab), "a tuple appears twice"
    assert got == dict(expected)


def test_the_vocabulary_counts_sum_to_the_projection(rows, vocab):
    """The worker's check that the two files describe the same build."""
    assert sum(row["vocab_tree_count"] for row in vocab) == len(rows)


def test_both_files_carry_the_citys_own_watermark():
    """One scalar per city, from its probe (shared/city_derived.py): a tree
    with no prediction row cannot split a vocabulary tuple on a null."""
    model = statements(RAW_DIR / "city_derived.preql")
    for template in ("city_tree_browser", "city_tree_vocab"):
        block = re.search(rf"template datasource {template} \(.*?;", model, re.S).group(0)
        assert "freshness by city_inputs_published_through" in block, template
