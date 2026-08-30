/** @vitest-environment happy-dom */
// The catalog drives the real cross-filter controller so the filters and bind
// parameters match what the pages send; that pulls in the dashboard bundle,
// which touches window and Worker at import time.
import { describe, it, expect, vi } from 'vitest'

// happy-dom has no Worker; the bundle constructs one for its SQL tooling at
// import time. Nothing in this suite executes SQL locally, so a stub suffices.
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
const TRILOGY_RESOLVER_URL = process.env.TRILOGY_RESOLVER_URL ?? 'https://trilogy-service.fly.dev'

// The resolver is a shared single instance whose throughput scales sublinearly:
// measured over 1/2/4/8 clients it went 0.04 -> 0.13 compiles/s while per-request
// latency grew 28s -> 43s. Fanning out wider does finish sooner, but not much,
// and it leaves the service slow for everyone else — including the next run.
// The slowness is an upstream issue, tracked there rather than tuned around.
const CONCURRENCY = Number(process.env.DASHBOARD_QUERY_CONCURRENCY ?? 4)
const CITY_TIMEOUT_MS = 900_000

type CompileFailure = {
  id: string
  error: string
}

async function compileAndRun(
  testCase: DashboardQueryCase,
  city: CityCode | null,
  database: FixtureDatabase | null,
): Promise<CompileFailure | null> {
  const body = {
    query: testCase.query,
    dialect: 'duckdb',
    full_model: {
      name: '',
      sources: [...ALL_MODEL_SOURCES, buildDashboardContextSource(city)],
    },
    imports: testCase.imports,
    extra_filters: testCase.filters,
    parameters: { ...buildDashboardContextParameters(city), ...(testCase.parameters ?? {}) },
  }

  let response: Response
  try {
    response = await fetch(`${TRILOGY_RESOLVER_URL}/generate_query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch (error) {
    return { id: testCase.id, error: `request failed: ${(error as Error).message}` }
  }

  const text = await response.text()
  if (!response.ok) {
    // The resolver reports compile failures as a non-2xx with a JSON detail.
    let detail = text
    try {
      const parsed = JSON.parse(text) as { detail?: unknown }
      if (parsed.detail != null) detail = typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail)
    } catch {
      // Not JSON — keep the raw body.
    }
    return { id: testCase.id, error: `HTTP ${response.status}: ${detail.slice(0, 600)}` }
  }

  const data = JSON.parse(text) as { generated_sql?: string; error?: string }
  if (data.error) {
    return { id: testCase.id, error: data.error.slice(0, 600) }
  }
  if (!data.generated_sql) {
    return { id: testCase.id, error: 'resolver returned no SQL and no error' }
  }
  // Compiling proves the planner produced SQL; running it against the fixtures
  // proves the SQL answers the question. A keyless join compiles and then
  // evaluates the filter against unrelated rows, so only the numbers catch it.
  if (!database) {
    return null
  }
  let rows: Array<Record<string, unknown>>
  try {
    rows = await database.run(data.generated_sql, {
      ...buildDashboardContextParameters(city),
      ...(testCase.parameters ?? {}),
    })
  } catch (error) {
    return { id: testCase.id, error: `executing the generated SQL failed: ${(error as Error).message}` }
  }
  const mismatch = checkResult(testCase, rows)
  if (mismatch) {
    return { id: testCase.id, error: `wrong result against the fixtures: ${mismatch}` }
  }
  return null
}

async function compileAll(cases: DashboardQueryCase[], city: CityCode | null) {
  const failures: CompileFailure[] = []
  let cursor = 0
  // One DuckDB per batch: the seeded tables are read-only once created, and
  // creating them costs a Parquet footer read each.
  const database = await FixtureDatabase.create()

  async function worker() {
    while (cursor < cases.length) {
      const testCase = cases[cursor++]
      const failure = await compileAndRun(testCase, city, database)
      if (failure) failures.push(failure)
    }
  }

  try {
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, cases.length) }, worker))
  } finally {
    database.close()
  }
  return failures.sort((a, b) => a.id.localeCompare(b.id))
}

function formatFailures(city: CityCode | null, failures: CompileFailure[]) {
  const label = city ?? 'ALL'
  return [
    `${failures.length} dashboard quer${failures.length === 1 ? 'y' : 'ies'} failed to compile for ${label}:`,
    ...failures.map((failure) => `  - ${failure.id}\n      ${failure.error.replace(/\n/g, '\n      ')}`),
  ].join('\n')
}

// Cross-filtered and species-selected states multiply the case count by ~20 per
// city, so the default run covers them for one city plus the all-cities view —
// every city still gets its base state above. DASHBOARD_QUERY_ALL_CITIES=1
// widens the interactive states to all fourteen; worth doing when a city model
// changes shape or a planner regression is suspected.
const INTERACTIVE_CITIES: Array<CityCode | null> = process.env.DASHBOARD_QUERY_ALL_CITIES
  ? [null, ...ALL_CITIES]
  : [null, 'USSFO']

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

async function skipUntilFirstBuild(city: CityCode | null): Promise<boolean> {
  if (city === null || (await cityParquetIsPublished(city))) return false
  console.warn(
    `${city}'s tree parquet is not on GCS yet -- skipping its dashboard queries. ` +
      'Run the Trilogy refresh for this city and re-run to cover it.',
  )
  return true
}

describe('dashboard query compilation', () => {
  for (const city of [null, ...ALL_CITIES] as Array<CityCode | null>) {
    it(
      `compiles and runs every summary and species query for ${city ?? 'all cities'}`,
      async () => {
        if (await skipUntilFirstBuild(city)) return
        const cases = cityQueryCases(city)
        expect(cases.length).toBeGreaterThan(0)
        const failures = await compileAll(cases, city)
        expect(failures, failures.length ? formatFailures(city, failures) : '').toEqual([])
      },
      CITY_TIMEOUT_MS,
    )
  }

  for (const city of INTERACTIVE_CITIES) {
    it(
      `compiles and runs every query under a species selection for ${city ?? 'all cities'}`,
      async () => {
        if (await skipUntilFirstBuild(city)) return
        const failures = await compileAll(speciesSelectionCases(city), city)
        expect(failures, failures.length ? formatFailures(city, failures) : '').toEqual([])
      },
      CITY_TIMEOUT_MS,
    )

    for (const fields of crossFilterFieldCombos(CROSS_FILTER_MODE)) {
      // Built at collection time so a dimension that cannot split the fixtures
      // in this city's context (nativeness in the all-cities view, where there
      // is no ecoregion to compare against) drops out of the test list instead
      // of registering a test that asserts nothing.
      const cases = cityCrossFilterCases(city, fields)
      if (cases.length === 0) continue

      it(
        `compiles and runs every query under a ${fields.join(' + ')} cross-filter for ${city ?? 'all cities'}`,
        async () => {
          if (await skipUntilFirstBuild(city)) return
          const failures = await compileAll(cases, city)
          expect(failures, failures.length ? formatFailures(city, failures) : '').toEqual([])
        },
        CITY_TIMEOUT_MS,
      )
    }
  }
})
