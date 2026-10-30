# Handoff: show tree height on the map

**Goal:** the map uses `height_m` where a tree has one. It should show a
"Height" row on the tree card and size the 3D tree sprite by it, falling back
to today's behaviour everywhere else.

**Read first:** `docs/TREE_HEIGHT.md` (what `height_m` is and how it was
matched), `docs/MOBILE_LOAD_BUDGET.md` (why the browser projection exists),
and `docs/CROWN_WIDTH_ROLLOUT_FAILURES_HANDOFF.md`. That last one matters
most: it is the last time a column was added end to end, and it broke 11 prod
jobs. The same trap is waiting here.

## What exists (PR #154, `tree-height`)

- **Only `usbos_tree_info_v2.parquet` has a `height_m` column** (float,
  metres, nullable). The other 40 city parquets do not have the column at
  all; it is not null there, it is absent.
- **Coverage:** about 75% of Boston and Cambridge inventory trees, 70% of the
  Arboretum, and 71% of predictions inside the two surveys. Nothing in
  Brookline, Somerville, Belmont or Watertown, or in the outer predicted
  ring.
- **Nothing downstream reads it yet:** not the rollup
  (`data/raw/full_tree_publish.preql`), the browser projection
  (`data/raw/tree_browser_city.preql`), the worker, or the app.
- **No provenance column is published, by decision.** There is no
  `height_source` and no crown id; both stay in the staging table. Don't add
  them back to show a source. Every height today is aerial LIDAR from 2024,
  so the card can say so as static text (below).

**Don't start until PR #154 is merged and `city-usbos` has been force-built.**
Check with `data/raw/tools/published_columns.py height_m`: it should list
USBOS only.

## Fields and fallback

| Use | Field | Fallback when null |
|---|---|---|
| Tree card "Height" row | `height_m` | Hide the row, like `crown_width_m` does today. Don't show an estimate. |
| Sprite vertical scale | `height_m` | Today's DBH-derived scale: `min(1, sqrt(dbh / 42))` with `dbh` defaulting to 3. |
| Chat, dashboards, URL filters | out of scope | — |

Rules:

- **Validate like crown width.** `crownQuery.ts` treats a width as valid only
  if `isfinite`, `> 0` and `<= MAX_CROWN_WIDTH`. Do the same with a
  `MAX_TREE_HEIGHT`. The staging build already drops heights outside 2–60 m;
  re-check anyway, because the worker shouldn't trust the file.
- **Card format:** copy `formatCrownWidth` in `TreeMap.vue`, which renders
  `12.3 m (40 ft)`. Label it "Height", with a small "aerial LIDAR, 2024"
  hint, because it's a survey reading rather than a field measurement. Put
  it next to "Crown spread".
- **Sprite scale:** the vertical factor is `a_shape.z`, written per vertex
  in `composables/treeCrownLayer.ts` (look for `Math.sqrt(dbh / 42)`). Map
  `height_m` onto the same 0–1 range, e.g. `min(1, height_m / H_FULL)`, and
  choose `H_FULL` so a typical tree looks about as tall as it does now. The
  matched DBH and height pairs in `docs/TREE_HEIGHT.md` (median 6.8 m under
  6", 18.3 m at 24"+) let you calibrate so the two scales agree on average.
  Then a city with heights doesn't look systematically taller or shorter
  than one without. Use the fallback per tree, not per city.
- **Merged crowns:** some trees share one LIDAR crown and inherit its
  tallest point, so a small tree can read a few metres tall. That's
  accepted; don't try to correct it client-side.

## The path, and the trap at each step

The worker reads one of two shapes (`workers/duckdbPipeline.worker.ts`,
around `projectionUrl`):

1. **The projection** (`{code}_tree_browser_v2.parquet`, the normal path).
   It's built from the rollup plus enrichment plus predictions by
   `tree_browser_city.preql`.
2. **The assembled fallback**, used when a city has no projection yet. It
   reads the city parquet directly and probes optional columns with
   `parquetHasColumn` (see `hasCrownWidth`, `hasCultivar`).

### Step 1, data: get `height_m` into the projection

**Trap:** the rollup reads all 41 city parquets as one multi-file
`read_parquet([...])`. DuckDB binds the schema from the first file
(`ussfo`), so selecting `height_m` there fails the whole rollup, and every
projection after it. Adding `height_m: ?height_m` to
`city_published_trees` would reproduce the crown-width outage.

Options, in order of preference:

- **A, recommended: join heights into the projection from a separate
  input.** Declare a datasource in `tree_browser_city.preql` (or its own
  `_source.preql`) over only the parquets that have heights: today
  `file [usbos_tree_info_v2.parquet]`, with `tree_id` and `height_m`.
  Left-join it in the projection the way predictions are joined. Adding a
  height city later is then one line. Two caveats:
  - The lookup lacks most `tree_id`s. See memory note
    `pytrilogy-0401-partial-only-regression`: model it as a complete root
    with `~` on the key, **never** `partial datasource`. The repro is in
    `data/raw/upstream_repro/partial_dimension_padding` (gitignored, local only).
  - Check that the projection's job orders after `city-usbos`'s output. The
    projection's freshness is `prediction_inputs_published_through`, so make
    sure a USBOS rebuild still marks it stale.
- **B: `union_by_name=true` on the rollup scan.** Only if pytrilogy can emit
  it for a `file [...]` list; `tools/species_audit.py` uses it in raw DuckDB.
  It's cleaner long term, but check upstream support first, and don't patch
  around a planner gap (AGENTS.md).
- **C: add `height_m` (null) to all 41 cities.** Don't. It forces a rebuild
  of every city before anything can read the column, which is exactly the
  crown-width failure.

Then add `height_m: ?height_m` to the `city_tree_browser` template. All
projections share the template, so non-height cities get a null column.
That's fine because the projection is built from one model. The projection
`assert @kept` still applies.

**Rollout order:** merge, then rebuild the projections (forced; a new column
reads as up to date), then check every `{code}_tree_browser_v2.parquet` has
`height_m` with `published_columns.py`. Only then ship the frontend that
selects it from the projection.

### Step 2, worker (`workers/cityTables.ts`, `duckdbPipeline.worker.ts`)

- **Projection path:** add `height_m` to the projection `SELECT` in
  `loadCityTablesSql`/`windowedCityTablesSql`, and carry it into
  `trees_fast` next to `crown_width_m`. Watch the memory budget:
  `docs/MOBILE_LOAD_BUDGET.md` counts every resident column. One float is
  small, but run `pnpm bench:memory` before and after, as AGENTS.md
  requires for loader changes.
- **Fallback path:** probe `parquetHasColumn(parquetUrl, 'height_m')` and
  select `CAST(NULL AS DOUBLE) AS height_m` when it's absent, exactly like
  `hasCrownWidth`. Without the probe, every non-Boston city's fallback load
  fails.
- **Even on the projection path**, guard the projection select the same way
  for the window between the frontend deploy and the last projection
  rebuild, or gate the deploy on the `published_columns.py` check.
- **Sprites:** add a `height` column to `crownQuery.ts`'s `nearby` select.
  Thread it through the crown type in `lib/treeCrowns.ts` (it has `dbh?:
  number`) into `treeCrownLayer.ts`.
- **Tree card:** add `height_m` to the card's select in `TreeMap.vue`
  (around the `tf.crown_width_m, tf.dbh` select) and to its row type.
- **`treesViewSql`** (the chat-facing view) can stay as it is; the chat is
  out of scope.

### Step 3, tests

- `workers/cityTables.test.ts` and `wasm/fixtures.ts`: add `height_m` to the
  fixtures, and add a fallback-path case with the column absent.
  `pnpm test:wasm` runs the worker SQL on real DuckDB-WASM; run it after the
  SQL change (AGENTS.md).
- `crownQuery.test.ts`: a valid height wins, and an invalid or null one
  falls back to the DBH scale.
- `data/raw/tests/test_browser_projection.py`: the projection carries
  `height_m`, and the height input's file list matches the cities whose
  models import `tree_height` (reuse `height_cities()` from
  `test_tree_height.py`).
- An e2e check that a Boston tree card shows "Height" and a Somerville one
  doesn't.

## Done when

- A Boston inventory tree's card shows "Height 12.3 m (40 ft)" with the
  LIDAR hint. A Brookline tree shows no row.
- 3D sprites in Back Bay vary with measured height. Elsewhere they look
  exactly as before.
- No city's load fails on either path. `pnpm test`, `pnpm test:wasm`, the
  data suite and `pnpm bench:memory` before and after are recorded in the PR.
