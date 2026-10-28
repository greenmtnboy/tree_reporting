import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { rmSync } from 'node:fs'
import { executedSql } from './wasm/nodeDuckDB'
import { FIXTURE_DIR, writeCityProjection } from './wasm/fixtures'
import { startWorker, type WorkerHarness } from './wasm/workerHarness'

vi.mock('@duckdb/duckdb-wasm', () => import('./wasm/nodeDuckDB'))
vi.mock('../duckdbAssetUrls', () => ({ DUCKDB_ASSET_URLS: { mvp: { mainModule: '', mainWorker: '' }, eh: { mainModule: '', mainWorker: '' } } }))
vi.mock('./parquetUrls', async () => (await import('./wasm/fixtures')).fixtureParquetUrls)

/**
 * The tile worker, unmodified, on DuckDB-WASM under Node: the engine the
 * browser runs, whose planner differs from native DuckDB's (see
 * wasm/nodeDuckDB.ts). The city is a synthetic browser projection, 300k
 * trees over central Boston.
 */
const CITY = 'USBOS'
const ROWS = 300_000
const BBOX: [number, number, number, number] = [-71.20, 42.28, -70.92, 42.44]
const CENTER: [number, number] = [-71.06, 42.36]

type TileResult = { tileBuffer: ArrayBuffer; transient: boolean }
type QueryResult = { columns: string[]; rows: Record<string, unknown>[] }
type TileRange = { minX: number; maxX: number; minY: number; maxY: number }

function lngLatToTile(lng: number, lat: number, z: number) {
  const n = 2 ** z
  const latRad = (lat * Math.PI) / 180
  return {
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  }
}

function rangeAround(z: number, radius: number): TileRange {
  const c = lngLatToTile(CENTER[0], CENTER[1], z)
  return { minX: c.x - radius, maxX: c.x + radius, minY: c.y - radius, maxY: c.y + radius }
}

type PlanNode = { name: string; children: PlanNode[]; extra_info?: Record<string, unknown> }

/**
 * The expressions DuckDB evaluates while scanning a table: the scan's own
 * pushed filters and any FILTER directly above it. Whatever is here runs on
 * every row of the city, before the tile range has narrowed anything.
 */
function scanFilterExpressions(plan: PlanNode[]): string[] {
  const out: string[] = []
  const isScan = (node: PlanNode) => node.name.trim().endsWith('SCAN')
  const walk = (node: PlanNode) => {
    if (isScan(node)) out.push(JSON.stringify(node.extra_info ?? {}))
    if (node.name.trim() === 'FILTER' && node.children.some(isScan)) out.push(JSON.stringify(node.extra_info ?? {}))
    node.children.forEach(walk)
  }
  plan.forEach(walk)
  return out
}

describe('tile worker on DuckDB-WASM', () => {
  let worker: WorkerHarness
  /** The tile SQL the worker ran during the walk, before any test's own queries. */
  let walkTileSql: string[] = []

  const plan = async (sql: string): Promise<PlanNode[]> => {
    const result = await worker.call<QueryResult>('query', { sql: `EXPLAIN (FORMAT json) ${sql}` })
    return JSON.parse(String(result.rows[0].explain_value))
  }
  const geometryInScan = async (sql: string) =>
    scanFilterExpressions(await plan(sql)).filter(e => /ST_AsMVTGeom|ST_IsEmpty/i.test(e))

  beforeAll(async () => {
    await writeCityProjection({ city: CITY, rows: ROWS, bbox: BBOX })
    worker = await startWorker()
    await worker.call('ensureInit', { city: CITY })
    await worker.call('setCityContext', { city: CITY })
  })
  afterAll(() => {
    rmSync(FIXTURE_DIR, { recursive: true, force: true })
  })

  it('loads the city projection and the spatial extension', async () => {
    const count = await worker.call<QueryResult>('query', { sql: 'SELECT count(*) AS n FROM trees_fast' })
    expect(Number(count.rows[0].n)).toBe(ROWS)
    const spatial = await worker.call<QueryResult>('query', { sql: "SELECT loaded FROM duckdb_extensions() WHERE extension_name = 'spatial'" })
    expect(spatial.rows[0].loaded).toBe(true)
  })

  it('serves trees at every zoom of a walk from the city view to the street', async () => {
    for (const z of [11, 12, 13, 14, 15, 16, 17, 18]) {
      const range = rangeAround(z, 1)
      await worker.call('setViewportZoom', { zoom: z + 0.5 })
      await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
      await worker.call('setVisibleTileRange', { z, ...range })
      const c = lngLatToTile(CENTER[0], CENTER[1], z)
      const tile = await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })
      expect(tile.transient, `z${z}`).toBe(false)
      expect(tile.tileBuffer.byteLength, `z${z} centre tile is empty`).toBeGreaterThan(0)
    }
    await worker.call('prewarmLodCaches', {})
    walkTileSql = [...new Set(executedSql.filter(sql => /ST_AsMVT\(/.test(sql)))]
  })

  it('the plan check catches the geometry guard pushed into the scan (#144)', async () => {
    // The points batch as it was before #144: the guard as a WHERE over the
    // feature, which DuckDB-WASM evaluates in the scan, on every tree.
    const c = lngLatToTile(CENTER[0], CENTER[1], 16)
    const pushed = `
      WITH rows AS (
        SELECT (tf.xtile_z20 >> 4) AS xtile, (tf.ytile_z20 >> 4) AS ytile,
          { 'geom': ST_AsMVTGeom(ST_Point(tf.x_3857, tf.y_3857), ST_Extent(ST_TileEnvelope(16, (tf.xtile_z20 >> 4), (tf.ytile_z20 >> 4))), 4096, 64, true) } AS feature
        FROM trees_fast tf
        WHERE (tf.xtile_z20 >> 4) BETWEEN ${c.x} AND ${c.x + 1} AND (tf.ytile_z20 >> 4) BETWEEN ${c.y} AND ${c.y + 1}
      )
      SELECT xtile, ytile, ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
      FROM rows WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)
      GROUP BY xtile, ytile`
    expect(await geometryInScan(pushed)).not.toEqual([])
  })

  it('no tile query the worker ran evaluates tile geometry while scanning the city', async () => {
    const tileQueries = walkTileSql
    // Points batches, overview aggregates and the z15 feature table all ran.
    expect(tileQueries.length).toBeGreaterThanOrEqual(3)
    const offenders: string[] = []
    for (const sql of tileQueries) {
      const found = await geometryInScan(sql)
      if (found.length) offenders.push(`${sql.trim().slice(0, 300)}\n  scan evaluates: ${found.join(' | ').slice(0, 300)}`)
    }
    expect(offenders, offenders.join('\n\n')).toEqual([])
  })

  it('an intro prefetch of an overview range is one aggregate and serves the range with fetching paused (#146)', async () => {
    await worker.call('invalidateTileCaches', {})
    const range = rangeAround(14, 2)
    const before = worker.perf.length
    expect(await worker.call('prefetchVisibleTilesAtZoom', { z: 14, range })).toBe('executed')
    const batches = worker.perf.slice(before).filter(p => p.label.includes('neighborhood-batch') && p.detail.z === 14)
    expect(batches).toHaveLength(1)
    expect(batches[0].detail.tiles).toBe(25)

    await worker.call('setAutoTileFetchEnabled', { enabled: false })
    try {
      for (let x = range.minX; x <= range.maxX; x += 1) {
        for (let y = range.minY; y <= range.maxY; y += 1) {
          const tile = await worker.call<TileResult>('getTile', { z: 14, x, y })
          expect(tile.transient, `z14 ${x}/${y} was not warmed`).toBe(false)
        }
      }
      // The level below was not warmed, so it answers the transient empty a
      // paused fetch gives: what the intro used to draw on the way out.
      const c13 = lngLatToTile(CENTER[0], CENTER[1], 13)
      const cold = await worker.call<TileResult>('getTile', { z: 13, x: c13.x, y: c13.y })
      expect(cold).toMatchObject({ transient: true })
      expect(cold.tileBuffer.byteLength).toBe(0)
    } finally {
      await worker.call('setAutoTileFetchEnabled', { enabled: true })
    }
  })
})
