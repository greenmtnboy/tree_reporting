import { describe, it, expect } from 'vitest'
import { ALL_MODEL_SOURCES } from '../trilogyModels'
import { buildDashboardContextSource } from '../composables/dashboardContextSource'
// Read the version rather than hardcoding it, so a DATA_VERSION bump does not
// require editing an assertion that has nothing to do with what is being tested.
import { TREE_DATA_VERSION } from '../workers/parquetUrls'

const TRILOGY_RESOLVER_URL = 'https://trilogy-service.fly.dev'

type ResolverResponse = {
  generated_sql?: string
  error?: string
  parameters?: Record<string, unknown>
}

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
    imports: options?.imports ?? [{ name: 'tree_enrichment', alias: '' }],
    extra_filters: [],
    parameters: options?.parameters ?? {},
  }
  const res = await fetch(`${TRILOGY_RESOLVER_URL}/generate_query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`)
  return await res.json() as ResolverResponse
}

async function compilePreQL(
  query: string,
  options?: {
    extraSources?: Array<{ alias: string; contents: string }>
    imports?: Array<{ name: string; alias: string }>
  },
): Promise<string> {
  const data = await compilePreQLFull(query, options)
  if (data.error) throw new Error(`Trilogy compile error: ${data.error}`)
  return data.generated_sql!
}

describe('Trilogy resolver smoke tests', () => {
  it('compiles a species count query for SF', async () => {
    const sql = await compilePreQL(`
SELECT
    species,
    count(tree_id) as tree_count
WHERE city = 'USSFO'
ORDER BY tree_count desc
LIMIT 10
`)
    expect(sql).toBeTruthy()
    expect(sql.toLowerCase()).toContain('select')
    expect(sql.toLowerCase()).toContain('sf_tree_info')
  }, 30_000)

  it('compiles a species count query for NYC', async () => {
    const sql = await compilePreQL(`
SELECT
    species,
    count(tree_id) as tree_count
WHERE city = 'USNYC'
ORDER BY tree_count desc
LIMIT 10
`)
    expect(sql).toBeTruthy()
    expect(sql.toLowerCase()).toContain('select')
    expect(sql.toLowerCase()).toContain('nyc_tree_info')
  }, 30_000)

  it('compiles a species count query for Boston', async () => {
    const sql = await compilePreQL(`
SELECT
    species,
    count(tree_id) as tree_count
WHERE city = 'USBOS'
ORDER BY tree_count desc
LIMIT 10
`)
    expect(sql).toBeTruthy()
    expect(sql.toLowerCase()).toContain('select')
    expect(sql.toLowerCase()).toContain('boston_tree_info')
  }, 30_000)

  it('compiles summary dashboard context queries for Boston', async () => {
    const sql = await compilePreQL(
      `
SELECT
    native_locality_bucket,
    count(tree_id) as tree_count
WHERE city = 'USBOS'
ORDER BY tree_count desc
`,
      {
        extraSources: [buildDashboardContextSource('USBOS')],
        imports: [
          { name: 'tree_enrichment', alias: '' },
          { name: 'dashboard_context', alias: '' },
        ],
      },
    )
    expect(sql).toBeTruthy()
    expect(sql.toLowerCase()).toContain('native_locality_bucket')
    expect(sql.toLowerCase()).toContain(`tree_enrichment_v${TREE_DATA_VERSION}.parquet`)
  }, 30_000)

  it('checks whether resolver returns parameters for dashboard context constants', async () => {
    const response = await compilePreQLFull(
      `
SELECT
    native_locality_bucket,
    count(tree_id) as tree_count
WHERE city = 'FRPAR'
ORDER BY tree_count desc
`,
      {
        extraSources: [buildDashboardContextSource('FRPAR')],
        imports: [
          { name: 'tree_enrichment', alias: '' },
          { name: 'dashboard_context', alias: '' },
        ],
        parameters: {
          active_city: 'FRPAR',
          active_city_ecoregion: 664,
          active_city_usda_zone: 8,
          active_city_biome: 'Temperate Broadleaf & Mixed Forests',
          active_city_realm: 'palearctic',
        },
      },
    )
    console.log('=== RESOLVER RESPONSE ===')
    console.log('generated_sql:', response.generated_sql?.substring(0, 500))
    console.log('parameters:', JSON.stringify(response.parameters))
    console.log('has :active_city_ecoregion placeholder:', response.generated_sql?.includes(':active_city_ecoregion'))
    expect(response.generated_sql).toBeTruthy()
  }, 30_000)
})
