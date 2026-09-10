"""The crown-width model: its coefficient table, its fallbacks, and its wiring.

`tree_predictions.preql` applies `crown_width_m = 2 * scale * dbh_cm ** b`
from a committed CSV that `crown_allometry_fit.py` writes from Tallo. Nothing
in a refresh checks that the CSV is well-formed or that the constants rendered
into the model still match it -- a stale block or a row with a negative
exponent builds fine and publishes crowns that shrink with diameter.

The wiring tests are the ones `test_cloud_jobs.py` makes for the rollup and
enrichment, applied to a job that consumes both.
"""

from __future__ import annotations

import csv
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
REPO_DIR = DATA_DIR.parent

sys.path.insert(0, str(RAW_DIR))

import crown_allometry_fit as fit  # noqa: E402

MODEL = RAW_DIR / "tree_predictions.preql"
COEFFICIENTS = RAW_DIR / "crown_width_coefficients.csv"


def statements(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with COEFFICIENTS.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_csv_has_the_columns_the_model_reads(rows):
    assert list(rows[0].keys()) == fit.CSV_COLUMNS
    for column in ("level", "taxon", "fit_level", "fit_taxon", "n", "b", "scale", "dbh_max_cm"):
        assert f"{column}:" in statements(MODEL), f"the model no longer maps {column}"


def test_every_genus_row_is_a_usable_power_law(rows):
    """The gate the fit applies, re-checked on what was committed."""
    genus_rows = [r for r in rows if r["level"] == "genus"]
    assert len(genus_rows) > 1000, "Tallo knows ~1,450 genera; the table is truncated"
    assert len({r["taxon"] for r in genus_rows}) == len(genus_rows), "duplicate genus rows"
    for r in genus_rows:
        n, b, scale, r2 = int(r["n"]), float(r["b"]), float(r["scale"]), float(r["r2"])
        dbh_max = float(r["dbh_max_cm"])
        assert n >= fit.MIN_N, r
        assert fit.B_RANGE[0] <= b <= fit.B_RANGE[1], r
        assert r2 >= fit.MIN_R2, r
        assert math.isfinite(scale) and scale > 0, r
        assert dbh_max >= 20, r  # a fit that never saw a 20 cm stem is not a tree fit
        assert r["fit_level"] in ("genus", "family", "division"), r
        assert r["taxon"][0].isupper() and " " not in r["taxon"], r


def test_fallback_rows_exist(rows):
    by_key = {(r["level"], r["taxon"]) for r in rows}
    assert ("division", "Angiosperm") in by_key
    assert ("division", "Gymnosperm") in by_key
    assert ("global", "all") in by_key


def test_the_fallback_block_in_the_model_is_current(rows):
    """`--check` without shelling out: the constants come from the CSV."""
    text = MODEL.read_text(encoding="utf-8")
    assert fit.current_block(text) == fit.render_block(rows), (
        "tree_predictions.preql's crown_fallbacks block disagrees with "
        "crown_width_coefficients.csv; run crown_allometry_fit.py --write"
    )


def test_check_flag_agrees():
    result = subprocess.run(
        [sys.executable, str(RAW_DIR / "crown_allometry_fit.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=RAW_DIR,
    )
    assert result.returncode == 0, result.stderr


def test_common_street_genera_get_their_own_fit(rows):
    """The genera that carry most of the map should not be on a fallback."""
    by_genus = {r["taxon"]: r for r in rows if r["level"] == "genus"}
    for genus in ("Acer", "Quercus", "Fraxinus", "Tilia", "Ulmus", "Platanus", "Prunus", "Pinus", "Picea", "Betula"):
        assert by_genus[genus]["fit_level"] == "genus", f"{genus} fell back to {by_genus[genus]['fit_level']}"


def test_predictions_are_plausible(rows):
    """Order-of-magnitude checks against the urban literature.

    Coombes et al. (2019) put an open-grown 30 cm broadleaf at about 8 m of
    crown; Tallo's forest-grown fits run lower, which is documented, but a
    genus fit outside 3-12 m at that size is a fit that should not have passed
    the gate.
    """
    by_genus = {r["taxon"]: r for r in rows if r["level"] == "genus"}
    for genus in ("Acer", "Quercus", "Fraxinus", "Tilia", "Ulmus", "Platanus", "Prunus", "Betula", "Gleditsia"):
        r = by_genus[genus]
        crown_30 = 2 * float(r["scale"]) * 30 ** float(r["b"])
        crown_60 = 2 * float(r["scale"]) * 60 ** float(r["b"])
        assert 3 < crown_30 < 12, (genus, crown_30)
        assert crown_60 > crown_30, (genus, crown_30, crown_60)


def test_scale_is_the_bias_corrected_intercept(rows):
    for r in rows[:50]:
        expected = math.exp(float(r["ln_a"]) + float(r["sigma"]) ** 2 / 2)
        assert math.isclose(float(r["scale"]), expected, rel_tol=1e-4), r


# --- wiring -----------------------------------------------------------------


def test_the_prediction_job_consumes_its_inputs_by_name():
    """Two derived edges, both hanging on a datasource name.

    The rollup and the enrichment table are reached through root datasources
    named exactly like the managed ones that write them; rename either side
    and the platform stops ordering this job after its producers, silently.
    """
    model = statements(MODEL)
    assert "import full_tree_info_source;" in model
    assert "import tree_enrichment_source;" in model
    enrichment_source = statements(RAW_DIR / "tree_enrichment_source.preql")
    assert re.search(r"^root partial datasource tree_enrichment \(", enrichment_source, re.M), (
        "tree_enrichment_source.preql must declare `tree_enrichment` as a root "
        "*partial* datasource: root so a consumer never rebuilds it, partial so "
        "the planner joins it with an outer join and a tree whose species has "
        "no enrichment row yet is still predicted"
    )
    assert re.search(r"^datasource tree_enrichment \(", statements(RAW_DIR / "tree_enrichment.preql"), re.M)
    # And the model itself must not be a second route to either.
    assert "import tree_enrichment;" not in model
    assert "import full_tree_publish;" not in model


def test_the_source_views_stay_out_of_the_frontend_bundle():
    """`genus` is a key here and a property in the enrichment model.

    The two declarations cannot share a scope, and the frontend bundles the
    enrichment model. `trilogyModels.ts` lists its files explicitly and globs
    only per-city directories, so a `_source.preql` in `raw/` is out unless
    someone adds it; this is what says not to.
    """
    bundle = (REPO_DIR / "src" / "src" / "trilogyModels.ts").read_text(encoding="utf-8")
    for name in ("full_tree_info_source", "tree_enrichment_source", "tree_predictions"):
        assert name not in bundle, f"{name}.preql is in the frontend model bundle"


def test_the_model_parses():
    """A parse failure is otherwise found on a provisioned VM at 06:00 UTC."""
    from trilogy import Environment

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    names = {d.name for d in env.datasources.values()}
    assert "tree_predictions" in names
    assert "full_tree_info" in names
    assert "tree_enrichment" in names
    assert "crown_width_coefficients" in names


# --- execution ------------------------------------------------------------


@pytest.fixture(scope="module")
def predictions(tmp_path_factory) -> list[dict]:
    """The model's own SQL, run by DuckDB over nine fixture trees.

    The planner is asked for the target's columns, the two `read_parquet`
    URLs it names are pointed at small local parquets, and the const bind
    parameters are filled from the model text -- the same three substitutions
    a cloud refresh makes, minus the network. What comes back is what the
    published parquet would hold for these trees.
    """
    import duckdb
    from trilogy import Dialects, Environment

    tmp = tmp_path_factory.mktemp("predictions")
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE rollup AS SELECT * FROM (VALUES
            ('t-acer-30',   'USSFO', 'SF_OPENDATA', 'Acer platanoides',  30.0 / 2.54, 37.7700, -122.4200),
            ('t-acer-huge', 'USSFO', 'SF_OPENDATA', 'Acer platanoides', 5000.0,       37.7700, -122.4200),
            ('t-acer-nodbh','USSFO', 'SF_OPENDATA', 'Acer platanoides',  NULL,        37.7700, -122.4200),
            ('t-acer-zero', 'USSFO', 'SF_OPENDATA', 'Acer platanoides',  0.0,         37.7700, -122.4200),
            ('t-palm',      'USSFO', 'SF_OPENDATA', 'Washingtonia robusta', 20.0,     37.7700, -122.4200),
            ('t-unknown',   'USSFO', 'SF_OPENDATA', 'Unknown',           12.0,        37.7800, -122.4300),
            ('t-new',       'USSFO', 'SF_OPENDATA', 'Nothingia nova',    12.0,        37.7800, -122.4300),
            ('t-conifer',   'USSFO', 'SF_OPENDATA', 'Newconifer alba',   12.0,        37.7800, -122.4300),
            ('t-nowhere',   'USSFO', 'SF_OPENDATA', 'Acer platanoides',  12.0,        NULL,    NULL)
        ) AS t(tree_id, city, data_source, species, diameter_at_breast_height, latitude, longitude)
        """
    )
    conn.execute(
        """
        CREATE TABLE enrichment AS SELECT * FROM (VALUES
            ('Acer platanoides',    'Acer',        'broadleaf'),
            ('Washingtonia robusta','Washingtonia','palm'),
            ('Unknown',             NULL,          'default'),
            ('Newconifer alba',     'Newconifer',  'conifer')
        ) AS t(species, genus, tree_form)
        """
    )
    rollup = tmp / "full_tree_info.parquet"
    enrichment = tmp / "tree_enrichment.parquet"
    conn.execute(f"COPY rollup TO '{rollup.as_posix()}' (FORMAT PARQUET)")
    conn.execute(f"COPY enrichment TO '{enrichment.as_posix()}' (FORMAT PARQUET)")

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    # Ask for the derivation, not the published parquet: with the target in
    # scope the planner would read tree_predictions_v2.parquet back instead.
    del env.datasources["tree_predictions"]
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    (sql,) = executor.generate_sql(
        """
        select
            tree_id, city, genus, dbh_cm, predicted_crown_width_m,
            crown_model_level, crown_model_taxon, crown_model_n,
            local_tree_density_per_ha, predicted_height_m, predicted_age_years
        where tree_id is not null;
        """
    )
    sql = re.sub(r"read_parquet\('[^']*full_tree_info_v[^']*'\)", f"read_parquet('{rollup.as_posix()}')", sql)
    sql = re.sub(r"read_parquet\('[^']*tree_enrichment_v[^']*'\)", f"read_parquet('{enrichment.as_posix()}')", sql)
    for name, value in re.findall(r"^const (\w+) <- ([-\d.]+);", MODEL.read_text(encoding="utf-8"), re.M):
        sql = re.sub(rf":{name}\b", value, sql)
    assert "uv_run" not in sql, "the probe should not be needed for these columns"
    assert ":" not in re.sub(r"'[^']*'", "", sql), "an unbound parameter survived"
    columns = [d[0] for d in conn.execute(sql).description]
    return [dict(zip(columns, row)) for row in conn.execute(sql).fetchall()]


def by_id(predictions: list[dict]) -> dict[str, dict]:
    return {row["tree_id"]: row for row in predictions}


def test_every_fixture_tree_comes_back_once(predictions):
    ids = [row["tree_id"] for row in predictions]
    assert sorted(ids) == sorted(set(ids)) and len(ids) == 9, ids


def test_a_genus_fit_is_applied_as_documented(predictions, rows):
    acer = next(r for r in rows if r["level"] == "genus" and r["taxon"] == "Acer")
    row = by_id(predictions)["t-acer-30"]
    expected = 2 * float(acer["scale"]) * 30 ** float(acer["b"])
    assert row["crown_model_level"] == "genus"
    assert row["crown_model_taxon"] == "Acer"
    assert row["crown_model_n"] == int(acer["n"])
    assert math.isclose(row["dbh_cm"], 30.0, rel_tol=1e-9)
    assert math.isclose(row["predicted_crown_width_m"], expected, rel_tol=1e-6)


def test_diameter_is_clamped_to_the_fitted_range(predictions, rows):
    acer = next(r for r in rows if r["level"] == "genus" and r["taxon"] == "Acer")
    row = by_id(predictions)["t-acer-huge"]
    expected = 2 * float(acer["scale"]) * float(acer["dbh_max_cm"]) ** float(acer["b"])
    assert math.isclose(row["predicted_crown_width_m"], expected, rel_tol=1e-6)


def test_no_diameter_means_no_prediction(predictions):
    """DuckDB's greatest() skips nulls; the first cut predicted a 70 cm crown here."""
    for tree in ("t-acer-nodbh", "t-acer-zero"):
        row = by_id(predictions)[tree]
        assert row["dbh_cm"] is None, row
        assert row["predicted_crown_width_m"] is None, row
        assert row["crown_model_level"] == "none", row
        assert row["crown_model_taxon"] is None and row["crown_model_n"] is None, row


def test_palms_get_no_crown_model(predictions):
    row = by_id(predictions)["t-palm"]
    assert row["predicted_crown_width_m"] is None
    assert row["crown_model_level"] == "none"


def test_fallbacks_by_form_and_sentinel(predictions):
    got = by_id(predictions)
    assert got["t-unknown"]["crown_model_level"] == "global"
    assert got["t-unknown"]["crown_model_taxon"] == "all"
    assert got["t-new"]["crown_model_level"] == "division"
    assert got["t-new"]["crown_model_taxon"] == "Angiosperm"
    assert got["t-conifer"]["crown_model_taxon"] == "Gymnosperm"
    for tree in ("t-unknown", "t-new", "t-conifer"):
        assert got[tree]["predicted_crown_width_m"] > 0
    # Same diameter, different fits: a conifer's crown is the narrower one.
    assert got["t-conifer"]["predicted_crown_width_m"] < got["t-new"]["predicted_crown_width_m"]


def test_density_counts_the_cell_and_tolerates_no_coordinates(predictions):
    got = by_id(predictions)
    # Five trees at one point share one 50 m cell (0.25 ha); three at another.
    for tree in ("t-acer-30", "t-acer-huge", "t-acer-nodbh", "t-acer-zero", "t-palm"):
        assert got[tree]["local_tree_density_per_ha"] == 5 / 0.25, got[tree]
    for tree in ("t-unknown", "t-new", "t-conifer"):
        assert got[tree]["local_tree_density_per_ha"] == 3 / 0.25, got[tree]
    assert got["t-nowhere"]["local_tree_density_per_ha"] is None
    assert got["t-nowhere"]["predicted_crown_width_m"] > 0, "a tree with no coordinates still has a crown"


def test_placeholders_are_null(predictions):
    for row in predictions:
        assert row["predicted_height_m"] is None
        assert row["predicted_age_years"] is None
