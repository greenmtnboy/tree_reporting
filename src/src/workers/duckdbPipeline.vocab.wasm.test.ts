import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { rmSync } from 'node:fs'
import { FIXTURE_DIR, fixtureParquetUrls, writeCityProjection, writeCityVocab } from './wasm/fixtures'
import { startWorker, type WorkerHarness } from './wasm/workerHarness'
import { executedSql } from './wasm/nodeDuckDB'
import { CITY_ENUM_TYPES } from './cityTables'
import type { WindowPolicy } from './cityWindow'

vi.mock('@duckdb/duckdb-wasm', () => import('./wasm/nodeDuckDB'))
vi.mock('../duckdbAssetUrls', () => ({ DUCKDB_ASSET_URLS: { mvp: { mainModule: '', mainWorker: '' }, eh: { mainModule: '', mainWorker: '' } } }))
vi.mock('./parquetUrls', async () => (await import('./wasm/fixtures')).fixtureParquetUrls)

/**
 * The ENUM vocabularies of a windowed city come from the pipeline's
 * vocabulary file (tree_browser_city.preql, `city_tree_vocab`) and never
 * from a scan of the projection, which in DuckDB-wasm reads most of the
 * file. The file is trusted only when its tree counts sum to the
 * projection's row count; otherwise, or when it is missing, the worker
 * scans the projection as before. Either way the types hold exactly the
 * projection's values, since a window casting a value its ENUM lacks fails.
 */
const ROWS = 20_000
const BBOX: [number, number, number, number] = [-71.20, 42.28, -70.92, 42.44]
const POLICY: WindowPolicy = { floorZoom: 13, budgetRows: 40_000, radiusCells: 3 }
/** Each city's file situation: matching, from another build, not published. */
const MATCHING = 'USBOS'
const DISAGREEING = 'USNYC'
const MISSING = 'USSFO'

type QueryResult = { columns: string[]; rows: Record<string, unknown>[]; totalRows: number }

describe('a windowed city types its strings from the vocabulary file', () => {
  let worker: WorkerHarness
  const values = async (sql: string) =>
    (await worker.call<QueryResult>('query', { sql, maxRows: 100 })).rows.map((row) => String(row.v)).sort()
  const enumValues = (type: string) => values(`SELECT unnest(enum_range(NULL::${type})) AS v`)
  const lastLoad = () => {
    const cityLoads = worker.perf.filter((p) => p.label.includes('city-load'))
    return cityLoads[cityLoads.length - 1].detail
  }

  /** Load `city`, returning the statements that built its types. */
  const load = async (city: string) => {
    const from = executedSql.length
    await worker.call('setCityContext', { city, window: POLICY })
    return executedSql.slice(from).filter((sql) => sql.startsWith('CREATE TYPE') && sql.includes('SELECT'))
  }

  beforeAll(async () => {
    for (const city of [MATCHING, DISAGREEING, MISSING]) await writeCityProjection({ city, rows: ROWS, bbox: BBOX })
    await writeCityVocab(MATCHING)
    await writeCityVocab(DISAGREEING, { extraTrees: 1 })
    worker = await startWorker()
    await worker.call('ensureInit', { city: MISSING, window: POLICY })
  })
  afterAll(() => {
    rmSync(FIXTURE_DIR, { recursive: true, force: true })
  })

  it('reads every vocabulary from the file when its counts match the projection', async () => {
    const types = await load(MATCHING)
    expect(lastLoad().vocabulary).toBe('vocab-file')
    expect(types).toHaveLength(6)
    for (const sql of types) {
      expect(sql).toContain(fixtureParquetUrls.cityVocabParquetUrl(MATCHING))
      expect(sql).not.toContain('_tree_browser_v')
    }
  })

  it('types hold exactly the projection’s values', async () => {
    expect(await enumValues(CITY_ENUM_TYPES.species)).toEqual(
      await values('SELECT DISTINCT species AS v FROM trees_published WHERE species IS NOT NULL'))
    expect(await enumValues(CITY_ENUM_TYPES.name)).toEqual(
      await values('SELECT DISTINCT display_name AS v FROM trees_published WHERE display_name IS NOT NULL'))
    expect(await enumValues(CITY_ENUM_TYPES.city)).toEqual([MATCHING])
    expect(await enumValues(CITY_ENUM_TYPES.source)).toEqual(['FIXTURE'])
  })

  it('scans the projection when the file is from another build', async () => {
    const types = await load(DISAGREEING)
    expect(lastLoad().vocabulary).toBe('scan')
    for (const sql of types) expect(sql).toContain(fixtureParquetUrls.cityBrowserParquetUrl(DISAGREEING))
    expect(await enumValues(CITY_ENUM_TYPES.city)).toEqual([DISAGREEING])
  })

  it('scans the projection when there is no file', async () => {
    const types = await load(MISSING)
    expect(lastLoad().vocabulary).toBe('scan')
    for (const sql of types) expect(sql).toContain(fixtureParquetUrls.cityBrowserParquetUrl(MISSING))
    expect(await enumValues(CITY_ENUM_TYPES.city)).toEqual([MISSING])
  })
})
