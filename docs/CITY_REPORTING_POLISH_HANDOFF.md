# Handoff: polish the city dashboards' outputs

Written 2026-11-01, with the production resolver on pytrilogy 0.3.439 and main
at f350d35 (sf #164).

**Your job:** go over what the Summary and Species dashboards actually show
for real cities, and make the answers more useful. Hide placeholders. Label
gaps honestly. Drop or fix charts that say almost nothing. The list below is
what one pass over Boston and the all-cities view turned up. It's a starting
point, not the whole job, so look at a few more cities too: a community-only
one (GRMLO), a big municipal one (USNYC, GBLON) and a small one (USSTO).

## Rules

- **Use a worktree**, branched from `origin/main`. Other agents work in the
  main checkout. Commit, push and PR only when the user asks; they've
  authorized PR work on this task, so get the PR up and track it to green.
- **pnpm only** under `src/`. Read `AGENTS.md` first.
- **Don't work around a planner bug in a query.** If a fix you want doesn't
  plan, or plans wrong, file a repro under `upstream_repro/` (gitignored) and
  an issue on trilogy-data/pytrilogy. Leave the test red. If an issue is
  closed, open a new one that links it; don't comment on the closed one.
- **Keep the summaries serving.** Most default charts are answered from small
  published summaries instead of the city's tree file. That's the phone
  memory budget (`docs/MOBILE_LOAD_BUDGET.md`). The summaries are keyed by
  `(city, species)`, plus plant year, DBH class or map cell. A filter on
  `species`, or on anything derived from species, stays served. A filter on a
  column that isn't in the summary, such as `data_source`, sends the chart
  back to the trees. Re-run the coverage probe (below) before and after, and
  say in the PR which charts move.
- **Decisions that change what a number means go to the user first.** The
  first item below is one.

## What the outputs look like now

These are prod's answers on 2026-11-01. The raw dumps are in
`upstream_repro/rollup_default_views/prod_outputs_2026-11-01/`
(`outputs_usbos.txt`, `outputs_all.txt`, and the generated SQL as
`agesql_prod-*.json`).

### 1. Boston is mostly satellite predictions with no species (ask the user)

Boston's view counts 1,695,098 trees. 1,527,075 of them are
`PREDICTED_USBOS`, the imagery model's detections (see the
`predicted-partition-poc` memory and `docs/DATA_PIPELINE.md`), and
1,372,274 of those have species `Unknown`. As a result:
- **Unknown is 82% of the city.** It's rank 1 on the dominance curve and in
  the Top 100 spotlight.
- **Top 5 Share reads 90.4%,** and four of those five are Unknown.
- **The DBH chart shows 1.46M trees at 6–12"**: 1.41M of those are predicted
  trees.
- **Boston is the biggest city on the world map** (1.7M), ahead of Bogotá
  (1.49M) and London (1.27M).

Whether predicted trees belong in species-composition charts at all is a
product call. Bring the user the options with numbers. For example: count
them only in Total Trees and the dot map; or show "N trees mapped from
imagery, species not identified" as its own line. Remember the
summary-serving rule: filtering on `data_source` breaks the summaries
unless the rollup grain changes. `OSM_USBOS` behaves the same way on a
smaller scale: 20,186 of its 20,470 trees are Unknown.

### 2. Placeholders shown as if they were species or buckets

- **`Unknown`, "Species not recorded"** is row 1 of `top-tree-spotlight` and
  `sp-spotlight` (2.87M trees across all cities), and rank 1 of
  `dominance-curve`. It also inflates `top-5-share`. `sp-top-species`
  already excludes it with `REAL_SPECIES_PREDICATE`
  (`src/src/data/species.ts`), so use that pattern. Decide whether
  shares should be over identified trees or over all trees, and say which
  in the subtitle.
- **`tree_form = 'default'`** is the largest Tree Forms bar: 1.49M of
  Boston's trees and 6.4M across all cities. It's the renderer's fallback
  shape, not a form. 2,655 of the 4,573 enrichment rows have it.
- **"Unknown" buckets dominate** Hardiness Zone Fit (1,694,922 of 1.7M),
  Water vs Drought and Lifespan Profile. Meanwhile Wildlife Value, Growth
  Rate and Fire Risk silently drop their unknowns. Pick one rule for all of
  them. A good one: chart the known trees only, with an "x% of trees have
  this data" note.
- **Genus-only records** (`Quercus` "Oak", `Fraxinus` "Ash", `Tilia`, `Acer`
  and so on) sit in the species rankings beside the species, e.g. "Oak"
  next to "Northern red oak". They're real data. Consider labelling them
  "Oak (species not recorded)". The species picker also lists the bare
  genus (`Platanus` among the Platanus species), and its labels are
  scientific names only.

### 3. Charts with almost no data behind them

Coverage in `tree_enrichment` (4,573 species):

| field | species with a value | chart |
|---|---|---|
| `usda_zone_max` | 29 | Hardiness Zone Fit: in Boston, 165 trees have a bucket |
| `fire_risk` | 58 | Fire Risk: 324 trees in Boston |
| `lifespan_max_years` / `lifespan_min_years` | 56 / 109 | Lifespan Profile |
| `sun_exposure` | 626 | Sun Exposure Fit: one bar ("Full sun") in Boston and across all cities |
| `water_needs` | 2,050 | Water vs Drought |
| `native_ecoregions` | 3,271 | Nativeness |

Even Norway maple and eastern white pine have no hardiness zones. Two ways
forward, which the user should pick between:
- **Backfill the enrichment.** That's an LLM job; see
  `docs/SPECIES_ENRICHMENT.md` and `data/raw/enrichment/`. It's the larger
  but better fix.
- **Hide a chart** below a coverage threshold, and show the coverage on the
  cards that remain.

### 4. Data artifacts the charts take at face value

- **Brookline stamps `1994-01-01` on 11,468 trees.** That makes 1994 the
  biggest year in Boston's Planting Waves; the next biggest is a few hundred.
  It's almost certainly an ingest default, so fix it at the ingest, the way
  Vancouver's planted date was fixed (`fix-vancouver-date-planted`).
  `docs/DATA_PIPELINE.md` has the rebuild steps.
- **Plant years before about 1850** show up in the all-cities view (1702,
  1716, and so on). Check whether they're real (arboretum accessions) or
  bad dates.
- The probe's `species:context` returns an arbitrary row when no species is
  selected, e.g. "Pinus armandii" as the common name. Check what the page
  shows before a species is picked.

### 5. Not yet checked

- `summary:native-locality`, `local-native-share`, `sp-biome-fit` and
  `sp-city-distribution` take the `:active_city_*` parameters, and the dump
  script doesn't bind them. Look at them in the running app (`/run`, or
  `pnpm dev` in `src/`) instead.
- Titles and subtitles against what the numbers mean. For example, "Top 5
  Share %" when the shares count Unknown, or "Biome Fit %" when half the
  species have no range.
- Colors and ordering. Buckets have a natural order (lifespan, DBH, zone fit)
  that a sort by count breaks.

## How to look and how to verify

- **Dump real answers.** Copy `upstream_repro/rollup_default_views/zz-rollup-probe.test.ts`
  into `src/src/tests/` and run it:
  ```
  PROBE_ROLLUP=0 PROBE_SQL=C:/tmp/x PROBE_OUT=C:/tmp/probe.txt pnpm exec vitest run src/tests/zz-rollup-probe.test.ts
  ```
  That writes the SQL prod generates for Boston and all cities to
  `C:/tmp/x-{USBOS,ALL}.json`. Then run
  `uv run --no-project --with "duckdb<2" python upstream_repro/rollup_default_views/dump_outputs.py C:/tmp/x-USBOS.json`
  to see the answers. **Delete the copied test afterwards.** Without
  `TRILOGY_RESOLVER_URL` it uses prod. Edit `PROBE_CITY` handling in the
  test for other cities.
- **Coverage probe:** the same run's `probe.txt` marks each query HIT or
  miss. That's 57 of 73 now; don't let it drop without saying why.
- **`answers_diff.py`** (same folder) compares two runs' answers query by
  query. Use it to show a change touched only what you meant it to.
- **Gates:** `pnpm test` (317), `pnpm lint`, `pnpm test:queries` (8; the
  dashboard sweep), plus the data tests from `data/raw` if you touch models.
  Their commands are in `AGENTS.md`.
- **The sweep checks answers against fixtures** in
  `src/src/tests/dashboardFixtures.ts` and `dashboardExecution.ts`
  (`checkResult`, `BUCKET_CHART_FIELDS`). If you change what a chart counts,
  for example excluding Unknown, update the fixture-side rule in the same
  change so the check stays a real check. A fixture group of one tree hides
  n² bugs; that's why fx-10 and fx-11 exist.

## Where things live

| | |
|---|---|
| Chart queries | `src/src/composables/summaryDashboardConfig.ts`, `speciesDashboardConfig.ts` |
| Bucket definitions (hardiness, water, lifespan, sun, nativeness) | `src/src/composables/dashboardContextSource.ts` |
| Sentinels, `REAL_SPECIES_PREDICATE` | `src/src/data/species.ts` |
| Summaries | `data/raw/tree_rollups.preql` (built by the `refresh-rollups` job), `tree_rollup_source.preql` (the browser's roots) |
| Enrichment | `data/raw/enrichment/`, `docs/SPECIES_ENRICHMENT.md` |
| Predicted trees | `data/raw/tree_predictions*.preql`, `predicted-partition-poc` memory |
