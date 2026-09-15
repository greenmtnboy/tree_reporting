# Testing the frontend, the dashboard queries and the chat

Commands are listed in `AGENTS.md`; this file is the reasoning behind each
suite, what it has caught, and how to read a red run.

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

### What the chat resolves against

The chat compiles PreQL through a different path on each screen, and the
`imports` it sends decide which parquet the browser downloads — or whether the
query resolves at all.

| Screen | Call site | `imports` |
|--------|-----------|-----------|
| Map | `compilePreQL` in `useChat.ts` | `MAP_CHAT_IMPORTS` (`chatModelImports.ts`): `tree_enrichment` + `tree_info` |
| Summary, species | `executeSummaryRunQuery` / `executeSpeciesRunQuery` | `SUMMARY_DASHBOARD_IMPORTS`: `tree_enrichment` + `dashboard_context`, which imports `tree_info` itself |

Both reach the same pair, and both must: `tree_enrichment` is the species
dimension only, so **enrichment alone reaches no tree datasource**. The map
chat shipped that way once — the literal `[tree_enrichment]` was correct while
`tree_enrichment.preql` still imported `tree_info`, and when enrichment became
species-only the fix landed in `dashboardContextSource.ts` and missed the map
chat. Every `run_query` and `publish_results` then returned an HTTP 422 from
the resolver ("No datasource exists for root concept local.tree_id"), surfaced
in the UI as a compile error. Nothing caught it: the chat integration test
mocks `resolve_query`, and each resolver-backed suite declared its own import
list rather than the app's. `dashboard-pushdown.test.ts` now compiles the
real `MAP_CHAT_IMPORTS`, and `chat-publish.integration.test.ts` asserts the
call site still sends it.

**A query naming two or three cities reads the rollup, and that is expected.**
One city matches a `complete where city = 'X'` partition; two match none, so
`tree_info.preql`'s unfiltered rollup datasource is the only single source
that covers the predicate and the planner takes it. Take the rollup out of
scope and the same planner emits a `UNION ALL` over exactly the cities named
(pruned — 41 city models in scope, a two-city query still reads two parquets),
so preferring the union for a bounded set of cities is a pytrilogy-side
improvement. Do not chase it by narrowing what the browser imports: the import
list would then have to be derived from the query text, and the all-cities
view still wants the one-file read. What the test pins is only that the answer
is *complete* — the rollup, or exactly the cities named, never a subset.

### The agent chat benchmark

`pnpm bench:chat` (from `src/`) runs every prompt the chat panel suggests --
`MAP_SUGGESTIONS`, `SUMMARY_SUGGESTIONS` and `SPECIES_SUGGESTIONS` in
`src/src/composables/chatSuggestions.ts`, so a new suggestion is benchmarked
without touching the harness -- ten rounds each, through the **real** chat
loop: `useChat`'s system prompts and `executeTool`, the library's
`runToolLoop`, `TrilogyResolver` and `QueryExecutionService`, and the
`DemoProvider` minting the same public token a visitor gets. Only what needs a
browser is stood in for (`src/src/bench/nodeChatEngine.ts`): the DuckDB worker
becomes node DuckDB over the same published parquets, the summary page's
WASM connection becomes a node `ExecutionConnectionProvider`, and the map,
route and landmark state are plain refs. The driver is
`src/src/bench/chat-benchmark.test.ts`; read its header for the verdicts.

**Only a `clean` round passes**: `return_to_user` reached with every tool
call succeeding. A round where the model wrote a query the resolver rejected
and then fixed it is a `tool_error`, because the suggestions are the prompts
we tell users to try and a visible compile error is what they would have
seen. The report groups the error text by prompt and tool, which is what
points at a prompt or schema fix; a JSON with every round's tool calls, model
responses, system prompt and the app's own console output lands in
`src/bench-results/` (gitignored) beside a markdown summary.

It spends the per-IP demo budget and the shared resolver's time, so it is
not part of `pnpm test` and has its own config. **The demo token is one
$0.50 key per IP with an hour's TTL**, and a chat turn carries an ~11 KB
system prompt plus the tool schemas on every model call, so the budget is
about 190 model calls -- 40-odd conversations, or a third of a full sweep.
When it is spent every call answers 403; the harness scores that round
`budget_exhausted`, marks the rest `not_run`, and reports rates over the
rounds that reached the model. The first full sweep (September 2026) covered
the four map prompts and ran out on the fifth; the summary prompts were
measured in the next hour's windows with `CHAT_BENCH_CASES=summary`, and the
species prompts the following day with `CHAT_BENCH_CASES=species`;
`node src/bench/mergeRuns.mjs` folds every run in `bench-results/` into one
table, keeping only rounds in which the model answered at least once on the
right screen -- a round the transport never delivered says nothing about
the prompt. To do
a sweep in one sitting, pass `OPENROUTER_API_KEY` (your key, same model).
Other knobs: `CHAT_BENCH_ROUNDS`, `CHAT_BENCH_CASES` (case ids or id
prefixes -- not prompt text, which is how `species` once selected the
summary page's "top 5 species" prompt and spent a window on it),
`CHAT_BENCH_CITY`, `CHAT_BENCH_MODEL`, and `CHAT_BENCH_FAIL_ON_ERRORS=1` to
make any non-clean round red. Run one sweep at a time: two share the same
key, and a waiter left running once doubled up on a window.

What the first sweep found on the map screen: three of the four prompts were
clean ten times out of ten, and "Show me trees in bloom right now!" was clean
zero times. The map system prompt advertises a `bloom_season (string)`
concept that does not exist -- the enrichment model has `bloom_months
list<int>` -- and offers no idiom for array membership, so every round spent
three to eight failed compiles (`contains`, `array_to_string`, `array_filter`
with a lambda, `IN UNNEST`) before finding one that planned, at 8 model calls
and 25 s a round against 2-3 calls for the prompts that work. That is a
prompt fix, and the benchmark is how to prove it landed. On the summary
page three prompts were clean every time and "What share of the city is
concentrated in the top 5 species?" was clean 17 times in 29: the model
reaches for a `FROM (subquery)` or an `ORDER BY` on a rank it did not
project, both of which Trilogy rejects with a message that names the fix,
so a second or third compile usually lands but the user has seen an error.
The four species prompts were clean in every round that reached the model
(39 of 39), all through `set_species_filters` and `inspect_species_view`.

Both failures were the prompt, and both fixes are in `useChat.ts`. The map
prompt's enrichment block was a copy written against an earlier table
(`bloom_season`, `native_status`, `mature_height_ft`, the `species ::
common name` convention); it now shares `ENRICHMENT_CONCEPTS` with the
summary prompt, and the `bloom_months` line gives the idiom
`N in bloom_months` with this month's number. The summary prompt
describes `dominance_rank` and `cumulative_tree_share_pct` and quotes the
Top 5 Share chart's own one-line query, plus the rule that an ORDER BY or
HAVING field must be selected (hidden with `--`). Re-run on the same
afternoon, the top-5 prompt went from 17 clean in 29 to 10 in 10 and the
bloom prompt from 0 in 10 to 10 in 10, at 2.9 model calls a round instead
of 8.3. The membership idiom is `in` rather than
`contains` because that is what the hosted resolver compiles today:
`contains(bloom_months, N)` plans on **pytrilogy 0.3.359**, where `contains`
was extended to arrays, and the service still answered `Invalid argument
type 'ArrayType<INTEGER>' passed into CONTAINS` on 2026-09-15. Once the
service's pin has moved, either spelling is fine and the prompt can say so. `@trilogy-data/trilogy-studio-components`
0.1.25 adds a docs tool pack (`search_docs`, `read_doc`) the model could
consult instead of guessing syntax; its `ToolRegistry` is not on the `./llm`
export yet, so wiring it here waits on the library.

**The harness once measured the wrong screen, and the tell was in the
transcript.** The route mock's ref was created when the mock factory first
ran and survived `vi.resetModules()`, so every summary and species prompt
ran with the map screen's prompt and tools; the summary rounds "failed" on a
`native_status` concept only the map prompt describes. Each round now records
the tool names the model was offered, and a summary round offering
`publish_results` is a harness bug, not a model result.

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
many cities the map has. The next data point agrees: Bogotá, Taipei,
Copenhagen and Helsinki took the bundle to **92 sources**, and the default
sweep ran its eight batches in **7.6-17.7s each, 83s total** on the afternoon
they were added (the four new cities were skipped as not yet on GCS, so that
is the model cost alone).

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
