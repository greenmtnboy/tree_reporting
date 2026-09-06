/** @vitest-environment happy-dom */
// The catalog drives the real cross-filter controller so the filters and bind
// parameters match what the pages send; that pulls in the dashboard bundle,
// which touches window and Worker at import time.
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest'

// happy-dom has no Worker; the bundle constructs one for its SQL tooling at
// import time. Nothing here drives that Worker — the fixture DuckDB runs
// in-process through @duckdb/node-api — so a stub suffices.
vi.hoisted(() => {
  const globals = globalThis as { Worker?: unknown }
  globals.Worker ??= class {
    postMessage() {}
    terminate() {}
    addEventListener() {}
    removeEventListener() {}
  }
})
import { ALL_MODEL_SOURCES } from '../trilogyModels'
import {
  buildDashboardContextParameters,
  buildDashboardContextSource,
} from '../composables/dashboardContextSource'
import type { CityCode } from '../composables/useMapData'
import { cityTreeParquetUrl } from '../workers/parquetUrls'
import { FixtureDatabase, checkResult } from './dashboardExecution'
import { postToResolver } from './resolverFetch'
import {
  ALL_CITIES,
  cityCrossFilterCases,
  cityQueryCases,
  crossFilterFieldCombos,
  speciesSelectionCases,
  type CrossFilterMode,
  type DashboardQueryCase,
} from './dashboardQueryCatalog'

// Every dashboard query is compiled by the hosted resolver before the browser
// ever sees SQL, so a query that fails to plan fails at the resolver — which is
// what "trilogy generation error" in the UI means. This suite compiles the full
// catalog for every city, then runs the SQL it gets back against the fixtures
// in `dashboardFixtures.ts`, so both a failure to plan and a plan that answers
// the wrong question show up here rather than in prod.

// How many resolver requests are in flight at once. **One**, and that is a
// correctness setting rather than a performance one.
//
// The resolver is a single shared instance whose throughput does not improve
// with fan-out, so extra clients only queue -- that much was always true, and
// is why this was already capped rather than tuned (four finished the compile
// in 56s where eight took 261s, per-batch latency climbing from ~8s to 232s).
// What changed is the size of a batch. Model hydration is superlinear in the
// number of preql sources, and the bundle has grown with every city: measured
// against the live service with one identical trivial batch, 60 sources
// compile a single query in 2.1s and 39 of them in 54.6s, while 72 sources
// take 7.4s and 70.8s. A batch of *real* dashboard queries at 72 sources is
// 65-81s on its own.
//
// Fly's proxy gives up at 120s. So four such batches queued on one instance no
// longer merely take four times as long -- each request crosses the ceiling
// and comes back 504, and because a batch fails as a block that reads as "34
// dashboard queries failed" rather than as a timeout. Serially every batch
// fits with room to spare and the whole sweep finishes in 584s, which is
// *faster* than the 13-17 minutes the four-way runs took before failing.
//
// Raise this only if the resolver stops being a single shared instance.
const CONCURRENCY = Number(process.env.DASHBOARD_QUERY_CONCURRENCY ?? 1)

// The whole catalog is compiled once in beforeAll, so that is where the budget
// lives; the it() blocks only execute SQL against an in-process DuckDB.
//
// 25 minutes, sized for the *wide* sweep rather than the default one. The
// narrow run is five batches and finishes in a couple of minutes; the wide run
// is one request per city and grows with every city added -- at 21 cities on a
// loaded resolver it reached batch 22 of 25 at 866s.
//
// Give it room, because a hook that times out never registers its tests: the
// overrun is reported as "25 tests skipped", which reads like a suite that
// declined to run rather than one that ran out of budget.
const COMPILE_TIMEOUT_MS = 1_500_000
const EXECUTE_TIMEOUT_MS = 120_000

type CompiledCase = { sql?: string; error?: string }

/**
 * One it() block's worth of cases: a page state, for one city.
 *
 * Compilation is batched per group rather than per case. `/generate_queries`
 * takes one model plus a list of queries and hydrates the model once, which is
 * the entire reason this suite runs in a minute rather than in twenty: a lone
 * compile costs ~560ms against an idle resolver, of which ~375ms is parsing the
 * 43 preql sources in `ALL_MODEL_SOURCES` and only ~190ms is planning the
 * query. Paying that parse 820 times was the cost, not the planner.
 *
 * The endpoint is the same planner reached the same way — a batch of one
 * returns byte-identical SQL to `/generate_query`, and a batch of many differs
 * only in the generated CTE names, which come off a per-response counter.
 *
 * The whole default run is 820 queries in 21 requests.
 */
type QueryGroup = {
  /** Names the it() block, and keys its compiled results. */
  key: string
  label: string
  city: CityCode | null
  cases: DashboardQueryCase[]
}

type CompileFailure = {
  id: string
  error: string
}

async function compileBatch(
  city: CityCode | null,
  imports: DashboardQueryCase['imports'],
  cases: DashboardQueryCase[],
): Promise<Map<string, CompiledCase>> {
  // Results are matched back by label, so two cases sharing an id would quietly
  // answer for each other and halve the coverage rather than fail.
  const labels = new Set(cases.map((testCase) => testCase.id))
  if (labels.size !== cases.length) {
    throw new Error(
      `duplicate case ids in one batch for ${city ?? 'ALL'}: ` +
        cases
          .map((testCase) => testCase.id)
          .filter((id, index, all) => all.indexOf(id) !== index)
          .join(', '),
    )
  }

  const contextParameters = buildDashboardContextParameters(city)
  const body = {
    dialect: 'duckdb',
    full_model: {
      name: '',
      sources: [...ALL_MODEL_SOURCES, buildDashboardContextSource(city)],
    },
    imports,
    parameters: contextParameters,
    queries: cases.map((testCase) => ({
      query: testCase.query,
      label: testCase.id,
      extra_filters: testCase.filters,
      parameters: { ...contextParameters, ...(testCase.parameters ?? {}) },
    })),
  }

  const failAll = (error: string) =>
    new Map(cases.map((testCase) => [testCase.id, { error }] as const))

  // A per-query compile failure comes back inside a 200 (see below), so a
  // non-2xx means the batch itself was rejected and every case in it is
  // unanswered rather than failed. postToResolver retries that; it never
  // retries a 200.
  const result = await postToResolver('/generate_queries', body)
  if (!result.ok) return failAll(result.error)

  let parsed: { queries?: Array<{ label?: string; generated_sql?: string; error?: string }> }
  try {
    parsed = JSON.parse(result.text) as typeof parsed
  } catch {
    return failAll(`resolver returned a non-JSON body: ${result.text.slice(0, 600)}`)
  }

  // Results carry the label they were sent with, and a query that fails to plan
  // reports its own error while the rest of the batch still returns SQL.
  const byLabel = new Map<string, CompiledCase>()
  for (const entry of parsed.queries ?? []) {
    if (entry.label == null) continue
    byLabel.set(
      entry.label,
      entry.error
        ? { error: entry.error.slice(0, 600) }
        : entry.generated_sql
          ? { sql: entry.generated_sql }
          : { error: 'resolver returned no SQL and no error' },
    )
  }

  return new Map(
    cases.map(
      (testCase) =>
        [
          testCase.id,
          byLabel.get(testCase.id) ?? { error: 'resolver returned no result for this query' },
        ] as const,
    ),
  )
}

/** Run `tasks` with at most CONCURRENCY in flight, preserving nothing but effects. */
async function runPool(tasks: Array<() => Promise<void>>) {
  let cursor = 0
  async function worker() {
    while (cursor < tasks.length) {
      await tasks[cursor++]()
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, tasks.length) }, worker))
}

/**
 * Compile every group's cases, batched by (group, imports).
 *
 * `imports` is a property of the whole batch in the request schema, not of a
 * query in it, so cases declaring different imports cannot ride together — they
 * would compile against the wrong scope. The summary and species pages happen
 * to declare the same two imports today, which is why this comes to one request
 * per group; the grouping does not assume it.
 */
async function compileGroups(groups: QueryGroup[]) {
  const compiled = new Map<string, Map<string, CompiledCase>>()
  const batches: Array<{ group: QueryGroup; imports: DashboardQueryCase['imports']; cases: DashboardQueryCase[] }> = []

  for (const group of groups) {
    compiled.set(group.key, new Map())
    const byImports = new Map<string, DashboardQueryCase[]>()
    for (const testCase of group.cases) {
      const key = JSON.stringify(testCase.imports)
      const existing = byImports.get(key)
      if (existing) existing.push(testCase)
      else byImports.set(key, [testCase])
    }
    for (const cases of byImports.values()) {
      batches.push({ group, imports: cases[0].imports, cases })
    }
  }

  // One line per batch. The resolver's latency is the only thing that decides
  // how long this suite takes, and it varies by two orders of magnitude
  // depending on how recently the instance was hammered — without this, a slow
  // run is a silent ten-minute hang with no way to tell a degraded service from
  // a genuine regression.
  let done = 0
  const startedAt = Date.now()
  await runPool(
    batches.map((batch) => async () => {
      const batchStartedAt = Date.now()
      const results = await compileBatch(batch.group.city, batch.imports, batch.cases)
      const target = compiled.get(batch.group.key)!
      for (const [id, result] of results) target.set(id, result)
      done += 1
      const failed = [...results.values()].filter((result) => result.error).length
      console.log(
        `[${String(done).padStart(2)}/${batches.length}] ${batch.group.key} ` +
          `${batch.cases.length}q in ${((Date.now() - batchStartedAt) / 1000).toFixed(1)}s` +
          `${failed ? ` (${failed} failed)` : ''} ` +
          `— ${((Date.now() - startedAt) / 1000).toFixed(0)}s elapsed`,
      )
    }),
  )

  return compiled
}

/**
 * Run each compiled query against the fixtures and report what came back wrong.
 *
 * Compiling proves the planner produced SQL; running it proves the SQL answers
 * the question. A keyless join compiles and then evaluates the filter against
 * unrelated rows, so only the numbers catch it.
 */
async function executeGroup(
  group: QueryGroup,
  compiled: Map<string, CompiledCase>,
  database: FixtureDatabase,
): Promise<CompileFailure[]> {
  const failures: CompileFailure[] = []
  for (const testCase of group.cases) {
    const result = compiled.get(testCase.id)
    if (!result) {
      failures.push({ id: testCase.id, error: 'was never compiled' })
      continue
    }
    if (result.error || !result.sql) {
      failures.push({ id: testCase.id, error: result.error ?? 'no SQL' })
      continue
    }
    let rows: Array<Record<string, unknown>>
    try {
      rows = await database.run(result.sql, {
        ...buildDashboardContextParameters(group.city),
        ...(testCase.parameters ?? {}),
      })
    } catch (error) {
      failures.push({
        id: testCase.id,
        error: `executing the generated SQL failed: ${(error as Error).message}`,
      })
      continue
    }
    const mismatch = checkResult(testCase, rows)
    if (mismatch) {
      failures.push({ id: testCase.id, error: `wrong result against the fixtures: ${mismatch}` })
    }
  }
  return failures.sort((a, b) => a.id.localeCompare(b.id))
}

function formatFailures(city: CityCode | null, failures: CompileFailure[]) {
  const label = city ?? 'ALL'
  return [
    `${failures.length} dashboard quer${failures.length === 1 ? 'y' : 'ies'} failed for ${label}:`,
    ...failures.map((failure) => `  - ${failure.id}\n      ${failure.error.replace(/\n/g, '\n      ')}`),
  ].join('\n')
}

// Cross-filtered and species-selected states multiply the case count by ~20 per
// city, so the default run covers them for one city plus the all-cities view —
// every city still gets its base state above. DASHBOARD_QUERY_ALL_CITIES=1
// widens the interactive states to all seventeen; worth doing when a city model
// changes shape or a planner regression is suspected.
const INTERACTIVE_CITIES: Array<CityCode | null> = process.env.DASHBOARD_QUERY_ALL_CITIES
  ? [null, ...ALL_CITIES]
  : [null, 'USSFO']

// Cities whose *model* is not shaped like the others, and so are the ones whose
// base sweep proves something the rest do not.
//
// Every city sends the same catalog with the same imports; what differs is the
// `dashboard_context` constants and a `city = 'X'` filter. So compiling all of
// them re-proves the same plan once per city -- and it costs a whole request
// each, because batching is per (city, imports). A request re-hydrates the
// model before it plans anything: ~375ms of a ~560ms warm compile is parsing
// the preql sources, and there are now ~49 of them. Trimming queries per city
// would not remove a single request; trimming cities removes them wholesale,
// which is the only thing that moves this suite's runtime.
//
// What the default keeps is every city that is structurally its own case:
//
//   GBLON  the only city declaring a column the others do not (`borough`), on
//          all three of its partitions
//   USBOS  four municipal partitions and the only species-keyed aggregate (its
//          dbh imputation), which is what forced `?species` to be nullable
//   GRMLO  community-only: no municipal source at all, and the single-partition
//          completeness proof that goes with it
//   USSFO  heads the rollup's file list and is already the interactive city
//
// plus the all-cities view, which is the union rather than a partition and is
// the plan most likely to break on its own. Everything else is a city whose
// model `new_city.py` generated from the same template.
//
// This is a coverage trade and it is worth naming: a city outside this set can
// break its own model and go green here. Two things cover that. A change that
// touches any city model or the city config widens the run to every city (see
// the `dashboard-queries` job in ci.yml), which is exactly the city-addition
// case where this suite earns its keep; and every push to main sweeps all of
// them regardless. `DASHBOARD_QUERY_ALL_CITIES=1` forces the wide run by hand.
const REPRESENTATIVE_CITIES: CityCode[] = ['USSFO', 'USBOS', 'GBLON', 'GRMLO']

// `DASHBOARD_QUERY_CITIES` adds cities to that default: a comma-separated list
// of codes, or `all`. CI fills it from the diff, so a pull request that edits
// three city models sweeps those three plus the representatives rather than all
// twenty-one -- the cities it changed are the ones whose plans could have moved.
//
// It is deliberately separate from DASHBOARD_QUERY_ALL_CITIES, which widens the
// *interactive* states too (a species selection and a cross-filter per city) and
// takes the run from 25 batches to 60. That is the diagnostic sweep, not the
// city-coverage one, and conflating them is how this job first came back with 60
// batches when 25 were intended.
function requestedCities(): CityCode[] {
  const raw = (process.env.DASHBOARD_QUERY_CITIES ?? '').trim()
  if (!raw) return []
  if (raw.toLowerCase() === 'all') return [...ALL_CITIES]
  const known = new Set<string>(ALL_CITIES)
  return raw
    .split(',')
    .map((code) => code.trim().toUpperCase())
    .filter((code): code is CityCode => known.has(code))
}

const BASE_CITIES: Array<CityCode | null> = process.env.DASHBOARD_QUERY_ALL_CITIES
  ? [null, ...ALL_CITIES]
  : [
      null,
      ...new Set([
        ...REPRESENTATIVE_CITIES.filter((city) => ALL_CITIES.includes(city)),
        ...requestedCities(),
      ]),
    ]

// Which cross-filter states to exercise. The default is the one dimension that
// reaches enrichment through the unnest+merge axis; `all` sweeps every
// dimension and `pairs` every two-dimension combination, which is where the
// stacked-click failures live.
const CROSS_FILTER_MODE = (process.env.DASHBOARD_QUERY_CROSS_FILTERS ?? 'default') as CrossFilterMode

// A city whose parquet is not on GCS *at all* is the one carve-out, the same
// one parquetSchema.test.ts makes and for the same reason: a brand-new city has
// nothing published until the pipeline's first credentialed build, and the
// execution half of this suite seeds its fixture tables from the real parquet's
// footer, so every one of that city's cases would fail on the 404 for the whole
// life of the city-addition PR. Compilation still proves nothing here, so the
// city is skipped loudly rather than reported green. Any other status is left
// to fail: a parquet that exists and cannot be read is exactly what this suite
// is for.
async function cityParquetIsPublished(city: CityCode): Promise<boolean> {
  const url = cityTreeParquetUrl(city)
  if (!url) return true
  try {
    const head = await fetch(url, { method: 'HEAD' })
    return head.status !== 404
  } catch {
    // A network failure is not "unpublished" — let the run report it.
    return true
  }
}

async function citiesAwaitingFirstBuild(): Promise<Set<CityCode>> {
  const unpublished = await Promise.all(
    ALL_CITIES.map(async (city) => ((await cityParquetIsPublished(city)) ? null : city)),
  )
  const skipped = new Set(unpublished.filter((city): city is CityCode => city !== null))
  for (const city of skipped) {
    console.warn(
      `${city}'s tree parquet is not on GCS yet -- skipping its dashboard queries. ` +
        'Run the Trilogy refresh for this city and re-run to cover it.',
    )
  }
  return skipped
}

/** Every it() block this run will register, in the order they are declared. */
function buildGroups(): QueryGroup[] {
  const groups: QueryGroup[] = []

  for (const city of BASE_CITIES) {
    groups.push({
      key: `base:${city ?? 'ALL'}`,
      label: `compiles and runs every summary and species query for ${city ?? 'all cities'}`,
      city,
      cases: cityQueryCases(city),
    })
  }

  for (const city of INTERACTIVE_CITIES) {
    groups.push({
      key: `species:${city ?? 'ALL'}`,
      label: `compiles and runs every query under a species selection for ${city ?? 'all cities'}`,
      city,
      cases: speciesSelectionCases(city),
    })

    for (const fields of crossFilterFieldCombos(CROSS_FILTER_MODE)) {
      // Built at collection time so a dimension that cannot split the fixtures
      // in this city's context (nativeness in the all-cities view, where there
      // is no ecoregion to compare against) drops out of the test list instead
      // of registering a test that asserts nothing.
      const cases = cityCrossFilterCases(city, fields)
      if (cases.length === 0) continue
      groups.push({
        key: `cross:${fields.join('+')}:${city ?? 'ALL'}`,
        label: `compiles and runs every query under a ${fields.join(' + ')} cross-filter for ${city ?? 'all cities'}`,
        city,
        cases,
      })
    }
  }

  return groups
}

const GROUPS = buildGroups()

describe('dashboard query compilation', () => {
  // One DuckDB for the whole suite: the seeded tables are keyed by the Parquet
  // URL the SQL reads and are read-only once created, so sharing them across
  // cities saves a footer read per city per table. Which fixture rows a table
  // gets is decided by the URL, not by whoever asked for it first.
  let database: FixtureDatabase
  let compiled = new Map<string, Map<string, CompiledCase>>()
  let skipped = new Set<CityCode>()

  beforeAll(async () => {
    skipped = await citiesAwaitingFirstBuild()
    database = await FixtureDatabase.create()
    compiled = await compileGroups(
      GROUPS.filter((group) => group.city === null || !skipped.has(group.city)),
    )
  }, COMPILE_TIMEOUT_MS)

  afterAll(() => {
    database?.close()
  })

  for (const group of GROUPS) {
    it(
      group.label,
      async () => {
        if (group.city !== null && skipped.has(group.city)) return
        expect(group.cases.length).toBeGreaterThan(0)
        const failures = await executeGroup(group, compiled.get(group.key) ?? new Map(), database)
        expect(failures, failures.length ? formatFailures(group.city, failures) : '').toEqual([])
      },
      EXECUTE_TIMEOUT_MS,
    )
  }
})
