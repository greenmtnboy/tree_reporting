import { describe, it, expect, beforeAll } from 'vitest'
import { ALL_MODEL_SOURCES } from '../trilogyModels'
import { buildDashboardContextSource } from '../composables/dashboardContextSource'
// Read the version rather than hardcoding it, so a DATA_VERSION bump does not
// require editing an assertion that has nothing to do with what is being tested.
import { TREE_DATA_VERSION } from '../workers/parquetUrls'
import { postToResolverOrThrow } from './resolverFetch'

type ResolverResponse = {
  generated_sql?: string
  error?: string
  parameters?: Record<string, unknown>
}

const TREE_IMPORTS = [
  // Trees and enrichment are two imports, not one: `tree_enrichment` is the
  // species dimension only, and `tree_info` is the union model whose per-city
  // partitions are what the assertions below are about. The app imports both
  // the same way, through `dashboard_context`.
  { name: 'tree_enrichment', alias: '' },
  { name: 'tree_info', alias: '' },
]

const DASHBOARD_IMPORTS = [
  { name: 'tree_enrichment', alias: '' },
  { name: 'dashboard_context', alias: '' },
]

async function compilePreQLFull(
  query: string,
  options?: {
    extraSources?: Array<{ alias: string; contents: string }>
    imports?: Array<{ name: string; alias: string }>
    parameters?: Record<string, unknown>
  },
): Promise<ResolverResponse> {
  const body = {
    query,
    dialect: 'duckdb',
    full_model: { name: '', sources: [...ALL_MODEL_SOURCES, ...(options?.extraSources ?? [])] },
    imports: options?.imports ?? TREE_IMPORTS,
    extra_filters: [],
    parameters: options?.parameters ?? {},
  }
  // Retries a 5xx or a dropped connection; see resolverFetch for why, and why
  // the budget below covers the whole set rather than one test.
  return JSON.parse(await postToResolverOrThrow('/generate_query', body)) as ResolverResponse
}

/**
 * Compile several queries against one model in a single request.
 *
 * `/generate_queries` hydrates the model once. That is worth more than the
 * query count suggests: a lone compile costs ~560ms against an *idle* resolver,
 * of which ~375ms is parsing the 43 preql sources in `ALL_MODEL_SOURCES` and
 * only ~190ms is planning — and the whole bill is multiplied by however drained
 * the shared Fly instance's burst quota is. In the CI run that prompted this,
 * one of the three city compiles below took over 120s on its own.
 *
 * A batch of one returns SQL byte-identical to `/generate_query`, and a batch
 * of many differs only in the generated CTE names, which nothing here reads.
 * The two dashboard-context tests still go through `/generate_query`, so the
 * single-query endpoint the chat surface uses stays covered.
 */
async function compileBatch(
  queries: Array<{ label: string; query: string }>,
  imports: Array<{ name: string; alias: string }>,
): Promise<Map<string, ResolverResponse>> {
  const body = {
    dialect: 'duckdb',
    full_model: { name: '', sources: ALL_MODEL_SOURCES },
    imports,
    parameters: {},
    queries: queries.map((entry) => ({
      query: entry.query,
      label: entry.label,
      extra_filters: [],
      parameters: {},
    })),
  }
  const parsed = JSON.parse(await postToResolverOrThrow('/generate_queries', body)) as {
    queries?: Array<{ label?: string; generated_sql?: string; error?: string }>
  }
  const byLabel = new Map<string, ResolverResponse>()
  for (const entry of parsed.queries ?? []) {
    if (entry.label != null) byLabel.set(entry.label, entry)
  }
  return byLabel
}

function speciesCountQuery(city: string) {
  return `
SELECT
    species,
    count(tree_id) as tree_count
WHERE city = '${city}'
ORDER BY tree_count desc
LIMIT 10
`
}

// The three city compiles share a model and imports, so they are one request.
const CITY_CASES = [
  { label: 'USSFO', city: 'USSFO', parquet: 'sf_tree_info' },
  { label: 'USNYC', city: 'USNYC', parquet: 'nyc_tree_info' },
  { label: 'USBOS', city: 'USBOS', parquet: 'boston_tree_info' },
]

// Every compile happens in beforeAll, so that is where the budget lives; the
// it() blocks only assert on what came back. A per-test timeout is the wrong
// place for it — a throttled resolver can take tens of seconds per request, and
// five independent 120s budgets fail on whichever test the quota happens to run
// out under rather than on anything about the query.
const COMPILE_TIMEOUT_MS = 600_000

describe('Trilogy resolver smoke tests', () => {
  let cityResults = new Map<string, ResolverResponse>()
  let bostonContextSql = ''
  let parisContextResponse: ResolverResponse = {}

  beforeAll(async () => {
    cityResults = await compileBatch(
      CITY_CASES.map((entry) => ({ label: entry.label, query: speciesCountQuery(entry.city) })),
      TREE_IMPORTS,
    )

    const boston = await compilePreQLFull(
      `
SELECT
    native_locality_bucket,
    count(tree_id) as tree_count
WHERE city = 'USBOS'
ORDER BY tree_count desc
`,
      {
        extraSources: [buildDashboardContextSource('USBOS')],
        imports: DASHBOARD_IMPORTS,
      },
    )
    if (boston.error) throw new Error(`Trilogy compile error: ${boston.error}`)
    bostonContextSql = boston.generated_sql!

    parisContextResponse = await compilePreQLFull(
      `
SELECT
    native_locality_bucket,
    count(tree_id) as tree_count
WHERE city = 'FRPAR'
ORDER BY tree_count desc
`,
      {
        extraSources: [buildDashboardContextSource('FRPAR')],
        imports: DASHBOARD_IMPORTS,
        parameters: {
          active_city: 'FRPAR',
          active_city_ecoregion: 664,
          active_city_usda_zone: 8,
          active_city_biome: 'Temperate Broadleaf & Mixed Forests',
          active_city_realm: 'palearctic',
        },
      },
    )
  }, COMPILE_TIMEOUT_MS)

  for (const entry of CITY_CASES) {
    it(`compiles a species count query for ${entry.city}`, () => {
      const result = cityResults.get(entry.label)
      if (!result) throw new Error(`resolver returned no result for ${entry.label}`)
      if (result.error) throw new Error(`Trilogy compile error: ${result.error}`)
      const sql = result.generated_sql!
      expect(sql).toBeTruthy()
      expect(sql.toLowerCase()).toContain('select')
      expect(sql.toLowerCase()).toContain(entry.parquet)
    })
  }

  it('compiles summary dashboard context queries for Boston', () => {
    expect(bostonContextSql).toBeTruthy()
    expect(bostonContextSql.toLowerCase()).toContain('native_locality_bucket')
    expect(bostonContextSql.toLowerCase()).toContain(`tree_enrichment_v${TREE_DATA_VERSION}.parquet`)
  })

  it('checks whether resolver returns parameters for dashboard context constants', () => {
    console.log('=== RESOLVER RESPONSE ===')
    console.log('generated_sql:', parisContextResponse.generated_sql?.substring(0, 500))
    console.log('parameters:', JSON.stringify(parisContextResponse.parameters))
    console.log(
      'has :active_city_ecoregion placeholder:',
      parisContextResponse.generated_sql?.includes(':active_city_ecoregion'),
    )
    expect(parisContextResponse.generated_sql).toBeTruthy()
  })
})
