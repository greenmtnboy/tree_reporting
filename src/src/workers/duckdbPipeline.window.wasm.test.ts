import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { rmSync } from 'node:fs'
import { FIXTURE_DIR, writeCityProjection } from './wasm/fixtures'
import { startWorker, type WorkerHarness } from './wasm/workerHarness'
import { CELL_ZOOM, cellAt, cellsBounds, cellKey, type WindowPolicy } from './cityWindow'

vi.mock('@duckdb/duckdb-wasm', () => import('./wasm/nodeDuckDB'))
vi.mock('../duckdbAssetUrls', () => ({ DUCKDB_ASSET_URLS: { mvp: { mainModule: '', mainWorker: '' }, eh: { mainModule: '', mainWorker: '' } } }))
vi.mock('./parquetUrls', async () => (await import('./wasm/fixtures')).fixtureParquetUrls)

/**
 * The phone's window (cityWindow.ts) on the real worker and DuckDB-WASM: a
 * city held as the cells its tiles ask for, never more than the budget.
 * The city is 300k synthetic trees over central Boston, about 13 by 10
 * cells, so one cell holds a couple of thousand.
 */
const CITY = 'USBOS'
const ROWS = 300_000
const BBOX: [number, number, number, number] = [-71.20, 42.28, -70.92, 42.44]
const CENTER: [number, number] = [-71.06, 42.36]
const FAR: [number, number] = [-71.19, 42.43]
const POLICY: WindowPolicy = { floorZoom: 13, budgetRows: 40_000, radiusCells: 3 }

type TileResult = { tileBuffer: ArrayBuffer; transient: boolean }
type QueryResult = { columns: string[]; rows: Record<string, unknown>[] }

function lngLatToTile(lng: number, lat: number, z: number) {
  const n = 2 ** z
  const latRad = (lat * Math.PI) / 180
  return {
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  }
}

describe('a phone holds the city in windows', () => {
  let worker: WorkerHarness
  const count = async (sql: string) => Number((await worker.call<QueryResult>('query', { sql })).rows[0].n)
  const loads = () => worker.perf.filter((p) => p.label.includes('window-load'))
  const lookAt = async ([lng, lat]: [number, number], zoom: number) => {
    await worker.call('setViewportZoom', { zoom })
    await worker.call('setViewportCenter', { lng, lat })
  }

  beforeAll(async () => {
    await writeCityProjection({ city: CITY, rows: ROWS, bbox: BBOX })
    worker = await startWorker()
    await worker.call('ensureInit', { city: CITY, window: POLICY })
    await worker.call('setCityContext', { city: CITY, window: POLICY })
  })
  afterAll(() => {
    rmSync(FIXTURE_DIR, { recursive: true, force: true })
  })

  it('loads no rows until a tile asks, and keeps every row reachable as published', async () => {
    expect(await count('SELECT count(*) AS n FROM trees_fast')).toBe(0)
    expect(await count('SELECT count(*) AS n FROM trees')).toBe(0)
    expect(await count('SELECT count(*) AS n FROM trees_published')).toBe(ROWS)
  })

  it('answers a tile below the floor empty and transient, loading nothing', async () => {
    await lookAt(CENTER, 12.5)
    const t = lngLatToTile(CENTER[0], CENTER[1], 12)
    const tile = await worker.call<TileResult>('getTile', { z: 12, ...t })
    expect(tile).toMatchObject({ transient: true })
    expect(loads()).toHaveLength(0)
  })

  it('loads exactly the cell a street tile draws from, then serves it', async () => {
    await lookAt(CENTER, 16.5)
    const t = lngLatToTile(CENTER[0], CENTER[1], 16)
    const tile = await worker.call<TileResult>('getTile', { z: 16, ...t })
    expect(tile.transient).toBe(false)
    expect(tile.tileBuffer.byteLength).toBeGreaterThan(0)

    const cell = cellAt(CENTER[0], CENTER[1])
    const box = cellsBounds([cellKey(cell.x, cell.y)])
    const inCell = await count(`SELECT count(*) AS n FROM trees_published
      WHERE longitude >= ${box.west} AND longitude < ${box.east} AND latitude > ${box.south} AND latitude <= ${box.north}`)
    expect(inCell).toBeGreaterThan(1000)
    expect(await count('SELECT count(*) AS n FROM trees_fast')).toBe(inCell)
    expect(loads()).toHaveLength(1)
  })

  it('gathers the tiles of one view into a single load', async () => {
    await lookAt(CENTER, 13.5)
    const c = lngLatToTile(CENTER[0], CENTER[1], 13)
    const before = loads().length
    const tiles = await Promise.all([-1, 0, 1].flatMap((dx) => [-1, 0, 1].map((dy) =>
      worker.call<TileResult>('getTile', { z: 13, x: c.x + dx, y: c.y + dy }))))
    expect(tiles.every((tile) => !tile.transient)).toBe(true)
    expect(loads().length - before).toBe(1)
    // A z13 tile spans 2x2 cells; the centre cell was already held.
    expect(loads()[loads().length - 1].detail.cells).toBe(9 * 4 - 1)
    expect(await count('SELECT count(*) - count(DISTINCT tree_id) AS n FROM trees_fast')).toBe(0)
  })

  it('answers a tile beyond the radius empty and transient', async () => {
    await lookAt(CENTER, 13.5)
    const before = loads().length
    const far = lngLatToTile(FAR[0], FAR[1], 15)
    expect(await worker.call<TileResult>('getTile', { z: 15, ...far })).toMatchObject({ transient: true })
    expect(loads().length).toBe(before)
  })

  it('drops the farthest cells once the held rows pass the budget', async () => {
    const centreCell = cellAt(CENTER[0], CENTER[1])
    await lookAt(FAR, 15.5)
    const far = lngLatToTile(FAR[0], FAR[1], 15)
    const tile = await worker.call<TileResult>('getTile', { z: 15, ...far })
    expect(tile.transient).toBe(false)
    const last = loads()[loads().length - 1].detail
    expect(Number(last.evicted)).toBeGreaterThan(0)
    expect(Number(last.held)).toBeLessThanOrEqual(POLICY.budgetRows)
    expect(await count(`SELECT count(*) AS n FROM trees_fast
      WHERE (xtile_z20 >> ${20 - CELL_ZOOM}) = ${centreCell.x} AND (ytile_z20 >> ${20 - CELL_ZOOM}) = ${centreCell.y}`)).toBe(0)

    // Back at the centre, its cell loads again.
    await lookAt(CENTER, 16.5)
    const t = lngLatToTile(CENTER[0], CENTER[1], 16)
    expect((await worker.call<TileResult>('getTile', { z: 16, ...t })).tileBuffer.byteLength).toBeGreaterThan(0)
  })

  it('holds the whole city again when the window is turned off', async () => {
    await worker.call('setCityContext', { city: CITY, window: null })
    expect(await count('SELECT count(*) AS n FROM trees_fast')).toBe(ROWS)
  })
})
