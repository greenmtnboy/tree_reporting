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
