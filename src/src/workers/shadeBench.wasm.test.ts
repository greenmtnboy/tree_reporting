import { afterAll, beforeAll, describe, it, vi } from 'vitest'
import { copyFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { executedSql } from './wasm/nodeDuckDB'
import { FIXTURE_DIR, fixtureParquetUrls } from './wasm/fixtures'
import { startWorker, type WorkerHarness } from './wasm/workerHarness'
import { speciesShadeSql } from '../lib/foliageSeason'

vi.mock('@duckdb/duckdb-wasm', () => import('./wasm/nodeDuckDB'))
vi.mock('../duckdbAssetUrls', () => ({ DUCKDB_ASSET_URLS: { mvp: { mainModule: '', mainWorker: '' }, eh: { mainModule: '', mainWorker: '' } } }))
vi.mock('./parquetUrls', async () => (await import('./wasm/fixtures')).fixtureParquetUrls)

/**
 * Leaf-shade audit bench: the real tile worker on DuckDB-WASM against the
 * real Boston projection, enrichment and vocabulary (SHADE_BENCH_DATA=dir),
 * walked once with the tree-form colours and once with the leaf-shade table.
 * Not a test: it prints a timing table and the feature counts per tile.
 */

const DATA = process.env.SHADE_BENCH_DATA
const CITY = 'USBOS'
const CENTER: [number, number] = [-71.0589, 42.3601]
const OUT = process.env.SHADE_BENCH_OUT

type TileResult = { tileBuffer: ArrayBuffer; transient: boolean }
type QueryResult = { columns: string[]; rows: Record<string, unknown>[] }
type TileRange = { minX: number; maxX: number; minY: number; maxY: number }

const require = createRequire(require_resolve_base())
function require_resolve_base() { return createRequire(import.meta.url).resolve('maplibre-gl') }
const { VectorTile } = require('@mapbox/vector-tile')
const Pbf = require('pbf')

function lngLatToTile(lng: number, lat: number, z: number) {
  const n = 2 ** z
  const latRad = (lat * Math.PI) / 180
  return {
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  }
}
function rangeAround(z: number, rx: number, ry: number): TileRange {
  const c = lngLatToTile(CENTER[0], CENTER[1], z)
  return { minX: c.x - rx, maxX: c.x + rx, minY: c.y - ry, maxY: c.y + ry }
}
/** A 1400x900 desktop viewport at integer zoom: tiles across and down, as radii. */
function desktopRange(z: number): TileRange { return rangeAround(z, 3, 2) }

function decode(buf: ArrayBuffer): { features: number; colors: Set<string>; bytes: number } {
  const colors = new Set<string>()
  if (buf.byteLength === 0) return { features: 0, colors, bytes: 0 }
  const tile = new VectorTile(new Pbf(new Uint8Array(buf)))
  const layer = tile.layers.trees
  if (!layer) return { features: 0, colors, bytes: buf.byteLength }
  for (let i = 0; i < layer.length; i++) colors.add(String(layer.feature(i).properties.display_color))
  return { features: layer.length, colors, bytes: buf.byteLength }
}

const lines: string[] = []
function log(line: string) { lines.push(line); console.log(line) }

describe.skipIf(!DATA)('leaf-shade tile cost on the real Boston projection', () => {
  let worker: WorkerHarness
  const perfSince = (n: number) => worker.perf.slice(n)
  const now = () => performance.now()

  beforeAll(async () => {
    copyFileSync(`${DATA}/usbos_tree_browser_v2.parquet`, fixtureParquetUrls.cityBrowserParquetUrl(CITY))
    copyFileSync(`${DATA}/usbos_tree_vocab_v2.parquet`, fixtureParquetUrls.cityVocabParquetUrl(CITY))
    copyFileSync(`${DATA}/tree_enrichment_v2.parquet`, fixtureParquetUrls.REMOTE_SPECIES_PARQUET_URL)
    worker = await startWorker()
    const t0 = now()
    await worker.call('ensureInit', { city: CITY })
    await worker.call('setCityContext', { city: CITY })
    log(`city load: ${Math.round(now() - t0)} ms`)
    const n = await worker.call<QueryResult>('query', { sql: 'SELECT count(*) AS n FROM trees_fast' })
    log(`rows: ${n.rows[0].n}`)
  })
  afterAll(() => {
    if (OUT) writeFileSync(OUT, lines.join('\n') + '\n')
    rmSync(FIXTURE_DIR, { recursive: true, force: true })
  })

  async function walk(label: string) {
    log(`\n=== ${label} ===`)
    const perfStart = worker.perf.length
    const sqlStart = executedSql.length
    // Overview zooms: the intro's whole-footprint prefetch, then every tile of the view.
    for (const z of [11, 12, 13, 14]) {
      const range = desktopRange(z)
      await worker.call('setViewportZoom', { zoom: z + 0.3 })
      await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
      const t0 = now()
      await worker.call('prefetchVisibleTilesAtZoom', { z, range })
      const prefetchMs = Math.round(now() - t0)
      let features = 0, bytes = 0, tiles = 0
      const colors = new Set<string>()
      for (let x = range.minX; x <= range.maxX; x++) for (let y = range.minY; y <= range.maxY; y++) {
        const tile = await worker.call<TileResult>('getTile', { z, x, y })
        const d = decode(tile.tileBuffer)
        features += d.features; bytes += d.bytes; tiles += d.features > 0 ? 1 : 0
        d.colors.forEach((c) => colors.add(c))
      }
      log(`z${z} view ${range.maxX - range.minX + 1}x${range.maxY - range.minY + 1}: prefetch ${prefetchMs} ms; ${tiles} tiles, ${features} features, ${(bytes / 1024).toFixed(0)} KB, ${colors.size} colours`)
    }
    // Detail zooms: the visible range, batched by the worker.
    for (const z of [15, 16, 17, 18]) {
      const range = desktopRange(z)
      await worker.call('setViewportZoom', { zoom: z + 0.3 })
      await worker.call('setViewportCenter', { lng: CENTER[0], lat: CENTER[1] })
      await worker.call('setVisibleTileRange', { z, ...range })
      const c = lngLatToTile(CENTER[0], CENTER[1], z)
      const t0 = now()
      await worker.call<TileResult>('getTile', { z, x: c.x, y: c.y })
      const firstMs = Math.round(now() - t0)
      let features = 0, bytes = 0, tiles = 0
      const colors = new Set<string>()
      for (let x = range.minX; x <= range.maxX; x++) for (let y = range.minY; y <= range.maxY; y++) {
        const tile = await worker.call<TileResult>('getTile', { z, x, y })
        const d = decode(tile.tileBuffer)
        features += d.features; bytes += d.bytes; tiles += d.features > 0 ? 1 : 0
        d.colors.forEach((c) => colors.add(c))
      }
      log(`z${z} view: first tile ${firstMs} ms; ${tiles} tiles, ${features} features, ${(bytes / 1024).toFixed(0)} KB, ${colors.size} colours`)
    }
    // The desktop prewarm after the intro lands (z15 feature table + blocks around the camera at z16).
    await worker.call('setViewportZoom', { zoom: 16.3 })
    const t0 = now()
    await worker.call('prewarmLodCaches', {})
    log(`prewarm: ${Math.round(now() - t0)} ms`)
    // The sprite layer's query at a z16 camera.
    const bounds = { west: CENTER[0] - 0.012, east: CENTER[0] + 0.012, south: CENTER[1] - 0.006, north: CENTER[1] + 0.006 }
    const t1 = now()
    const crowns = await worker.call<unknown[]>('getMapTrees', { lng: CENTER[0], lat: CENTER[1], altitude: 600, bounds, visibleBounds: bounds })
    log(`crown query (z16 camera): ${Math.round(now() - t1)} ms, ${crowns.length} trees`)
    const t2 = now()
    await worker.call<unknown[]>('getMapTrees', { lng: CENTER[0] + 0.004, lat: CENTER[1], altitude: 600, bounds, visibleBounds: bounds })
    log(`crown query again (warm): ${Math.round(now() - t2)} ms`)
    for (const p of perfSince(perfStart)) {
      if (/neighborhood-batch|feature-table|prewarm:done|invalidate/.test(p.label)) log(`  ${p.label.replace('[Perf] duckdb-worker:', '')} ${JSON.stringify(p.detail)}`)
    }
    return executedSql.slice(sqlStart)
  }

  it('walks the city in both colour schemes', async () => {
    const colorsMsg = () => worker.messages.filter((m) => m.type === 'colorMapUpdate').slice(-1)[0] as { distinctColors?: string[] } | undefined
    await worker.call('setSpeciesShadeSql', { sql: null })
    log(`heatmap colours, tree form: ${colorsMsg()?.distinctColors?.length}`)
    await walk('tree form (cold)')

    const t0 = now()
    await worker.call('setSpeciesShadeSql', { sql: speciesShadeSql('autumn') })
    log(`\nsetSpeciesShadeSql(autumn): ${Math.round(now() - t0)} ms; heatmap colours: ${colorsMsg()?.distinctColors?.length}`)
    const shadeRows = await worker.call<QueryResult>('query', { sql: 'SELECT count(*) AS n, sum(len(shades)) AS shades FROM __species_shade' })
    log(`__species_shade rows: ${shadeRows.rows[0].n}, shades: ${shadeRows.rows[0].shades}`)
    const foliageSql = await walk('leaf shade (autumn)')

    await worker.call('setSpeciesShadeSql', { sql: null })
    await walk('tree form (again)')

    // --- Alternatives, timed on the worker's own SQL with the colour read swapped ---
    log('\n=== alternatives (worker SQL, colour read swapped) ===')
    const batchZ11 = foliageSql.find((s) => /ST_AsMVT\(/.test(s) && /\(tf\.xtile_z20 >> 9\)/.test(s) && /GROUP BY 1, 2, 3, 4, 5(, 6)?/.test(s))
    const batchZ14 = foliageSql.find((s) => /ST_AsMVT\(/.test(s) && /\(tf\.xtile_z20 >> 6\)/.test(s) && /GROUP BY 1, 2, 3, 4, 5(, 6)?/.test(s))
    const batchZ13 = foliageSql.find((s) => /ST_AsMVT\(/.test(s) && /\(tf\.xtile_z20 >> 7\)/.test(s) && /GROUP BY 1, 2, 3, 4, 5(, 6)?/.test(s))
    const batchZ12 = foliageSql.find((s) => /ST_AsMVT\(/.test(s) && /\(tf\.xtile_z20 >> 8\)/.test(s) && /GROUP BY 1, 2, 3, 4, 5(, 6)?/.test(s))
    // The same aggregate handed back as columns instead of MVT: what an Arrow-fed layer would ask for.
    const asColumns = (sql: string) => sql.slice(0, sql.indexOf('), rows AS (')) + ') SELECT xtile, ytile, gx, gy, display_color, point_count FROM agg'
    for (const [name, sql] of [['z11 block', batchZ11], ['z12 block', batchZ12], ['z13 block', batchZ13]] as const) {
      if (!sql) continue
      const t = now()
      const r = await worker.call<QueryResult>('query', { sql: asColumns(sql) })
      log(`${name} as columns, no MVT: ${Math.round(now() - t)} ms, ${r.rows.length} cells`)
    }
    const featureTable = foliageSql.find((s) => /CREATE OR REPLACE TEMP TABLE tile_features_z15/.test(s))
    const batchZ16 = foliageSql.find((s) => /ST_AsMVT\(/.test(s) && /\(tf\.xtile_z20 >> 4\)/.test(s) && !/tile_features/.test(s))
    const time = async (name: string, sql: string | undefined) => {
      if (!sql) { log(`${name}: (sql not captured)`); return }
      const t = now()
      const r = await worker.call<QueryResult>('query', { sql })
      // The query RPC normalises a BLOB to a plain number array.
      const feats = r.rows.reduce((n, row) => n + (Array.isArray(row.mvt) ? decode(new Uint8Array(row.mvt as number[]).buffer).features : 0), 0)
      log(`${name}: ${Math.round(now() - t)} ms${r.rows.length ? `, ${r.rows.length} tiles, ${feats} features` : ''}`)
    }
    await worker.call('setSpeciesShadeSql', { sql: speciesShadeSql('autumn') })
    const join = "LEFT JOIN __species_shade __ss ON CAST(tf.species AS VARCHAR) = __ss.species"
    const expr = "COALESCE(list_extract(__ss.shades, 1 + CAST(hash(tf.tree_id) % len(__ss.shades) AS INTEGER)), '#9e9e9e')"
    for (const [name, sql] of [['z11 block', batchZ11], ['z13 block', batchZ13], ['z14 block', batchZ14], ['z16 batch', batchZ16]] as const) {
      if (!sql) continue
      await time(`${name} as shipped`, sql)
      await time(`${name} one shade per species (no hash)`, sql.replace(expr, "COALESCE(list_extract(__ss.shades, 1), '#9e9e9e')"))
    }
    // ENUM-keyed shade table: join on the enum, no per-row cast.
    await worker.call('query', { sql: `CREATE OR REPLACE TEMP TABLE __species_shade_enum AS SELECT TRY_CAST(species AS tree_species_t) AS species, shades FROM __species_shade WHERE TRY_CAST(species AS tree_species_t) IS NOT NULL` })
    const enumJoin = (sql: string) => sql.replace(join, 'LEFT JOIN __species_shade_enum __ss ON tf.species = __ss.species')
    for (const [name, sql] of [['z11 block', batchZ11], ['z13 block', batchZ13], ['z14 block', batchZ14], ['z16 batch', batchZ16]] as const) {
      if (sql) await time(`${name} enum-keyed shade table`, enumJoin(sql))
    }
    // Materialised shade column: one UPDATE at scheme change, then no join and no hash per read.
    const pal = await worker.call<QueryResult>('query', { sql: 'SELECT DISTINCT unnest(shades) AS c FROM __species_shade ORDER BY 1' })
    const palette = [...pal.rows.map((r) => String(r.c)), '#9e9e9e']
    let t = now()
    await worker.call('query', { sql: `CREATE TYPE shade_t AS ENUM (${palette.map((c) => `'${c}'`).join(', ')})` })
    await worker.call('query', { sql: 'ALTER TABLE trees_fast ADD COLUMN shade shade_t' })
    await worker.call('query', { sql: `UPDATE trees_fast SET shade = CAST(${expr.replace(/tf\./g, 'trees_fast.')} AS shade_t) FROM __species_shade __ss WHERE CAST(trees_fast.species AS VARCHAR) = __ss.species` })
    await worker.call('query', { sql: `UPDATE trees_fast SET shade = '#9e9e9e' WHERE shade IS NULL` })
    log(`materialise shade column (ADD COLUMN + UPDATE, 1.7M rows): ${Math.round(now() - t)} ms`)
    const colExpr = 'CAST(tf.shade AS VARCHAR)'
    const exprAgg = "COALESCE(list_extract(__ss.shades, 1), '#9e9e9e')"
    const materialised = (sql: string) => sql.replace(join, '').replace(expr, colExpr).replace(exprAgg, colExpr)
    for (const [name, sql] of [['z11 block', batchZ11], ['z13 block', batchZ13], ['z14 block', batchZ14], ['z16 batch', batchZ16]] as const) {
      if (sql) await time(`${name} materialised shade column`, materialised(sql))
    }
    if (featureTable) {
      await time('z15 feature table as shipped', featureTable.replace('tile_features_z15', 'tile_features_bench'))
      await time('z15 feature table materialised column', materialised(featureTable).replace('tile_features_z15', 'tile_features_bench2'))
      await time('z15 feature table tree form', featureTable.replace(join, '').replace(expr, "CASE tf.tree_form WHEN 'palm' THEN '#F9A825' ELSE '#81C784' END").replace('tile_features_z15', 'tile_features_bench3'))
    }
    // Which colours does the city actually hold? (the heatmap layer list)
    t = now()
    const present = await worker.call<QueryResult>('query', { sql: 'SELECT count(DISTINCT shade) AS n FROM trees_fast' })
    log(`distinct shades held by the city: ${present.rows[0].n} (${Math.round(now() - t)} ms to count)`)
    const grey = await worker.call<QueryResult>('query', { sql: "SELECT count(*) FILTER (WHERE shade = '#9e9e9e') AS grey, count(*) AS n FROM trees_fast" })
    log(`grey trees: ${grey.rows[0].grey} of ${grey.rows[0].n}`)
  }, 1_800_000)
})
