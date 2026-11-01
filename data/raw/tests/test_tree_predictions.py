"""The predictions model: its crown table, its fits, its gates and its wiring.

`tree_predictions.preql` applies `crown_width_m = 2 * scale * dbh_cm ** b`
from a committed CSV that `crown_allometry_fit.py` writes from Tallo. The
diameter-from-age and height models are fitted inside the model on every
build, on the trees it reads; the execution tests below run the model's own
SQL over a generated population and check every fit against a least-squares
reference computed here.

The wiring tests are the ones `test_cloud_jobs.py` makes for the rollup and
enrichment, applied to a job that consumes both.
"""

from __future__ import annotations

import csv
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
REPO_DIR = DATA_DIR.parent

sys.path.insert(0, str(RAW_DIR))
# The crown fit is a workstation tool and lives with the rest of them.
sys.path.insert(0, str(RAW_DIR / "tools"))

import crown_allometry_fit as fit  # noqa: E402

MODEL = RAW_DIR / "tree_predictions.preql"
COEFFICIENTS = RAW_DIR / "crown_width_coefficients.csv"
TARGETS = ("tree_predictions", "allometry_genus_fits", "allometry_fallback_fits")


def statements(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )


def model_consts() -> dict[str, float]:
    return {name: float(value) for name, value in re.findall(r"^const (\w+) <- ([-\d.]+);", MODEL.read_text(encoding="utf-8"), re.M)}


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
    """The model reads these three as the fallbacks, keyed by the tree's class."""
    by_key = {(r["level"], r["taxon"]) for r in rows}
    assert ("division", "Angiosperm") in by_key
    assert ("division", "Gymnosperm") in by_key
    assert ("global", "all") in by_key


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


def test_the_fits_reapply_the_ingest_guard_with_the_same_number():
    """A diameter past the ingest's ceiling is a typo; the fits leave it out by the same number."""
    import shared.ingest as ingest

    assert model_consts()["fit_max_dbh_cm"] == pytest.approx(ingest.DBH_MAX_INCHES * 2.54)


def test_no_generated_code_in_the_model():
    """The fallbacks are data now: the CSV's rows, or this build's own fits."""
    text = MODEL.read_text(encoding="utf-8")
    assert "# BEGIN" not in text and "do not edit" not in text
    for stale in ("dbh_age_coefficients", "tree_height_coefficients", "angiosperm_", "gymnosperm_"):
        assert stale not in statements(MODEL), stale


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
    assert re.search(r"^root datasource tree_enrichment \(", enrichment_source, re.M), (
        "tree_enrichment_source.preql must declare `tree_enrichment` as a root "
        "datasource: root so a consumer never rebuilds it"
    )
    assert re.search(r"^\s+species: ~species,$", enrichment_source, re.M), (
        "tree_enrichment_source.preql must bind `species: ~species`, a partial "
        "key on an otherwise complete table: the planner pads the join and a "
        "tree whose species has no enrichment row yet is still predicted, with "
        "its fallbacks. A `partial datasource` instead stamps every column and "
        "every scalar derived from one partial, and pytrilogy >= 0.3.391 "
        "refuses the prediction columns outright."
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
    for name in (
        "full_tree_info_source",
        "tree_enrichment_source",
        "tree_predictions",
        "tree_browser_city",
        "ecoregion_browser",
    ):
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
    assert "crown_width_fallbacks" in names
    assert "allometry_genus_fits" in names
    assert "allometry_fallback_fits" in names
    assert "published_tree_heights" in names


# --- execution ------------------------------------------------------------

TODAY = date.today()
POINT_A = (37.7700, -122.4200)  # five trees share this 50 m cell
POINT_B = (37.7800, -122.4300)  # three
POINT_C = (37.7900, -122.4400)  # the age fixtures, seven
LN_10 = math.log(10)


def years_ago(years: float) -> date:
    return TODAY - timedelta(days=round(years * 365.25))


def age_of(planted: date) -> float:
    """What the model computes: days to the build date, over 365.25."""
    return (TODAY - planted).days / 365.25


# (tree_id, species, dbh_in, plant_date, point, height_m, height_crown_radius_m)
FIXTURE_TREES = [
    ("t-acer-30",       "Acer platanoides",     30.0 / 2.54, years_ago(20),   POINT_A, 12.0, 4.0),   # its own crown, a plausible height
    ("t-acer-huge",     "Acer platanoides",     5000.0,      None,            POINT_A, None, None),
    ("t-acer-nodbh",    "Acer platanoides",     None,        None,            POINT_A, None, None),
    ("t-acer-zero",     "Acer platanoides",     0.0,         None,            POINT_A, None, None),
    ("t-palm",          "Washingtonia robusta", 20.0,        years_ago(20),   POINT_A, 9.0, 3.0),    # no height model to check against
    ("t-unknown",       "Unknown",              12.0,        None,            POINT_B, 45.0, 3.0),   # its own crown, far too tall
    ("t-new",           "Nothingia nova",       12.0,        years_ago(20),   POINT_B, 12.0, 15.0),  # under a crown five times its own
    ("t-conifer",       "Newconifer alba",      12.0,        years_ago(20),   POINT_B, 2.0, 2.0),    # its own crown, far too short
    ("t-nowhere",       "Acer platanoides",     12.0,        None,            None,    None, None),
    # The age fallback: a planting date and no diameter.
    ("t-aged",          "Acer platanoides",     None,        years_ago(20),   POINT_C, 40.0, 15.0),  # a diameter from age, not a measured stem
    ("t-sapling",       "Acer platanoides",     None,        years_ago(0.25), POINT_C, None, None),  # clamped to one year
    ("t-old",           "Acer platanoides",     None,        years_ago(250),  POINT_C, None, None),  # past the fit window: clamped to the fit's oldest
    ("t-unknown-aged",  "Unknown",              None,        years_ago(20),   POINT_C, None, None),  # the global fits
    ("t-lilac",         "Syringa vulgaris",     None,        years_ago(20),   POINT_C, None, None),  # no genus fit, no Tallo row
    ("t-abarema",       "Abarema jupunba",      None,        years_ago(20),   POINT_C, None, None),  # a Tallo row, no genus fit
    ("t-oak",           "Quercus rubra",        12.0,        years_ago(20),   POINT_B, None, None),  # a genus too thin to fit
]

ENRICHMENT = {
    "Acer platanoides": ("Acer", "broadleaf"),
    "Washingtonia robusta": ("Washingtonia", "palm"),
    "Unknown": (None, "default"),
    "Newconifer alba": ("Newconifer", "conifer"),
    "Syringa vulgaris": ("Syringa", "multi_trunk"),
    "Abarema jupunba": ("Abarema", "broadleaf"),
    "Pinus strobus": ("Pinus", "conifer"),
    "Quercus rubra": ("Quercus", "broadleaf"),
}


def generated_trees() -> list[tuple]:
    """The population the fits are made on: three genera along a power law each.

    Acer and Pinus pass both gates; Quercus has too few trees for a genus fit
    and is predicted on the angiosperm fit. Every tree has its own 50 m cell,
    a crown its stem can carry, a height and a planting date, and the three
    `acer-tall` trees are a neighbour's crown on a sapling's point: the band
    must drop them from the height fit.
    """
    out: list[tuple] = []

    def add(prefix, species, count, height_law, dbh_law):
        for i in range(count):
            dbh_cm = 8.0 + 72.0 * i / (count - 1)
            height = height_law[0] * dbh_cm ** height_law[1] * 10 ** (0.05 * math.sin(1.7 * i))
            age = (dbh_cm / dbh_law[0]) ** (1 / dbh_law[1]) * 10 ** (0.04 * math.cos(2.3 * i))
            point = (36.0 + 0.001 * len(out), -121.0)
            out.append((f"{prefix}-{i:03d}", species, dbh_cm / 2.54, years_ago(age), point, height, 1.0))

    add("acer", "Acer platanoides", 150, (2.6, 0.44), (2.5, 0.70))
    add("pinus", "Pinus strobus", 120, (4.5, 0.33), (3.0, 0.62))
    add("quercus", "Quercus rubra", 40, (2.2, 0.50), (2.2, 0.72))
    for i in range(3):
        out.append((f"acer-tall-{i}", "Acer platanoides", 10.0 / 2.54, years_ago(6), (35.9 - 0.001 * i, -121.0), 60.0, 1.0))
    return out


ALL_TREES = FIXTURE_TREES + generated_trees()


# --- the reference fit ------------------------------------------------------
#
# What the model is documented to do, in Python: log10-log10 least squares per
# genus, per fallback class and over every tree, the gate, the fallback, the
# band and its two refits.


def fallback_class(species: str) -> str:
    form = ENRICHMENT.get(species, (None, None))[1]
    if species in ("Unknown", "Dead", "Shrub"):
        return "all"
    if form == "palm" or species in ("Palm", "Cactus"):
        return "none"
    return "Gymnosperm" if form == "conifer" else "Angiosperm"


def reference_trees() -> list[dict]:
    trees = []
    for tree_id, species, dbh_in, planted, _, height, radius in ALL_TREES:
        trees.append(
            {
                "tree_id": tree_id,
                "genus": ENRICHMENT.get(species, (None, None))[0],
                "class": fallback_class(species),
                "dbh_cm": dbh_in * 2.54 if dbh_in and dbh_in > 0 else None,
                "age": age_of(planted) if planted else None,
                "height": height,
                "radius": radius,
            }
        )
    return trees


def ols(points: list[tuple[float, float]]) -> dict:
    n = len(points)
    mean = lambda values: math.fsum(values) / n  # noqa: E731
    mx, my = mean([x for x, _ in points]), mean([y for _, y in points])
    sxx = mean([x * x for x, _ in points]) - mx ** 2
    sxy = mean([x * y for x, y in points]) - mx * my
    syy = mean([y * y for _, y in points]) - my ** 2
    b = sxy / sxx
    s = math.sqrt(max(n / (n - 2) * (syy - b * sxy), 0.0)) if n > 2 else None
    return {"n": n, "b": b, "a": my - b * mx, "s": s, "r2": b * sxy / syy, "xmax": max(x for x, _ in points)}


def age_gate(f: dict) -> bool:
    c = model_consts()
    return (f["n"] >= c["dbh_fit_min_n"] and f["r2"] >= c["dbh_fit_min_r2"]
            and c["dbh_fit_min_b"] <= f["b"] <= c["dbh_fit_max_b"] and f["xmax"] >= math.log10(c["dbh_fit_min_oldest_years"]))


def height_gate(f: dict) -> bool:
    c = model_consts()
    return (f["n"] >= c["height_fit_min_n"] and c["height_fit_min_b"] <= f["b"] <= c["height_fit_max_b"]
            and f["xmax"] >= math.log10(c["height_fit_min_largest_dbh_cm"]))


def fit_all(trees: list[dict], points: dict[str, tuple[float, float]], gate) -> dict:
    """Every group's fit, and each tree's: (level, taxon, fit) or None."""
    def grouped(key):
        groups: dict = {}
        for tree in trees:
            if tree["tree_id"] in points and tree[key] is not None:
                groups.setdefault(tree[key], []).append(points[tree["tree_id"]])
        return {k: ols(v) for k, v in groups.items() if len(v) > 2}

    genus, division = grouped("genus"), grouped("class")
    everything = ols(list(points.values()))
    applied = {}
    for tree in trees:
        own = genus.get(tree["genus"])
        if own and gate(own):
            applied[tree["tree_id"]] = ("genus", tree["genus"], own)
        elif tree["class"] == "none":
            applied[tree["tree_id"]] = None
        elif tree["class"] != "all" and tree["class"] in division and gate(division[tree["class"]]):
            applied[tree["tree_id"]] = ("division", tree["class"], division[tree["class"]])
        else:
            applied[tree["tree_id"]] = ("global", "all", everything)
    return {"genus": genus, "division": division, "global": everything, "applied": applied}


def scale_of(f: dict) -> float:
    return 10 ** (f["a"] + f["s"] ** 2 * LN_10 / 2)


def crown_width(tree: dict, dbh_cm: float, crown_rows: list[dict[str, str]]) -> float | None:
    by_genus = {r["taxon"]: r for r in crown_rows if r["level"] == "genus"}
    fallbacks = {r["taxon"]: r for r in crown_rows if r["level"] in ("division", "global")}
    row = by_genus.get(tree["genus"]) or fallbacks.get(tree["class"])
    if row is None:
        return None
    return 2 * float(row["scale"]) * min(max(dbh_cm, 1.0), float(row["dbh_max_cm"])) ** float(row["b"])


@pytest.fixture(scope="module")
def reference(rows) -> dict:
    c = model_consts()
    trees = reference_trees()
    by_id = {t["tree_id"]: t for t in trees}

    age_points = {
        t["tree_id"]: (math.log10(t["age"]), math.log10(t["dbh_cm"]))
        for t in trees
        if t["age"] is not None and c["dbh_fit_min_age_years"] <= t["age"] <= c["dbh_fit_max_age_years"]
        and t["dbh_cm"] is not None and t["dbh_cm"] <= c["fit_max_dbh_cm"] and t["class"] != "none"
    }
    age = fit_all(trees, age_points, age_gate)

    def shaded(t):
        width = crown_width(t, t["dbh_cm"], rows) if t["dbh_cm"] else None
        return t["radius"] is not None and width is not None and t["radius"] > c["shaded_crown_ratio"] * width / 2

    points = {
        t["tree_id"]: (math.log10(t["dbh_cm"]), math.log10(t["height"]))
        for t in trees
        if t["height"] and t["dbh_cm"] is not None and not shaded(t)
        and c["height_fit_min_dbh_cm"] <= t["dbh_cm"] <= c["fit_max_dbh_cm"] and t["class"] != "none"
    }
    dropped: set[str] = set()
    for _ in range(2):
        rounds = fit_all(trees, points, height_gate)
        kept = {}
        for tree_id, (x, y) in points.items():
            _, _, f = rounds["applied"][tree_id]
            if abs(y - f["a"] - f["b"] * min(max(x, 0.0), f["xmax"])) <= c["height_band_sigmas"] * f["s"]:
                kept[tree_id] = (x, y)
            else:
                dropped.add(tree_id)
        points = kept
    height = fit_all(trees, points, height_gate)

    out = {}
    for t in trees:
        row: dict = {"shaded": shaded(t) if t["dbh_cm"] else False}
        applied = age["applied"][t["tree_id"]]
        row["dbh_model"] = applied
        predicted_dbh = None
        if applied and t["age"] is not None:
            _, _, f = applied
            predicted_dbh = scale_of(f) * min(max(t["age"], 1.0), 10 ** f["xmax"]) ** f["b"]
        row["predicted_dbh_cm"] = predicted_dbh
        stem = t["dbh_cm"] if t["dbh_cm"] is not None else predicted_dbh
        applied = height["applied"][t["tree_id"]]
        row["height_model"] = applied
        row["predicted_height_m"] = None
        if applied and stem is not None:
            _, _, f = applied
            row["predicted_height_m"] = scale_of(f) * min(max(stem, 1.0), 10 ** f["xmax"]) ** f["b"]
            row["gate_low"] = 10 ** (-c["height_band_sigmas"] * f["s"] - f["s"] ** 2 * LN_10 / 2)
            row["gate_high"] = 10 ** (c["height_band_sigmas"] * f["s"] - f["s"] ** 2 * LN_10 / 2)
        out[t["tree_id"]] = row
    return {"trees": out, "age": age, "height": height, "band_dropped": dropped, "by_id": by_id}


@pytest.fixture(scope="module")
def fixture_files(tmp_path_factory) -> dict[str, Path]:
    import duckdb

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
        [(tree_id, species, dbh, planted, *(point or (None, None))) for tree_id, species, dbh, planted, point, _, _ in ALL_TREES],
    )
    conn.execute("CREATE TABLE enrichment (species VARCHAR, genus VARCHAR, tree_form VARCHAR)")
    conn.executemany("INSERT INTO enrichment VALUES (?, ?, ?)", [(s, g, f) for s, (g, f) in ENRICHMENT.items()])
    # The published LIDAR heights: a city parquet holds a fraction of the
    # rollup's trees, and a tree it lacks keeps its row with a null height.
    conn.execute("CREATE TABLE heights (tree_id VARCHAR, height_m FLOAT, height_crown_radius_m FLOAT)")
    conn.executemany(
        "INSERT INTO heights VALUES (?, ?, ?)",
        [(tree_id, height, radius) for tree_id, _, _, _, _, height, radius in ALL_TREES if height is not None]
        + [("t-elsewhere", 20.0, 3.0)],
    )
    files = {
        "full_tree_info_v": tmp / "full_tree_info.parquet",
        "tree_enrichment_v": tmp / "tree_enrichment.parquet",
        "usbos_tree_info_v": tmp / "usbos_tree_info.parquet",
    }
    for table, path in zip(("rollup", "enrichment", "heights"), files.values()):
        conn.execute(f"COPY {table} TO '{path.as_posix()}' (FORMAT PARQUET)")
    return files


def run_model(files: dict[str, Path], query: str) -> list[dict]:
    """The model's own SQL, run by DuckDB over the fixture files.

    The planner is asked for the targets' columns, the `read_parquet` URLs it
    names are pointed at the local parquets, and the const bind parameters are
    filled from the model text -- the same substitutions a cloud refresh
    makes, minus the network.
    """
    import duckdb
    from trilogy import Dialects, Environment

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    # Ask for the derivation, not the published parquets: with the targets in
    # scope the planner would read them back instead.
    for name in TARGETS:
        del env.datasources[name]
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    (sql,) = executor.generate_sql(query)
    for pattern, path in files.items():
        sql = re.sub(rf"read_parquet\(\[?'[^']*{pattern}[^']*'\]?\)", f"read_parquet('{path.as_posix()}')", sql)
    for name, value in model_consts().items():
        sql = re.sub(rf":{name}\b", repr(value), sql)
    assert "uv_run" not in sql, "the probe should not be needed for these columns"
    assert ":" not in re.sub(r"'[^']*'", "", sql), "an unbound parameter survived"
    conn = duckdb.connect()
    columns = [d[0] for d in conn.execute(sql).description]
    return [dict(zip(columns, row)) for row in conn.execute(sql).fetchall()]


@pytest.fixture(scope="module")
def predictions(fixture_files) -> list[dict]:
    return run_model(
        fixture_files,
        """
        select
            tree_id, city, genus, dbh_cm, age_years, predicted_dbh_cm,
            dbh_model_level, dbh_model_taxon, dbh_model_n, crown_dbh_source,
            predicted_crown_width_m, crown_model_level, crown_model_taxon, crown_model_n,
            local_tree_density_per_ha, predicted_height_m, height_m, predicted_age_years,
            height_crown_radius_m, height_check, accepted_height_m
        where tree_id is not null;
        """,
    )


def by_id(predictions: list[dict]) -> dict[str, dict]:
    return {row["tree_id"]: row for row in predictions}


def genus_row(rows: list[dict[str, str]], genus: str) -> dict[str, str]:
    return next(r for r in rows if r["level"] == "genus" and r["taxon"] == genus)


def crown_from(row: dict[str, str], dbh_cm: float) -> float:
    dbh_cm = min(max(dbh_cm, 1.0), float(row["dbh_max_cm"]))
    return 2 * float(row["scale"]) * dbh_cm ** float(row["b"])


def test_every_fixture_tree_comes_back_once(predictions):
    ids = [row["tree_id"] for row in predictions]
    assert sorted(ids) == sorted(set(ids)) and len(ids) == len(ALL_TREES), len(ids)


def test_the_fixture_exercises_every_fallback(reference):
    """The population is built to reach each branch; if it stops, the tests below prove less."""
    levels = {tree_id: r["dbh_model"][0] for tree_id, r in reference["trees"].items() if r["dbh_model"]}
    assert levels["t-aged"] == "genus" and levels["t-oak"] == "division"
    assert levels["t-conifer"] == "division" and levels["t-unknown-aged"] == "global"
    assert reference["band_dropped"] >= {"acer-tall-0", "acer-tall-1", "acer-tall-2"}
    assert not reference["age"]["genus"]["Quercus"]["n"] >= model_consts()["dbh_fit_min_n"]


# --- crown ---------------------------------------------------------------------


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


def test_palms_get_no_model(predictions):
    row = by_id(predictions)["t-palm"]
    assert row["predicted_crown_width_m"] is None and row["crown_model_level"] == "none"
    assert row["predicted_height_m"] is None
    # Dated, and still no diameter model: a palm's stem does not thicken.
    assert row["age_years"] is not None
    assert row["predicted_dbh_cm"] is None and row["dbh_model_level"] == "none"


def test_crown_fallbacks_are_the_csvs_rows(predictions, rows):
    got = by_id(predictions)
    fallbacks = {r["taxon"]: r for r in rows if r["level"] in ("division", "global")}
    for tree, taxon, level in (("t-unknown", "all", "global"), ("t-new", "Angiosperm", "division"), ("t-conifer", "Gymnosperm", "division")):
        row = got[tree]
        assert (row["crown_model_level"], row["crown_model_taxon"]) == (level, taxon), row
        assert row["crown_model_n"] == int(fallbacks[taxon]["n"])
        assert math.isclose(row["predicted_crown_width_m"], crown_from(fallbacks[taxon], 12.0 * 2.54), rel_tol=1e-6)


def test_density_counts_the_cell_and_tolerates_no_coordinates(predictions):
    got = by_id(predictions)
    counts: dict = {}
    for _, _, _, _, point, _, _ in ALL_TREES:
        if point is not None:
            counts[point] = counts.get(point, 0) + 1
    for tree_id, _, _, _, point, _, _ in FIXTURE_TREES:
        if point is None:
            assert got[tree_id]["local_tree_density_per_ha"] is None
        else:
            assert got[tree_id]["local_tree_density_per_ha"] == counts[point] / 0.25, got[tree_id]
    assert got["t-nowhere"]["predicted_crown_width_m"] > 0, "a tree with no coordinates still has a crown"


def test_the_age_placeholder_is_null(predictions):
    for row in predictions:
        assert row["predicted_age_years"] is None


# --- diameter from age -----------------------------------------------------------


@pytest.mark.parametrize("tree", ["t-acer-30", "t-aged", "t-sapling", "t-old", "t-oak", "t-conifer", "t-new",
                                  "t-unknown-aged", "t-lilac", "t-abarema", "acer-010", "pinus-050"])
def test_the_age_fit_is_the_least_squares_fit(predictions, reference, tree):
    row, expected = by_id(predictions)[tree], reference["trees"][tree]
    level, taxon, f = expected["dbh_model"]
    assert (row["dbh_model_level"], row["dbh_model_taxon"], row["dbh_model_n"]) == (level, taxon, f["n"])
    assert math.isclose(row["predicted_dbh_cm"], expected["predicted_dbh_cm"], rel_tol=1e-6), row


def test_a_measured_diameter_wins_over_the_predicted_one(predictions):
    """Both are published; the crown is built on the measurement."""
    row = by_id(predictions)["t-acer-30"]
    assert row["crown_dbh_source"] == "measured"
    assert math.isclose(row["age_years"], age_of(years_ago(20)), rel_tol=1e-9)


def test_a_dated_tree_with_no_diameter_gets_a_crown_from_its_age(predictions, rows):
    row = by_id(predictions)["t-aged"]
    assert row["dbh_cm"] is None and row["crown_dbh_source"] == "age"
    assert row["crown_model_level"] == "genus" and row["crown_model_taxon"] == "Acer"
    assert math.isclose(row["predicted_crown_width_m"], crown_from(genus_row(rows, "Acer"), row["predicted_dbh_cm"]), rel_tol=1e-6)


def test_the_clamps(predictions, reference):
    """Below a year the power law heads for zero; past the oldest fitted tree the stem stops growing."""
    got = by_id(predictions)
    _, _, acer = reference["trees"]["t-aged"]["dbh_model"]
    assert 0 < got["t-sapling"]["age_years"] < 1
    assert math.isclose(got["t-sapling"]["predicted_dbh_cm"], scale_of(acer), rel_tol=1e-6)
    assert got["t-old"]["age_years"] > 10 ** acer["xmax"]
    assert math.isclose(got["t-old"]["predicted_dbh_cm"], scale_of(acer) * (10 ** acer["xmax"]) ** acer["b"], rel_tol=1e-6)


def test_a_genus_with_no_fit_and_a_genus_with_no_tallo_row(predictions):
    """Two tables keyed on genus; neither may drop the other's genera.

    Syringa has neither an age fit nor a Tallo row; Abarema has a Tallo row
    and no age fit. Both are predicted, each on what it has.
    """
    got = by_id(predictions)
    assert got["t-lilac"]["dbh_model_level"] == "division" and got["t-lilac"]["crown_model_level"] == "division"
    assert got["t-abarema"]["dbh_model_level"] == "division" and got["t-abarema"]["crown_model_taxon"] != "Angiosperm"
    for tree in ("t-lilac", "t-abarema"):
        assert got[tree]["crown_dbh_source"] == "age" and got[tree]["predicted_crown_width_m"] > 0


# --- height ------------------------------------------------------------------------


@pytest.mark.parametrize("tree", ["t-acer-30", "t-acer-huge", "t-aged", "t-oak", "t-conifer", "t-new", "t-unknown",
                                  "acer-010", "pinus-050", "quercus-020"])
def test_the_height_fit_is_the_trimmed_least_squares_fit(predictions, reference, tree):
    row, expected = by_id(predictions)[tree], reference["trees"][tree]
    assert math.isclose(row["predicted_height_m"], expected["predicted_height_m"], rel_tol=1e-5), (row, expected)


def test_an_observed_height_is_carried_beside_the_prediction(predictions):
    got = by_id(predictions)
    assert got["t-acer-30"]["height_m"] == pytest.approx(12.0)
    assert got["t-acer-30"]["predicted_height_m"] > 0, "the prediction is published even where a height was measured"
    assert got["t-palm"]["height_m"] == pytest.approx(9.0), "a measured palm keeps its measurement"
    assert got["t-acer-nodbh"]["height_m"] is None and got["t-acer-nodbh"]["predicted_height_m"] is None
    assert "t-elsewhere" not in got, "a height row with no rollup tree is not a prediction row"


def test_the_height_gates(predictions, reference):
    """Shaded and out-of-band heights are kept as height_m and not drawn."""
    got = by_id(predictions)
    checks = {tree: got[tree]["height_check"] for tree in
              ("t-acer-30", "t-palm", "t-new", "t-unknown", "t-conifer", "t-aged", "acer-tall-0")}
    assert checks == {
        "t-acer-30": "accepted",
        "t-palm": "unchecked",
        "t-new": "shaded",
        "t-unknown": "out_of_band",
        "t-conifer": "out_of_band",
        "t-aged": "unchecked",
        "acer-tall-0": "out_of_band",
    }
    for tree, check in checks.items():
        accepted = got[tree]["accepted_height_m"]
        if check in ("accepted", "unchecked"):
            assert accepted == pytest.approx(got[tree]["height_m"]), tree
        else:
            assert accepted is None and got[tree]["height_m"] is not None, tree
    assert got["t-acer-nodbh"]["height_check"] is None and got["t-acer-nodbh"]["accepted_height_m"] is None


def test_every_population_height_is_judged_as_the_reference_judges_it(predictions, reference):
    """The band on the published fit: what the model accepts is what the reference keeps."""
    got = by_id(predictions)
    for tree_id, expected in reference["trees"].items():
        row = got[tree_id]
        if row["height_check"] not in ("accepted", "out_of_band"):
            continue
        ratio = row["height_m"] / expected["predicted_height_m"]
        inside = expected["gate_low"] <= ratio <= expected["gate_high"]
        assert (row["height_check"] == "accepted") == inside, (tree_id, row, expected)


# --- the published fits ------------------------------------------------------------


def test_the_genus_fits_are_published(fixture_files, reference):
    published = {
        row["genus"]: row
        for row in run_model(
            fixture_files,
            """
            select genus, dbh_genus_fit_n, dbh_genus_fit_b, dbh_genus_fit_passes,
                height_genus_fit_n, height_genus_fit_b, height_genus_fit_passes
            where genus is not null and (dbh_genus_fit_n > 0 or height_genus_fit_n > 0);
            """,
        )
    }
    assert set(published) == {"Acer", "Pinus", "Quercus", "Newconifer"}, published
    for genus in ("Acer", "Pinus"):
        age, height = reference["age"]["genus"][genus], reference["height"]["genus"][genus]
        assert published[genus]["dbh_genus_fit_passes"] and published[genus]["height_genus_fit_passes"]
        assert published[genus]["dbh_genus_fit_n"] == age["n"] and published[genus]["height_genus_fit_n"] == height["n"]
        assert math.isclose(published[genus]["dbh_genus_fit_b"], age["b"], rel_tol=1e-6)
        assert math.isclose(published[genus]["height_genus_fit_b"], height["b"], rel_tol=1e-6)
    assert not published["Quercus"]["dbh_genus_fit_passes"] and not published["Quercus"]["height_genus_fit_passes"]


def test_the_fallback_fits_are_published(fixture_files, reference):
    published = {
        row["crown_fallback_class"]: row
        for row in run_model(
            fixture_files,
            """
            select crown_fallback_class, dbh_fallback_level, dbh_fallback_n, height_fallback_level, height_fallback_n
            where crown_fallback_class is not null and crown_fallback_class != 'none';
            """,
        )
    }
    assert set(published) == {"Angiosperm", "Gymnosperm", "all"}, published
    assert published["all"]["dbh_fallback_level"] == "global" and published["all"]["height_fallback_level"] == "global"
    assert published["all"]["dbh_fallback_n"] == reference["age"]["global"]["n"]
    for division in ("Angiosperm", "Gymnosperm"):
        assert published[division]["dbh_fallback_level"] == "division"
        assert published[division]["dbh_fallback_n"] == reference["age"]["division"][division]["n"]
        assert published[division]["height_fallback_n"] == reference["height"]["division"][division]["n"]
