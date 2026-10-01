# Testing

Commands are in `AGENTS.md`. This is what each suite proves and how to read
a red run.

## Frontend suites (from `src/`)

- `pnpm test`: vitest. Offline except `trilogy-smoketest.test.ts` and
  `dashboard-pushdown.test.ts`, which compile against the hosted resolver.
- `pnpm test:e2e`: Playwright. Its webServer runs `pnpm build:e2e`, a
  `--mode e2e` build that loads `src/.env.e2e` and compiles in the fixture
  seam `src/src/lib/e2eFixtures.ts`, so specs can seed an auth session and
  contribution history through `window.__treeE2E`. `VITE_E2E` is statically
  replaced, so a normal build contains none of it; check with
  `grep -c __treeE2E dist/assets/index-*.js`. Desktop and mobile coverage
  are viewport-parametrised describes in one spec file, not separate
  Playwright projects.
- `pnpm test:queries`: the dashboard query sweep below. Needs the network;
  gates every pull request as the `dashboard-queries` job in
  `.github/workflows/ci.yml`.
- `pnpm bench:chat`: the agent chat benchmark below. Run by hand.

## Map entry paths and load benchmark

On mobile, a map route without an explicit experience offers **Near Me** and
**Explore City**, before starting the map or requesting location. `city` only
sets the destination for Explore City; it never selects an experience. Explicit
`mode` and shared `tree` links open directly. Shared tree links record explore
mode so closing the card keeps the map open. Desktop opens the full city map without an experience chooser
or experience-switch buttons, including when a mobile nearby link is opened.
On mobile, the choices float in the center; map actions remain hidden through
location acquisition. Unknown city/mode parameters keep the chooser open.
Location denial, timeout, and missing coverage offer retry or Explore City.
Neither map experience shows entry-choice buttons once the map opens. Use zoom,
the city picker, and the existing **Find Me** control to navigate.
Explicit mobile routes use one `?` and `&` between parameters:

- `/#/?city=USBOS&mode=nearby`: ask for the viewer's location, resolve its city,
  then open at zoom 17. Location takes precedence over the city hint. Coordinates
  are never written into the link. Permission denial, timeout, unavailable
  location, and locations outside the supported city radius expose retry/explore.
- `/#/?city=USBOS&mode=explore`: the existing city view (zoom 13 on mobile,
  animated intro on desktop), with no location permission prompt.
- Shared tree destinations take precedence over nearby mode on entry, so opening
  someone else's tree link does not redirect to the recipient's city.

`e2e/map-experience.spec.ts` covers both viewports, the chooser, real rendered
trees near a supplied browser location, reload/back navigation, mode switching,
denial, timeout/retry, out-of-area handling, and late location cancellation.
`startup-city-resolution.spec.ts`, `find-me.spec.ts`, and `tree-deeplink.spec.ts`
cover the existing exploration paths.

For measurements, serve a normal `pnpm build` using
`pnpm preview --port 6173`, then run `pnpm bench:map` from another terminal.
This uses Playwright with a 390×844 viewport, a fixed Boston browser location,
three rounds per mode, alternating order, and both fresh contexts and reloads.
Results are saved to the gitignored `src/bench-results/map-load.json`.
Reload reuses browser caches but recreates DuckDB. A fresh context does not
flush OS, CDN, or network caches. Keep other browser tests idle while measuring.

Override `MAP_BENCH_URL` for a deployed site (include its base path),
`MAP_BENCH_CITY`, `MAP_BENCH_LAT`, `MAP_BENCH_LNG` for another mapped location,
and `MAP_BENCH_ROUNDS` for more samples. Coordinates must match the target city
for a fair comparison. The harness fails if it cannot find rendered trees;
choose a location with inventory rather than an empty block.

The browser Performance API exposes `trees:location` and
`trees:nearby:load` / `trees:explore:load`. Map load runs from component setup to
the tree source being loaded; it excludes location acquisition and does not
promise a rendered tree. The benchmark separately waits for rendered tree
features and records navigation-to-visible-trees, including location and startup.
No fixed millisecond CI threshold is imposed on live network data.

Nearby mode skips the intro, the initial 3.5× tile-range expansion, and background
LOD tile warming. **It still materializes the city parquet in DuckDB** and loads
shared species metadata. It does not claim neighborhood-only network transfer.
The next data experiment should compare spatially sorted row groups with small
spatial partitions, measuring cold bytes, peak memory, first visible trees,
tree-card latency, and panning across partition boundaries on a throttled phone.
Use a representative small city and a large inventory. A bounded SQL predicate
alone will not remove the existing full-city materialization cost. Keep the
published full-city tables for chat and analytics while testing a separate map
detail source; do not silently restrict their query scope to the neighborhood.

## The dashboard query sweep (`src/src/tests/dashboard-queries.test.ts`)

Every chart on the summary and species pages sends PreQL to the hosted
resolver and renders the SQL it gets back. The suite compiles the whole
catalog and then **executes the SQL** against fixtures, because a query can
compile and still answer the wrong question.

- Queries come from `dashboardQueryCatalog.ts`, derived from the same
  constants the views render, so a chart is covered as long as its query
  lives in `summaryDashboardConfig.ts` or `speciesDashboardConfig.ts`. It
  drives the real cross-filter controller.
- Execution uses `dashboardFixtures.ts` (nine trees, four species, four
  ecoregions, chosen so every cross-filterable dimension splits them) and
  `dashboardExecution.ts`, which builds one DuckDB table per parquet the SQL
  reads, taking the schema from the real parquet's footer. Expected numbers
  are derived from the fixtures by a reference implementation of the
  `dashboard_context` buckets. A per-city parquet is seeded with only that
  city's trees. Resolver `parameters` keys carry their leading colon.
- **Never work around a planner bug in a query.** File a self-contained
  repro under `upstream_repro/` (gitignored) and let the test stay red until
  the resolver's pytrilogy pin moves.
- Compiles go through `/generate_queries`, one request per (page state,
  imports), so the model is hydrated once per batch. A query that cannot
  plan comes back inside a 200 with its own error; a batch that fails as a
  block is transport.
- The default run covers the all-cities view, `REPRESENTATIVE_CITIES`
  (London, Boston, Milos, San Francisco), the cities whose model files the
  pull request touched (`DASHBOARD_QUERY_CITIES`, set by CI from the diff),
  and the interactive states. Every push to main sweeps `all`. A city whose
  parquet is not on GCS yet is skipped, not failed.

```bash
pnpm test:queries                                            # the default sweep
DASHBOARD_QUERY_CITIES=all pnpm test:queries                 # every city
DASHBOARD_QUERY_ALL_CITIES=1 pnpm test:queries               # every city, every interactive state
DASHBOARD_QUERY_CROSS_FILTERS=all|pairs pnpm test:queries    # every cross-filter dimension, or every pair
```

### Reading a red run

- A failure on specific queries is a regression or a planner change; the
  service's pytrilogy pin floats, so a failure with no local change means an
  upstream release moved.
- Every batch failing as a block with `HTTP 504: Request timed out after
  120s` means a batch has outgrown what the shared instance compiles inside
  the proxy limit. Model hydration cost grows with the number of preql
  sources (two per city). Time a single `/generate_query` against the full
  model on your branch and on main before re-running; the lever is batch
  size, not concurrency.
- `DASHBOARD_QUERY_CONCURRENCY` stays at 1. The resolver is one shared
  instance; fan-out only queues.
- Every resolver call goes through `src/src/tests/resolverFetch.ts`, which
  retries a 5xx or dropped connection with backoff and never retries a 200.
  Resolver-backed tests carry a 120s timeout and compile in `beforeAll`
  through the batch endpoint. `GET /health` says nothing about compile
  latency; only a real compile does.
- Do not run the sweep in a tight loop; leave a few minutes between sweeps.

## What the chat resolves against

| Screen | Call site | `imports` |
|---|---|---|
| Map | `compilePreQL` in `useChat.ts` | `MAP_CHAT_IMPORTS`: `tree_enrichment` + `tree_info` |
| Summary, species | `executeSummaryRunQuery` / `executeSpeciesRunQuery` | `SUMMARY_DASHBOARD_IMPORTS`: `tree_enrichment` + `dashboard_context` |

`tree_enrichment` is the species dimension only, so enrichment alone reaches
no tree datasource and every query returns HTTP 422.
`dashboard-pushdown.test.ts` compiles the real import lists and asserts that
a per-city dashboard and a city-filtered chat query resolve to that city's
own parquet, that only the all-cities view reads the rollup, and that a query
naming several cities reads the rollup or exactly those cities, never a
subset. `chat-publish.integration.test.ts` asserts the call site sends the
real list.

## The agent chat benchmark (`src/src/bench/`)

`pnpm bench:chat` runs every prompt in `src/src/composables/chatSuggestions.ts`
(the panel's suggestions, so a new one is benchmarked automatically), ten
rounds each, through the real chat loop: `useChat`'s prompts and tools, the
library's `runToolLoop`, `TrilogyResolver`, `QueryExecutionService`, and the
`DemoProvider` minting the public token. `nodeChatEngine.ts` stands in only
for what needs a browser (node DuckDB over the published parquets, a node
execution connection, plain refs for map and route state).

- **Only a `clean` round passes**: `return_to_user` reached with every tool
  call succeeding. A round that recovered from a rejected query is a
  `tool_error`, because the user would have seen the error.
- Results land in `src/bench-results/` (gitignored): a JSON with every
  round's tool calls, model responses and system prompt, and a markdown
  summary grouping error text by prompt and tool.
- The demo token is one $0.50 key per IP with an hour's TTL, about 190 model
  calls or 40-odd conversations. When spent, rounds score
  `budget_exhausted` and the rest `not_run`. `node src/bench/mergeRuns.mjs`
  folds every run into one table, keeping only rounds in which the model
  answered on the right screen. For a full sweep in one sitting pass
  `OPENROUTER_API_KEY`.
- Knobs: `CHAT_BENCH_ROUNDS`, `CHAT_BENCH_CASES` (case ids or id prefixes,
  e.g. `map-3,species`), `CHAT_BENCH_CITY`, `CHAT_BENCH_MODEL`,
  `CHAT_BENCH_FAIL_ON_ERRORS=1`. Run one sweep at a time.
- Each round records the tool names the model was offered; a summary round
  offering `publish_results` is a harness bug, not a model result.
- The array-membership idiom the prompts teach is `N in bloom_months`.
  `contains(bloom_months, N)` also plans on pytrilogy 0.3.359 and later, so
  once the hosted resolver carries that release either spelling works.
- `@trilogy-data/trilogy-studio-components` 0.1.25 ships a docs tool pack
  (`search_docs`, `read_doc`); its registry is not exported from the `./llm`
  entry yet.


## Badges, missions, and contribution refresh

`pnpm test:e2e e2e/badges.spec.ts e2e/missions.spec.ts e2e/contributions.spec.ts e2e/account-linking.spec.ts`
covers desktop/mobile badges, points, distinct city mission progress, target
links, biome selection, missing rankings, history beyond 50 visits and tab-return
refresh. Fixtures use the existing e2e-only auth/contributions seam; production
builds cannot select those fixtures.

`treeRankings.test.ts` executes the actual target-query SQL in DuckDB;
`test_tree_rankings.py` pins percentage boundaries, sentinels, ties, invalid
measurements and scheduling. CheckinDialog tests cover delayed/failed snapshots,
and the Firebase emulator suite tests their write shape.

Profile and My Contributions fetch on mount, auth changes, visible-tab return,
and the explicit Refresh button. Reads require a server response and each
collection updates independently, exposing lookup failures while preserving its
previous data. Badge history reads are no longer capped at 50. The public tree
counter still only increments once per user/tree per 20 hours; this is distinct
from personal check-in history. Cross-device history requires the same Account ID.


`useAuth.test.ts` covers anonymous-to-Google upgrades where Firebase mutates the
same User instance without another auth-state callback: popup and redirect
completion, the rendered Profile label, and credential-already-in-use errors.
The app keeps SDK users in a shallow ref and explicitly publishes completed
credentials, including notifying Vue when object identity and UID stay the same.

Mobile/desktop account-linking browser tests verify that an existing Google
profile collision is visibly explained and focused while the guest session and
its badges remain intact. The unit suite separately covers redirect-fallback
errors that previously escaped without setting the displayed error state.
The account-login alternative requires a warning confirmation before leaving
guest work behind. Browser tests cover cancelling that warning and switching to
an existing profile with different badges on mobile and desktop. Unit tests
verify that switching uses sign-in rather than linking, including its redirect
fallback, and never signs the guest out before Google sign-in succeeds.
