# Project Context

## Data ingest

The map's parquets are built by scheduled jobs on trilogy-cloud, declared as
`[[cloud.job]]` entries in `data/trilogy.toml` and deployed by
`.github/workflows/cloud-sync.yml` on merge to main. Read that file's header
before changing anything under `data/` — it carries the rationale for every
cadence — and `EXTENDING.md` for the city-addition runbook.

The shape, in one paragraph: **each city is an independent pipeline** with its
own three jobs (`osm-{code}` weekly extraction, `city-{code}` refresh on a
cadence matched to its portal, and a `landmarks-{code}` publish with no cron
where the landmarks are a curated CSV), and a **daily core** (`publish-full`,
`refresh-enrichment`, `refresh-ecoregions`, plus weekly `refresh-landmarks`)
that reads only published parquets.

Four things about that are load-bearing, and each replaced something that broke:

- **A job's bundle is its entrypoint's reachable imports.** `trilogy refresh`
  adopts every managed datasource it can reach, so what a model imports decides
  what a job builds, probes and needs memory for. A city model importing only
  `tree_common` and `community_tree_info` is what makes `city-{code}` exactly
  one city. Check with `trilogy refresh --dry-run <entrypoint>`: more than one
  asset for a city job means an import reaches too far.
- **The core must not reach a portal.** `raw/full_tree_publish.preql` reads the
  published city parquets directly (one `file [...]` multi-file scan) and the
  enrichment job's entrypoint reaches the rollup through a root datasource;
  neither imports the city models. While enrichment imported `tree_info` it
  could rebuild any city's parquet inside its own container.
  `data/raw/tests/test_cloud_jobs.py` pins this.
- **The job's file set and the browser's are different on purpose.** The
  enrichment *job* runs `raw/enrichment_refresh.preql`, which imports the
  enrichment model plus `raw/full_tree_info_source.preql`; the *browser* bundles
  `raw/tree_enrichment.preql`, which is species-only. Collapsing the two puts a
  second tree source in the planner's scope and changes what the charts return —
  see the join-type bug under Dashboard query compilation below.
- **Cadence is measured.** `data/raw/portal_cadence.py --record` samples every
  freshness probe and derives each portal's real publishing interval from the
  distinct watermarks it has recorded in `portal_cadence.json`. Do not retune a
  cron from a single observation.
- **A missing job is silent.** Nothing errors when a city has no schedule; its
  parquet simply stops updating. `test_cloud_jobs.py` is the only thing that
  catches it, so run `cd data/raw && uv run --with pytest python -m pytest tests -q`
  after touching the job table.

## Tech

Vite, vue, typescript

Use pnpm not NPM for all management. 

This is critical - NO NPM.

## Testing

From `src/`: `pnpm test` (vitest), `pnpm test:e2e` (Playwright), `pnpm lint`.
`pnpm test:queries` is separate — see Dashboard query compilation below. It is
excluded from `pnpm test` because it needs the network and takes tens of
minutes, which does not fit the CI `test` job's ten-minute budget; it runs
nightly and on demand via `.github/workflows/dashboard-queries.yml`.

Playwright's webServer runs `pnpm build:e2e` — a `--mode e2e` build that loads
`src/.env.e2e` and compiles in the fixture seam in `src/src/lib/e2eFixtures.ts`.
The seam lets specs seed an auth session and contribution history through
`window.__treeE2E` (via `page.addInitScript`), which is the only way to reach
the achievement/badge UI without a live Firebase. `import.meta.env.VITE_E2E` is
statically replaced, so a normal `pnpm build` contains none of it — verify with
`grep -c __treeE2E dist/assets/index-*.js` after building.

Desktop and mobile coverage is expressed as viewport-parametrised describes in
one spec file (`for (const [label, viewport] of [...])`), not as separate
Playwright projects — see `e2e/achievements.spec.ts` and `e2e/tree-card.spec.ts`.

### Dashboard query compilation

Every chart on the summary and species pages sends PreQL to the hosted Trilogy
resolver and renders the SQL it gets back, so a query that fails to plan is a
"trilogy generation error" in the UI and nothing else catches it — the queries
are string constants, and TypeScript has no opinion about them.

`src/src/tests/dashboard-queries.test.ts` compiles the whole catalog against the
live resolver and then **runs the SQL it gets back**. Compiling proves the
planner produced SQL; only executing proves the SQL answers the question. Both
halves have caught a live bug:

- a keyless join (`on 1=1`) compiles fine and then evaluates the filter against
  unrelated rows — the dot map returns all 7 of San Francisco's fixture trees
  under a nativeness filter that should leave 3;
- `cumulative_tree_share_pct` compiles to a `rank() over (...)` nested inside
  another window's `ORDER BY`, which DuckDB rejects outright ("window functions
  are not allowed in window definitions"), so the Dominance Curve and the Top 5
  Share KPI are broken on every city while compiling perfectly. That one is an
  upstream planner bug, fixed in pytrilogy after the version
  `trilogy-service.fly.dev` currently runs — the test stays red on those two
  charts until the service picks the fix up, which is the point of having it.

Both are upstream bugs and **neither is worked around here**. Aggregating the
dot map query avoids the first, and ordering the cumulative sum by the
underlying expression avoids the second; both were tried, both work, and both
were reverted deliberately — hiding a planner bug behind a query rewrite takes
the pressure off the fix and leaves the next chart to rediscover it.

Each one gets a self-contained repro under `upstream_repro/`, which is
gitignored: those go to the upstream project rather than into this repo's
history, so a path named here is a local working directory, not something a
fresh clone will have.

A third lives in `upstream_repro/join_type_varies_by_source/`, and it is the
reason `src/src/tests/dashboard-pushdown.test.ts` exists. Declaring a second
datasource that satisfies the same concepts — a cross-city rollup beside the
seventeen per-city partitions — changes the JOIN TYPE on the enrichment side
from `RIGHT OUTER` to `INNER`, which drops every tree whose species has no
enrichment row. The query never names the second source and it contributes no
rows; the count simply comes back lower. `uv run repro_query.py` in that
directory reproduces it offline in about a second (2 vs 1) and exits non-zero
while the bug stands.

We are not exposed today, because the enrichment job's file set and the
browser's model bundle are deliberately kept apart (see the data ingest section
above). `dashboard-pushdown.test.ts` is what keeps them apart: it asserts that
a per-city dashboard, and a chat query that filters to a city dynamically, both
resolve to that city's own Parquet, and that only the all-cities view reads the
rollup. That is worth pinning for its own sake — the browser downloads whatever
the SQL names, and the rollup is 5.9M rows against San Francisco's 206k.

The queries come from `dashboardQueryCatalog.ts`, which derives them from the
same constants the views render — a new chart is covered automatically as long
as its query lives in `summaryDashboardConfig.ts` or `speciesDashboardConfig.ts`
(which is why the queries that used to sit inline in the views were moved
there). It drives the real cross-filter controller, so filters and bind
parameters match what the pages send.

Execution runs against `dashboardFixtures.ts`: nine trees, four species, four
ecoregions, chosen so every cross-filterable dimension splits them — some trees
in the bucket, some out — which is what makes "the filter did nothing"
detectable. `dashboardExecution.ts` creates one DuckDB table per Parquet the SQL
reads, taking the schema from the real Parquet (`LIMIT 0`, a footer read) so a
column can never be missing, fills it with fixture rows, and repoints the
`read_parquet('https://…')` call at it. Expected numbers are derived from the
fixture rows by a reference implementation of the `dashboard_context` buckets,
so changing a fixture cannot leave a stale expectation behind.

Two things to know when adding fixtures: a per-city Parquet is seeded with only
that city's trees, because each city's datasource asserts
`complete where city = 'X'` and the planner may legitimately omit the predicate
— foreign rows would make a correct plan look like a dropped filter. And the
resolver wants `parameters` keys carrying their leading colon (`':nlb'`, not
`'nlb'`); without it the filter fails to parse with a bare `Syntax error`.

Flags, for widening the sweep when a planner regression is suspected:

```bash
cd src
pnpm test:queries                                            # the default sweep
DASHBOARD_QUERY_CROSS_FILTERS=all pnpm test:queries          # every cross-filter dimension
DASHBOARD_QUERY_CROSS_FILTERS=pairs pnpm test:queries        # every pair of dimensions
DASHBOARD_QUERY_ALL_CITIES=1 pnpm test:queries               # interactive states in all 14 cities
```

The default run is the base state for every city plus one cross-filter
dimension (nativeness — the one that reaches enrichment through the
`unnest(native_ecoregions)` merge, where both planner failures have lived). It
needs network: a live check against `https://trilogy-service.fly.dev`, whose
pytrilogy pin floats, so a failure with no local change means an upstream
release moved under us.

Compiles run about 0.4s against an idle resolver, but the service drops to
10-60s per compile after a few hundred requests and takes tens of minutes to
recover, which turns a three-minute run into an hours-long one. That is being
tracked as its own upstream handoff, not worked around by tuning
`DASHBOARD_QUERY_CONCURRENCY` (throughput barely moves with it: 0.04 → 0.13
compiles/s from 1 to 8 clients).
