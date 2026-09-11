"""The crown-width model: its coefficient tables, its fallbacks, and its wiring.

`tree_predictions.preql` applies `crown_width_m = 2 * scale * dbh_cm ** b`
from a committed CSV that `crown_allometry_fit.py` writes from Tallo, and,
for a tree with a planting date and no diameter, first
`dbh_cm = scale * age_years ** b` from a second CSV that `dbh_age_fit.py`
writes from the rollup's own dated, measured trees. Nothing in a refresh
checks that either CSV is well-formed or that the constants rendered into the
model still match it -- a stale block or a row with a negative exponent builds
fine and publishes crowns that shrink with diameter.

The wiring tests are the ones `test_cloud_jobs.py` makes for the rollup and
enrichment, applied to a job that consumes both.
"""

from __future__ import annotations

import csv
import math
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
REPO_DIR = DATA_DIR.parent

sys.path.insert(0, str(RAW_DIR))

import crown_allometry_fit as fit  # noqa: E402
import dbh_age_fit as age_fit  # noqa: E402

MODEL = RAW_DIR / "tree_predictions.preql"
COEFFICIENTS = RAW_DIR / "crown_width_coefficients.csv"
AGE_COEFFICIENTS = RAW_DIR / "dbh_age_coefficients.csv"


def statements(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with COEFFICIENTS.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def age_rows() -> list[dict[str, str]]:
    with AGE_COEFFICIENTS.open(encoding="utf-8", newline="") as handle:
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


# --- the age model's table ---------------------------------------------------


def test_age_csv_has_the_columns_the_model_reads(age_rows):
    assert list(age_rows[0].keys()) == age_fit.CSV_COLUMNS
    for column in ("level", "taxon", "fit_level", "fit_taxon", "n", "b", "scale", "age_max_years"):
        assert f"{column}:" in statements(MODEL), f"the model no longer maps {column}"


def test_every_age_genus_row_is_a_usable_power_law(age_rows):
    """The gate `dbh_age_fit.py` applies, re-checked on what was committed."""
    genus_rows = [r for r in age_rows if r["level"] == "genus"]
    assert len(genus_rows) > 100, "the rollup carries dated, measured trees of well over 100 genera"
    assert len({r["taxon"] for r in genus_rows}) == len(genus_rows), "duplicate genus rows"
    for r in genus_rows:
        n, b, scale, r2 = int(r["n"]), float(r["b"]), float(r["scale"]), float(r["r2"])
        age_max = float(r["age_max_years"])
        assert n >= age_fit.MIN_N, r
        assert age_fit.B_RANGE[0] <= b <= age_fit.B_RANGE[1], r
        assert r2 >= age_fit.MIN_R2, r
        assert math.isfinite(scale) and scale > 0, r
        assert age_fit.MIN_FITTED_AGE_MAX_YEARS <= age_max <= age_fit.MAX_AGE_YEARS, r
        assert int(r["cities"]) >= 1, r
        assert r["fit_level"] in ("genus", "division", "global"), r
        assert r["taxon"][0].isupper() and " " not in r["taxon"], r
        expected = math.exp(float(r["ln_a"]) + float(r["sigma"]) ** 2 / 2)
        assert math.isclose(scale, expected, rel_tol=1e-4), r


def test_age_fallback_rows_exist(age_rows):
    by_key = {(r["level"], r["taxon"]) for r in age_rows}
    assert ("division", "Angiosperm") in by_key
    assert ("division", "Gymnosperm") in by_key
    assert ("global", "all") in by_key


def test_the_age_fallback_block_in_the_model_is_current(age_rows):
    text = MODEL.read_text(encoding="utf-8")
    assert age_fit.current_block(text) == age_fit.render_block(age_rows), (
        "tree_predictions.preql's dbh_age_fallbacks block disagrees with "
        "dbh_age_coefficients.csv; run dbh_age_fit.py --write"
    )


def test_age_check_flag_agrees():
    result = subprocess.run(
        [sys.executable, str(RAW_DIR / "dbh_age_fit.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=RAW_DIR,
    )
    assert result.returncode == 0, result.stderr


def test_common_street_genera_get_their_own_age_fit(age_rows):
    by_genus = {r["taxon"]: r for r in age_rows if r["level"] == "genus"}
    for genus in ("Acer", "Quercus", "Fraxinus", "Tilia", "Ulmus", "Platanus", "Prunus", "Pinus", "Picea", "Betula"):
        assert by_genus[genus]["fit_level"] == "genus", f"{genus} fell back to {by_genus[genus]['fit_level']}"


def test_age_predictions_are_plausible(age_rows):
    """i-Tree's open-grown base rate is 0.83 cm of diameter a year (Nowak 1994).

    A 30-year-old street tree of a common genus is therefore somewhere around
    25 cm; a genus fit outside 12-45 cm at that age is one the gate should
    have stopped, and a diameter that does not grow with age is not a growth
    model at all.
    """
    by_genus = {r["taxon"]: r for r in age_rows if r["level"] == "genus"}
    for genus in ("Acer", "Quercus", "Fraxinus", "Tilia", "Ulmus", "Platanus", "Prunus", "Betula", "Gleditsia"):
        r = by_genus[genus]
        dbh_30 = float(r["scale"]) * 30 ** float(r["b"])
        dbh_60 = float(r["scale"]) * 60 ** float(r["b"])
        assert 12 < dbh_30 < 45, (genus, dbh_30)
        assert dbh_60 > dbh_30, (genus, dbh_30, dbh_60)


def test_the_fit_reapplies_the_ingest_guards_with_the_same_numbers():
    """The fit reads parquets built before the guards; its copies must not drift."""
    import _ingest_shared as ingest

    assert age_fit.DBH_MAX_INCHES == ingest.DBH_MAX_INCHES
    assert age_fit.PLANT_DATE_MIN_YEAR == ingest.PLANT_DATE_MIN_YEAR


def test_the_nulled_dates_are_the_ones_the_city_ingests_null():
    """An entry is a city code and the date that city's ingest declares."""
    import importlib.util

    declared = {}
    for code, path in (("CAEDM", "caedm/edmonton_tree_info.py"), ("AUMEL", "aumel/melbourne_tree_info.py")):
        spec = importlib.util.spec_from_file_location(f"ingest_{code}", RAW_DIR / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        declared[code] = module.PLACEHOLDER_PLANT_DATE.isoformat()
    for city, day in age_fit.INGEST_NULLED_PLANT_DATES:
        assert re.fullmatch(r"[A-Z]{5}", city), city
        assert declared.get(city) == day, (
            f"{city} {day}: the fit re-applies a date its ingest does not null; "
            "the ingest is the authority, and an entry here is transitional"
        )


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
    assert "dbh_age_coefficients" in names


# --- execution ------------------------------------------------------------

TODAY = date.today()
POINT_A = (37.7700, -122.4200)  # five trees share this 50 m cell
POINT_B = (37.7800, -122.4300)  # three
POINT_C = (37.7900, -122.4400)  # the age fixtures, seven


def years_ago(years: float) -> date:
    return TODAY - timedelta(days=round(years * 365.25))


def age_of(planted: date) -> float:
    """What the model computes: days to the build date, over 365.25."""
    return (TODAY - planted).days / 365.25


# (tree_id, species, dbh_in, plant_date, point)
FIXTURE_TREES = [
    ("t-acer-30",       "Acer platanoides",     30.0 / 2.54, years_ago(20),      POINT_A),  # measured and dated: the measurement wins
    ("t-acer-huge",     "Acer platanoides",     5000.0,      None,               POINT_A),
    ("t-acer-nodbh",    "Acer platanoides",     None,        None,               POINT_A),
    ("t-acer-zero",     "Acer platanoides",     0.0,         None,               POINT_A),
    ("t-palm",          "Washingtonia robusta", 20.0,        years_ago(20),      POINT_A),
    ("t-unknown",       "Unknown",              12.0,        None,               POINT_B),
    ("t-new",           "Nothingia nova",       12.0,        years_ago(20),      POINT_B),  # in neither table
    ("t-conifer",       "Newconifer alba",      12.0,        years_ago(20),      POINT_B),
    ("t-nowhere",       "Acer platanoides",     12.0,        None,               None),
    # The age fallback: a planting date and no diameter.
    ("t-aged",          "Acer platanoides",     None,        years_ago(20),      POINT_C),
    ("t-sapling",       "Acer platanoides",     None,        years_ago(0.25),    POINT_C),  # clamped to one year
    ("t-old",           "Acer platanoides",     None,        years_ago(250),     POINT_C),  # past the fit window: clamped to the fit's oldest
    ("t-unknown-aged",  "Unknown",              None,        years_ago(20),      POINT_C),  # the global age fit
    ("t-lilac",         "Syringa vulgaris",     None,        years_ago(20),      POINT_C),  # an age fit, no Tallo row
    ("t-abarema",       "Abarema jupunba",      None,        years_ago(20),      POINT_C),  # a Tallo row, no age fit
]


@pytest.fixture(scope="module")
def predictions(tmp_path_factory) -> list[dict]:
    """The model's own SQL, run by DuckDB over the fixture trees.

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
        CREATE TABLE rollup (
            tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR,
            diameter_at_breast_height DOUBLE, plant_date DATE, latitude DOUBLE, longitude DOUBLE
        )
        """
    )
    conn.executemany(
        "INSERT INTO rollup VALUES (?, 'USSFO', 'SF_OPENDATA', ?, ?, ?, ?, ?)",
        [
            (tree_id, species, dbh, planted, *(point or (None, None)))
            for tree_id, species, dbh, planted, point in FIXTURE_TREES
        ],
    )
    conn.execute(
        """
        CREATE TABLE enrichment AS SELECT * FROM (VALUES
            ('Acer platanoides',    'Acer',        'broadleaf'),
            ('Washingtonia robusta','Washingtonia','palm'),
            ('Unknown',             NULL,          'default'),
            ('Newconifer alba',     'Newconifer',  'conifer'),
            ('Syringa vulgaris',    'Syringa',     'multi_trunk'),
            ('Abarema jupunba',     'Abarema',     'broadleaf')
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
            tree_id, city, genus, dbh_cm, age_years, predicted_dbh_cm,
            dbh_model_level, dbh_model_taxon, dbh_model_n, crown_dbh_source,
            predicted_crown_width_m, crown_model_level, crown_model_taxon, crown_model_n,
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


def genus_row(rows: list[dict[str, str]], genus: str) -> dict[str, str]:
    return next(r for r in rows if r["level"] == "genus" and r["taxon"] == genus)


def crown_from(row: dict[str, str], dbh_cm: float) -> float:
    dbh_cm = min(max(dbh_cm, 1.0), float(row["dbh_max_cm"]))
    return 2 * float(row["scale"]) * dbh_cm ** float(row["b"])


def dbh_from(row: dict[str, str], age_years: float) -> float:
    age_years = min(max(age_years, 1.0), float(row["age_max_years"]))
    return float(row["scale"]) * age_years ** float(row["b"])


def test_every_fixture_tree_comes_back_once(predictions):
    ids = [row["tree_id"] for row in predictions]
    assert sorted(ids) == sorted(set(ids)) and len(ids) == len(FIXTURE_TREES), ids


def test_a_genus_fit_is_applied_as_documented(predictions, rows):
    acer = genus_row(rows, "Acer")
    row = by_id(predictions)["t-acer-30"]
    assert row["crown_model_level"] == "genus"
    assert row["crown_model_taxon"] == "Acer"
    assert row["crown_model_n"] == int(acer["n"])
    assert math.isclose(row["dbh_cm"], 30.0, rel_tol=1e-9)
    assert math.isclose(row["predicted_crown_width_m"], crown_from(acer, 30.0), rel_tol=1e-6)


def test_diameter_is_clamped_to_the_fitted_range(predictions, rows):
    acer = genus_row(rows, "Acer")
    row = by_id(predictions)["t-acer-huge"]
    assert math.isclose(row["predicted_crown_width_m"], crown_from(acer, float(acer["dbh_max_cm"])), rel_tol=1e-6)


def test_no_diameter_and_no_date_means_no_prediction(predictions):
    """DuckDB's greatest() skips nulls; the first cut predicted a 70 cm crown here."""
    for tree in ("t-acer-nodbh", "t-acer-zero"):
        row = by_id(predictions)[tree]
        assert row["dbh_cm"] is None, row
        assert row["age_years"] is None and row["predicted_dbh_cm"] is None, row
        assert row["dbh_model_level"] == "none" and row["crown_dbh_source"] is None, row
        assert row["predicted_crown_width_m"] is None, row
        assert row["crown_model_level"] == "none", row
        assert row["crown_model_taxon"] is None and row["crown_model_n"] is None, row


def test_palms_get_no_crown_model(predictions):
    row = by_id(predictions)["t-palm"]
    assert row["predicted_crown_width_m"] is None
    assert row["crown_model_level"] == "none"
    # Dated, and still no diameter model: a palm's stem does not thicken.
    assert row["age_years"] is not None
    assert row["predicted_dbh_cm"] is None and row["dbh_model_level"] == "none"


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


# --- the age fallback --------------------------------------------------------


def test_a_measured_diameter_wins_over_the_predicted_one(predictions, age_rows):
    """Both are published; the crown is built on the measurement."""
    acer = genus_row(age_rows, "Acer")
    row = by_id(predictions)["t-acer-30"]
    assert row["crown_dbh_source"] == "measured"
    assert math.isclose(row["age_years"], age_of(years_ago(20)), rel_tol=1e-9)
    assert math.isclose(row["predicted_dbh_cm"], dbh_from(acer, row["age_years"]), rel_tol=1e-6)
    assert row["dbh_model_level"] == "genus" and row["dbh_model_taxon"] == "Acer"
    assert row["dbh_model_n"] == int(acer["n"])


def test_a_dated_tree_with_no_diameter_gets_a_crown_from_its_age(predictions, rows, age_rows):
    acer_age = genus_row(age_rows, "Acer")
    acer_crown = genus_row(rows, "Acer")
    row = by_id(predictions)["t-aged"]
    assert row["dbh_cm"] is None
    assert row["crown_dbh_source"] == "age"
    predicted_dbh = dbh_from(acer_age, age_of(years_ago(20)))
    assert math.isclose(row["predicted_dbh_cm"], predicted_dbh, rel_tol=1e-6)
    assert row["dbh_model_level"] == "genus" and row["dbh_model_taxon"] == "Acer"
    # The crown model is the same one, applied to the predicted stem.
    assert row["crown_model_level"] == "genus" and row["crown_model_taxon"] == "Acer"
    assert math.isclose(row["predicted_crown_width_m"], crown_from(acer_crown, predicted_dbh), rel_tol=1e-6)
    assert 15 < row["predicted_dbh_cm"] < 45, row  # a 20-year-old maple
    assert 3 < row["predicted_crown_width_m"] < 12, row


def test_a_sapling_is_clamped_to_one_year(predictions, age_rows):
    """The fit's floor: below a year the power law heads for zero."""
    acer = genus_row(age_rows, "Acer")
    row = by_id(predictions)["t-sapling"]
    assert 0 < row["age_years"] < 1
    assert math.isclose(row["predicted_dbh_cm"], float(acer["scale"]), rel_tol=1e-6)
    assert row["crown_dbh_source"] == "age"


def test_an_old_tree_is_clamped_to_the_fitted_range(predictions, age_rows):
    """Past the oldest tree the genus was fitted on, the stem stops growing."""
    acer = genus_row(age_rows, "Acer")
    row = by_id(predictions)["t-old"]
    assert row["age_years"] > float(acer["age_max_years"])
    assert math.isclose(row["predicted_dbh_cm"], dbh_from(acer, float(acer["age_max_years"])), rel_tol=1e-6)
    assert row["crown_dbh_source"] == "age"


def test_age_fallbacks_by_form_and_sentinel(predictions, age_rows):
    got = by_id(predictions)
    by_key = {(r["level"], r["taxon"]): r for r in age_rows}
    assert got["t-unknown-aged"]["dbh_model_level"] == "global"
    assert got["t-unknown-aged"]["dbh_model_taxon"] == "all"
    assert got["t-unknown-aged"]["dbh_model_n"] == int(by_key[("global", "all")]["n"])
    assert got["t-unknown-aged"]["crown_model_level"] == "global"
    # Measured, so the crown is on the measurement, but the age model still
    # reports which fit it used.
    assert got["t-new"]["dbh_model_level"] == "division"
    assert got["t-new"]["dbh_model_taxon"] == "Angiosperm"
    assert got["t-new"]["dbh_model_n"] == int(by_key[("division", "Angiosperm")]["n"])
    assert got["t-conifer"]["dbh_model_taxon"] == "Gymnosperm"
    assert got["t-conifer"]["dbh_model_n"] == int(by_key[("division", "Gymnosperm")]["n"])
    for tree in ("t-new", "t-conifer"):
        assert got[tree]["crown_dbh_source"] == "measured"
        assert got[tree]["predicted_dbh_cm"] > 0


def test_a_genus_in_one_coefficient_table_keeps_its_row_from_the_other(predictions, rows, age_rows):
    """Two root tables keyed on genus; neither may drop the other's genera.

    Syringa has an age fit and no Tallo row (its crown falls to the division
    constants); Abarema has a Tallo row and no age fit. An inner join between
    the two tables would lose both rows and demote both trees silently.
    """
    got = by_id(predictions)
    lilac = got["t-lilac"]
    syringa = genus_row(age_rows, "Syringa")
    assert lilac["dbh_model_level"] == syringa["fit_level"] and lilac["dbh_model_taxon"] == syringa["fit_taxon"]
    assert math.isclose(lilac["predicted_dbh_cm"], dbh_from(syringa, age_of(years_ago(20))), rel_tol=1e-6)
    assert lilac["crown_dbh_source"] == "age"
    assert lilac["crown_model_level"] == "division" and lilac["crown_model_taxon"] == "Angiosperm"

    abarema = got["t-abarema"]
    abarema_crown = genus_row(rows, "Abarema")
    assert abarema["dbh_model_level"] == "division" and abarema["dbh_model_taxon"] == "Angiosperm"
    assert abarema["crown_model_level"] == abarema_crown["fit_level"]
    assert abarema["crown_model_taxon"] == abarema_crown["fit_taxon"]
    assert math.isclose(
        abarema["predicted_crown_width_m"], crown_from(abarema_crown, abarema["predicted_dbh_cm"]), rel_tol=1e-6
    )


def test_density_counts_the_cell_and_tolerates_no_coordinates(predictions):
    got = by_id(predictions)
    counts = {POINT_A: 0, POINT_B: 0, POINT_C: 0}
    for _, _, _, _, point in FIXTURE_TREES:
        if point is not None:
            counts[point] += 1
    for tree_id, _, _, _, point in FIXTURE_TREES:
        if point is None:
            assert got[tree_id]["local_tree_density_per_ha"] is None
        else:
            assert got[tree_id]["local_tree_density_per_ha"] == counts[point] / 0.25, got[tree_id]
    assert got["t-nowhere"]["predicted_crown_width_m"] > 0, "a tree with no coordinates still has a crown"


def test_placeholders_are_null(predictions):
    for row in predictions:
        assert row["predicted_height_m"] is None
        assert row["predicted_age_years"] is None
