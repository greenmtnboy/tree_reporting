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
  `tree_common`, `community_tree_info` and the shared `tree_dedup` (which has
  no managed datasource of its own) is what makes `city-{code}` exactly
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

### Adding a city

Do not hand-write the twenty-odd registry edits. `data/raw/new_city.py` writes
the mechanical ones from a single spec and `data/raw/tests/test_city_wiring.py`
walks the same list and names anything still missing — the enum, the ecoregion
case, four sets of freshness properties, the cross-city imports and merges, the
rollup file list, the frontend config, the attribution. Almost every one of
those fails *silently* when it is skipped, which is why the sweep exists.
See the quick path at the top of `EXTENDING.md`.

Two shared modules carry what used to be copied per city, and a new city should
reach for them before writing anything:

- **`_osm_shared.py`** — the Overpass extraction every city's `osm-{code}` job
  runs, so a city's OSM wiring is one ~28-line shim.
- **`_arcgis_shared.py`** — layer paging, both freshness watermarks, Esri's
  epoch-milliseconds, and a Hub catalogue search
  (`uv run _arcgis_shared.py <hub-host>` lists a portal's tree layers). ArcGIS
  is what most North American cities publish on. Two details in it are
  correctness rather than convenience and were bugs in the copies it replaced:
  the page size comes from the layer's own `maxRecordCount` (asking for more is
  silently capped, and a capped page reads as the end of the data), and paging
  terminates on `exceededTransferLimit` rather than the short-page heuristic.

The judgement steps are deliberately left manual: the field mapping, the
freshness probe, the landmark source, and the dedup cell size — which is
calibrated per city with `osm_dedup_validation.py` and must never be copied
from another city.

## Tech

Vite, vue, typescript

Use pnpm not NPM for all management. 

This is critical - NO NPM.

## Testing

From `src/`: `pnpm test` (vitest), `pnpm test:e2e` (Playwright), `pnpm lint`.
`pnpm test:queries` is separate — see Dashboard query compilation below. It is
excluded from `pnpm test` because it needs the network, not because it is slow:
it compiles the whole 820-query catalog in under two minutes, and it gates every
pull request as the `dashboard-queries` job in `.github/workflows/ci.yml`.

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
halves have caught a live bug, and both bugs were execution-side — they compiled
perfectly:

- a keyless join (`on 1=1`) compiles fine and then evaluates the filter against
  unrelated rows — the dot map returns all 7 of San Francisco's fixture trees
  under a nativeness filter that should leave 3;
- `cumulative_tree_share_pct` compiles to a `rank() over (...)` nested inside
  another window's `ORDER BY`, which DuckDB rejects outright ("window functions
  are not allowed in window definitions"), so the Dominance Curve and the Top 5
  Share KPI were broken on every city.

Both were upstream planner bugs; `trilogy-service.fly.dev` has since picked up
the pytrilogy releases that fix them and the suite runs green, which is what
made it fit to gate pull requests. The test going red on those charts again
means the service's floating pin moved backwards.

Neither was **worked around here**, and that is the standing rule. Aggregating the
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
the per-city partitions — changes the JOIN TYPE on the enrichment side
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
DASHBOARD_QUERY_ALL_CITIES=1 pnpm test:queries               # interactive states in every city
```

The default run is **34 queries for the all-cities view plus 39 per city**,
then 123 interactive (a species selection, and one cross-filter dimension —
nativeness, the one that reaches enrichment through the
`unnest(native_ecoregions)` merge, where both planner failures have lived).
That was 820 at seventeen cities, and it grows by 39 with each new one:
`ALL_CITIES` in `dashboardQueryCatalog.ts` is `Object.keys(CITY_CONFIG)`, so
adding a city to `cityConfig.json` enrols it here too.

**A city whose parquet is not on GCS yet is skipped, not failed.** The
execution harness takes each table's schema from the real Parquet, so a
just-added city would otherwise fail on a 404 footer read; instead the suite
prints `USDEN's tree parquet is not on GCS yet -- skipping its dashboard
queries` and carries on. That is what keeps a city-addition PR green before its
first credentialed build — and equally, a green run does not prove a brand-new
city's charts work. Re-run once its refresh has published.

Deduplicating buys nothing; all but a handful of request bodies are distinct,
because a city's context source and filters are part of the request. It needs
network: a live check against `https://trilogy-service.fly.dev`, whose
pytrilogy pin floats, so a failure with no local change means an upstream
release moved under us.

**Those hundreds of queries are ~21 requests, and that is the whole performance story.**
A lone `/generate_query` costs ~560ms against an idle resolver, of which ~375ms
is parsing the 43 preql sources in `ALL_MODEL_SOURCES` and only ~190ms is
planning. Paying that parse once per query was what made this suite feel like an
overnight job. `/generate_queries` takes one model plus a list of queries, each
with its own `extra_filters` and `parameters`, and hydrates the model once — so
the suite batches per (page state, imports) and finishes in about 70 seconds.

That comes to one request per `it` block today, because the summary and species
pages happen to declare the same two imports. The grouping still keys on imports
rather than assuming that: `imports` is a property of the whole batch in the
request schema, so a page that adds one has to be sent separately or it compiles
against the wrong scope.

It is the same planner reached the same way: a batch of one returns SQL
byte-identical to `/generate_query`, and a batch of many differs only in the
generated CTE names, which come off a per-response counter. A query that cannot
plan comes back inside a 200 with its own `error` and the rest of the batch
still returns SQL, so batching does not hide a failure or blur which chart
failed.

**`DASHBOARD_QUERY_CONCURRENCY` is a ceiling at 4, not a tuning knob.** The
service is a single shared instance whose throughput does not improve with
fan-out, so extra clients only queue. Measured on the same catalog against the
same deploy: four clients compile in 56s (1m12s wall); eight take 261s (4m29s
wall), with per-batch latency climbing from ~8s to 232s as the queue backs up.
Both pass. Raising it does not make the suite faster, it makes it four times
slower.

**The slow-after-load behaviour is Fly CPU throttling, not the planner.** The
service runs on a shared-CPU Fly instance with a burst quota: sustained
compiling drains it, and once drained every request is throttled until it
refills. That is the whole shape — a fast first sweep, a slow one right after,
and recovery from nothing but leaving it alone. It is being fixed on the service
side; until it is, the symptom is a property of the host, so **a slow run is not
evidence of a query regression**. Read the per-batch timings before concluding
anything: throttling makes every batch slow together, while a real regression
shows up as a failure, not a stall.

A September 2026 deploy improved the picture without removing the throttle:
eight clients no longer return HTTP 502 and the run passes at roughly twice the
old speed. But do not read a single good probe as "fixed" — a compile taken
immediately after each of the first two sweeps came back in 0.4s, and a third
sweep shortly afterwards ran past eight minutes and left the service at 92s per
compile. Quota, not state.

Practical consequences: **do not run this suite in a tight loop**, leave a few
minutes between sweeps when iterating on it, and expect the CI job to be
genuinely slow now and then. The suite retries a 5xx or a dropped connection
twice (that is the instance being unwell, not a verdict) and never retries a
200.

It also means CI's two resolver-touching jobs — `dashboard-queries` and `test`,
which carries `trilogy-smoketest.test.ts` and `dashboard-pushdown.test.ts` — are
part of each other's load, because they run concurrently. The first CI run with
both had `test` fail on an HTTP 502 and two 30s timeouts while the sweep passed.

They are kept concurrent and made survivable instead of serialised. Every
resolver call in all three suites goes through `src/src/tests/resolverFetch.ts`,
which retries a 5xx or a dropped connection with 2s/4s/8s backoff and never
retries a 200 — the transport is the instance being unwell, a 200 is a verdict.
Retrying costs time, so those tests carry a 120s timeout rather than 30s: under
throttling a single compile can take tens of seconds on its own, and 30s could
absorb neither the compile nor the backoff. A new suite that talks to the
resolver should use the same helper and budget.

`GET /health` is sub-second no matter how loaded the service is, so it tells you
nothing about compile latency. The only honest readout is a real compile against
the full model — `POST /generate_query` with `ALL_MODEL_SOURCES` — which is
~0.4-1.0s warm and tens of seconds when the service is unwell.
