#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb"]
# ///
"""Fit the genus-level age-to-diameter model tree_predictions.preql falls back on.

    cd data/raw && uv run dbh_age_fit.py --write     # refit from the published rollup
    cd data/raw && uv run dbh_age_fit.py --check     # exit 1 if the model's block is stale

Why
---
The crown model is a function of stem diameter, and 303,612 published trees
(September 2026: 188k in Amsterdam, 45k in Melbourne, 29k in San Francisco,
22k in Los Angeles) carry no diameter but do carry a planting date. This
model predicts the diameter such a tree would have from its age, so the crown
model has something to apply. It is a fallback: a measured diameter is always
used when the source recorded one.

Source
------
The map's own published trees: every row of the cross-city rollup carrying
both a planting date and a measured diameter (1.9M of them in 23 cities,
once the window and the nulled dates below are applied), joined to the
enrichment table for the corrected genus and the growth form,
exactly as `tree_predictions.preql` joins them. No open reference dataset
records age and diameter for urban trees at genus rank -- Tallo has no ages,
and the Urban Tree Database (McPherson et al. 2016, USFS PSW-GTR-253) is
species-by-climate-zone for ~170 species in the US -- and the population the
fit serves is the rollup's own, so its own dated, measured trees are the
right reference: street and park trees, open-grown, in the same cities.

Model
-----
    ln(dbh_cm) = ln_a + b * ln(age_years)

ordinary least squares in log-log space, per genus, back-transform bias
corrected (Baskerville 1972), so the prediction the model applies is

    dbh_cm = scale * age_years ** b        (age clamped to [1, age_max_years])

the same shape `crown_allometry_fit.py` uses, for the same reason: pytrilogy
has `**` and no `exp` or `ln`. The exponent comes out at 0.7-0.9 for the
common street genera, which is diameter growth that slows with age, as it
should.

Fallback resolution happens at fit time. A genus whose own fit fails the gate
carries its division's fit (angiosperm or gymnosperm, decided by the
enrichment table's `tree_form` the way the model decides it), else the fit
over every usable tree. A genus with too few dated trees to attempt a fit has
no row, and the model falls through to the division constants `--write`
renders into its generated block.

The gate: n >= 100, r2 >= 0.2, 0.3 <= b <= 1.3, and an oldest fitted tree of
at least 20 years. Lagerstroemia (r2 = 0.00) and Washingtonia (a palm, and
excluded before the gate) show why the r2 floor is there.

What is excluded, and why
-------------------------
An inventory's planting date is often a year, published as January 1 (Boston
records 1994 on 30% of its dated trees that way; Paris, Amsterdam, Berlin and
Melbourne publish every date on January 1) or as a June 1 (Edmonton). A year
is an age, so those rows stay in. What is not an age is nulled by the
*ingest*, not here, so that the model has nothing to second-guess:

* A default the portal stamped. Edmonton carries 1990-06-01 on 54% of its
  trees, with diameters of 13-58 cm across the middle 80%; Melbourne carries
  1900-01-01 on a third of its dated trees at a 35 cm median. A cohort has
  one diameter and a default has the city's, which is the tell -- not the
  day, since Edmonton's real cohorts are June 1 too. Each city's ingest
  nulls its own (`PLACEHOLDER_PLANT_DATE` in `caedm/edmonton_tree_info.py`
  and `aumel/melbourne_tree_info.py`), found from the per-city report
  `--write` prints: every date carrying a tenth or more of a city's dated
  trees, with its diameter spread, so the next reviewer can make the same
  call for the next portal.
* A date no planting has: before 1500, or in the future. `enforce_tree_schema`
  nulls those for every city.

Until each of those cities has rebuilt, the published rollup still carries
the dates, so this script re-applies the same rules to what it reads --
`INGEST_NULLED_PLANT_DATES` is the city-specific half, and an entry is
deleted once its city has rebuilt. The fit window is then 1-200 years: a
tree planted less than a year ago is clamped to one year (the power law
heads for zero below it), and past 200 the rollup holds a few dozen
estimated ages on monumental Berlin and Paris trees, which are real and not
what a street tree's clamp bound should be set by.

What this model is not
----------------------
It is not site-specific: the same genus grows at different rates in different
climates and in a pit versus a lawn, and the fit pools every city. The
pooled exponent is the honest genus-level answer with this data, and the
`dbh_model_*` columns say which fit a tree got and how many trees stood
behind it, so a curator can see how far the prediction reached.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
COEFFICIENTS_CSV = HERE / "dbh_age_coefficients.csv"
MODEL = HERE / "tree_predictions.preql"
BEGIN = "# BEGIN dbh_age_fallbacks -- generated by dbh_age_fit.py from dbh_age_coefficients.csv; do not edit\n"
END = "# END dbh_age_fallbacks\n"

TREES_BASE_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"
ROLLUP_URL = f"{TREES_BASE_URL}/full_tree_info_v2.parquet"
ENRICHMENT_URL = f"{TREES_BASE_URL}/tree_enrichment_v2.parquet"

MIN_N = 100
MIN_R2 = 0.2
B_RANGE = (0.3, 1.3)
MIN_AGE_YEARS = 1.0  # the fit window; see the docstring
MAX_AGE_YEARS = 200.0
MIN_FITTED_AGE_MAX_YEARS = 20.0  # a fit made on saplings alone would clamp every mature tree to a sapling's stem

# The ingest's own guards (`_ingest_shared.DBH_MAX_INCHES`, `PLANT_DATE_MIN_YEAR`),
# re-applied to parquets built before them; `test_tree_predictions.py` keeps
# the numbers in step.
DBH_MAX_INCHES = 200.0
PLANT_DATE_MIN_YEAR = 1500

# The defaults two portals stamp, which their ingests now publish as null.
# Re-applied here until each city has rebuilt past that change; delete an
# entry once its published parquet no longer carries the date.
INGEST_NULLED_PLANT_DATES: frozenset[tuple[str, str]] = frozenset(
    {
        ("CAEDM", "1990-06-01"),
        ("AUMEL", "1900-01-01"),
    }
)

SENTINELS = ("Unknown", "Dead", "Shrub", "Palm", "Cactus")

CSV_COLUMNS = [
    "level",           # genus | division | global
    "taxon",           # the genus / division name, or 'all'
    "fit_level",       # which fit a genus row carries: genus | division | global
    "fit_taxon",       # the taxon that fit came from
    "n",               # trees behind the fit
    "cities",          # cities they came from
    "ln_a",
    "b",
    "sigma",
    "r2",
    "age_min_years",
    "age_max_years",
    "scale",           # exp(ln_a + sigma^2/2): dbh_cm = scale * age_years ** b
]

# The division the model assigns a tree, from the enrichment table's growth
# form -- the same case as `crown_fallback_class` in tree_predictions.preql,
# which is what decides which division constants a tree with no genus fit
# gets. 'none' is a palm: its stem does not thicken with age.
CLASS_SQL = """
CASE
    WHEN species IN ('Unknown', 'Dead', 'Shrub') THEN 'all'
    WHEN tree_form = 'palm' OR species IN ('Palm', 'Cactus') THEN 'none'
    WHEN tree_form = 'conifer' THEN 'Gymnosperm'
    ELSE 'Angiosperm'
END
"""

FIT_SQL = """
SELECT
    {group} AS taxon,
    count(*) AS n,
    count(DISTINCT city) AS cities,
    regr_intercept(y, x) AS ln_a,
    regr_slope(y, x) AS b,
    sqrt(regr_syy(y, x) * (1 - regr_r2(y, x)) / (count(*) - 2)) AS sigma,
    regr_r2(y, x) AS r2,
    min(exp(x)) AS age_min_years,
    max(exp(x)) AS age_max_years
FROM usable
WHERE {group} IS NOT NULL {extra}
GROUP BY 1
HAVING count(*) >= {min_n}
"""


def load(conn: duckdb.DuckDBPyConnection, rollup: str, enrichment: str) -> None:
    conn.execute("INSTALL httpfs; LOAD httpfs")
    placeholders = ", ".join(f"('{city}', DATE '{day}')" for city, day in sorted(INGEST_NULLED_PLANT_DATES))
    conn.execute(
        f"""
        CREATE TABLE dated AS
        SELECT t.city, t.species, t.plant_date,
               t.diameter_at_breast_height * 2.54 AS dbh_cm,
               date_diff('day', t.plant_date, current_date) / 365.25 AS age_years,
               e.genus,
               {CLASS_SQL.replace('species', 't.species').replace('tree_form', 'e.tree_form')} AS division
        FROM read_parquet('{rollup}') t
        LEFT JOIN read_parquet('{enrichment}') e ON e.species = t.species
        WHERE t.plant_date IS NOT NULL
        """
    )
    conn.execute(
        f"""
        CREATE TABLE usable AS
        SELECT city, species, genus, division, dbh_cm, age_years,
               ln(age_years) AS x, ln(dbh_cm) AS y
        FROM dated
        WHERE dbh_cm > 0
          AND dbh_cm <= {DBH_MAX_INCHES} * 2.54
          AND year(plant_date) >= {PLANT_DATE_MIN_YEAR}
          AND plant_date <= current_date
          AND (city, plant_date) NOT IN (VALUES {placeholders})
          AND age_years >= {MIN_AGE_YEARS}
          AND age_years <= {MAX_AGE_YEARS}
          AND division <> 'none'
        """
    )


def passes_gate(row: dict) -> bool:
    return (
        row["n"] >= MIN_N
        and row["r2"] is not None
        and row["r2"] >= MIN_R2
        and B_RANGE[0] <= row["b"] <= B_RANGE[1]
        and row["age_max_years"] >= MIN_FITTED_AGE_MAX_YEARS
    )


def fits(conn: duckdb.DuckDBPyConnection, group: str, extra: str = "") -> dict[str, dict]:
    columns = ["taxon", "n", "cities", "ln_a", "b", "sigma", "r2", "age_min_years", "age_max_years"]
    rows = conn.execute(FIT_SQL.format(group=group, extra=extra, min_n=MIN_N)).fetchall()
    return {r[0]: dict(zip(columns, r)) for r in rows}


def with_scale(row: dict) -> dict:
    out = dict(row)
    out["scale"] = math.exp(row["ln_a"] + row["sigma"] ** 2 / 2)
    return out


def build_rows(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    genus_fits = fits(conn, "genus")
    division_fits = fits(conn, "division", extra="AND division IN ('Angiosperm', 'Gymnosperm')")
    global_fit = fits(conn, "'all'")["all"]

    # Which division each fitted genus belongs to, by the form its trees carry.
    genus_division = dict(
        conn.execute(
            """
            SELECT genus, mode(division) FROM usable
            WHERE genus IS NOT NULL AND division IN ('Angiosperm', 'Gymnosperm')
            GROUP BY 1
            """
        ).fetchall()
    )

    rows: list[dict] = []
    for genus in sorted(genus_fits):
        division = genus_division.get(genus)
        candidates = [
            ("genus", genus, genus_fits[genus]),
            ("division", division, division_fits.get(division) if division else None),
            ("global", "all", global_fit),
        ]
        for fit_level, fit_taxon, fit in candidates:
            if fit is not None and passes_gate(fit):
                break
        else:  # pragma: no cover - the global fit always passes
            raise RuntimeError("no fit passed the gate, not even the global one")
        rows.append(
            {
                "level": "genus",
                "taxon": genus,
                "fit_level": fit_level,
                "fit_taxon": fit_taxon,
                **{k: v for k, v in with_scale(fit).items() if k != "taxon"},
            }
        )
    for division, fit in sorted(division_fits.items()):
        assert passes_gate(fit), f"the {division} fit failed the gate"
        rows.append(
            {"level": "division", "taxon": division, "fit_level": "division", "fit_taxon": division,
             **{k: v for k, v in with_scale(fit).items() if k != "taxon"}}
        )
    assert passes_gate(global_fit), "the global fit failed the gate"
    rows.append(
        {"level": "global", "taxon": "all", "fit_level": "global", "fit_taxon": "all",
         **{k: v for k, v in with_scale(global_fit).items() if k != "taxon"}}
    )
    return rows


def format_row(row: dict) -> dict[str, str]:
    out = {}
    for column in CSV_COLUMNS:
        value = row[column]
        out[column] = f"{value:.6g}" if isinstance(value, float) else str(value)
    return out


def write_csv(rows: list[dict]) -> None:
    with COEFFICIENTS_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(format_row(row))


def read_csv_rows() -> list[dict[str, str]]:
    with COEFFICIENTS_CSV.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def render_block(rows: list[dict[str, str]]) -> str:
    """The division and global constants, as `const` declarations.

    The fallbacks for a genus the coefficient table does not carry, rendered
    into the model for the reason the crown block is: the division is derived
    from `tree_form` and cannot be a join key without a merge.
    """
    by_key = {(r["level"], r["taxon"]): r for r in rows}
    n = int(by_key[("global", "all")]["n"])
    cities = int(by_key[("global", "all")]["cities"])
    lines = [
        BEGIN,
        f"# The map's own published trees ({n:,} carrying a planting date and a measured diameter, in {cities} cities): "
        "dbh_cm = scale * age_years ** b, age clamped to [1, age_max_years]\n",
    ]
    for label, key in (
        ("angiosperm", ("division", "Angiosperm")),
        ("gymnosperm", ("division", "Gymnosperm")),
        ("global", ("global", "all")),
    ):
        row = by_key[key]
        lines.append(f"const {label}_dbh_scale <- {float(row['scale']):.6g};\n")
        lines.append(f"const {label}_dbh_b <- {float(row['b']):.6g};\n")
        lines.append(f"const {label}_dbh_n <- {int(row['n'])};\n")
        lines.append(f"const {label}_dbh_age_max_years <- {float(row['age_max_years']):.6g};\n")
    lines.append(END)
    return "".join(lines)


def splice_block(text: str, block: str) -> str:
    start = text.index(BEGIN)
    end = text.index(END) + len(END)
    return text[:start] + block + text[end:]


def current_block(text: str) -> str:
    start = text.index(BEGIN)
    end = text.index(END) + len(END)
    return text[start:end]


def report(conn: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    genus_rows = [r for r in rows if r["level"] == "genus"]
    by_level: dict[str, int] = {}
    for r in genus_rows:
        by_level[r["fit_level"]] = by_level.get(r["fit_level"], 0) + 1
    print(f"{len(genus_rows)} genera: {by_level}", file=sys.stderr)

    # Dates that carry a tenth or more of a city's dated trees: where a
    # portal's stamped default shows up. A cohort has one diameter; a default
    # has the city's.
    print("dates carrying >= 10% of a city's dated trees (median and 10-90% diameter, cm):", file=sys.stderr)
    for city, day, n, pct, med, p10, p90, age in conn.execute(
        """
        WITH d AS (
            SELECT city, plant_date, count(*) AS n,
                   median(dbh_cm) FILTER (WHERE dbh_cm > 0) AS med,
                   quantile_cont(dbh_cm, 0.1) FILTER (WHERE dbh_cm > 0) AS p10,
                   quantile_cont(dbh_cm, 0.9) FILTER (WHERE dbh_cm > 0) AS p90
            FROM dated GROUP BY 1, 2
        ), tot AS (SELECT city, sum(n) AS total FROM d GROUP BY 1)
        SELECT d.city, d.plant_date, d.n, 100.0 * d.n / tot.total, med, p10, p90,
               date_diff('day', d.plant_date, current_date) / 365.25
        FROM d JOIN tot USING (city)
        WHERE d.n >= 100 AND 100.0 * d.n / tot.total >= 10
        ORDER BY 4 DESC
        """
    ).fetchall():
        spread = f"{med:5.1f} ({p10:5.1f}-{p90:5.1f})" if med is not None else "  no diameters"
        excluded = (city, day.isoformat()) in INGEST_NULLED_PLANT_DATES or not (MIN_AGE_YEARS <= age <= MAX_AGE_YEARS)
        flag = "  excluded" if excluded else ""
        print(f"  {city} {day}  {n:>8,}  {pct:5.1f}%  {spread}{flag}", file=sys.stderr)

    # Against i-Tree's base rate for open-grown trees, 0.83 cm of diameter a
    # year (Nowak 1994, Chicago street trees), as an order of magnitude.
    print("diameter at 10 / 30 / 60 years, cm (i-Tree open-grown reference: 8.3 / 24.9 / 49.8):", file=sys.stderr)
    lookup = {r["taxon"]: r for r in genus_rows}
    for genus in ("Acer", "Quercus", "Fraxinus", "Tilia", "Ulmus", "Platanus", "Prunus",
                  "Gleditsia", "Pinus", "Picea", "Malus", "Betula", "Lagerstroemia"):
        r = lookup.get(genus)
        if r is None:
            continue
        dbh = [r["scale"] * min(a, r["age_max_years"]) ** r["b"] for a in (10, 30, 60)]
        print(f"  {genus:<14} {dbh[0]:5.1f} {dbh[1]:5.1f} {dbh[2]:5.1f}   ({r['fit_level']} fit, n={r['n']:,}, {r['cities']} cities, b={r['b']:.2f}, r2={r['r2']:.2f})", file=sys.stderr)

    # The population this fallback serves, by the fit it would get.
    conn.execute("CREATE TABLE genus_rows AS SELECT * FROM read_csv(?)", [str(COEFFICIENTS_CSV)])
    placeholders = ", ".join(f"('{city}', DATE '{day}')" for city, day in sorted(INGEST_NULLED_PLANT_DATES))
    share = conn.execute(
        f"""
        SELECT CASE WHEN d.division = 'none' THEN 'none (palm)'
                    WHEN (d.city, d.plant_date) IN (VALUES {placeholders}) OR year(d.plant_date) < {PLANT_DATE_MIN_YEAR} OR d.plant_date > current_date
                         THEN 'none (a date the ingest now nulls)'
                    ELSE coalesce(g.fit_level, 'division (no genus row)') END AS level,
               count(*) AS trees
        FROM dated d LEFT JOIN genus_rows g ON g.level = 'genus' AND g.taxon = d.genus
        WHERE d.dbh_cm IS NULL OR d.dbh_cm <= 0
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()
    total = sum(n for _, n in share)
    print(f"published trees with a planting date and no diameter ({total:,}), by the fit they would get:", file=sys.stderr)
    for level, n in share:
        print(f"  {level:<26} {n:>9,}  {100 * n / total:5.1f}%", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="refit from the published rollup and rewrite the CSV and the model's fallback block")
    parser.add_argument("--check", action="store_true", help="exit 1 if the model's fallback block disagrees with the CSV")
    parser.add_argument("--rollup", default=ROLLUP_URL, help="the rollup parquet to fit on (a local path works)")
    parser.add_argument("--enrichment", default=ENRICHMENT_URL, help="the enrichment parquet to take genus and form from")
    args = parser.parse_args()

    if args.check:
        rows = read_csv_rows()
        text = MODEL.read_text(encoding="utf-8")
        if current_block(text) != render_block(rows):
            print(f"{MODEL.name}'s dbh_age_fallbacks block is stale; run dbh_age_fit.py --write", file=sys.stderr)
            return 1
        print("dbh_age_fallbacks block is current")
        return 0

    if not args.write:
        parser.error("one of --write or --check is required")

    conn = duckdb.connect()
    load(conn, args.rollup, args.enrichment)
    rows = build_rows(conn)
    write_csv(rows)
    text = MODEL.read_text(encoding="utf-8")
    MODEL.write_text(splice_block(text, render_block(read_csv_rows())), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {COEFFICIENTS_CSV.name} and the fallback block in {MODEL.name}", file=sys.stderr)
    report(conn, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
