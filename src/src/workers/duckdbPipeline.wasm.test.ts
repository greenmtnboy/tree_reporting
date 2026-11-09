import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { executedSql, registeredFiles } from './wasm/nodeDuckDB'
import { FIXTURE_DIR, fetchedWorldFiles, writeCityProjection, writeWorld } from './wasm/fixtures'
import { startWorker, type WorkerHarness } from './wasm/workerHarness'
import { TREES_AT_TOKEN } from './cityTables'
import type { PointChunkBatch, PointChunkColors, PointChunkDirectory, PointKeys } from './cityPoints'
import { blockAt, blockKey, type WorldDirectory } from './worldBlocks'

vi.mock('@duckdb/duckdb-wasm', () => import('./wasm/nodeDuckDB'))
vi.mock('../duckdbAssetUrls', () => ({ DUCKDB_ASSET_URLS: { mvp: { mainModule: '', mainWorker: '' }, eh: { mainModule: '', mainWorker: '' } } }))
vi.mock('./parquetUrls', async () => (await import('./wasm/fixtures')).fixtureParquetUrls)

/**
 * The tile worker, unmodified, on DuckDB-WASM under Node: the engine the
 * browser runs, whose planner differs from native DuckDB's (see
 * wasm/nodeDuckDB.ts). The world is two synthetic cities cut into blocks as
 * the pipeline cuts them: 300k trees over central Boston and 20k over
 * Burlington, 290 km away, with species of its own. The worker runs as a
 * desktop does: whole blocks, held near the camera.
 */
const CITY = 'USBOS'
const ROWS = 300_000
const BBOX: [number, number, number, number] = [-71.20, 42.28, -70.92, 42.44]
const CENTER: [number, number] = [-71.06, 42.36]
const OTHER = 'USBTV'
const OTHER_ROWS = 20_000
const OTHER_BBOX: [number, number, number, number] = [-73.26, 44.44, -73.17, 44.50]
const OTHER_CENTER: [number, number] = [-73.2121, 44.4759]
const OTHER_SPECIES: [string, string, string, string] = ['Ulmus americana', 'Acer saccharum', 'Betula papyrifera', 'Tilia americana']
const blockOf = ([lng, lat]: [number, number]) => { const b = blockAt(lng, lat); return blockKey(b.x, b.y) }

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

const requireFromMaplibre = createRequire(createRequire(import.meta.url).resolve('maplibre-gl'))
const { VectorTile } = requireFromMaplibre('@mapbox/vector-tile')
const Pbf = requireFromMaplibre('pbf')

/** A tile's source layers, each with the property names its features carry. */
function tileLayers(buf: ArrayBuffer): Record<string, { features: number; properties: string[] }> {
  const tile = new VectorTile(new Pbf(new Uint8Array(buf)))
  return Object.fromEntries(Object.entries(tile.layers).map(([name, layer]: [string, any]) => {
    const properties = new Set<string>()
    for (let i = 0; i < layer.length; i++) Object.keys(layer.feature(i).properties).forEach((k) => properties.add(k))
    return [name, { features: layer.length, properties: [...properties].sort() }]
  }))
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

  let world: { blocks: number; trees: number }
  const count = async (sql: string) => Number((await worker.call<QueryResult>('query', { sql })).rows[0].n)

  beforeAll(async () => {
    await writeCityProjection({ city: CITY, rows: ROWS, bbox: BBOX })
    await writeCityProjection({ city: OTHER, rows: OTHER_ROWS, bbox: OTHER_BBOX, species: OTHER_SPECIES })
    world = await writeWorld([CITY, OTHER])
    worker = await startWorker()
    await worker.call('ensureInit', { city: CITY })
    await worker.call('setCityContext', { city: CITY })
  })
  afterAll(() => {
    rmSync(FIXTURE_DIR, { recursive: true, force: true })
  })

  it('reads the manifest and the vocabulary, loads the spatial extension, and holds no tree yet', async () => {
    expect(await count('SELECT count(*) AS n FROM trees_fast')).toBe(0)
    // The bookmark's city-wide reads come from its projection, in place.
    expect(await count('SELECT count(*) AS n FROM trees_published')).toBe(ROWS)
    expect(fetchedWorldFiles.filter((name) => name.startsWith('blocks/'))).toEqual([])
    const directory = await worker.call<WorldDirectory>('getWorldDirectory')
    expect(directory.trees).toBe(ROWS + OTHER_ROWS)
    expect(directory.blocks.length / 3).toBe(world.blocks)
    expect(directory.cities.map((c) => [c.code, c.trees])).toEqual([[CITY, ROWS], [OTHER, OTHER_ROWS]])
    // One vocabulary for the world: a species only the far city has is already a value of the type.
    const species = await worker.call<QueryResult>('query', { sql: 'SELECT unnest(enum_range(NULL::tree_species_t)) AS v' })
    expect(species.rows.map((r) => r.v)).toEqual(expect.arrayContaining(['Acer rubrum', ...OTHER_SPECIES]))
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

  it('the walk loaded the blocks around the camera whole, each fetched once, and left no file registered', async () => {
    const held = await count('SELECT count(*) AS n FROM trees_fast')
    expect(held).toBeGreaterThan(50_000)
    expect(held).toBeLessThanOrEqual(ROWS)
    expect(await count('SELECT count(*) - count(DISTINCT tree_id) AS n FROM trees_fast')).toBe(0)
    const blocks = fetchedWorldFiles.filter((name) => name.startsWith('blocks/'))
    expect(blocks.length).toBeGreaterThan(0)
    expect(new Set(blocks).size).toBe(blocks.length)
    // Nothing of the far city: the camera has not been there.
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE city = '${OTHER}'`)).toBe(0)
    expect([...registeredFiles]).toEqual([])
  })

  it('builds the z15 feature table a held block at a time, holding every held tree once', async () => {
    const built = worker.perf.filter(p => /duckdb-worker:feature-table$/.test(p.label)).slice(-1)[0]
    const tables = await count("SELECT count(*) AS n FROM duckdb_tables() WHERE table_name LIKE 'trees_cell_%'")
    expect(built?.detail.slices).toBe(tables)
    const held = await count('SELECT count(*) AS n FROM trees_fast')
    const n = await worker.call<QueryResult>('query', { sql: 'SELECT count(*) AS n, count(DISTINCT tree_id) AS ids FROM tile_features_z15' })
    expect(Number(n.rows[0].n)).toBe(held)
    expect(Number(n.rows[0].ids)).toBe(held)
  })

  it('every tile holds one source layer per colour, named by the colour (mvtLayers.ts)', async () => {
    // What each zoom's features carry: the heatmaps read point_count and
    // grid_m, the circles (tiles z12 and up) dbh, a tap the tree id.
    const expected: Record<number, string[]> = {
      11: ['grid_m', 'point_count'],
      13: ['dbh', 'grid_m', 'point_count'],
      16: ['dbh', 'id'],
    }
    for (const [z, properties] of Object.entries(expected).map(([z, p]) => [Number(z), p] as const)) {
      await worker.call('setViewportZoom', { zoom: z + 0.5 })
      await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
      await worker.call('setVisibleTileRange', { z, ...rangeAround(z, 1) })
      const c = lngLatToTile(CENTER[0], CENTER[1], z)
      const layers = tileLayers((await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })).tileBuffer)
      const names = Object.keys(layers)
      expect(names.length, `z${z} has no layers`).toBeGreaterThan(0)
      for (const name of names) {
        expect(name, `z${z}`).toMatch(/^#[0-9a-fA-F]{6}$/)
        expect(layers[name].properties, `z${z} ${name}`).toEqual(properties)
      }
    }
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

  it('a colour change keeps the z15 feature table and recolours its tiles at read time', async () => {
    await worker.call('setViewportZoom', { zoom: 15.5 })
    await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
    await worker.call('prewarmLodCaches', {})
    const builds = () => worker.perf.filter(p => p.label.includes('feature-table')).length
    const before = builds()
    expect(before).toBeGreaterThanOrEqual(1)
    // A per-tree override, as a chat publish installs one.
    await worker.call('setColorOverrideSql', { sql: "SELECT tree_id, '#123456' AS override_color FROM trees" })
    await worker.call('prewarmLodCaches', {})
    expect(builds(), 'the colour change rebuilt the feature table').toBe(before)
    const c = lngLatToTile(CENTER[0], CENTER[1], 15)
    await worker.call('setVisibleTileRange', { z: 15, ...rangeAround(15, 1) })
    const tile = await worker.call<TileResult>('getTile', { z: 15, x: c.x, y: c.y })
    expect(tile.tileBuffer.byteLength).toBeGreaterThan(0)
    const served = worker.perf.filter(p => p.label.includes('neighborhood-batch') && p.detail.z === 15).slice(-1)[0]
    expect(served?.detail.mode).toBe('feature-table')
    expect(Object.keys(tileLayers(tile.tileBuffer))).toEqual(['#123456'])
    await worker.call('setColorOverrideSql', { sql: null })
    expect(builds()).toBe(before)
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

  // --- The seamless world: the desktop's resident layer and the bookmark ---

  it('the chunk directory is the manifest: every block of the world, read from no table', async () => {
    const before = executedSql.length
    const directory = await worker.call<PointChunkDirectory>('getPointChunkDirectory')
    expect(executedSql.length).toBe(before)
    expect(directory.keys).toHaveLength(world.blocks)
    expect(directory.counts.reduce((a, b) => a + b, 0)).toBe(ROWS + OTHER_ROWS)
    expect(directory.exact).toBe(true)
    expect(directory.keys).toEqual(expect.arrayContaining([blockOf(CENTER), blockOf(OTHER_CENTER)]))
  })

  it('moving the camera to another city loads its blocks on demand, beside the first city\'s', async () => {
    const { rowsKey } = await worker.call<PointKeys>('getPointKeys')
    const far = blockOf(OTHER_CENTER)
    await worker.call('setViewportZoom', { zoom: 13.5 })
    await worker.call('setViewportCenter', { lng: OTHER_CENTER[0], lat: OTHER_CENTER[1] })
    const batch = await worker.call<PointChunkBatch>('getPointChunks', { rowsKey, keys: [far] })
    expect(batch.keys).toEqual([far])
    expect(batch.x.length).toBeGreaterThan(1000)
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE city = '${OTHER}'`)).toBe(batch.x.length)
    // Boston is still held: nothing was switched, only added.
    expect(await count(`SELECT count(*) AS n FROM trees_fast WHERE city = '${CITY}'`)).toBeGreaterThan(50_000)
    // Each point's rowid is its place in the block file, dense and unique within the chunk.
    expect(new Set(batch.rowid).size).toBe(batch.rowid.length)
    expect(Math.max(...batch.rowid)).toBe(batch.rowid.length - 1)
  })

  it('a tree is read by its block and row, from that block\'s table alone', async () => {
    const { rowsKey } = await worker.call<PointKeys>('getPointKeys')
    const far = blockOf(OTHER_CENTER)
    const batch = await worker.call<PointChunkBatch>('getPointChunks', { rowsKey, keys: [far] })
    const rn = batch.rowid[7]
    const before = executedSql.length
    const card = await worker.call<QueryResult>('query', {
      sql: `SELECT tf.tree_id, CAST(tf.species AS VARCHAR) AS species FROM ${TREES_AT_TOKEN} tf WHERE tf.rn = ${rn}`,
      at: { lng: OTHER_CENTER[0], lat: OTHER_CENTER[1] },
    })
    expect(card.rows).toHaveLength(1)
    expect(OTHER_SPECIES).toContain(card.rows[0].species)
    const ran = executedSql.slice(before).join('\n')
    expect(ran).toContain(`trees_cell_${far} tf`)
    expect(ran).not.toMatch(/UNION ALL/)
  })

  it('the bookmark moves without touching the trees: same rows, same blocks, new city-wide reads', async () => {
    const before = await worker.call<PointKeys>('getPointKeys')
    const held = await count('SELECT count(*) AS n FROM trees_fast')
    const fetched = fetchedWorldFiles.length
    const messages = worker.messages.length
    await worker.call('setCityContext', { city: OTHER })
    // The map republishes the new bookmark's default query: the same rows as the last one's.
    await worker.call('setTileQuery', { sql: `SELECT tree_id, species, latitude, longitude, diameter_at_breast_height
      FROM trees WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND city = '${OTHER}'` })
    expect(await worker.call<PointKeys>('getPointKeys')).toEqual(before)
    expect(await count('SELECT count(*) AS n FROM trees_fast')).toBe(held)
    expect(fetchedWorldFiles.length).toBe(fetched)
    expect(await count('SELECT count(*) AS n FROM trees_published')).toBe(OTHER_ROWS)
    const ready = worker.messages.slice(messages).filter((m) => m.type === 'cityContextReady').pop()
    expect(ready?.treeCount).toBe(OTHER_ROWS)
    await worker.call('setCityContext', { city: CITY })
  })

  it('the trunk scheme colours a block loaded after it was turned on', async () => {
    await worker.call('setDbhColors', { enabled: true })
    const keys = await worker.call<PointKeys>('getPointKeys')
    const far = blockOf(OTHER_CENTER)
    const colors = await worker.call<PointChunkColors>('getPointChunkColors', { rowsKey: keys.rowsKey, keys: [far] })
    // The fixture's diameters run 5 to 34 inches: all four bands, never the fallback or the unknown grey.
    expect(new Set(colors.color).has(0)).toBe(false)
    expect(new Set(colors.color).size).toBe(4)
    expect(colors.palette.slice(1).sort()).toEqual(['#225ea8', '#41b6c4', '#7fcdbb', '#9a9a9a', '#c7e9b4'])
    await worker.call('setDbhColors', { enabled: false })
  })

  it('a filter written against the published schema is read over the blocks asked for, and an empty chunk is empty', async () => {
    await worker.call('setTileQuery', { sql: `SELECT map_scope.* FROM (SELECT tree_id, species, latitude, longitude, diameter_at_breast_height FROM trees) map_scope
      WHERE map_scope.tree_id IN (SELECT tree_id FROM trees WHERE species IN ('${OTHER_SPECIES[0]}'))` })
    const { rowsKey } = await worker.call<PointKeys>('getPointKeys')
    const directory = await worker.call<PointChunkDirectory>('getPointChunkDirectory')
    expect(directory.exact).toBe(false)
    const batch = await worker.call<PointChunkBatch>('getPointChunks', { rowsKey, keys: [blockOf(OTHER_CENTER), blockOf(CENTER)] })
    const [far, near] = [batch.offsets[1] - batch.offsets[0], batch.offsets[2] - batch.offsets[1]]
    // A quarter of the far city's trees in that block; none of Boston's are that species.
    expect(far).toBeGreaterThan(100)
    expect(near).toBe(0)
    await worker.call('setTileQuery', { sql: null })
  })
})
