import { describe, test, expect } from 'vitest'
import { DuckDBInstance } from '@duckdb/node-api'
import { REMOTE_TREES_PARQUET_URL, REMOTE_SPECIES_PARQUET_URL } from './parquetUrls'

async function withDuckDB<T>(fn: (conn: Awaited<ReturnType<DuckDBInstance['connect']>>) => Promise<T>): Promise<T> {
  const instance = await DuckDBInstance.create(':memory:')
  const conn = await instance.connect()
  try {
    return await fn(conn)
  } finally {
    conn.closeSync()
  }
}

describe('parquet schema', () => {
  test('full_tree_info loads and has required columns including city', async () => {
    await withDuckDB(async (conn) => {
      await conn.run(`CREATE TABLE trees AS SELECT * FROM read_parquet('${REMOTE_TREES_PARQUET_URL}')`)

      const colResult = await conn.runAndReadAll(`SELECT column_name FROM information_schema.columns WHERE table_name = 'trees'`)
      const cols = colResult.getRowObjects().map((r) => r.column_name as string)
      const required = ['tree_id', 'city', 'species', 'latitude', 'longitude', 'diameter_at_breast_height']
      for (const col of required) {
        expect(cols, `missing column: ${col}`).toContain(col)
      }

      const countResult = await conn.runAndReadAll(`SELECT city, COUNT(*) AS n FROM trees GROUP BY city ORDER BY city`)
      const counts = countResult.getRowObjects()
      expect(counts.length, 'expected at least one city in data').toBeGreaterThan(0)
      const cities = counts.map((r) => r.city as string)
      expect(cities).toContain('USSFO')
      expect(cities).toContain('USNYC')
    })
  }, 60_000)

  test('tree_enrichment loads and has required columns', async () => {
    await withDuckDB(async (conn) => {
      await conn.run(`CREATE TABLE enrichment AS SELECT * FROM read_parquet('${REMOTE_SPECIES_PARQUET_URL}')`)

      const colResult = await conn.runAndReadAll(`SELECT column_name FROM information_schema.columns WHERE table_name = 'enrichment'`)
      const cols = colResult.getRowObjects().map((r) => r.column_name as string)
      expect(cols, 'missing column: species').toContain('species')
      expect(
        cols.includes('native_ecoregions') || cols.includes('native_status'),
        'missing nativeness column (expected native_ecoregions or legacy native_status)',
      ).toBe(true)
      expect(
        cols.includes('tree_form') || cols.includes('tree_category'),
        'missing tree-form column (expected tree_form or legacy tree_category)',
      ).toBe(true)

      const countResult = await conn.runAndReadAll(`SELECT COUNT(*) AS n FROM enrichment`)
      const n = Number(countResult.getRowObjects()[0].n)
      expect(n, 'enrichment table should have rows').toBeGreaterThan(0)
    })
  }, 60_000)
})
