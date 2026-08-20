#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pytrilogy"]
# ///
"""Build an N-city copy of the urban-forest model shape and plan a refresh of
its merged datasource — the operation that raises

    ValueError: CTE dependency graph contains a cycle

Usage:
    uv run generate.py 14          # build ./generated/ with 14 cities and plan
    uv run generate.py 3 5 8 14    # sweep, reporting the smallest N that fails

Everything is local CSV: no GCS, no python datasources, no credentials. The
shape mirrors data/raw in this repository —

  * each city has two disjoint raw partitions (municipal + community), keyed by
    a per-city `{code}_source` enum, so Trilogy can prove per-city coverage;
  * each city has two scalar watermark roots and an
    `auto {code}_published_data_updated_through <- greatest(muni, community)`;
  * each city has a materialized `complete where city = '{CODE}'` datasource;
  * the merged model unions all of them, merges the per-city source enums into
    one `data_source` key, and takes
    `auto latest_update_through <- greatest(<every city's auto>)`.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

OUT = Path(__file__).parent / "generated"


def code(i: int) -> str:
    return f"CITY{i:02d}"


def slug(i: int) -> str:
    return f"city{i:02d}"


def write_city(i: int, imputed: bool = False) -> None:
    """Write one city's model + CSVs.

    *imputed* gives this city the shape Boston has in the real model: the raw
    column feeds a private concept, a derived concept cleans it, an aggregate
    imputes the gaps, and the result is merged into the shared concept the
    merged datasource selects.
    """
    c, s = code(i), slug(i)
    (OUT / f"{s}_trees.csv").write_text(
        f"tree_id,city,{s}_source,species,dbh\n"
        f"{s}-1,{c},{c}_OPENDATA,Quercus robur,10.0\n",
        encoding="utf-8",
    )
    (OUT / f"{s}_community.csv").write_text(
        f"tree_id,city,{s}_source,species,dbh\n"
        f"{s}-c1,{c},COMMUNITY_{c},Tilia cordata,8.0\n",
        encoding="utf-8",
    )
    (OUT / f"{s}_update_time.csv").write_text(
        "data_updated_through\n2026-08-01 00:00:00\n", encoding="utf-8"
    )
    (OUT / f"{s}_published.csv").write_text(
        f"tree_id,city,{s}_source,species,dbh,{s}_published_data_updated_through\n"
        f"{s}-1,{c},{c}_OPENDATA,Quercus robur,10.0,2026-08-01 00:00:00\n",
        encoding="utf-8",
    )
    # Boston's shape: raw -> cleaned -> imputed-by-group, merged into the
    # concept the merged datasource selects.
    imputation = (
        f"""
property tree_id._{s}_raw_dbh float;
property tree_id._{s}_cleaned_dbh <- case when _{s}_raw_dbh < 0 then null else _{s}_raw_dbh end;
auto {s}_processed_dbh <- coalesce(_{s}_cleaned_dbh, avg(_{s}_cleaned_dbh) by city, species);
merge {s}_processed_dbh into diameter_at_breast_height;
"""
        if imputed
        else ""
    )
    raw_dbh = f"?_{s}_raw_dbh" if imputed else "?diameter_at_breast_height"

    (OUT / f"{s}.preql").write_text(
        f"""import core;

key {s}_source enum<string>['{c}_OPENDATA', 'COMMUNITY_{c}'];
{imputation}

auto {s}_published_data_updated_through <- greatest(
    {s}_data_updated_through,
    {s}_community_data_updated_through
);

root datasource {s}_update_time (
    data_updated_through: {s}_data_updated_through
)
file `./{s}_update_time.csv`;

root datasource {s}_community_update_time (
    {s}_community_data_updated_through: {s}_community_data_updated_through
)
file `./community_update_time.csv`;

root partial datasource {s}_raw_tree_info (
    tree_id: tree_id,
    city: city,
    {s}_source: {s}_source,
    species: species,
    dbh: {raw_dbh},
)
grain (tree_id)
complete where city = '{c}' and {s}_source = '{c}_OPENDATA'
file `./{s}_trees.csv`;

root partial datasource {s}_community_tree_info (
    tree_id: tree_id,
    city: city,
    community_source: {s}_source,
    species: species,
    dbh: {raw_dbh},
)
grain (tree_id)
complete where city = '{c}' and {s}_source = 'COMMUNITY_{c}'
file `./community_trees.csv`;

partial datasource {s}_tree_info (
    tree_id,
    city,
    {s}_source: {s}_source,
    species,
    dbh: ?diameter_at_breast_height,
    {s}_published_data_updated_through,
)
grain (tree_id)
complete where city = '{c}'
file `./{s}_published.csv`
freshness by {s}_published_data_updated_through;
""",
        encoding="utf-8",
    )


def write_model(n: int, imputed_cities: int = 0) -> Path:
    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob("*"):
        if stale.is_file():
            stale.unlink()

    cities = range(n)
    (OUT / "trilogy.toml").write_text(
        '[project]\nname = "cte_cycle_repro"\n\n[engine]\ndialect = "duck_db"\n',
        encoding="utf-8",
    )
    (OUT / "core.preql").write_text(
        "key city enum<string>["
        + ", ".join(f"'{code(i)}'" for i in cities)
        + "];\n\n"
        "key tree_id string;\n"
        "property tree_id.species string;\n"
        "property tree_id.diameter_at_breast_height float;\n\n"
        + "".join(
            f"property <*>.{slug(i)}_data_updated_through datetime;\n"
            f"property <*>.{slug(i)}_community_data_updated_through datetime;\n"
            for i in cities
        ),
        encoding="utf-8",
    )
    # One row of per-city columns, exactly as the real community probe emits.
    (OUT / "community_update_time.csv").write_text(
        ",".join(f"{slug(i)}_community_data_updated_through" for i in cities)
        + "\n"
        + ",".join("2026-07-01 00:00:00" for _ in cities)
        + "\n",
        encoding="utf-8",
    )
    # ONE community source for every city, sliced by each city's `complete
    # where` — the real model does this with data/raw/community_tree_info.py.
    (OUT / "community_trees.csv").write_text(
        "tree_id,city,community_source,species,dbh\n"
        + "".join(
            f"{slug(i)}-c1,{code(i)},COMMUNITY_{code(i)},Tilia cordata,8.0\n"
            for i in cities
        ),
        encoding="utf-8",
    )
    for i in cities:
        write_city(i, imputed=i < imputed_cities)

    (OUT / "merged.csv").write_text(
        "tree_id,city,data_source,species,diameter_at_breast_height,"
        "latest_update_through\n"
        "city00-1,CITY00,CITY00_OPENDATA,Quercus robur,10.0,2026-08-01 00:00:00\n",
        encoding="utf-8",
    )
    merged = OUT / "merged.preql"
    merged.write_text(
        "".join(f"import {slug(i)};\n" for i in cities)
        + "\nkey data_source string;\n"
        + "".join(f"merge {slug(i)}_source into data_source;\n" for i in cities)
        + "\nauto latest_update_through <- greatest(\n"
        + ",\n".join(f"    {slug(i)}_published_data_updated_through" for i in cities)
        + "\n);\n\n"
        "datasource merged_tree_info (\n"
        "    tree_id,\n    city,\n    data_source,\n    species,\n"
        "    ?diameter_at_breast_height,\n    latest_update_through\n)\n"
        "grain (tree_id)\n"
        "file `./merged.csv`\n"
        "freshness by latest_update_through;\n",
        encoding="utf-8",
    )
    return merged


def plan_refresh(model: Path) -> tuple[bool, str, float]:
    """Plan + dry-run refresh of merged_tree_info. Returns (ok, detail, seconds)."""
    from trilogy import Dialects, Environment
    from trilogy.execution.state import (
        RefreshPolicy,
        create_refresh_plan,
        execute_refresh_plan,
    )

    env = Environment(working_path=model.parent)
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    executor.parse_text(model.read_text(encoding="utf-8"), root=model)
    skip = {
        ds_id
        for ds_id, ds in executor.environment.datasources.items()
        if ds_id != "merged_tree_info" and not ds.is_root
    }
    start = time.time()
    try:
        plan = create_refresh_plan(
            executor,
            policy=RefreshPolicy(force_sources={"merged_tree_info"}),
            skip_datasources=skip,
        )
        execute_refresh_plan(executor, plan, dry_run=True)
        return True, "planned and rendered", time.time() - start
    except Exception as e:  # noqa: BLE001 - the point is to report any failure
        traceback.print_exc(limit=4)
        return False, f"{type(e).__name__}: {str(e)[:200]}", time.time() - start


if __name__ == "__main__":
    args = sys.argv[1:]
    imputed = 0
    if "--imputed" in args:
        at = args.index("--imputed")
        imputed = int(args[at + 1])
        del args[at : at + 2]
    sizes = [int(a) for a in args] or [14]
    for n in sizes:
        model = write_model(n, imputed_cities=imputed)
        ok, detail, seconds = plan_refresh(model)
        print(f"n={n:>3} imputed={imputed:<3} {'OK  ' if ok else 'FAIL'}  {seconds:6.1f}s  {detail}")
        if not ok:
            print(f"\nSmallest failing size in this sweep: {n} cities ({OUT})")
            sys.exit(1)
