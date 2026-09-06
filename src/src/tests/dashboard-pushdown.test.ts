/** @vitest-environment happy-dom */
// A city's dashboard must plan against that city's own Parquet.
//
// The browser downloads whatever the resolver's SQL names. `full_tree_info` is
// the 5.9M-row cross-city rollup; `ussfo_tree_info` is 206k rows. Both answer a
// `city = 'USSFO'` question, and the planner picks between them purely on what
// sources are in scope — so this is decided by the *shape of the model bundle*,
// not by anything in the query, and it changes silently.
//
// It changed silently once already. Giving `tree_enrichment.preql` a root
// datasource over the rollup (to declare an input the platform could order the
// enrichment job on) put a second tree source in the planner's scope, and it did
// not merely pick the slower path — the summary tree count under a nativeness
// cross-filter came back 2 where the fixtures say 3. `pnpm test:queries` caught
// the wrong number; nothing caught the source switch, which is the cause and the
// cheaper thing to detect.
//
// So: the per-city partitions live in `tree_info.preql`, `dashboard_context`
// imports it, and the enrichment job's own view of the rollup is kept in a
// separate file set (`raw/enrichment_refresh.preql`) that the frontend does not
// bundle. This asserts the outcome of that arrangement rather than its spelling.
//
// Network: compiles against the live resolver, like trilogy-smoketest. No SQL is
// executed — only the parquet names in the generated SQL are read.
import { describe, it, expect, beforeAll, vi } from 'vitest'

// happy-dom has no Worker; the dashboard bundle constructs one at import time.
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
import { summaryQueryCases, speciesQueryCases, type DashboardQueryCase } from './dashboardQueryCatalog'
import { postToResolver } from './resolverFetch'
import { CITY_CONFIG, type CityCode } from '../composables/useMapData'

/**
 * One it() block's worth of queries, compiled in a single request.
 *
 * `/generate_queries` takes one model plus a list of queries and hydrates the
 * model once. That matters more here than the query count suggests: a lone
 * compile costs ~560ms against an *idle* resolver, of which ~375ms is parsing
 * the 43 preql sources in `ALL_MODEL_SOURCES` and only ~190ms is planning. This
 * suite used to pay that parse once per query — eight sequential requests per
 * city — and the whole bill is multiplied by however throttled the shared Fly
 * instance is, which is what put a single it() over its 120s budget in CI while
 * its neighbours passed at ~50s. See the same reasoning in
 * `dashboard-queries.test.ts`, where batching is what made the 820-query sweep
 * finish in a minute.
 *
 * It is the same planner reached the same way: a batch of one returns SQL
 * byte-identical to `/generate_query`, and a batch of many differs only in the
 * generated CTE names — which nothing here reads.
 */
type PushdownGroup = {
  /** Keys the compiled results, and names the batch in the log. */
  key: string
  /** The dashboard-context city; null is the all-cities view. */
  city: CityCode | null
  cases: DashboardQueryCase[]
}

/** Tree parquets named by one query's SQL, without the version suffix. */
type CompiledCase = { parquets?: string[]; error?: string }

function treeParquetsIn(sql: string): string[] {
  return [...new Set([...sql.matchAll(/duckdb\/trees\/([a-z_]+)_v\d+\.parquet/g)].map((m) => m[1]))]
}

async function compileBatch(
  city: CityCode | null,
  imports: DashboardQueryCase['imports'],
  cases: DashboardQueryCase[],
): Promise<Map<string, CompiledCase>> {
  // Results are matched back by label, so two cases sharing an id would quietly
  // answer for each other rather than fail.
  const labels = new Set(cases.map((testCase) => testCase.id))
  if (labels.size !== cases.length) {
    throw new Error(`duplicate case ids in one batch for ${city ?? 'ALL'}`)
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

  // A per-query compile failure comes back inside a 200, so a non-2xx means the
  // batch itself was rejected and every case in it is unanswered rather than
  // failed. postToResolver retries that; it never retries a 200.
  const result = await postToResolver('/generate_queries', body)
  if (!result.ok) return failAll(result.error)

  let parsed: { queries?: Array<{ label?: string; generated_sql?: string; error?: string }> }
  try {
    parsed = JSON.parse(result.text) as typeof parsed
  } catch {
    return failAll(`resolver returned a non-JSON body: ${result.text.slice(0, 600)}`)
  }

  const byLabel = new Map<string, CompiledCase>()
  for (const entry of parsed.queries ?? []) {
    if (entry.label == null) continue
    byLabel.set(
      entry.label,
      entry.error
        ? { error: entry.error.slice(0, 600) }
        : entry.generated_sql
          ? { parquets: treeParquetsIn(entry.generated_sql) }
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

/**
 * Compile every group, batched by (group, imports).
 *
 * `imports` is a property of the whole batch in the request schema, not of a
 * query in it, so cases declaring different imports cannot ride together — they
 * would compile against the wrong scope. The summary and species pages happen
 * to declare the same two imports today, which is why this comes to one request
 * per group; the grouping does not assume it.
 *
 * Sequential on purpose. The resolver is a single shared instance whose
 * throughput does not improve with fan-out, and in CI this suite already runs
 * concurrently with the `dashboard-queries` job — each is part of the other's
 * load.
 */
async function compileGroups(groups: PushdownGroup[]) {
  const compiled = new Map<string, Map<string, CompiledCase>>()
  const batches: Array<{ group: PushdownGroup; imports: DashboardQueryCase['imports']; cases: DashboardQueryCase[] }> = []

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

  // One line per batch. Resolver latency varies by two orders of magnitude
  // depending on how recently the instance was hammered, and without this a
  // slow run is a silent hang with no way to tell a degraded service from a
  // genuine regression.
  const startedAt = Date.now()
  let done = 0
  for (const batch of batches) {
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
  }

  return compiled
}

// A representative slice rather than the whole catalog: the source choice is a
// property of the model bundle, so it is the same for every chart on a surface,
// and the full sweep already compiles all of them.
function sampleCases(city: CityCode | null): DashboardQueryCase[] {
  return [...summaryQueryCases(city).slice(0, 4), ...speciesQueryCases(city).slice(0, 4)]
}

// The chat surface sends the agent's own PreQL with SUMMARY_DASHBOARD_IMPORTS
// (`tree_enrichment` + `dashboard_context`), so the city is not baked into the
// model the way a dashboard page bakes it — the agent writes the predicate
// itself, and it may name a city other than the one on screen. Resolution has
// to happen off that predicate.
//
// This is why `dashboard_context` imports `tree_info` explicitly. It used to
// reach the per-city partitions transitively through `tree_enrichment`, which
// is now species-only; without the explicit import a chat question about
// Boston would scan the whole rollup.
function chatCase(city: CityCode): DashboardQueryCase {
  return {
    id: `chat:${city}`,
    surface: 'summary',
    query: `SELECT species, count(tree_id) -> tree_count WHERE city = '${city}' ORDER BY tree_count desc LIMIT 10`,
    imports: [
      { name: 'tree_enrichment', alias: '' },
      { name: 'dashboard_context', alias: '' },
    ],
    filters: [],
    state: { city: null, genus: null, species: null, crossFilters: [] },
  }
}

const CITY_GROUPS = (['USSFO', 'FRPAR', 'GRATH'] as CityCode[]).map((city) => ({
  key: `city:${city}`,
  city,
  cases: sampleCases(city),
}))

// No city context: the all-cities view, where the query's own predicate is the
// only thing that says which city is wanted.
const CHAT_CITIES = ['USBOS', 'GRMLO'] as CityCode[]
const CHAT_GROUP: PushdownGroup = { key: 'chat', city: null, cases: CHAT_CITIES.map(chatCase) }

const ALL_GROUP: PushdownGroup = { key: 'all-cities', city: null, cases: sampleCases(null) }

const GROUPS: PushdownGroup[] = [...CITY_GROUPS, CHAT_GROUP, ALL_GROUP]

// Every compile happens in beforeAll, so that is where the budget lives; the
// it() blocks below only read parquet names out of what came back. A throttled
// resolver can take tens of seconds per request on its own, and one budget over
// the whole batch set absorbs that far better than a per-test one — which is
// what failed in CI when each it() paid for eight sequential compiles.
const COMPILE_TIMEOUT_MS = 600_000

describe('dashboard parquet pushdown', () => {
  let compiled = new Map<string, Map<string, CompiledCase>>()

  beforeAll(async () => {
    compiled = await compileGroups(GROUPS)
  }, COMPILE_TIMEOUT_MS)

  function parquetsFor(group: PushdownGroup, id: string): string[] {
    const result = compiled.get(group.key)?.get(id)
    if (!result) throw new Error(`${id} was never compiled`)
    if (result.error || !result.parquets) throw new Error(`compile error for ${id}: ${result.error}`)
    return result.parquets
  }

  for (const group of CITY_GROUPS) {
    const city = group.city as CityCode
    it(`plans ${city} against ${city.toLowerCase()}_tree_info, not the rollup`, () => {
      for (const queryCase of group.cases) {
        const parquets = parquetsFor(group, queryCase.id)
        expect(parquets, `${queryCase.id} named no tree parquet`).not.toHaveLength(0)
        expect(
          parquets,
          `${queryCase.id} reads the cross-city rollup for a single city; the ` +
            `browser would download 5.9M rows instead of ${city}'s own file`,
        ).not.toContain('full_tree_info')
        expect(parquets).toContain(`${city.toLowerCase()}_tree_info`)
      }
    })
  }

  for (const city of CHAT_CITIES) {
    it(`resolves a chat query filtering to ${city} down to its parquet`, () => {
      const parquets = parquetsFor(CHAT_GROUP, `chat:${city}`)
      expect(parquets).toContain(`${city.toLowerCase()}_tree_info`)
      expect(
        parquets,
        `a chat query filtered to ${city} scanned the cross-city rollup`,
      ).not.toContain('full_tree_info')
    })
  }

  it('plans the all-cities view against every city, or the rollup', () => {
    // The other half of the contract: with no city there is no partition to
    // push down to. Two plans are complete: the rollup, or the union of every
    // city's own parquet. The planner used to pick the rollup; since the city
    // models moved their raw sources onto the shared raw_* concepts
    // (tree_dedup.preql) it picks the union, which downloads the same rows as
    // eighteen files instead of one. What must never pass is a strict subset:
    // while only some cities were converted, the resolver answered the
    // all-cities dot map from those cities alone and silently dropped the rest
    // (see upstream_repro/partition_subset_chosen), and a regression that
    // pointed every city at the rollup would still pass the tests above if
    // they were the only ones here.
    const every = Object.keys(CITY_CONFIG).map((c) => `${c.toLowerCase()}_tree_info`)
    for (const queryCase of ALL_GROUP.cases) {
      const parquets = parquetsFor(ALL_GROUP, queryCase.id)
      if (parquets.includes('full_tree_info')) continue
      expect(
        parquets.filter((p) => p !== 'full_tree_info').sort(),
        `${queryCase.id} read a strict subset of the cities and not the rollup`,
      ).toEqual(every.sort())
    }
  })
})
