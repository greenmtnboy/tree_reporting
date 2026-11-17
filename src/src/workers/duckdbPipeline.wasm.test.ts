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
 * wasm/nodeDuckDB.ts). No extension is installed in it: a tile query that
 * reached for the spatial extension again would fail here. The world is two synthetic cities cut into blocks as
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

/** A tile's features with their properties and place in the tile, by source layer. */
function tileFeatures(buf: ArrayBuffer): Record<string, Array<Record<string, unknown> & { x: number; y: number }>> {
  const tile = new VectorTile(new Pbf(new Uint8Array(buf)))
  return Object.fromEntries(Object.entries(tile.layers).map(([name, layer]: [string, any]) => [
    name,
    Array.from({ length: layer.length }, (_, i) => ({ ...layer.feature(i).properties, ...layer.feature(i).loadGeometry()[0][0] })),
  ]))
}

describe('tile worker on DuckDB-WASM', () => {
  let worker: WorkerHarness
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

  it('reads the manifest and the vocabulary, loads no extension, and holds no tree yet', async () => {
    expect(await count('SELECT count(*) AS n FROM trees_fast')).toBe(0)
    // The bookmark's city-wide reads come from its projection, in place, and
    // the projection is not opened until one asks: binding the view reads the
    // file's footer, which the first tree used to wait behind.
    const bindings = () => executedSql.filter((sql) => /VIEW trees_published/.test(sql)).length
    expect(bindings()).toBe(0)
    expect(await count('SELECT count(*) AS n FROM trees_published')).toBe(ROWS)
    expect(bindings()).toBe(1)
    expect(await count('SELECT count(*) AS n FROM trees_published')).toBe(ROWS)
    expect(bindings()).toBe(1)
    expect(fetchedWorldFiles.filter((name) => name.startsWith('blocks/'))).toEqual([])
    const directory = await worker.call<WorldDirectory>('getWorldDirectory')
    expect(directory.trees).toBe(ROWS + OTHER_ROWS)
    expect(directory.blocks.length / 3).toBe(world.blocks)
    expect(directory.cities.map((c) => [c.code, c.trees])).toEqual([[CITY, ROWS], [OTHER, OTHER_ROWS]])
    // One vocabulary for the world: a species only the far city has is already a value of the type.
    const species = await worker.call<QueryResult>('query', { sql: 'SELECT unnest(enum_range(NULL::tree_species_t)) AS v' })
    expect(species.rows.map((r) => r.v)).toEqual(expect.arrayContaining(['Acer rubrum', ...OTHER_SPECIES]))
    // The bookmark's landmarks were read from a file fetched whole, and it is not left registered.
    expect([...registeredFiles]).toEqual([])
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
  })

  it('the parquet extension asked for beside the engine is the one this engine loads', async () => {
    // The fixtures stand in for parquetUrls.ts; this is the module itself.
    const { DUCKDB_EXTENSION_VERSION, parquetExtensionUrl } = await vi.importActual<typeof import('./parquetUrls')>('./parquetUrls')
    const engine = await worker.call<QueryResult>('query', { sql: 'SELECT library_version AS v FROM pragma_version()' })
    expect(DUCKDB_EXTENSION_VERSION).toBe(engine.rows[0].v)
    expect(parquetExtensionUrl('/assets/duckdb-eh-abc123.wasm')).toBe(`https://extensions.duckdb.org/${engine.rows[0].v}/wasm_eh/parquet.duckdb_extension.wasm`)
    expect(parquetExtensionUrl('/assets/duckdb-mvp-abc123.wasm')).toContain('/wasm_mvp/')
  })

  it('draws its tiles without the spatial extension: nothing the walk ran names it, and it was never loaded', async () => {
    // 23 MB of wasm that a phone fetched and compiled before its first tree (pointTiles.ts).
    expect(executedSql.filter((sql) => /\bST_\w+\s*\(|\b(LOAD|INSTALL)\s+spatial/i.test(sql))).toEqual([])
    const spatial = await worker.call<QueryResult>('query', { sql: "SELECT loaded FROM duckdb_extensions() WHERE extension_name = 'spatial'" })
    expect(spatial.rows.map((r) => r.loaded)).not.toContain(true)
  })

  it('a street tile holds each of its trees once, at its own place in the tile', async () => {
    const z = 16
    const c = lngLatToTile(CENTER[0], CENTER[1], z)
    await worker.call('setViewportZoom', { zoom: z + 0.5 })
    await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
    await worker.call('setVisibleTileRange', { z, ...rangeAround(z, 1) })
    const drawn = Object.values(tileFeatures((await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })).tileBuffer)).flat()
    const held = await worker.call<QueryResult>('query', { sql: `
      SELECT tree_id, x_3857, y_3857, dbh FROM trees_fast
      WHERE (xtile_z20 >> 4) = ${c.x} AND (ytile_z20 >> 4) = ${c.y}` })
    expect(held.rows.length).toBeGreaterThan(10)
    expect(drawn.map((f) => f.id).sort()).toEqual(held.rows.map((r) => r.tree_id).sort())
    // Tile units from the tile's north-west corner, 4096 to a side.
    const half = 20037508.342789244
    const size = (2 * half) / 2 ** z
    const byId = new Map(drawn.map((f) => [f.id, f]))
    for (const row of held.rows) {
      const feature = byId.get(row.tree_id)!
      expect(feature.x).toBe(Math.round((Number(row.x_3857) + half - c.x * size) / size * 4096))
      expect(feature.y).toBe(Math.round((half - c.y * size - Number(row.y_3857)) / size * 4096))
      expect(feature.x).toBeGreaterThanOrEqual(0)
      expect(feature.x).toBeLessThanOrEqual(4096)
      expect(feature.dbh).toBe(Number(row.dbh))
    }
  })

  it('an overview tile counts every tree of its ground once', async () => {
    const z = 13
    const c = lngLatToTile(CENTER[0], CENTER[1], z)
    await worker.call('setViewportZoom', { zoom: z + 0.5 })
    await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
    const cells = Object.values(tileFeatures((await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })).tileBuffer)).flat()
    const held = await count(`SELECT count(*) AS n FROM trees_fast WHERE (xtile_z20 >> 7) = ${c.x} AND (ytile_z20 >> 7) = ${c.y}`)
    expect(held).toBeGreaterThan(1000)
    expect(cells.reduce((sum, cell) => sum + Number(cell.point_count), 0)).toBe(held)
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

  it('tells the main thread its init stages in order and how many block loads are running, back to none', async () => {
    const stages = worker.messages.filter((m) => m.type === 'initStage').map((m) => m.stage)
    expect(stages).toEqual(['engine', 'guide', 'world', 'ready'])
    const loads = worker.messages.filter((m) => m.type === 'windowLoads').map((m) => m.active as number)
    // The walk loaded blocks: each load said it started and that it ended.
    expect(loads.length).toBeGreaterThan(1)
    expect(loads.length % 2).toBe(0)
    expect(Math.min(...loads)).toBeGreaterThanOrEqual(0)
    expect(Math.max(...loads)).toBeGreaterThan(0)
    expect(loads[loads.length - 1]).toBe(0)
  })

  it('every tile holds one source layer per colour, named by the colour (pointTiles.ts)', async () => {
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
        // `notable` rides only on a notable tree's own feature (written when true), never on an aggregate.
        const props = layers[name].properties
        expect(props.filter((p) => p !== 'notable'), `z${z} ${name}`).toEqual(properties)
        if (properties.includes('grid_m')) expect(props, `z${z} ${name}`).not.toContain('notable')
      }
    }
  })

  it('a colour change recolours the tiles: a tree\'s layer is its colour when the tile is read', async () => {
    const z = 15
    const c = lngLatToTile(CENTER[0], CENTER[1], z)
    await worker.call('setViewportZoom', { zoom: z + 0.5 })
    await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
    await worker.call('setVisibleTileRange', { z, ...rangeAround(z, 1) })
    const before = tileLayers((await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })).tileBuffer)
    const trees = Object.values(before).reduce((sum, layer) => sum + layer.features, 0)
    expect(trees).toBeGreaterThan(0)
    // A per-tree override, as a chat publish installs one.
    await worker.call('setColorOverrideSql', { sql: "SELECT tree_id, '#123456' AS override_color FROM trees" })
    const tile = await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })
    const recoloured = tileLayers(tile.tileBuffer)
    try {
      expect(Object.keys(recoloured)).toEqual(['#123456'])
      expect(recoloured['#123456'].features).toBe(trees)
    } finally {
      await worker.call('setColorOverrideSql', { sql: null })
    }
    expect(tileLayers((await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })).tileBuffer)).toEqual(before)
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
