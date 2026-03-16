import { describe, it, expect } from 'vitest'
import { ALL_MODEL_SOURCES } from '../trilogyModels'

const TRILOGY_RESOLVER_URL = 'https://trilogy-service.fly.dev'

async function compilePreQL(query: string): Promise<string> {
  const body = {
    query,
    dialect: 'duckdb',
    full_model: { name: '', sources: ALL_MODEL_SOURCES },
    imports: [{ name: 'tree_enrichment', alias: '' }],
    extra_filters: [],
    parameters: {},
  }
  const res = await fetch(`${TRILOGY_RESOLVER_URL}/generate_query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`)
  const data = await res.json() as { generated_sql?: string; error?: string }
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
})
