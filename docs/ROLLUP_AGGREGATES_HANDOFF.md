# Handoff: precomputed aggregates for the city dashboards

Written 2026-10-30, after sf #155 merged (phones range-read the sorted
projection; per-city ENUM vocabulary file). Goal of the next session: the
city dashboards (Summary + Species pages) answer their default views from
small precomputed summary parquets instead of scanning each city's
`{code}_tree_info` file, so a dashboard opens fast on a phone.

## Where it stands (updated 2026-10-30, second session)

- **0.3.423 is clean.** Measured against a local resolver on the released
  wheel: no plan errors, the no-city batch plans, and every summary-served
  answer matches the tree plan (one model-level exception, D below). 0.3.424
  is on pytrilogy main but not on PyPI; main gives the same plans.
- **Produced from the model**, on branch `rollup-aggregates`
  (worktree `../sf_tree_rollups`, uncommitted): `data/raw/tree_rollups.preql`
  builds `tree_rollup_species` (city, species, rollup_tree_count) from the
  published rollup through `full_tree_info_source`, guarded with
  `kept_by(species, city, 0.75)`. The new `refresh-rollups` job sits on the
  core cron. `tree_rollup_source.preql` is the browser's root over the
  parquet; `tests/test_tree_rollups.py` pins the wiring and checks that the
  model's SQL equals the trees grouped. The dry run shows exactly one asset,
  and all data tests pass (7419).
- **Not yet published, not yet in the dashboard scope.** Built by hand from
  the published rollup it is 93 KB (14,728 rows, 41 cities).
- **One grain only.** The (city, species, plant year) draft serves 0 of 73
  queries: a summary whose grain holds a derived concept is never read
  (pytrilogy #803, comment E). It is dropped until that is fixed.
- **Open upstream: pytrilogy #803** (filed 2026-10-30). A: a scalar of a key
  (`split(species, ' ')[1]`) misses, in the real bundle only. B: a ratio over
  `count(tree_id) by *` misses (top-15 %, top-5 share, dominance curve under
  a city filter). C: `count(x ? count(tree_id) by x > 0)` and
  `count_distinct(city)` miss. D: `species: ~species` on a datasource with an
  `assert` is refused. E: the derived-grain summary above. Repros are in
  `upstream_repro/rollup_served_misses/` (`repro.py`, `repro_year.py`, and
  `requests/` holding real-bundle request bodies for the local resolver).
- **D is a model fix, not a planner bug.** `tree_enrichment.preql` binds
  `species` as complete, so the planner INNER JOINs enrichment wherever it
  likes. On the summary path Boston's Unknown buckets lose 11 trees; and
  production's *no-city* view already loses 629,076 of 13.7M trees from every
  enrichment-bucket chart today, with or without a summary. The fix is
  `species: ~species` (as `tree_enrichment_source.preql` does), but the
  datasource's `@kept` assert then refuses to parse: that is D.

## Measured (local resolver on 0.3.423, catalog at main ad42161)

| view | queries | read the summary | plan errors | answers vs tree plan |
|---|---|---|---|---|
| Boston (USBOS) | 39 | 19 | 0 | 13 same; 6 short by the 11 unenriched trees (D) |
| no city | 34 | 19 | 0 | 19 same |

Bytes each served query pulls from GCS (DuckDB httpfs, no cache shared
between queries), tree plan vs summary plan:

| view | tree plan, per chart | summary plan, per chart | wall, per chart |
|---|---|---|---|
| Boston | 67-69 MiB | 0.09-1.0 MiB (the spotlight's 1 MiB is enrichment) | 2.3-3.5 s -> 0.0-0.6 s |
| no city | 123 MiB | 0.09-1.0 MiB | 3.5-4.9 s -> 0.0-1.4 s |

Not measured: the dashboard opening in a browser. The unserved charts
(dot map, plant year, unique species, selectors, native-locality) still read
the tree file, so the page as a whole still touches it until #803 lands; the
summary takes 19 of the ~40 tree-file readers off the first view.

Raw output: `upstream_repro/rollup_default_views/oct30_results/`; the answer
check is `check_rollup.py <tree parquet> <sql_with> <sql_without>` and the
byte count `bytes.py <sql_with> <sql_without>` (run beside the downloaded
parquets and a summary built from them).

### The old table (Oct 29), for history

| pytrilogy | queries served from the summary | wrong answers | plan errors | all-cities batch |
|---|---|---|---|---|
| 0.3.414 | 1 | dominance curve | 3 | 422 |
| 0.3.420 | 18 | 5 bucket charts x2 pages (one row per species) | top-5-share, dominance curve | 422 |
| #793 branch | 4 (totals, spotlights) | none | none | 422 |

Caution: the Oct 30 session found the probe had been sending to production
whenever `TRILOGY_RESOLVER_URL` was unset (see below); rows in this table may
be production's answers rather than the named version's.

## How to measure (all from earlier sessions; they worked)

1. **Local resolver on the version under test.** In
   trilogy-studio-core/pyserver: `uv pip install --target C:/tmp/pt423
   --python .venv/Scripts/python.exe pytrilogy==0.3.423 --no-deps`, then
   `PYTHONPATH='C:\tmp\pt423' .venv/Scripts/python.exe -m uvicorn main:app
   --port 5690`. Use the *wheel*: a source checkout has no built
   `_preql_import_resolver`, falls back to the venv's old one, and fails with
   `PyGraphCore has no attribute strongly_connected_components`. Don't run
   python from inside the pytrilogy checkout: the cwd shadows PYTHONPATH. Use
   uvicorn rather than `main.py`, whose `reload=True` leaves orphaned workers
   answering on 5678 after the parent is killed. Never `python -I`. Or `uv pip install
   pytrilogy==0.3.423` into a copy of the venv. See memory
   `local-resolver-e2e-recipe`.
2. **Probe which queries hit the summary.** Copy
   `upstream_repro/rollup_default_views/zz-rollup-probe.test.ts` into
   `src/src/tests/` and `tree_rollup_source.preql` into `data/raw/`, then from
   `src/`:
   `TRILOGY_RESOLVER_URL=http://localhost:5690 PROBE_OUT=probe.txt PROBE_SQL=sql_with pnpm vitest run src/tests/zz-rollup-probe.test.ts`
   (**`TRILOGY_RESOLVER_URL`, not `VITE_...`**: `resolverFetch.ts` reads the
   former, and without it every request silently goes to production.
   `PROBE_SINGLE=1` sends one query per request; `PROBE_DUMP=<dir>` saves
   failing request bodies, plus `PROBE_DUMP_ALL=1` for all of them.)
   and again with `PROBE_ROLLUP=0 PROBE_SQL=sql_without`. Lines read
   `HIT`/`miss`/`ERR` per query, for USBOS and for no city (ALL). Delete
   both copied files afterwards; they must not be committed.
3. **Check the answers.** `check_rollup.py` (same folder) runs every query
   that read the summary against the local Boston `usbos_tree_info_v2.parquet`
   plus a summary built from it, beside the same query planned without the
   summary, and prints SAME/DIFF:
   `python check_rollup.py sql_with-USBOS.json sql_without-USBOS.json`
   (run from a directory holding `usbos_tree_info_v2.parquet`).
4. **Upstream repros.** `upstream_repro/rollup_bucket_reaggregate/`
   (`repro.py`, `issue.md`, and `all_cities_spotlight_request.json` for
   `curl -X POST localhost:5678/generate_queries -H 'content-type: application/json' --data @…`)
   and `upstream_repro/rollup_default_views/repro.py` should both pass on 0.3.423.
   If anything is still wrong, file a new repro there and a new upstream
   issue; per AGENTS.md, do not work around a planner bug in a query.

## Next steps (updated 2026-10-30, third session)

Status: data side **PR #158, green**; scope change **PR #159, draft**
(stacked on #158, red against production on purpose); resolver bump
**trilogy-studio-core #280, draft**. The summary is **published**:
`tree_rollup_species_v2.parquet`, built locally, with 14,728 rows across 41
cities and 13,714,921 trees, identical to the hand-built one.

1. **Blocker: pytrilogy #808.** Since 0.3.405 the dot map under a nativeness
   cross-filter joins its filter `on 1=1` and returns every tree. This
   happens without the summary too, so no bump past 0.3.404 can ship until
   it is fixed. Repro: `upstream_repro/filter_cross_join_dot_map/` (real
   bundle; a small model does not reproduce it). It is *not* caused by
   `core`'s `city.ecoregion_id`: removing that leaves #808 in place.
2. When #808 is fixed: re-pin studio #280 to that release, then run
   `pnpm test:queries` and `pnpm test` from the #159 branch against a local
   resolver on it (expect 8/8 and 314/314). The user merges #280 and rolls
   the service.
3. Merge #158 (cloud-sync on main creates `refresh-rollups`), then rebase
   #159 onto main, mark it ready, and merge it once CI is green against the
   rolled resolver.
4. **Import order is load-bearing** in `dashboard_context`: the summary must
   come before `ecoregion_info`. Otherwise `core`'s
   `property city.ecoregion_id` redeclaration unbinds the ecoregion key and
   every nativeness query 422s (#803 comment F,
   `upstream_repro/import_order_summary/`). Dropping the city-level
   `ecoregion_id` (nothing reads it; `test_city_wiring.py` still demands
   it) is the model-side cleanup.
5. D (11 Boston trees, `~species` + assert) is still open on #803.
6. Measure in a browser with the bench recipe in `docs/MOBILE_LOAD_BUDGET.md`
   once #159 is live.

## Small things worth doing first or alongside

- ~~Studio components bump (0.1.29 -> 0.1.31)~~: done in #157.
- **Remove the merged worktree**: `git worktree remove ../sf_tree_sorted_bench`
  (branch `mobile-range-reads-vocab` is merged as #155). Scratch at
  `C:\tmp\sorted_bench` can go too; the rollup bits were copied into
  `upstream_repro/rollup_default_views/`.
- **Map follow-up (not aggregates):** phone pans into new ground went from
  0.2-4 s to 12-19 s because duckdb-wasm's readahead grows to 16 MB on a
  reused file handle; `TIERS.normal.budgetRows` stays 300k until that is
  fixed. See `docs/MOBILE_LOAD_BUDGET.md`.

## Constraints

- Another agent works in the main checkout; use a fresh worktree.
- HMAC keys for `trilogy refresh` are in the main checkout's `.env` (pass
  `--env`); never print or commit them. `trilogy refresh -f` takes one asset
  name; repeat `-f` per asset.
- Cloud jobs report `up_to_date` for a brand-new asset; force-build locally.
