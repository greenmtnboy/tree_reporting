import { describe, test, expect } from 'vitest'
import { DuckDBInstance } from '@duckdb/node-api'
import { REMOTE_TREES_PARQUET_URL, REMOTE_SPECIES_PARQUET_URL, cityTreeParquetUrl } from './parquetUrls'
import { formatDataSource } from '../data/dataSources'
import { CITY_CONFIG } from '../composables/useMapData'
import { SPECIES_SENTINELS } from '../data/species'

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
      const required = [
        'tree_id', 'city', 'species', 'latitude', 'longitude', 'diameter_at_breast_height',
        // The worker selects these by name; a parquet without them fails to load
        // at all rather than degrading, so assert the contract here.
        'data_source', 'submission_photo_url',
      ]
      for (const col of required) {
        expect(cols, `missing column: ${col}`).toContain(col)
      }

      const countResult = await conn.runAndReadAll(`SELECT city, COUNT(*) AS n FROM trees GROUP BY city ORDER BY city`)
      const counts = countResult.getRowObjects()
      expect(counts.length, 'expected at least one city in data').toBeGreaterThan(0)
      const cities = counts.map((r) => r.city as string)
      expect(cities).toContain('USSFO')
      expect(cities).toContain('USNYC')

      // Every row must be attributable to a source, and every source must be one
      // the frontend's picklist knows about.
      const sourceResult = await conn.runAndReadAll(
        `SELECT DISTINCT data_source FROM trees WHERE data_source IS NOT NULL`,
      )
      const sources = sourceResult.getRowObjects().map((r) => r.data_source as string)
      expect(sources.length, 'expected at least one data_source value').toBeGreaterThan(0)
      const nullSources = await conn.runAndReadAll(`SELECT COUNT(*) AS n FROM trees WHERE data_source IS NULL`)
      expect(Number(nullSources.getRowObjects()[0].n), 'every tree must carry a data_source').toBe(0)
      for (const source of sources) {
        expect(formatDataSource(source), `unlabelled data_source: ${source}`).toBeTruthy()
      }
    })
  }, 60_000)

  // The worker filters flagged cross-source duplicates out of every per-city
  // load with `AND NOT COALESCE(is_duplicate, false)`. A Parquet without the
  // column does not degrade -- DuckDB raises a binder error and that city's map
  // fails to load -- so this is the deploy gate: refresh all cities first.
  test.each(Object.keys(CITY_CONFIG))('%s tree parquet carries is_duplicate', async (city) => {
    const url = cityTreeParquetUrl(city)
    expect(url, `no per-city parquet url for ${city}`).toBeTruthy()
    await withDuckDB(async (conn) => {
      const result = await conn.runAndReadAll(
        `SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('${url}'))`,
      )
      const cols = result.getRowObjects().map((r) => r.column_name as string)
      expect(
        cols,
        `${city}'s parquet has no is_duplicate column -- run the Trilogy refresh ` +
          `before deploying, or the worker binder-errors on this city`,
      ).toContain('is_duplicate')
    })
  }, 60_000)

  // A sentinel is not a taxon, and `species` is the join key: one enrichment row
  // for "Unknown" labelled 189,139 trees across every city as Orania timikae.
  // purge_non_taxa() in data/raw/enrichment/_tree_shared.py removes them.
  test('tree_enrichment holds no row for a species sentinel', async () => {
    await withDuckDB(async (conn) => {
      const literals = SPECIES_SENTINELS.map((s) => `'${s.species.replace(/'/g, "''")}'`).join(', ')
      const result = await conn.runAndReadAll(
        `SELECT species FROM read_parquet('${REMOTE_SPECIES_PARQUET_URL}') WHERE species IN (${literals})`,
      )
      const found = result.getRowObjects().map((r) => r.species as string)
      expect(
        found,
        'the enrichment table has a row for a placeholder species; every tree ' +
          'carrying that sentinel inherits its description, icon and photo',
      ).toEqual([])
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
