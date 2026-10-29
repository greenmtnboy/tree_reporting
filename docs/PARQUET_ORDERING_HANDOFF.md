# Handoff: rebuild the city parquets sorted, then re-measure the phone

Written 2026-10-28 at the end of the session that finished PR #152 (the
phone's window, `mobile-map-window`). This is the next session's brief.

## Progress (2026-10-28, second session)

- Step 1 and step 2 are on branch `parquet-hilbert-order`. The floors are at
  0.3.414 in `AGENTS.md`, `EXTENDING.md`, CI, cloud-sync and
  `requirements.txt`. The template carries the order and the row-group size.
- **0.3.414 cannot build the sorted projection.** The write-order select
  grain-pins the stored derived columns, and a CTE usage walk renders
  `array_reduce` with the generic dialect. Both are fixed together upstream
  in trilogy-data/pytrilogy#783 (0.3.416); the repros are in
  `upstream_repro/write_order_derived_column/`. Raise the floors to 0.3.416
  once it is on PyPI, and check the cloud worker runs it, before merging.
  Otherwise the daily projection job fails for every city on its next
  rebuild.
- Step 5 was done with the patched pytrilogy, written to a local file: 166
  row groups of ~10k rows, the same 1,695,102 rows as the published file,
  and no extra column. A 4.5 km Blue Hills window overlaps 5 of 166 groups.
  The file is 71.3 MB against the published 73.0 MB, not the 47 MB the spec
  estimated. Steps 6 to 8 (re-measure, budget, rollout) wait on the release.
- **Precomputed aggregates for the summary and species default views are
  parked.** Datasources storing `count(tree_id)` at (city, species) and
  (city, species, plant_year) answer only the Top 100 carousel on the
  resolver's planner (0.3.404, and 0.3.414 too). With one in scope:
  - the Total Trees KPI fails to plan;
  - the dominance curve reads the rollup without the city filter, so its
    numbers are wrong;
  - the no-city batch 422s;
  - the bucket charts never use the rollup.

  Filed as trilogy-data/pytrilogy#782; the repro is in
  `upstream_repro/rollup_default_views/`.

## Goal

Write each city's browser projection, and probably its published
`tree_info`, in Hilbert order with small row groups, using pytrilogy's new
datasource clauses. Then re-measure the phone and raise the window's row
budget if the numbers allow. Success is a phone that reads a few MB per
window load instead of the whole file, and a chat question about the whole
city that no longer takes a phone tab past about 1 GB.

## Where things stand

- **pytrilogy #778 is merged and released as 0.3.414** (checked 2026-10-28;
  PyPI's latest is 0.3.414). It adds datasource `order by`, `storage
  (row_group_size = n)` and `hilbert_index` in `std.geography`. Read its
  tests for the exact syntax; `docs/TRILOGY_ORDER_BY_SPEC.md` holds the
  request as made, and its proposed syntax is not what shipped.
- **PR #152** (the window, plus the chat memory fixes) should be merged
  first; this work builds on its loader and bench. If it isn't merged, work
  on top of `mobile-map-window`.
- Nothing in `data/` has changed yet. `data/raw/tree_browser_city.preql`
  still says "Rows are not ordered".

## The measurements to beat

All of them are Boston (`USBOS`, 1.57M rows with predicted trees) on the
iPhone 13 profile, production data. Full tables are in
`docs/MOBILE_LOAD_BUDGET.md` ("Stage 2: the phone's window" and "The chat
reads the whole city").

| | Now |
|---|---:|
| `usbos_tree_browser_v2.parquet` | 67.7 MB, 13 row groups of ~122k, no order |
| A window load | scans the whole file; heap grew 127 MB for a 167k-row load |
| DuckDB heap after the city load (windowed) | 382 MB, 509 MB after the walk |
| Renderer peak, walk plus ten pans | 1,024 to 1,033 MB |
| `usbos_tree_info_v2.parquet`, which the chat reads | 90 MB, default row groups |
| DuckDB heap after a whole-city chat read / publish | 605 MB / 701 MB (from 382) |
| Renderer peak, whole-city chat publish (coloured) | 1,160 to 1,305 MB |

The spec's footer arithmetic for the projection: Hilbert order with
10,240-row groups is about 47 MB, and a dense 4.5 km window reads 17 groups
(5.2 MB) instead of all 13 (67.7 MB). See the table in
`docs/TRILOGY_ORDER_BY_SPEC.md` before choosing a row-group size: 20,480
makes a smaller file but prunes a little worse.

The chat's remaining cost is the part not measured yet. After PR #152 the
chat no longer copies results around; what is left is DuckDB's heap
keeping the high point of scanning `tree_info`. Smaller row groups should
lower it, because the reader holds a row group's columns at a time. That is
a hypothesis: measure it.

## Steps

1. **Bump pytrilogy** to `>=0.3.414` wherever it is pinned or floored:
   - the test command in `AGENTS.md`;
   - CI;
   - the cloud worker;
   - the resolver, which pins an exact version
     (trilogy-studio-core/pyserver; a bump PR there comes first, see the
     memory note on the resolver service).

   The browser never parses these clauses, but the resolver parses the
   model sources the app sends (`src/src/trilogyModels.ts`). That list
   includes `tree_dedup.preql` but not `tree_browser_city.preql`
   (checked 2026-10-28). So the projection can change without the
   resolver; changing `published_tree_info` in `tree_dedup.preql` needs the
   resolver live on 0.3.414 first, or every chat and dashboard query 422s.
   `pnpm test:queries` against the live resolver catches it.
2. **The projection.** Add the order and the row-group size to the
   `city_tree_browser` template in `data/raw/tree_browser_city.preql`
   (`order by` a `hilbert_index(latitude, longitude)`, row groups of about
   10k), and replace the "Rows are not ordered" note. This rewrites the
   existing `{code}_tree_browser_v2` files: no new asset, no new job.
3. **`tree_info`.** Decide whether to do the same on `published_tree_info`
   in `data/raw/tree_dedup.preql`. It serves the chat and the resolver's
   point lookups, so pruning helps both. It is a bigger file per city, and
   the guards (`kept`, `source_kept`) must still pass. Do the projection
   first and measure before deciding.
4. **Offline checks.** Run the `data/raw` pytest command in `AGENTS.md`, and
   the dry run for one city (it must report exactly one asset).
5. **Build Boston locally**, not in the cloud: the worker's pytrilogy may
   lag, and the first cloud build of a new kind of asset has failed before.
   Check the result with DuckDB's `parquet_metadata()`:
   - the row-group count;
   - per-group lat/long min/max that are tight;
   - no extra sort-key column in the schema.

   Remember the cache-buster when you read it back (GCS max-age is 3600).
6. **Re-measure**, same scenarios as before, from a production build served
   by `pnpm preview --port 6180`. Use one scenario per run, and run each
   twice:

   ```bash
   # window: load, walk, pans (Blue Hills, as in MOBILE_LOAD_BUDGET.md)
   MAP_BENCH_URL=http://localhost:6180/ MAP_BENCH_CITY=USBOS MAP_BENCH_LAT=42.2108 MAP_BENCH_LNG=-71.0852 MAP_BENCH_PANS=10 pnpm bench:memory
   # chat, one step per fresh tab; MAP_BENCH_ISOLATE=1 adds DuckDB's own heap
   MAP_BENCH_URL=http://localhost:6180/ MAP_BENCH_CITY=USBOS MAP_BENCH_CHAT=publish MAP_BENCH_ZOOMS=15 MAP_BENCH_ISOLATE=1 pnpm bench:memory
   ```

   To test a file before publishing it, use the `VITE_TREES_BASE_URL`
   recipe at the end of `docs/MOBILE_LOAD_BUDGET.md`. Also run
   `pnpm bench:tiles` before and after: a sorted file changes scan times.
7. **Raise the budget** if the heap allows it: `TIERS.normal.budgetRows` in
   `src/src/composables/useMapWindow.ts`, 300k now, toward the 500k the
   subset sweep allows. Its comment explains why it is 300k; rewrite it with
   the new numbers. Update `docs/MOBILE_LOAD_BUDGET.md` and mark step 1 of
   `docs/TRILOGY_ORDER_BY_SPEC.md` done.
8. **Roll out** to the other cities by triggering their jobs. A schema-only
   change may report up to date; see the memory notes on forced builds and
   stale-on-schema-drift. The Boston metro's predicted partition makes it
   the largest; check that a big city like NYC or London still builds
   within the cloud worker's memory.

## Watch out for

- **Insertion order.** The worker sets `preserve_insertion_order = false`,
  but that only affects the browser's own tables, not how the file is
  written. In the build, the local-stage write-through path re-reads the
  staged file and must keep both the order and the row-group size;
  pytrilogy #778 is supposed to handle all three COPY sites. Verify on the
  published file, not the staged one.
- **The window filters on a lat/long box** (`windowStagingSql` in
  `src/src/workers/cityTables.ts`), so pruning works with no frontend
  change. Look for `window-load` in the `[Perf]` lines and check its ms and
  the bytes the bench reports for the projection.
- **The vocabularies** (the ENUM builds in `cityShape`) still read whole
  columns of the file once per city load; sorting doesn't change that.
- **Out of scope here:** the summary and species views. They run on a
  second DuckDB-WASM instance in the same tab
  (`useSummaryDashboardExecution.ts`). The plan for them is precomputed
  aggregates for the default dashboard loads, with interactivity off behind
  a banner on mobile. That is a separate piece of work.
