import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { readFileSync, rmSync, statSync } from 'node:fs'
import { FIXTURE_DIR, fetchedWorldFiles, fetchedWorldRanges, rangeFault, writeCityProjection, writeWorld } from './wasm/fixtures'
import { executedSql } from './wasm/nodeDuckDB'
import { startWorker, type WorkerHarness } from './wasm/workerHarness'
import { CELL_ZOOM, cellAt, cellsBounds, cellKey, type WindowPolicy } from './cityWindow'
import { blockAt, blockKey } from './worldBlocks'

vi.mock('@duckdb/duckdb-wasm', () => import('./wasm/nodeDuckDB'))
vi.mock('../duckdbAssetUrls', () => ({ DUCKDB_ASSET_URLS: { mvp: { mainModule: '', mainWorker: '' }, eh: { mainModule: '', mainWorker: '' } } }))
vi.mock('./parquetUrls', async () => (await import('./wasm/fixtures')).fixtureParquetUrls)

/**
 * The phone's window (cityWindow.ts) on the real worker and DuckDB-WASM: the
 * world held as the cells its tiles ask for, cut out of their block files,
 * never more than the budget. The world is 300k synthetic trees over central
 * Boston, about 13 by 10 cells, so one cell holds a couple of thousand.
 */
const CITY = 'USBOS'
const ROWS = 300_000
const BBOX: [number, number, number, number] = [-71.20, 42.28, -70.92, 42.44]
const CENTER: [number, number] = [-71.06, 42.36]
const FAR: [number, number] = [-71.19, 42.43]
/** A corner of the city no test looks at, so its cells are never held. */
const UNSEEN = 'longitude > -70.95 AND latitude < 42.30'
const POLICY: WindowPolicy = { floorZoom: 13, budgetRows: 40_000, radiusCells: 3 }

type TileResult = { tileBuffer: ArrayBuffer; transient: boolean }
type QueryResult = { columns: string[]; rows: Record<string, unknown>[]; totalRows: number }

function lngLatToTile(lng: number, lat: number, z: number) {
  const n = 2 ** z
  const latRad = (lat * Math.PI) / 180
  return {
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  }
}

/** A block as the fixture world's manifest lists it: its file and its rows. */
function manifestBlock([lng, lat]: [number, number]): { file: string; rows: number; bytes: number } {
  const at = blockAt(lng, lat)
  const manifest = JSON.parse(readFileSync(`${FIXTURE_DIR}/world/v1/manifest.json`, 'utf-8')) as { blocks: [number, number, string][] }
  const [key, rows, hash] = manifest.blocks.find((block) => block[0] === blockKey(at.x, at.y))!
  const file = `blocks/${key}-${hash}.parquet`
  return { file, rows, bytes: statSync(`${FIXTURE_DIR}/world/v1/${file}`).size }
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
    await writeWorld([CITY])
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
    // The cell came out of the one block file that holds it, and the engine
    // read it from memory: its own range reads of a remote block cost more
    // requests and more bytes than the file (WHOLE_BLOCK_LOADS).
    const block = blockAt(CENTER[0], CENTER[1])
    const read = executedSql.filter((sql) => /CREATE TABLE trees_window_staging/.test(sql)).flatMap((sql) => [...sql.matchAll(/blocks\/(\d+)-/g)].map((m) => Number(m[1])))
    expect(read).toEqual([blockKey(block.x, block.y)])
    // The block is a city centre's, tens of thousands of trees, so it was not
    // fetched whole: its footer, then the row groups this cell falls in
    // (sparseBlocks.ts). The cell above holds every one of its trees all the same.
    const listed = manifestBlock(CENTER)
    expect(listed.rows).toBeGreaterThan(30_000)
    expect(fetchedWorldFiles.filter((name) => name.startsWith('blocks/'))).toEqual([])
    expect(fetchedWorldRanges.every((range) => range.name === listed.file)).toBe(true)
    expect(fetchedWorldRanges.length).toBeGreaterThanOrEqual(2)
    expect(fetchedWorldRanges[0].start + fetchedWorldRanges[0].bytes).toBe(listed.bytes)
    expect(fetchedWorldRanges.reduce((sum, range) => sum + range.bytes, 0)).toBeLessThan(listed.bytes * 0.75)
  })

  it('reads the next cell of a block from the row groups it kept, fetching only the ones it lacks', async () => {
    const before = fetchedWorldFiles.length
    const ranges = fetchedWorldRanges.length
    const held = fetchedWorldRanges.reduce((sum, range) => sum + range.bytes, 0)
    const cell = cellAt(CENTER[0], CENTER[1])
    const box = cellsBounds([cellKey(cell.x + 1, cell.y)])
    const next: [number, number] = [(box.west + box.east) / 2, (box.south + box.north) / 2]
    const here = blockAt(CENTER[0], CENTER[1]), there = blockAt(next[0], next[1])
    expect([there.x, there.y]).toEqual([here.x, here.y])
    await lookAt(next, 16.5)
    const loaded = loads().length
    const tile = await worker.call<TileResult>('getTile', { z: 16, ...lngLatToTile(next[0], next[1], 16) })
    expect(tile.transient).toBe(false)
    expect(loads().length).toBe(loaded + 1)
    expect(fetchedWorldFiles.length).toBe(before)
    // No second read of the footer, and never a byte twice: what is held of the block stays under its size.
    const listed = manifestBlock(CENTER)
    const more = fetchedWorldRanges.slice(ranges)
    expect(more.every((range) => range.name === listed.file && range.start + range.bytes < listed.bytes)).toBe(true)
    expect(held + more.reduce((sum, range) => sum + range.bytes, 0)).toBeLessThanOrEqual(listed.bytes)
    const inCell = await count(`SELECT count(*) AS n FROM trees_published
      WHERE longitude >= ${box.west} AND longitude < ${box.east} AND latitude > ${box.south} AND latitude <= ${box.north}`)
    expect(await count(`SELECT count(*) AS n FROM trees_fast
      WHERE (xtile_z20 >> ${20 - CELL_ZOOM}) = ${cell.x + 1} AND (ytile_z20 >> ${20 - CELL_ZOOM}) = ${cell.y}`)).toBe(inCell)
  })

  it('gathers the tiles of one view into a single load', async () => {
    await lookAt(CENTER, 13.5)
    const c = lngLatToTile(CENTER[0], CENTER[1], 13)
    const before = loads().length
    const tiles = await Promise.all([-1, 0, 1].flatMap((dx) => [-1, 0, 1].map((dy) =>
      worker.call<TileResult>('getTile', { z: 13, x: c.x + dx, y: c.y + dy }))))
    expect(tiles.every((tile) => !tile.transient)).toBe(true)
    expect(loads().length - before).toBe(1)
    // A z13 tile spans 2x2 cells; the centre cell and the one east of it were already held.
    expect(loads()[loads().length - 1].detail.cells).toBe(9 * 4 - 2)
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

  // The chat reads the whole city, not the window (useChat.ts), and its
  // results land on the same heap.
  it('returns a capped read in its own order and counts the rest', async () => {
    const result = await worker.call<QueryResult>('query', {
      sql: 'SELECT tree_id FROM trees_published ORDER BY tree_id DESC',
      maxRows: 5,
    })
    expect(result.columns).toEqual(['tree_id'])
    expect(result.rows).toHaveLength(5)
    expect(result.totalRows).toBe(ROWS)
    const ids = result.rows.map((row) => String(row.tree_id))
    expect(ids.every((id) => id !== 'undefined')).toBe(true)
    expect(ids).toEqual([...ids].sort().reverse())
    const empty = await worker.call<QueryResult>('query', { sql: 'SELECT tree_id FROM trees_published WHERE false', maxRows: 5 })
    expect(empty).toEqual({ columns: ['tree_id'], rows: [], totalRows: 0 })
  })

  it("filters tiles by the chat's published ids, wherever in the city they are", async () => {
    await lookAt(CENTER, 16.5)
    const t = lngLatToTile(CENTER[0], CENTER[1], 16)
    const tileBytes = async () => (await worker.call<TileResult>('getTile', { z: 16, ...t })).tileBuffer.byteLength
    const unfiltered = await tileBytes()

    await worker.call('setPublishedTreeIdFilterSql', { sql: 'SELECT tree_id FROM trees_published' })
    expect(await tileBytes()).toBe(unfiltered)
    await worker.call('setPublishedTreeIdFilterSql', { sql: `SELECT tree_id FROM trees_published WHERE ${UNSEEN}` })
    expect(await tileBytes()).toBeLessThan(unfiltered / 10)
    await worker.call('setPublishedTreeIdFilterSql', { sql: null })
  })

  it('lists a chat colour first met outside the window, so the map draws it', async () => {
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE ${UNSEEN}`)).toBe(0)
    const before = worker.messages.length
    await worker.call('setColorOverrideSql', { sql: `SELECT tree_id,
      CASE WHEN ${UNSEEN} THEN '#123456' ELSE '#654321' END AS override_color
      FROM trees_published` })
    const update = worker.messages.slice(before).filter((m) => m.type === 'colorMapUpdate').pop()
    expect(update?.distinctColors).toEqual(expect.arrayContaining(['#123456', '#654321']))
    await worker.call('setColorOverrideSql', { sql: null })
  })

  // The chat's standing rule (cityTables.ts `treeRuleRowsSql`): SQL over
  // `trees` as the resolver writes it, asked of each cell as it loads.
  it("asks the chat's rule of every cell as it loads, so a filter follows the camera", async () => {
    const MATCH = 'hash(tree_id) % 10 = 0'
    const RULE = `SELECT "map_trees"."tree_id" as "tree_id" FROM "trees" as "map_trees" WHERE hash("map_trees"."tree_id") % 10 = 0`
    const tileBytes = async (at: [number, number]) =>
      (await worker.call<TileResult>('getTile', { z: 16, ...lngLatToTile(at[0], at[1], 16) })).tileBuffer.byteLength
    // The rule's answers are exactly the matching trees held, however the cells came and went.
    const inStep = async () => expect(await count('SELECT count(*) AS n FROM published_tree_filter_ids'))
      .toBe(await count(`SELECT count(*) AS n FROM trees_fast WHERE ${MATCH}`))

    await lookAt(CENTER, 16.5)
    const all = await tileBytes(CENTER)
    await worker.call('setTreeRule', { rule: { sql: RULE, color: false } })
    expect(await tileBytes(CENTER)).toBeLessThan(all / 3)
    await inStep()

    // Somewhere no test has looked: its cells load under the rule, and the far ones are dropped for them.
    const beyond: [number, number] = [-71.13, 42.31]
    const cell = cellAt(beyond[0], beyond[1])
    const inCell = `(xtile_z20 >> ${20 - CELL_ZOOM}) = ${cell.x} AND (ytile_z20 >> ${20 - CELL_ZOOM}) = ${cell.y}`
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE ${inCell}`)).toBe(0)
    await lookAt(beyond, 16.5)
    const filtered = await tileBytes(beyond)
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE ${inCell}`)).toBeGreaterThan(1000)
    expect(filtered).toBeGreaterThan(0)
    await inStep()
    for (const at of [FAR, CENTER, beyond] as [number, number][]) {
      await lookAt(at, 16.5)
      await tileBytes(at)
    }
    await inStep()

    await worker.call('setTreeRule', { rule: null })
    await lookAt(beyond, 16.5)
    expect(await tileBytes(beyond)).toBeGreaterThan(filtered * 3)
  })

  it("lists the colours a rule names before any tree has them, and colours the trees it matches", async () => {
    const before = worker.messages.length
    await worker.call('setTreeRule', { rule: { color: true, sql: `SELECT "map_trees"."tree_id" as "tree_id",
      CASE WHEN ${UNSEEN.replace(/(longitude|latitude)/g, '"map_trees"."$1"')} THEN '#123456' ELSE '#654321' END as "override_color"
      FROM "trees" as "map_trees"` } })
    const update = worker.messages.slice(before).filter((m) => m.type === 'colorMapUpdate').pop()
    expect(update?.distinctColors).toEqual(expect.arrayContaining(['#123456', '#654321']))
    expect(await count(`SELECT count(*) AS n FROM __tree_color_override WHERE display_color = '#654321'`))
      .toBe(await count('SELECT count(*) AS n FROM trees_fast'))
    // A published list's "no colours" is not about a rule's; clearing the rule is.
    await worker.call('setColorOverrideSql', { sql: null })
    expect(await count('SELECT count(*) AS n FROM __tree_color_override')).toBeGreaterThan(0)
    await worker.call('setTreeRule', { rule: null })
    expect(await count(`SELECT count(*) AS n FROM duckdb_tables() WHERE table_name = '__tree_color_override'`)).toBe(0)
  })

  // Two ways a partial read can go wrong, each in a large block beside the
  // centre's that nothing has read yet: the requests fail, or the engine is
  // handed bytes it cannot read. Either way the cell must hold exactly its
  // published trees, from the whole file.
  for (const [mode, lng] of [['refuse', -70.99], ['garble', -71.15]] as const) {
    it(`reads a block whole when its partial read fails (${mode})`, async () => {
      const at: [number, number] = [lng, 42.36]
      const listed = manifestBlock(at)
      expect(listed.rows).toBeGreaterThan(30_000)
      const cell = cellAt(at[0], at[1])
      const box = cellsBounds([cellKey(cell.x, cell.y)])
      const whole = () => fetchedWorldFiles.filter((name) => name === listed.file).length
      const before = whole()
      const warned = vi.spyOn(console, 'warn').mockImplementation(() => {})
      rangeFault.mode = mode
      try {
        await lookAt(at, 16.5)
        const tile = await worker.call<TileResult>('getTile', { z: 16, ...lngLatToTile(at[0], at[1], 16) })
        expect(tile.transient).toBe(false)
        expect(tile.tileBuffer.byteLength).toBeGreaterThan(0)
      } finally {
        rangeFault.mode = 'none'
      }
      expect(warned.mock.calls.some((call) => /read(ing)? it whole/.test(String(call[0])))).toBe(true)
      warned.mockRestore()
      expect(whole()).toBe(before + 1)
      const inCell = await count(`SELECT count(*) AS n FROM trees_published
        WHERE longitude >= ${box.west} AND longitude < ${box.east} AND latitude > ${box.south} AND latitude <= ${box.north}`)
      expect(inCell).toBeGreaterThan(1000)
      expect(await count(`SELECT count(*) AS n FROM trees_fast
        WHERE (xtile_z20 >> ${20 - CELL_ZOOM}) = ${cell.x} AND (ytile_z20 >> ${20 - CELL_ZOOM}) = ${cell.y}`)).toBe(inCell)
    })
  }

  it('Near Me loads the cells under its fix as soon as the engine is up, before a tile asks', async () => {
    const fix: [number, number] = [-70.94, 42.29]
    const cell = cellAt(fix[0], fix[1])
    const inCell = `(xtile_z20 >> ${20 - CELL_ZOOM}) = ${cell.x} AND (ytile_z20 >> ${20 - CELL_ZOOM}) = ${cell.y}`
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE ${inCell}`)).toBe(0)
    const before = loads().length
    await worker.call('prefetchWindowAt', { lng: fix[0], lat: fix[1], city: CITY })
    await vi.waitFor(() => expect(loads().length).toBe(before + 1))
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE ${inCell}`)).toBeGreaterThan(300)
    // The view's tile then draws from what is held.
    await lookAt(fix, 16.5)
    const tile = await worker.call<TileResult>('getTile', { z: 16, ...lngLatToTile(fix[0], fix[1], 16) })
    expect(tile.transient).toBe(false)
    expect(tile.tileBuffer.byteLength).toBeGreaterThan(0)
    expect(loads().length).toBe(before + 1)
  })

  it('starts over with whole blocks when the window is turned off', async () => {
    await worker.call('setCityContext', { city: CITY, window: null })
    // A desktop's hold: nothing until asked, then a block at a time.
    expect(await count('SELECT count(*) AS n FROM trees_fast')).toBe(0)
    await lookAt(CENTER, 16.5)
    const t = lngLatToTile(CENTER[0], CENTER[1], 16)
    expect((await worker.call<TileResult>('getTile', { z: 16, ...t })).tileBuffer.byteLength).toBeGreaterThan(0)
    const block = blockAt(CENTER[0], CENTER[1])
    expect(await count(`SELECT count(*) AS n FROM trees_fast
      WHERE (xtile_z20 >> 8) <> ${block.x} OR (ytile_z20 >> 8) <> ${block.y}`)).toBe(0)
    const cell = cellAt(CENTER[0], CENTER[1])
    expect(await count(`SELECT count(*) AS n FROM trees_fast
      WHERE (xtile_z20 >> ${20 - CELL_ZOOM}) <> ${cell.x} OR (ytile_z20 >> ${20 - CELL_ZOOM}) <> ${cell.y}`)).toBeGreaterThan(0)
  })
})
