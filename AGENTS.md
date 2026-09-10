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
`refresh-enrichment`, `refresh-predictions`, `validate-core`,
`refresh-ecoregions`, plus weekly `refresh-landmarks`) that reads only
published parquets.

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
- **A consumer of published data reaches it through a `_source.preql`.**
  `raw/full_tree_info_source.preql` and `raw/tree_enrichment_source.preql`
  are root (unmanaged) views of the rollup and the enrichment table, named
  the same as the managed datasources that write them; that shared name is
  the derived edge trilogy-cloud orders a tick by. `raw/tree_predictions.preql`
  imports both and nothing else, which is what makes `refresh-predictions`
  run after its two producers and never rebuild either. Neither source file
  may enter the frontend's model bundle (`test_tree_predictions.py`).
- **The core validates what it published.** `raw/core_validate.preql` runs
  `validate datasource` over the rollup and the enrichment table after both
  land, and fails the tick on a repeated key. A refresh proves what it
  builds, not what it built: the rollup carried 23,078 OSM ids twice, one per
  neighbouring city, and every city's own parquet was clean.
- **An unattributed tree is assigned by territory, not by sanity box.**
  `CITY_BOUNDS` is the generous box a municipal row must fall in;
  `CITY_TERRITORY` (both in `_ingest_shared.py`) is the explicit,
  non-overlapping set of rectangles that decides which city an OSM node or a
  community submission belongs to. `test_city_territory.py` checks every
  pair; a city that gains a neighbour has to carve both territories.
- **A missing job is silent.** Nothing errors when a city has no schedule; its
  parquet simply stops updating. `test_cloud_jobs.py` is the only thing that
  catches it, so run `cd data/raw && uv run --with pytest python -m pytest tests -q`
  after touching the job table.

### Tree-level predictions

`raw/tree_predictions.preql` publishes `tree_predictions_v{n}.parquet`: one
row per rollup tree with a predicted crown width, a stand-density covariate,
and null placeholders for height and age. The crown model is a genus-level
power law fitted on the open Tallo database by `raw/crown_allometry_fit.py`,
committed as `raw/crown_width_coefficients.csv` and applied in DuckDB (no
script at refresh time but the freshness probe). Read the fit script's
docstring before touching the model: it records why Tallo, the quality gate,
the fallback order, and the two things the model deliberately is not
(urban-calibrated, density-adjusted). The map does not read this parquet yet;
see "Tree-level predictions" in `EXTENDING.md` for the refit and the shape.

### Correcting a species by hand

`data/raw/enrichment_admin.py` is a localhost form over the enrichment table
for the case where a reviewer already knows the answer -- a photo of the wrong
plant, a description of the wrong taxon, a trait that is off -- and re-asking
the model (`backfill_enrichment.py`) is the long way round. Edits are staged
locally; **Publish** re-reads the live parquet, patches the staged rows in
(and every alias row of the taxon), uploads, and reads it back to verify, the same
path `tree_enrichment.py --limit` takes. An edited row carries today's
`enriched_at`, which is what keeps `refresh-enrichment` from overwriting it,
and the form refuses a row without a common name and a growth form because
the freshness probe would report the table stale for ever. Sentinel rows are
authored in `_ingest_shared.py` and are read-only there, as is the alias row
of a name in `SPECIES_SYNONYMS` or `SPECIES_MISSPELLINGS` (the daily job
rewrites it from the accepted row). A duplicate species is merged from the
form by adding its name to the accepted row's `synonyms`; making the *ingest*
publish the accepted name takes a pair in `SPECIES_SYNONYMS` when Kew lists
one name under the other, and in `SPECIES_MISSPELLINGS` when the duplicate is
a name that does not exist -- `species_audit.py` decides which, and see
"Synonyms" and "Misspellings" in `EXTENDING.md`. Needs
`gcloud auth application-default login`; `tests/test_enrichment_admin.py`
pins the invariants. arborary.world shows the change on its next build.

### Adding a city

Do not hand-write the twenty-odd registry edits. `data/raw/new_city.py` writes
the mechanical ones from a single spec and `data/raw/tests/test_city_wiring.py`
walks the same list and names anything still missing — the enum, the ecoregion
case, four sets of freshness properties, the cross-city imports and merges, the
rollup file list, the frontend config, the attribution. Almost every one of
those fails *silently* when it is skipped, which is why the sweep exists.
See the quick path at the top of `EXTENDING.md`.

Shared modules carry what used to be copied per city, and a new city should
reach for them before writing anything:

- **`_osm_shared.py`** — the Overpass extraction every city's `osm-{code}` job
  runs, so a city's OSM wiring is one ~28-line shim.
- **`_arcgis_shared.py`** — layer paging, three freshness watermarks, Esri's
  epoch-milliseconds, field domains, geometry-to-WKT, and a Hub catalogue
  search (`uv run _arcgis_shared.py <hub-host>` lists a portal's tree layers).
  ArcGIS is what most North American cities publish on. Three details in it are
  correctness rather than convenience and were bugs in the copies it replaced:
  the page size comes from the layer's own `maxRecordCount` (asking for more is
  silently capped, and a capped page reads as the end of the data), paging
  terminates on `exceededTransferLimit` rather than the short-page heuristic,
  and `esri_point` refuses the *string* `"NaN"` that a server sends for a
  feature with no geometry.
- **`_socrata_shared.py`** and **`_ckan_shared.py`** — the same for the other
  two platforms this repo reads more than twice.
- **`_common_name_species.py`** — a curated common-name → accepted-binomial
  table, for the portals that publish an English name where the binomial should
  be. Not a platform module: what those cities shared was a question, not an
  API. Read its docstring before reaching for the enrichment table's inverse
  instead — that was tried, measured, and does not work, because the enrichment
  table carries the misspelled binomials the cities themselves published.

**Read a field's domain before deciding what a column holds.**
`coded_value_domain(layer, field)` has now answered three questions the column
names could not: Halifax's `DBH` is a size class whose bands the layer
publishes, Ajax's species symbols are named in English there, and Ottawa's
`SPECIES` — which stores `Maple Sugar`, `Oak Red` and reads exactly like a
common-name-only column — maps all 174 of its codes to the binomial. A layer
that looks like it does not identify its trees may be keeping the
identification in `fields[].domain`.

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

### Which cities the sweep covers

**The default run does not compile every city, and the reason is that a city is
a whole request.** Batching is per `(city, imports)`, and a request re-hydrates
the model before it plans anything — ~375ms of a ~560ms warm compile is parsing
the preql sources, of which there are now ~49. Every city sends the *same*
catalog with the same imports, differing only in the `dashboard_context`
constants and a `city = 'X'` filter, so compiling all of them re-proves one plan
once per city at full price. Trimming queries per city would not remove a single
request; trimming cities removes them wholesale. (The CI timings bear this out:
`39q` batches ranged 46.9s to 467s while a `34q` batch took 206s and a `40q` one
51s — query count does not predict the time.)

So the default is the all-cities view plus `REPRESENTATIVE_CITIES` in
`dashboard-queries.test.ts` — the cities whose *model* is its own case: London
(the only extra column, `borough`), Boston (four municipal partitions and the
only species-keyed aggregate), Milos (community-only, no municipal source) and
San Francisco (heads the rollup file list, and the interactive city). Then 123
interactive cases (a species selection, and one cross-filter dimension —
nativeness, the one that reaches enrichment through the
`unnest(native_ecoregions)` merge, where both planner failures have lived).

**That is a coverage trade, and it is bounded by the diff.** The
`dashboard-queries` job reads which `*_tree_info.preql` and `*_landmarks.preql`
files a pull request touched, and passes those city codes in
`DASHBOARD_QUERY_CITIES`; the suite adds them to the representatives. A city's
model is what decides its plan, so the cities a change could have broken are the
ones it edited — a three-city addition sweeps ten cities, not twenty-one. Every
push to main sweeps `all`, which backstops anything a narrow run let through.

`DASHBOARD_QUERY_CITIES` takes a comma-separated list or `all`, and is
deliberately **not** `DASHBOARD_QUERY_ALL_CITIES`: that one also widens the
interactive states (a species selection and a cross-filter per city) and takes
the run from 25 batches to 60. It is the diagnostic sweep, for when a planner
regression is suspected, not the city-coverage one.

The wide run is still 34 queries for the all-cities view plus 39 per city, and
it still grows by 39 with each new one: `ALL_CITIES` in
`dashboardQueryCatalog.ts` is `Object.keys(CITY_CONFIG)`, so adding a city to
`cityConfig.json` enrols it. **In wall-clock that is about 66 seconds per
city**, and it is the number to do arithmetic with before adding several.

Its budget is sized for that — `COMPILE_TIMEOUT_MS` and `hookTimeout` are 55
minutes and the job allows 60 — and it has now been raised twice for the same
reason, which is what a budget sized to today's fleet does. 21 cities overran
the original 15/20 at batch 22 of 25 and reported it as "25 tests skipped" (a
hook that times out never registers its tests, so a budget overrun does not
look like one). 31 cities then overran the 25/30 that replaced it, at batch 22
of 29, with every batch compiling healthily in 63-73s and nothing wrong except
that there were more of them than would fit — **and that failure lands on
`main`, not on the pull request**, because a PR sweeps a handful of cities and
a push to main sweeps `all`. A city-addition PR can be green and still take
main's sweep red on merge; check the arithmetic, not just the PR.

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

**`DASHBOARD_QUERY_CONCURRENCY` is 1, and that is correctness rather than
performance.** The service is a single shared instance whose throughput does
not improve with fan-out, so extra clients only queue -- that was always true,
and is why this was a capped ceiling rather than a tuning knob (four clients
compiled in 56s where eight took 261s, per-batch latency climbing from ~8s to
232s as the queue backed up; both still passed).

What changed is the size of a batch, and the cause is upstream: **model
hydration is superlinear in the number of preql sources**, and the bundle grows
with every city. Measured against the live service with one identical trivial
batch, 60 sources compile a single query in 2.1s and a 39-query batch in 54.6s;
72 sources take 7.4s and 70.8s. A batch of *real* dashboard queries at 72
sources is 65-81s on its own -- so a 20% bigger model costs 3.5x on a single
compile.

Fly's proxy gives up at 120s, so four such batches on one instance no longer
merely take four times as long: each request crosses the ceiling and returns
504. **That failure is easy to misread.** A batch fails as a block, so it
surfaces as "34 dashboard queries failed for ALL" with every entry reading
`HTTP 504: Request timed out after 120s` -- which looks exactly like the
service being unwell, and is instead the model having outgrown the request.
The tell that it is not mere overload: a single `/generate_query` against the
same model still returns in seconds. Serially every batch fits with room to
spare and the whole sweep finishes in 584s, *faster* than the 13-17 minutes the
four-way runs took before failing.

Raise it only if the resolver stops being a single shared instance. If batches
start breaching 120s serially, the model has grown again and the lever is batch
*size*, not concurrency.

**Watch the headroom, because it is a city or two wide.** The five cities added
in September 2026 took `ALL_MODEL_SOURCES` from 72 preql sources to 82, and the
default sweep now runs eight batches at **79-97s each** against a warm resolver
-- passing, 584s total, and within 20% of the 120s ceiling on the worst batch.
A single trivial `/generate_query` measured 1.8-2.8s at 72 sources and
2.1-3.4s at 82 on the same afternoon, so the cost is currently tracking the
source count rather than compounding. That is the number to re-measure before
the next city addition: the model grows by two sources per city (a tree model
and a landmark model), and the batch that breaks the ceiling will do it the way
the Canadian ArcGIS batch did -- as "34 dashboard queries failed" with every
entry reading `HTTP 504`, which looks like the service being unwell and is not.

**But re-measure it rather than doing the arithmetic, because that 79-97s is
mostly instance load.** Adding Tokyo took the bundle to 84 sources, and the
same default sweep then ran its eight batches in **5.5-13.5s each, 70s total**
-- roughly seven times faster at *more* sources, on a quiet afternoon. Both
numbers are real and neither is a property of the source count alone, so the
"within 20% of the ceiling" reading above is the busy end of the range and not
a standing fact. Time a sweep on your branch before concluding a city cannot
be afforded; and note that the arithmetic pointing at a hard ceiling is an
argument for fixing the per-request re-hydration upstream, not for capping how
many cities the map has.

**A slow run is not evidence of a query regression.** The service runs on
high-performance Fly instances, but it is still **one shared instance and it
can be overloaded**: enough concurrent compiling and requests come back 502, or
504 once Fly's proxy gives up at 120s, or simply take tens of seconds instead of
~0.5s.

So read the per-batch timings before concluding anything. Overload makes every
batch slow together; a real regression shows up as a failure on specific
queries, not a stall across all of them. The clearest tell is whether a batch
fails as a *block* — 39 queries failing together is transport, because a query
that genuinely cannot plan comes back inside a 200 carrying its own error while
the rest of the batch still returns SQL.

**But "transport" does not mean "not our change".** Those two are easy to
conflate and were, on the pull request that added the six Canadian ArcGIS
cities: every batch 504'd as a block, main's own CI was green, and the job was
re-run twice on the reading that the shared instance was merely unwell. It was
unwell *because the branch had grown the model past what a batch could compile
inside the proxy limit* — both halves were true at once. Before re-running,
time a single `/generate_query` against the full model on your branch and on
main; if the branch is materially slower, the model is the cause and a re-run
will not help. See the concurrency note above.

Two things bound the load rather than leaving it to chance. `ci.yml` has a
concurrency group, so a superseded pull-request run is cancelled instead of
piling on — without it, four pushes in quick succession put eight
resolver-backed jobs on one instance and every request 504s. And the default
sweep compiles a handful of representative cities rather than every one, since
each city is a whole request and they all send the same queries; see "Which
cities the sweep covers" below.

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

**A timeout is a budget, and a per-test one is the wrong place to keep it.**
`dashboard-pushdown.test.ts` used to compile eight queries one at a time inside
each `it`, and under CI throttling one city took 52s, another 45s, and the third
went past 120s and failed — the same eight compiles, on the same commit, at the
mercy of when the quota happened to drain. Both halves of the fix are the ones
the sweep already uses: compile through `/generate_queries` so a group's eight
queries are one request that parses the model once, and hoist the compiling into
`beforeAll` under one large budget so the `it` blocks only assert on what came
back. That suite is now 26 queries in 5 requests, and a slow resolver makes it
slow rather than red.

`GET /health` is sub-second no matter how loaded the service is, so it tells you
nothing about compile latency. The only honest readout is a real compile against
the full model — `POST /generate_query` with `ALL_MODEL_SOURCES` — which is
~0.4-1.0s warm and tens of seconds when the service is unwell.
