#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "requests"]
# ///
"""Fit the genus-level crown-width model tree_predictions.preql applies.

    cd data/raw && uv run tools/crown_allometry_fit.py --write            # refit from Tallo
    cd data/raw && uv run tools/crown_allometry_fit.py --write --coverage # + rollup coverage
    cd data/raw && uv run tools/crown_allometry_fit.py --publish          # upload the committed CSV

Tallo is a static reference dataset, so this fit is run by hand and its CSV
committed; the age and height models, fitted on the map's own trees, are
fitted inside tree_predictions.preql on every build.

The committed CSV is the source and the reviewed diff; the jobs never see it.
It is excluded from the cloud sync, and allometry_model.preql reads a parquet
of it from the public bucket instead, named by the CSV's content hash
(`published_url()`).  So a refit is three steps: `--write`, `--publish`, then
paste the URL `--publish` prints into allometry_model.preql's two `file`
clauses.  The hash in the URL is what makes the edit reach the jobs -- the
preql changes, so the sync ships it -- and test_tree_predictions.py fails
until the URL names the committed CSV's hash.

Source
------
Tallo (Jucker et al. 2022, Global Change Biology, doi:10.1111/gcb.16302): 498,838
georeferenced trees with stem diameter, height and/or crown radius, 312,829 of
them with a crown radius, across 1,453 genera. Archived on Zenodo under CC-BY
4.0 (doi:10.5281/zenodo.6637599); the same files are mirrored at
https://github.com/selva-lab-repo/TALLO, which is where this script downloads
from when `--tallo` is not given, because Zenodo rate-limits scripted downloads.

It is the only open dataset that supports a *genus*-level fit. The published
urban models are species-level and narrow -- McPherson et al. (2016, USFS
PSW-GTR-253) cover ~170 species by US climate zone, Pretzsch et al. (2015,
Urban For. Urban Green. 14:466) cover 22 species, Coombes et al. (2019, Urban
For. Urban Green. 44:126421) four -- and the FIA models (Bechtold 2003, 2004)
are species-level and stand-grown. The rollup carries ~900 genera.

Model
-----
    ln(crown_radius_m) = ln_a + b * ln(stem_diameter_cm)

fitted by ordinary least squares in log-log space per genus, with the
back-transform bias corrected (Baskerville 1972): the `scale` column is
`exp(ln_a + sigma^2 / 2)`, so the prediction the model applies is simply

    crown_width_m = 2 * scale * dbh_cm ** b

which needs no `exp` or `ln` -- pytrilogy has neither natively -- only
`power`. A power law is the standard form for this relationship (crown
radius, not area: the exponent is 0.5-0.85 for these forest-dominated data,
and would be nearer 1.0 for open-grown urban trees, see below).

Fallback resolution happens here, at fit time, so the model does one join:
every genus Tallo knows gets a row whose coefficients are its own fit when
that fit passes the gate below, else its family's fit, else its division's
(angiosperm / gymnosperm). `fit_level` and `fit_taxon` record which. A genus
Tallo has never heard of (Syringa, Washingtonia, Koelreuteria, ...) falls
through to the CSV's division row, chosen by the enrichment table's
`tree_form`, or its global row for a sentinel.

The gate: n >= 30, r2 >= 0.2, 0.3 <= b <= 1.3, and a largest fitted stem of
at least 20 cm. Without it Malus (n = 33, b = -0.11, r2 = 0.00) and Ostrya
(n = 1,482, b = 0.17, r2 = 0.03) would ship fits that make crowns *shrink*
with diameter, and a genus measured only as saplings would clamp every street
tree of that genus to a sapling's crown.

Past the largest stem a fit saw (`dbh_max_cm`), the crown keeps growing at
the rate of the row's *tail* fit -- its division's (angiosperm / gymnosperm),
else the global one -- from the value the row's own fit reaches there, and
stops at the largest stem the tail saw (`tail_dbh_max_cm`):

    d = min(max(dbh_cm, 1), tail_dbh_max_cm)
    m = dbh_max_cm
    crown_width_m = 2 * scale * min(d, m) ** b * (max(d, m) / m) ** tail_b

Below `m` that is the row's own power law; above it the curve stays
continuous and bends to the tail's exponent. Most genus fits are short of the
trees the map carries: Tallo is forest plots, and a genus with a few dozen
measured crowns has rarely seen a veteran. Salix is fitted on 59 trees, none
over 33 cm, and the map holds 70,000 willows, 40% of them larger; Platanus on
76 trees up to 71 cm, beside 680,000 planes. Clamping each tree to its genus's
largest fitted stem drew a 290 cm, five-stem white willow with a 6 m crown;
2.8% of the rollup's measured trees were past their genus fit's range. The
tail's range (385 cm for angiosperms, 770 for the global fit) is what still
stops a data-entry DBH of 400 inches extrapolating without bound. A division
or global row's tail is itself, so for it the formula is the plain clamp.

What this model is not
----------------------
Tallo is forest plots, not street trees; open-grown urban crowns are wider
per unit of stem. Coombes et al. (2019) measured 400 free-standing urban
broadleaves in two UK cities and found crown diameter / DBH settling at about
25:1 for DBH >= 0.3 m (pooled fit: CD = 0.8304 + 27.82 D - 10.68 D^2, metres).
Against that, the Tallo genus fits run roughly 15-30% low for a 30-60 cm
broadleaf, and the gap widens with size because their exponents are below 1.
`--write` prints the comparison for the commonest genera. It is deliberately
not applied: it is four species in one climate, and the point of this table
is one sourced model per genus with its provenance attached. Calibrating the
urban offset is the curation step this dataset exists to feed.

Stand density is *not* a term. Tallo records no competition measure, and the
one large study that tried one -- Bechtold (2003), 87 species, FHM plots --
found the stand basal-area coefficient "a mixture of positive and negative"
signs with partial r-squares under 0.02 and dropped it. The prediction
parquet still carries a per-tree density covariate (trees per hectare in a
50 m cell, from the rollup's own coordinates) so the curation stage can look
at crown width against it; it does not enter the prediction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import subprocess
import math
import sys
import zipfile
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
RAW = HERE.parent
COEFFICIENTS_CSV = RAW / "crown_width_coefficients.csv"
# The imagery model's packaged copy: its training labels (crown_size.py) run
# where data/raw is not shipped. The columns it reads, rewritten with the CSV.
TRAINING_COPY = RAW.parent.parent / "imagery_model" / "src" / "urban_tree_ml" / "crown_width_coefficients.csv"
TRAINING_COLUMNS = ["level", "taxon", "family", "scale", "b", "dbh_max_cm", "fit_level", "fit_taxon",
                    "tail_b", "tail_dbh_max_cm"]

TALLO_ZIP_URL = "https://raw.githubusercontent.com/selva-lab-repo/TALLO/main/DB/Tallo.csv.zip"
TALLO_VERSION = "Tallo 1.0.0 (Jucker et al. 2022)"
TALLO_CACHE = HERE / ".cache" / "Tallo.csv"

PUBLISHED_PREFIX = "gs://trilogy_public_models/duckdb/trees/reference"
PUBLIC_PREFIX = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/reference"

ROLLUP_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/full_tree_info_v2.parquet"

MIN_N = 30
MIN_R2 = 0.2
B_RANGE = (0.3, 1.3)
MIN_DBH_CM = 1.0
MIN_FITTED_DBH_MAX_CM = 20.0  # a fit made on saplings alone would clamp every street tree to a sapling's crown

CSV_COLUMNS = [
    "level",        # genus | family | division | global
    "taxon",        # the genus / family / division name, or 'all'
    "family",       # of a genus row; blank otherwise
    "division",     # Angiosperm | Gymnosperm, where Tallo knows it
    "fit_level",    # which fit a genus row carries: genus | family | division
    "fit_taxon",    # the taxon that fit came from
    "n",
    "ln_a",
    "b",
    "sigma",
    "r2",
    "dbh_min_cm",
    "dbh_max_cm",
    "scale",        # exp(ln_a + sigma^2/2): crown_radius_m = scale * dbh_cm ** b
    "tail_taxon",   # the fit that carries the crown past dbh_max_cm: the row's division, else 'all'
    "tail_b",       # its exponent
    "tail_dbh_max_cm",  # its largest stem: where the crown finally stops growing
]


def crown_width_m(row: dict, dbh_cm: float) -> float:
    """The prediction every consumer of the CSV applies; see the docstring."""
    m, top = float(row["dbh_max_cm"]), float(row["tail_dbh_max_cm"])
    d = min(max(dbh_cm, 1.0), top)
    return 2 * float(row["scale"]) * min(d, m) ** float(row["b"]) * (max(d, m) / m) ** float(row["tail_b"])

FIT_SQL = """
SELECT
    {group} AS taxon,
    count(*) AS n,
    regr_intercept(y, x) AS ln_a,
    regr_slope(y, x) AS b,
    sqrt(regr_syy(y, x) * (1 - regr_r2(y, x)) / (count(*) - 2)) AS sigma,
    regr_r2(y, x) AS r2,
    min(exp(x)) AS dbh_min_cm,
    max(exp(x)) AS dbh_max_cm
FROM usable
WHERE {group} IS NOT NULL
GROUP BY 1
HAVING count(*) >= {min_n}
"""


def tallo_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            sys.exit(f"--tallo {path} does not exist")
        return path
    if TALLO_CACHE.is_file():
        return TALLO_CACHE
    import requests  # noqa: PLC0415  (only the download path needs it)

    print(f"downloading {TALLO_ZIP_URL} to {TALLO_CACHE}", file=sys.stderr)
    response = requests.get(TALLO_ZIP_URL, timeout=300)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        member = next(n for n in archive.namelist() if n.endswith("Tallo.csv") and "__MACOSX" not in n)
        TALLO_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TALLO_CACHE.write_bytes(archive.read(member))
    return TALLO_CACHE


def load(conn: duckdb.DuckDBPyConnection, path: Path) -> None:
    conn.execute(
        "CREATE TABLE tallo AS SELECT * FROM read_csv(?, header = true, nullstr = 'NA')",
        [str(path)],
    )
    # Log-log rows the fit uses: a crown radius, a stem diameter, not flagged.
    conn.execute(
        f"""
        CREATE TABLE usable AS
        SELECT genus, family, division,
               ln(stem_diameter_cm) AS x, ln(crown_radius_m) AS y
        FROM tallo
        WHERE crown_radius_m > 0
          AND stem_diameter_cm >= {MIN_DBH_CM}
          AND crown_radius_outlier = 'N'
        """
    )


def passes_gate(row: dict) -> bool:
    return (
        row["n"] >= MIN_N
        and row["r2"] is not None
        and row["r2"] >= MIN_R2
        and B_RANGE[0] <= row["b"] <= B_RANGE[1]
        and row["dbh_max_cm"] >= MIN_FITTED_DBH_MAX_CM
    )


def fits(conn: duckdb.DuckDBPyConnection, group: str) -> dict[str, dict]:
    columns = ["taxon", "n", "ln_a", "b", "sigma", "r2", "dbh_min_cm", "dbh_max_cm"]
    rows = conn.execute(FIT_SQL.format(group=group, min_n=MIN_N)).fetchall()
    return {r[0]: dict(zip(columns, r)) for r in rows}


def with_scale(row: dict) -> dict:
    out = dict(row)
    out["scale"] = math.exp(row["ln_a"] + row["sigma"] ** 2 / 2)
    return out


def build_rows(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    genus_fits = fits(conn, "genus")
    family_fits = fits(conn, "family")
    division_fits = fits(conn, "division")
    global_fit = fits(conn, "'all'")["all"]

    # Every genus Tallo knows, with its family and division -- including the
    # ones with no crown measurement at all, which still get a fallback row so
    # a tree of that genus resolves to the right division rather than to the
    # enrichment table's guess at its form.
    taxonomy = conn.execute(
        """
        SELECT genus, any_value(family), any_value(division)
        FROM tallo WHERE genus IS NOT NULL GROUP BY 1 ORDER BY 1
        """
    ).fetchall()

    def tail(division: str | None, own_max: float) -> dict:
        """The division's fit, else the global one, to grow a crown past own_max."""
        fit = division_fits.get(division) if division else None
        name = division
        if fit is None or not passes_gate(fit):
            fit, name = global_fit, "all"
        return {"tail_taxon": name, "tail_b": fit["b"], "tail_dbh_max_cm": max(fit["dbh_max_cm"], own_max)}

    family_division = {f: d for _, f, d in taxonomy if f and d}

    rows: list[dict] = []
    for genus, family, division in taxonomy:
        candidates = [
            ("genus", genus, genus_fits.get(genus)),
            ("family", family, family_fits.get(family) if family else None),
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
                "family": family or "",
                "division": division or "",
                "fit_level": fit_level,
                "fit_taxon": fit_taxon,
                **{k: v for k, v in with_scale(fit).items() if k != "taxon"},
                **(tail(division, fit["dbh_max_cm"]) if fit_level in ("genus", "family")
                   else {"tail_taxon": fit_taxon, "tail_b": fit["b"], "tail_dbh_max_cm": fit["dbh_max_cm"]}),
            }
        )
    for family, fit in sorted(family_fits.items()):
        if passes_gate(fit):
            rows.append(
                {"level": "family", "taxon": family, "family": family, "division": "",
                 "fit_level": "family", "fit_taxon": family,
                 **{k: v for k, v in with_scale(fit).items() if k != "taxon"},
                 **tail(family_division.get(family), fit["dbh_max_cm"])}
            )
    for division, fit in sorted(division_fits.items()):
        assert passes_gate(fit), f"the {division} fit failed the gate"
        rows.append(
            {"level": "division", "taxon": division, "family": "", "division": division,
             "fit_level": "division", "fit_taxon": division,
             **{k: v for k, v in with_scale(fit).items() if k != "taxon"},
             "tail_taxon": division, "tail_b": fit["b"], "tail_dbh_max_cm": fit["dbh_max_cm"]}
        )
    rows.append(
        {"level": "global", "taxon": "all", "family": "", "division": "",
         "fit_level": "global", "fit_taxon": "all",
         **{k: v for k, v in with_scale(global_fit).items() if k != "taxon"},
         "tail_taxon": "all", "tail_b": global_fit["b"], "tail_dbh_max_cm": global_fit["dbh_max_cm"]}
    )
    return rows


def format_row(row: dict) -> dict[str, str]:
    out = {}
    for column in CSV_COLUMNS:
        value = row[column]
        if isinstance(value, float):
            out[column] = f"{value:.6g}"
        else:
            out[column] = str(value)
    return out


def write_csv(rows: list[dict]) -> None:
    with COEFFICIENTS_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(format_row(row))
    with TRAINING_COPY.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRAINING_COLUMNS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(format_row(row))


def report(conn: duckdb.DuckDBPyConnection, rows: list[dict], coverage: bool) -> None:
    genus_rows = [r for r in rows if r["level"] == "genus"]
    by_level = {}
    for r in genus_rows:
        by_level[r["fit_level"]] = by_level.get(r["fit_level"], 0) + 1
    print(f"{len(genus_rows)} genera: {by_level}", file=sys.stderr)

    # Against Coombes et al. (2019): open-grown urban broadleaves settle at a
    # crown diameter of about 25 x DBH from 0.3 m up.
    print("crown diameter at 30 / 60 / 150 cm DBH, metres (Coombes urban ref: 8.2 / 13.7 / -):", file=sys.stderr)
    lookup = {r["taxon"]: r for r in genus_rows}
    for genus in ("Acer", "Quercus", "Fraxinus", "Tilia", "Ulmus", "Platanus", "Prunus",
                  "Gleditsia", "Pinus", "Picea", "Malus", "Betula", "Salix", "Zelkova"):
        r = lookup.get(genus)
        if r is None:
            continue
        cd = [crown_width_m(r, d) for d in (30, 60, 150)]
        print(f"  {genus:<10} {cd[0]:5.1f} {cd[1]:5.1f} {cd[2]:5.1f}   (max {r['dbh_max_cm']:.0f} cm, {r['fit_level']} fit, n={r['n']}, b={r['b']:.2f}, r2={r['r2']:.2f})", file=sys.stderr)

    if not coverage:
        return
    conn.execute("INSTALL httpfs; LOAD httpfs")
    conn.execute("CREATE TABLE genus_rows AS SELECT * FROM read_csv(?)", [str(COEFFICIENTS_CSV)])
    share = conn.execute(
        f"""
        WITH ours AS (
            SELECT split_part(species, ' ', 1) AS genus, count(*) AS n
            FROM read_parquet('{ROLLUP_URL}')
            WHERE species NOT IN ('Unknown', 'Palm', 'Shrub', 'Cactus', 'Dead',
                                  'Unknown deciduous', 'Unknown conifer', 'Unknown evergreen')
            GROUP BY 1
        )
        SELECT coalesce(g.fit_level, 'not in Tallo') AS level, sum(o.n) AS trees
        FROM ours o LEFT JOIN genus_rows g ON g.level = 'genus' AND g.taxon = o.genus
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()
    total = sum(n for _, n in share)
    print("identified trees in the published rollup, by the fit they would get:", file=sys.stderr)
    for level, n in share:
        print(f"  {level:<14} {n:>10,}  {100 * n / total:5.1f}%", file=sys.stderr)


def csv_digest(path: Path = COEFFICIENTS_CSV) -> str:
    """The first 12 hex digits of the CSV's sha256, line endings folded to LF
    so a Windows checkout and CI agree."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]


def published_name(path: Path = COEFFICIENTS_CSV) -> str:
    return f"crown_width_coefficients_{csv_digest(path)}.parquet"


def published_url(path: Path = COEFFICIENTS_CSV) -> str:
    return f"{PUBLIC_PREFIX}/{published_name(path)}"


# The column types the model's properties expect; the CSV is read with these
# rather than sniffed so the parquet's schema does not move with the values.
PARQUET_TYPES = {
    "level": "VARCHAR", "taxon": "VARCHAR", "family": "VARCHAR", "division": "VARCHAR",
    "fit_level": "VARCHAR", "fit_taxon": "VARCHAR", "n": "BIGINT",
    "ln_a": "DOUBLE", "b": "DOUBLE", "sigma": "DOUBLE", "r2": "DOUBLE",
    "dbh_min_cm": "DOUBLE", "dbh_max_cm": "DOUBLE", "scale": "DOUBLE",
    "tail_taxon": "VARCHAR", "tail_b": "DOUBLE", "tail_dbh_max_cm": "DOUBLE",
}


def write_parquet(target: Path, source: Path = COEFFICIENTS_CSV) -> None:
    columns = ", ".join(f"'{c}': '{t}'" for c, t in PARQUET_TYPES.items())
    duckdb.connect().execute(
        f"COPY (SELECT * FROM read_csv('{source.as_posix()}', header = true, columns = {{{columns}}})) "
        f"TO '{target.as_posix()}' (FORMAT PARQUET)"
    )


def publish() -> str:
    """Write the committed CSV as a parquet and upload it under its hash.
    Content-addressed, so an upload never replaces what a job reads."""
    import tempfile

    name = published_name()
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        write_parquet(local)
        subprocess.run(
            ["gcloud", "storage", "cp", str(local), f"{PUBLISHED_PREFIX}/{name}"],
            check=True, shell=sys.platform == "win32",
        )
    return published_url()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="refit from Tallo and rewrite the CSV")
    parser.add_argument("--tallo", help="path to Tallo.csv (downloaded and cached when omitted)")
    parser.add_argument("--coverage", action="store_true", help="also report coverage against the published rollup (network)")
    parser.add_argument("--publish", action="store_true", help="upload the committed CSV as the parquet the model reads")
    args = parser.parse_args()

    if args.publish and not args.write:
        url = publish()
        print(f"published; allometry_model.preql's file clauses should read:\n{url}")
        return 0
    if not args.write:
        parser.error("--write or --publish is required")

    conn = duckdb.connect()
    load(conn, tallo_path(args.tallo))
    rows = build_rows(conn)
    write_csv(rows)
    print(f"wrote {len(rows)} rows to {COEFFICIENTS_CSV.name}", file=sys.stderr)
    report(conn, rows, args.coverage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
