import * as duckdb from '@duckdb/duckdb-wasm'
import duckdb_wasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import duckdb_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import duckdb_wasm_eh from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import duckdb_worker_eh from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'

let db: duckdb.AsyncDuckDB | null = null
let conn: duckdb.AsyncDuckDBConnection | null = null
let ready = false
let initError: string | null = null
let initPromise: Promise<void> | null = null


const DEFAULT_BASE_QUERY_SQL = `
SELECT tree_id, species, latitude, longitude, diameter_at_breast_height
FROM trees
WHERE latitude IS NOT NULL AND longitude IS NOT NULL
`

import { REMOTE_TREES_PARQUET_URL, REMOTE_SPECIES_PARQUET_URL, REMOTE_LANDMARKS_PARQUET_URL, cityTreeParquetUrl, cityLandmarkParquetUrl } from './parquetUrls'

const WEB_MERCATOR_MAX = 20037508.342789244
const WEB_MERCATOR_WORLD = WEB_MERCATOR_MAX * 2
const MAX_TILE_CACHE_ENTRIES = 1536
const MAX_PARALLEL_TILE_WORK = 3
const PUBLISHED_TREE_FILTER_TABLE = 'published_tree_filter_ids'
const COLOR_MAP_TABLE = '__tree_color_map'

// Default category → hex color mapping (matches CATEGORY_COLORS in useTreeCategories.ts)
const DEFAULT_CATEGORY_COLORS: Record<string, string> = {
  palm: '#e6a835',
  broadleaf: '#4CAF50',
  spreading: '#8BC34A',
  coniferous: '#2E7D32',
  columnar: '#43A047',
  ornamental: '#E91E63',
  default: '#66BB6A',
}

const DEFAULT_CATEGORY_LABELS: Record<string, string> = {
  '#e6a835': 'Palm',
  '#4CAF50': 'Broadleaf',
  '#8BC34A': 'Spreading',
  '#2E7D32': 'Coniferous',
  '#43A047': 'Columnar',
  '#E91E63': 'Ornamental',
  '#66BB6A': 'Other',
}

type TileBounds = { minX: number; maxX: number; minY: number; maxY: number }
type PrefetchStatus = 'executed' | 'deduped' | 'skipped'

type QueuedTileRequest = {
  z: number
  x: number
  y: number
  enqueuedAt: number
  resolve: (tile: Uint8Array) => void
  reject: (error: unknown) => void
}

const tileCache = new Map<string, Uint8Array>()
const emptyTileKeys = new Set<string>()
const persistentTileCacheKeys = new Set<string>()
const inflightTileRequests = new Map<string, Promise<Uint8Array>>()
const zoomBatchReady = new Set<string>()
const inflightZoomBatch = new Map<string, Promise<void>>()
const inflightNeighborhoodBatch = new Map<string, Promise<void>>()
const preparedFeatureTablesReady = new Set<string>()
const inflightFeatureTableBuild = new Map<string, Promise<void>>()
const dataTileBoundsByZoom = new Map<number, TileBounds>()
const visibleTileRangeByZoom = new Map<number, TileBounds>()
const hasAggCacheByZoom = new Set<number>()
const prefetchedVisibleRangeSigByZoom = new Map<number, string>()
const pendingTileQueue: QueuedTileRequest[] = []

let tileQuerySql: string | null = null
let tileQueryRevision = 0
let publishedTreeIdFilterSql: string | null = null
let publishedTreeIdFilterSignature = 'all'
let colorMapSignature = 'default'
let activeDistinctColors: string[] = Object.values(DEFAULT_CATEGORY_COLORS)
let activeColorLabelMap: Record<string, string> = { ...DEFAULT_CATEGORY_LABELS }
let activeViewportZoom = 13
let activeViewportCenter: { lng: number; lat: number } | null = null
let spatialExtensionReady = false
let prewarmDoneRevision = -1
let prewarmPromise: Promise<void> | null = null
let autoTileFetchEnabled = true
let activeQueuedWorkers = 0
let activeCityDefaultQuery: string = DEFAULT_BASE_QUERY_SQL
/** The city code whose rows are currently loaded in the `trees` table (null = all cities). */
let loadedCity: string | null = null

// City-context gate: tile generation and city-table queries must not run until
// setCityContext has fully completed (color map, agg caches, bounds all built).
// Resolved once on first city load; reset + re-resolved on every city switch.
let cityContextReady = false
let cityContextReadyResolve: () => void = () => {}
let cityContextReadyPromise: Promise<void> = new Promise<void>((r) => { cityContextReadyResolve = r })

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

/** Block tile/query work until setCityContext has fully completed. */
async function waitForCityContext(): Promise<void> {
  if (cityContextReady) return
  await cityContextReadyPromise
}

/** Create a fresh (unresolved) city gate — called at the start of a city switch. */
function resetCityReadyGate(): void {
  cityContextReady = false
  cityContextReadyPromise = new Promise<void>((r) => { cityContextReadyResolve = r })
}

/** Resolve the gate — called at the end of setCityContext. */
function signalCityReady(): void {
  cityContextReady = true
  cityContextReadyResolve()
}

function sanitizeBaseQuery(sql: string | null): string {
  if (!sql?.trim()) return DEFAULT_BASE_QUERY_SQL
  return sql.trim().replace(/;+\s*$/, '')
}

function hashText(text: string): string {
  let hash = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(16)
}

function treeFilterSignature(): string {
  return `${publishedTreeIdFilterSignature}|${colorMapSignature}`
}

function applyTreeFilterToBaseQuery(baseQuery: string): string {
  if (!publishedTreeIdFilterSql?.trim()) return baseQuery
  return `
WITH __base AS (
  ${baseQuery}
)
SELECT __base.*
FROM __base
INNER JOIN ${PUBLISHED_TREE_FILTER_TABLE} __filter_ids
  ON CAST(__base.tree_id AS VARCHAR) = __filter_ids.tree_id
`
}

function effectiveBaseQuery(sql: string | null): string {
  return applyTreeFilterToBaseQuery(sanitizeBaseQuery(sql))
}

function normalizeSql(sql: string): string {
  return sql.replace(/\s+/g, ' ').trim().toLowerCase()
}

function isDefaultBaseQuery(sql: string): boolean {
  return normalizeSql(sql) === normalizeSql(activeCityDefaultQuery)
}

function tileCacheKey(z: number, x: number, y: number): string {
  return `${tileQueryRevision}:${treeFilterSignature()}:${z}/${x}/${y}`
}

function tileCacheKeyForRevision(rev: number, z: number, x: number, y: number): string {
  return `${rev}:${treeFilterSignature()}:${z}/${x}/${y}`
}

function parseTileCacheKey(key: string): { rev: number; filterSig: string; z: number; x: number; y: number } | null {
  const firstSep = key.indexOf(':')
  const secondSep = key.indexOf(':', firstSep + 1)
  if (firstSep < 0 || secondSep < 0) return null
  const revPart = key.slice(0, firstSep)
  const filterSig = key.slice(firstSep + 1, secondSep)
  const zxy = key.slice(secondSep + 1)
  const [zPart, xPart, yPart] = zxy.split('/')
  const rev = Number(revPart)
  const z = Number(zPart)
  const x = Number(xPart)
  const y = Number(yPart)
  if (![rev, z, x, y].every((v) => Number.isFinite(v))) return null
  return { rev, filterSig, z, x, y }
}

function zoomFromTileCacheKey(key: string): number | null {
  return parseTileCacheKey(key)?.z ?? null
}

function shouldPersistLowZoomSfTile(key: string): boolean {
  const parsed = parseTileCacheKey(key)
  if (!parsed) return false
  if (parsed.rev !== tileQueryRevision) return false
  if (parsed.filterSig !== treeFilterSignature()) return false
  if (parsed.z > 14) return false
  const bounds = getDataTileBounds(parsed.z)
  if (!bounds) return false
  return parsed.x >= bounds.minX
    && parsed.x <= bounds.maxX
    && parsed.y >= bounds.minY
    && parsed.y <= bounds.maxY
}

function zoomBatchKey(rev: number, z: number): string {
  return `${rev}:${treeFilterSignature()}:${z}`
}

function neighborhoodBatchKey(rev: number, z: number, minX: number, maxX: number, minY: number, maxY: number): string {
  return `${rev}:${treeFilterSignature()}:${z}:${minX}-${maxX}:${minY}-${maxY}`
}

function featureTableBuildKey(rev: number, z: number): string {
  return `${rev}:${treeFilterSignature()}:${z}`
}

function featureTableName(z: number): string {
  return `tile_features_z${z}`
}

function shouldUseZoomBatch(z: number): boolean {
  return z >= 11 && z <= 14
}

function neighborhoodBlockSizeForZoom(z: number): number {
  if (z === 15) return 8
  if (z === 16) return 6
  if (z >= 19) return 4
  if (z >= 17) return 6
  return 1
}

function tileXExpr(alias: string, z: number): string {
  if (z >= 13 && z <= 20) return `${alias}.xtile_z${z}`
  return `CAST(floor(((${alias}.x_3857) + ${WEB_MERCATOR_MAX}) / (${WEB_MERCATOR_WORLD} / pow(2, ${z}))) AS INTEGER)`
}

function tileYExpr(alias: string, z: number): string {
  if (z >= 13 && z <= 20) return `${alias}.ytile_z${z}`
  return `CAST(floor((${WEB_MERCATOR_MAX} - (${alias}.y_3857)) / (${WEB_MERCATOR_WORLD} / pow(2, ${z}))) AS INTEGER)`
}

function getDataTileBounds(z: number): TileBounds | null {
  return dataTileBoundsByZoom.get(z) ?? null
}

function getVisibleTileRange(z: number): TileBounds | null {
  return visibleTileRangeByZoom.get(z) ?? null
}

function isTileOutsideDataBounds(z: number, x: number, y: number): boolean {
  const b = getDataTileBounds(z)
  if (!b) return false
  return x < b.minX || x > b.maxX || y < b.minY || y > b.maxY
}

function baseSimplifyGridMetersForZoom(z: number): number {
  if (z <= 13) return 32
  if (z === 14) return 24
  return 0
}

function lngLatToTile(lng: number, lat: number, z: number): { x: number; y: number } {
  const n = Math.pow(2, z)
  const x = Math.floor(((lng + 180) / 360) * n)
  const latRad = (lat * Math.PI) / 180
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n,
  )
  return {
    x: Math.max(0, Math.min(n - 1, x)),
    y: Math.max(0, Math.min(n - 1, y)),
  }
}

function adaptiveLodForTile(z: number, x: number, y: number): { simplifyGridMeters: number; tileDistance: number | null } {
  let simplifyGridMeters = baseSimplifyGridMetersForZoom(z)
  if (simplifyGridMeters > 0) {
    return { simplifyGridMeters, tileDistance: null }
  }

  const viewport = activeViewportCenter
  if (!viewport) {
    return { simplifyGridMeters, tileDistance: null }
  }

  const center = lngLatToTile(viewport.lng, viewport.lat, z)
  const tileDistance = Math.max(Math.abs(x - center.x), Math.abs(y - center.y))

  if (z === 15) {
    if (tileDistance > 12) simplifyGridMeters = 96
    else if (tileDistance > 8) simplifyGridMeters = 64
    else if (tileDistance > 5) simplifyGridMeters = 32
  } else if (z === 16) {
    if (tileDistance > 14) simplifyGridMeters = 64
    else if (tileDistance > 10) simplifyGridMeters = 32
    else if (tileDistance > 7) simplifyGridMeters = 16
  }

  return { simplifyGridMeters, tileDistance }
}

function tileBounds3857(z: number, x: number, y: number): { minX: number; minY: number; maxX: number; maxY: number } {
  const n = Math.pow(2, z)
  const span = WEB_MERCATOR_WORLD / n
  const minX = -WEB_MERCATOR_MAX + x * span
  const maxX = minX + span
  const maxY = WEB_MERCATOR_MAX - y * span
  const minY = maxY - span
  return { minX, minY, maxX, maxY }
}

function pickNextQueuedTileIndex(): number {
  if (pendingTileQueue.length <= 1) return 0
  const viewport = activeViewportCenter
  const viewportZoom = Math.round(activeViewportZoom)
  const now = Date.now()
  let bestIdx = 0
  let bestScore = Number.POSITIVE_INFINITY

  for (let i = 0; i < pendingTileQueue.length; i += 1) {
    const q = pendingTileQueue[i]
    const zoomPenalty = Math.abs(q.z - viewportZoom) * 1000
    let distancePenalty = 0
    if (viewport) {
      const centerTile = lngLatToTile(viewport.lng, viewport.lat, q.z)
      distancePenalty = (Math.abs(q.x - centerTile.x) + Math.abs(q.y - centerTile.y)) * 10
    }
    const ageBonus = (now - q.enqueuedAt) / 100
    const score = zoomPenalty + distancePenalty - ageBonus
    if (score < bestScore) {
      bestScore = score
      bestIdx = i
    }
  }

  return bestIdx
}

function processTileQueue(): void {
  while (activeQueuedWorkers < MAX_PARALLEL_TILE_WORK && pendingTileQueue.length > 0) {
    const idx = pickNextQueuedTileIndex()
    const item = pendingTileQueue.splice(idx, 1)[0]
    activeQueuedWorkers += 1

    void generatePointTileMvt(item.z, item.x, item.y)
      .then((tile) => item.resolve(tile))
      .catch((e) => item.reject(e))
      .finally(() => {
        activeQueuedWorkers -= 1
        processTileQueue()
      })
  }
}

function queueTileRequest(z: number, x: number, y: number): Promise<Uint8Array> {
  return new Promise<Uint8Array>((resolve, reject) => {
    pendingTileQueue.push({ z, x, y, enqueuedAt: Date.now(), resolve, reject })
    processTileQueue()
  })
}

function getCachedTile(key: string): Uint8Array | null {
  const tile = tileCache.get(key)
  if (!tile) {
    if (emptyTileKeys.has(key)) return new Uint8Array()
    return null
  }
  tileCache.delete(key)
  tileCache.set(key, tile)
  return new Uint8Array(tile)
}

function setCachedTile(key: string, tile: Uint8Array): void {
  if (tile.byteLength === 0) {
    const z = zoomFromTileCacheKey(key)
    // Detailed zooms (15+) are more susceptible to transient empty responses
    // during camera motion/range churn. Avoid pinning those misses as
    // long-lived empty cache entries, which can manifest as blank segments.
    if (z != null && z >= 15) {
      emptyTileKeys.delete(key)
      if (tileCache.has(key)) tileCache.delete(key)
      return
    }

    emptyTileKeys.add(key)
    if (tileCache.has(key)) tileCache.delete(key)
    persistentTileCacheKeys.delete(key)
    return
  }

  emptyTileKeys.delete(key)
  if (shouldPersistLowZoomSfTile(key)) persistentTileCacheKeys.add(key)
  else persistentTileCacheKeys.delete(key)

  if (tileCache.has(key)) tileCache.delete(key)
  tileCache.set(key, tile)
  if (tileCache.size > MAX_TILE_CACHE_ENTRIES) {
    let evictionKey: string | undefined
    for (const candidate of tileCache.keys()) {
      if (!persistentTileCacheKeys.has(candidate)) {
        evictionKey = candidate
        break
      }
    }

    // Safety fallback: still enforce global cap even if all retained tiles are
    // currently marked persistent.
    if (!evictionKey) {
      evictionKey = tileCache.keys().next().value as string | undefined
    }

    if (evictionKey) {
      tileCache.delete(evictionKey)
      emptyTileKeys.delete(evictionKey)
      persistentTileCacheKeys.delete(evictionKey)
    }
  }
}

/**
 * Load (or reload) the `trees` and `trees_fast` tables for `city` from the
 * per-city optimised parquet. Pass undefined to load all cities from the full
 * file (only as a last-resort fallback when city is genuinely unknown).
 */
async function loadCityTrees(city?: string): Promise<void> {
  if (!conn) return
  if (city && loadedCity === city) return // already loaded

  if (!city) {
    throw new Error('City must be specified to load trees, to avoid accidentally loading all cities into memory.')
  }

  // Drop dependent tables so CREATE OR REPLACE can rebuild cleanly.
  await conn.query(`DROP TABLE IF EXISTS ${COLOR_MAP_TABLE}`)
  await conn.query(`DROP TABLE IF EXISTS agg_z14_cache`)
  preparedFeatureTablesReady.clear()
  hasAggCacheByZoom.clear()
  zoomBatchReady.clear()
  dataTileBoundsByZoom.clear()

  const parquetUrl = city ? (cityTreeParquetUrl(city) ?? REMOTE_TREES_PARQUET_URL) : REMOTE_TREES_PARQUET_URL

  await conn.query(`
    CREATE OR REPLACE TABLE trees AS
    SELECT
      tree_id,
      city,
      coalesce(nullif(trim(string_split(species, '::')[2]), ''), trim(string_split(species, '::')[1])) AS common_name,
      plant_date,
      species,
      latitude,
      longitude,
      diameter_at_breast_height
    FROM read_parquet('${parquetUrl}')
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
  `)

  loadedCity = city ?? null

  // Rebuild trees_fast (join with enrichment + pre-computed tile coordinates).
  await conn.query(`
    CREATE OR REPLACE TABLE trees_fast AS
    WITH joined AS (
      SELECT
        t.tree_id,
        t.city,
        t.common_name,
        t.plant_date,
        t.species,
        t.latitude,
        t.longitude,
        COALESCE(t.diameter_at_breast_height, 3) AS dbh,
        CASE lower(trim(COALESCE(se.tree_category, 'default')))
          WHEN 'palm' THEN 'palm'
          WHEN 'broadleaf' THEN 'broadleaf'
          WHEN 'spreading' THEN 'spreading'
          WHEN 'coniferous' THEN 'coniferous'
          WHEN 'columnar' THEN 'columnar'
          WHEN 'ornamental' THEN 'ornamental'
          ELSE 'default'
        END AS tree_category,
        se.native_status,
        se.is_evergreen,
        se.mature_height_ft,
        se.bloom_season,
        se.wildlife_value,
        se.fire_risk,
        LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878) AS latitude_clamped,
        ((t.longitude + 180.0) / 360.0) AS x_norm,
        ((1.0 - ln(tan(radians(LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878))) + (1.0 / cos(radians(LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878))))) / pi()) / 2.0) AS y_norm
      FROM trees t
      LEFT JOIN species_enrichment se
        ON t.species = se.species
      WHERE t.latitude IS NOT NULL
        AND t.longitude IS NOT NULL
    )
    SELECT
      tree_id,
      city,
      common_name,
      plant_date,
      species,
      latitude,
      longitude,
      TRY_CAST(dbh AS DOUBLE) AS dbh,
      tree_category,
      native_status,
      is_evergreen,
      mature_height_ft,
      bloom_season,
      wildlife_value,
      fire_risk,
      ((longitude * ${WEB_MERCATOR_MAX}) / 180.0) AS x_3857,
      (6378137.0 * ln(tan(pi() / 4.0 + radians(latitude_clamped) / 2.0))) AS y_3857,
      CAST(floor(x_norm * pow(2, 13)) AS INTEGER) AS xtile_z13,
      CAST(floor(y_norm * pow(2, 13)) AS INTEGER) AS ytile_z13,
      CAST(floor(x_norm * pow(2, 14)) AS INTEGER) AS xtile_z14,
      CAST(floor(y_norm * pow(2, 14)) AS INTEGER) AS ytile_z14,
      CAST(floor(x_norm * pow(2, 15)) AS INTEGER) AS xtile_z15,
      CAST(floor(y_norm * pow(2, 15)) AS INTEGER) AS ytile_z15,
      CAST(floor(x_norm * pow(2, 16)) AS INTEGER) AS xtile_z16,
      CAST(floor(y_norm * pow(2, 16)) AS INTEGER) AS ytile_z16,
      CAST(floor(x_norm * pow(2, 17)) AS INTEGER) AS xtile_z17,
      CAST(floor(y_norm * pow(2, 17)) AS INTEGER) AS ytile_z17,
      CAST(floor(x_norm * pow(2, 18)) AS INTEGER) AS xtile_z18,
      CAST(floor(y_norm * pow(2, 18)) AS INTEGER) AS ytile_z18,
      CAST(floor(x_norm * pow(2, 19)) AS INTEGER) AS xtile_z19,
      CAST(floor(y_norm * pow(2, 19)) AS INTEGER) AS ytile_z19,
      CAST(floor(x_norm * pow(2, 20)) AS INTEGER) AS xtile_z20,
      CAST(floor(y_norm * pow(2, 20)) AS INTEGER) AS ytile_z20
    FROM joined
  `)

  const landmarkUrl = cityLandmarkParquetUrl(city) ?? REMOTE_LANDMARKS_PARQUET_URL
  try {
    await conn.query(`
      CREATE OR REPLACE TABLE landmarks AS
      SELECT landmark_id, city, name, latitude, longitude
      FROM read_parquet('${landmarkUrl}')
      WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    `)
  } catch (e) {
    console.warn('[duckdb-worker] landmarks-load:failed', e)
  }
}

async function doInit(city?: string) {
  if (db) return
  const t0 = nowMs()
  console.info('[Perf] duckdb-worker:init:start', { city: city ?? 'all' })

  const bundles: duckdb.DuckDBBundles = {
    mvp: {
      mainModule: duckdb_wasm,
      mainWorker: duckdb_worker,
    },
    eh: {
      mainModule: duckdb_wasm_eh,
      mainWorker: duckdb_worker_eh,
    },
  }
  const bundle = await duckdb.selectBundle(bundles)

  const logger = new duckdb.ConsoleLogger()
  const worker = new Worker(bundle.mainWorker!)
  db = new duckdb.AsyncDuckDB(logger, worker)
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker)

  conn = await db.connect()

  // Load species enrichment and landmarks once — these are city-agnostic / small.
  try {
    await conn.query(`
      CREATE TABLE species_enrichment AS
      SELECT species, tree_category, native_status, is_evergreen, mature_height_ft, bloom_season, wildlife_value, fire_risk
      FROM read_parquet('${REMOTE_SPECIES_PARQUET_URL}')
    `)
  } catch (e) {
    console.warn('[Perf] duckdb-worker:species-load:failed', e)
  }

  spatialExtensionReady = false
  try {
    await conn.query('LOAD spatial;')
    spatialExtensionReady = true
  } catch {
    try {
      await conn.query('INSTALL spatial;')
      await conn.query('LOAD spatial;')
      spatialExtensionReady = true
    } catch (e) {
      console.error('DuckDB spatial extension unavailable:', e)
    }
  }

  // Load city-specific tree data. setCityContext will call this if city is not
  // yet known at init time, so we only do it here when city is already available.
  if (city) {
    await loadCityTrees(city)
    postColorMapUpdate()
  } 

  ready = true
  initError = null
  
  console.info('[Perf] duckdb-worker:init:done', { ms: Math.round(nowMs() - t0) })
}

/** Post the active color label map and distinct colors to the main thread */
function postColorMapUpdate() {
  ctx.postMessage({
    type: 'colorMapUpdate',
    distinctColors: activeDistinctColors,
    colorLabelMap: activeColorLabelMap,
  })
}

/** Create / reset the __tree_color_map table from default category→color assignment */
async function buildDefaultColorMap() {
  if (!conn) return
  await conn.query(`
    CREATE OR REPLACE TABLE ${COLOR_MAP_TABLE} AS
    SELECT
      tree_id,
      CASE COALESCE(tree_category, 'default')
        WHEN 'palm'        THEN '${DEFAULT_CATEGORY_COLORS.palm}'
        WHEN 'broadleaf'   THEN '${DEFAULT_CATEGORY_COLORS.broadleaf}'
        WHEN 'spreading'   THEN '${DEFAULT_CATEGORY_COLORS.spreading}'
        WHEN 'coniferous'  THEN '${DEFAULT_CATEGORY_COLORS.coniferous}'
        WHEN 'columnar'    THEN '${DEFAULT_CATEGORY_COLORS.columnar}'
        WHEN 'ornamental'  THEN '${DEFAULT_CATEGORY_COLORS.ornamental}'
        ELSE '${DEFAULT_CATEGORY_COLORS.default}'
      END AS display_color
    FROM trees_fast
  `)
  activeDistinctColors = Object.values(DEFAULT_CATEGORY_COLORS)
  activeColorLabelMap = { ...DEFAULT_CATEGORY_LABELS }
  colorMapSignature = 'default'
}

/** Rebuild the z14 aggregate cache using display_color from the color map */
async function rebuildAggCaches() {
  if (!conn) return
  hasAggCacheByZoom.clear()
  const z = 14
  const gridM = baseSimplifyGridMetersForZoom(z)
  if (gridM <= 0) return
  try {
    await conn.query(`
      CREATE OR REPLACE TABLE agg_z${z}_cache AS
      SELECT
        tf.xtile_z${z} AS xtile,
        tf.ytile_z${z} AS ytile,
        floor(tf.x_3857 / ${gridM}) * ${gridM} + ${gridM} / 2.0 AS gx,
        floor(tf.y_3857 / ${gridM}) * ${gridM} + ${gridM} / 2.0 AS gy,
        COALESCE(tf.tree_category, 'default') AS category,
        cm.display_color,
        AVG(TRY_CAST(tf.dbh AS DOUBLE)) AS dbh,
        COUNT(*) AS point_count
      FROM trees_fast tf
      INNER JOIN ${COLOR_MAP_TABLE} cm ON tf.tree_id = cm.tree_id
      GROUP BY 1, 2, 3, 4, 5, 6
    `)
    hasAggCacheByZoom.add(z)
  } catch {
    // runtime fallback stays active
  }
}

async function ensureInit(city?: string): Promise<void> {
  if (ready) return
  if (!initPromise) {
    initPromise = doInit(city).catch((e) => {
      initError = (e as Error).message
      ready = false
      throw e
    })
  }
  await initPromise
}

async function ensurePreparedFeatureTableForZoom(z: number, baseQuery: string): Promise<void> {
  if (!conn || z !== 15) return

  const rev = tileQueryRevision
  const key = featureTableBuildKey(rev, z)
  if (preparedFeatureTablesReady.has(key)) return

  const inflight = inflightFeatureTableBuild.get(key)
  if (inflight) {
    await inflight
    return
  }

  const xExpr = tileXExpr('tf', z)
  const yExpr = tileYExpr('tf', z)
  const table = featureTableName(z)

  const request = (async () => {
    const sql = `
CREATE OR REPLACE TEMP TABLE ${table} AS
WITH base AS (
  ${baseQuery}
), rows AS (
  SELECT
    ${xExpr} AS xtile,
    ${yExpr} AS ytile,
    {
      'geom': ST_AsMVTGeom(
        ST_Point(tf.x_3857, tf.y_3857),
        ST_Extent(ST_TileEnvelope(${z}, ${xExpr}, ${yExpr})),
        4096,
        64,
        true
      ),
      'id': COALESCE(base.tree_id, tf.tree_id, 'unkwn'),
      'dbh': TRY_CAST(COALESCE(base.diameter_at_breast_height, tf.dbh, 3) AS DOUBLE),
      'category': COALESCE(tf.tree_category, 'default'),
      'display_color': cm.display_color,
      'rotation': 0,
      'point_count': 1,
      'grid_m': 32
    } AS feature
  FROM base
  INNER JOIN trees_fast tf
    ON base.tree_id = tf.tree_id
  INNER JOIN ${COLOR_MAP_TABLE} cm
    ON tf.tree_id = cm.tree_id
)
SELECT xtile, ytile, feature
FROM rows
WHERE xtile >= 0
  AND ytile >= 0
  AND xtile < CAST(pow(2, ${z}) AS INTEGER)
  AND ytile < CAST(pow(2, ${z}) AS INTEGER)
  AND feature.geom IS NOT NULL
  AND NOT ST_IsEmpty(feature.geom)
`
    await conn.query(sql)
    if (rev === tileQueryRevision && key === featureTableBuildKey(tileQueryRevision, z)) {
      preparedFeatureTablesReady.add(key)
    }
  })()

  inflightFeatureTableBuild.set(key, request)
  try {
    await request
  } finally {
    inflightFeatureTableBuild.delete(key)
  }
}

async function ensureNeighborhoodBatchTiles(z: number, x: number, y: number, baseQuery: string): Promise<void> {
  if (!conn) return
  const blockSize = neighborhoodBlockSizeForZoom(z)
  if (blockSize <= 1) return

  const rev = tileQueryRevision
  const tileCount = Math.pow(2, z)
  const blockX = Math.floor(x / blockSize)
  const blockY = Math.floor(y / blockSize)
  let minX = Math.max(0, blockX * blockSize)
  let maxX = Math.min(tileCount - 1, minX + blockSize - 1)
  let minY = Math.max(0, blockY * blockSize)
  let maxY = Math.min(tileCount - 1, minY + blockSize - 1)

  const visibleRange = getVisibleTileRange(z)
  if (visibleRange) {
    minX = visibleRange.minX
    maxX = visibleRange.maxX
    minY = visibleRange.minY
    maxY = visibleRange.maxY
  }

  const dataBounds = getDataTileBounds(z)
  if (dataBounds) {
    minX = Math.max(minX, dataBounds.minX)
    maxX = Math.min(maxX, dataBounds.maxX)
    minY = Math.max(minY, dataBounds.minY)
    maxY = Math.min(maxY, dataBounds.maxY)
  }

  if (minX > maxX || minY > maxY) {
    setCachedTile(tileCacheKeyForRevision(rev, z, x, y), new Uint8Array())
    return
  }

  const bKey = neighborhoodBatchKey(rev, z, minX, maxX, minY, maxY)
  const inflight = inflightNeighborhoodBatch.get(bKey)
  if (inflight) {
    await inflight
    return
  }

  const request = (async () => {
    const sql = z === 15 ? `
WITH rows AS (
  SELECT
    xtile,
    ytile,
    feature
  FROM ${featureTableName(15)}
  WHERE xtile BETWEEN ${minX} AND ${maxX}
    AND ytile BETWEEN ${minY} AND ${maxY}
)
SELECT
  xtile,
  ytile,
  ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
FROM rows
GROUP BY xtile, ytile
` : `
WITH base AS (
  ${baseQuery}
), pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    COALESCE(base.tree_id, tf.tree_id, 'unkwn') AS id,
    COALESCE(base.diameter_at_breast_height, tf.dbh, 3) AS dbh,
    COALESCE(tf.tree_category, 'default') AS category,
    cm.display_color,
    0 AS rotation,
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile
  FROM base
  INNER JOIN trees_fast tf
    ON base.tree_id = tf.tree_id
  INNER JOIN ${COLOR_MAP_TABLE} cm
    ON tf.tree_id = cm.tree_id
  WHERE ${tileXExpr('tf', z)} BETWEEN ${minX} AND ${maxX}
    AND ${tileYExpr('tf', z)} BETWEEN ${minY} AND ${maxY}
), rows AS (
  SELECT
    xtile,
    ytile,
    {
      'geom': ST_AsMVTGeom(
        ST_Point(x_3857, y_3857),
        ST_Extent(ST_TileEnvelope(${z}, xtile, ytile)),
        4096,
        64,
        true
      ),
      'id': id,
      'dbh': TRY_CAST(dbh AS DOUBLE),
      'category': category,
      'display_color': display_color,
      'rotation': rotation,
      'point_count': 1,
      'grid_m': 32
    } AS feature
  FROM pts
)
SELECT
  xtile,
  ytile,
  ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
FROM rows
WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)
GROUP BY xtile, ytile
`

    const result = await conn.query(sql)
    const rows = result.toArray()

    if (rev === tileQueryRevision) {
      for (let tx = minX; tx <= maxX; tx += 1) {
        for (let ty = minY; ty <= maxY; ty += 1) {
          setCachedTile(tileCacheKeyForRevision(rev, z, tx, ty), new Uint8Array())
        }
      }
      for (const row of rows) {
        const xtile = Number(row.xtile)
        const ytile = Number(row.ytile)
        const raw = row.mvt as Uint8Array | undefined
        const tile = !raw || raw.length === 0
          ? new Uint8Array()
          : new Uint8Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength))
        setCachedTile(tileCacheKeyForRevision(rev, z, xtile, ytile), tile)
      }
    }
  })()

  inflightNeighborhoodBatch.set(bKey, request)
  try {
    await request
  } finally {
    inflightNeighborhoodBatch.delete(bKey)
  }
}

async function ensureZoomBatchTiles(z: number, baseQuery: string, simplifyGridMeters: number): Promise<void> {
  if (!conn) return
  const rev = tileQueryRevision
  const bKey = zoomBatchKey(rev, z)
  if (zoomBatchReady.has(bKey)) return

  const inflight = inflightZoomBatch.get(bKey)
  if (inflight) {
    await inflight
    return
  }

  const bounds = getDataTileBounds(z)
  const boundedFilter = bounds
    ? `WHERE xtile BETWEEN ${bounds.minX} AND ${bounds.maxX}\n    AND ytile BETWEEN ${bounds.minY} AND ${bounds.maxY}`
    : `WHERE xtile >= 0\n    AND ytile >= 0\n    AND xtile < CAST(pow(2, ${z}) AS INTEGER)\n    AND ytile < CAST(pow(2, ${z}) AS INTEGER)`
  const useAggCache = simplifyGridMeters > 0 && hasAggCacheByZoom.has(z) && isDefaultBaseQuery(baseQuery)

  const request = (async () => {
    const sql = useAggCache ? `
WITH agg AS (
  SELECT
    xtile,
    ytile,
    gx,
    gy,
    category,
    display_color,
    dbh,
    point_count
  FROM agg_z${z}_cache
  ${boundedFilter}
), rows AS (
  SELECT
    xtile,
    ytile,
    {
      'geometry': ST_AsMVTGeom(
        ST_Point(gx, gy),
        ST_Extent(ST_TileEnvelope(${z}, xtile, ytile)),
        4096,
        64,
        true
      ),
      'id': -1,
      'dbh': TRY_CAST(dbh AS DOUBLE),
      'category': COALESCE(category, 'default'),
      'display_color': display_color,
      'rotation': 0,
      'point_count': TRY_CAST(point_count AS INTEGER),
      'grid_m': ${simplifyGridMeters}
    } AS feature
  FROM agg
)
SELECT
  xtile,
  ytile,
  ST_AsMVT(feature, 'trees', 4096, 'geometry') AS mvt
FROM rows
WHERE feature.geometry IS NOT NULL AND NOT ST_IsEmpty(feature.geometry)
GROUP BY xtile, ytile
` : simplifyGridMeters > 0 ? `
WITH base AS (
  ${baseQuery}
), pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    COALESCE(tf.tree_category, 'default') AS category,
    cm.display_color,
    COALESCE(base.diameter_at_breast_height, tf.dbh, 3) AS dbh,
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile
  FROM base
  INNER JOIN trees_fast tf
    ON base.tree_id = tf.tree_id
  INNER JOIN ${COLOR_MAP_TABLE} cm
    ON tf.tree_id = cm.tree_id
), bounded AS (
  SELECT *
  FROM pts
  ${boundedFilter}
), agg AS (
  SELECT
    xtile,
    ytile,
    floor(x_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gx,
    floor(y_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gy,
    category,
    display_color,
    AVG(dbh) AS dbh,
    COUNT(*) AS point_count
  FROM bounded
  GROUP BY 1, 2, 3, 4, 5, 6
), rows AS (
  SELECT
    xtile,
    ytile,
    {
      'geometry': ST_AsMVTGeom(
        ST_Point(gx, gy),
        ST_Extent(ST_TileEnvelope(${z}, xtile, ytile)),
        4096,
        64,
        true
      ),
      'id': -1,
      'dbh': TRY_CAST(dbh AS DOUBLE),
      'category': COALESCE(category, 'default'),
      'display_color': display_color,
      'rotation': 0,
      'point_count': TRY_CAST(point_count AS INTEGER),
      'grid_m': ${simplifyGridMeters}
    } AS feature
  FROM agg
)
SELECT
  xtile,
  ytile,
  ST_AsMVT(feature, 'trees', 4096, 'geometry') AS mvt
FROM rows
WHERE feature.geometry IS NOT NULL AND NOT ST_IsEmpty(feature.geometry)
GROUP BY xtile, ytile
` : `
WITH base AS (
  ${baseQuery}
), points AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    COALESCE(base.tree_id, tf.tree_id, 'unkwn') AS id,
    COALESCE(base.diameter_at_breast_height, tf.dbh, 3) AS dbh,
    COALESCE(tf.tree_category, 'default') AS category,
    cm.display_color,
    0 AS rotation,
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile
  FROM base
  INNER JOIN trees_fast tf
    ON base.tree_id = tf.tree_id
  INNER JOIN ${COLOR_MAP_TABLE} cm
    ON tf.tree_id = cm.tree_id
), bounded AS (
  SELECT *
  FROM points
  ${boundedFilter}
), rows AS (
  SELECT
    xtile,
    ytile,
    {
      'geom': ST_AsMVTGeom(
        ST_Point(x_3857, y_3857),
        ST_Extent(ST_TileEnvelope(${z}, xtile, ytile)),
        4096,
        64,
        true
      ),
      'id': id,
      'dbh': TRY_CAST(dbh AS DOUBLE),
      'category': category,
      'display_color': display_color,
      'rotation': rotation,
      'point_count': 1,
      'grid_m': 32
    } AS feature
  FROM bounded
)
SELECT
  xtile,
  ytile,
  ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
FROM rows
WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)
GROUP BY xtile, ytile
`

    const result = await conn.query(sql)
    const rows = result.toArray()
    if (rev === tileQueryRevision) {
      for (const row of rows) {
        const xtile = Number(row.xtile)
        const ytile = Number(row.ytile)
        const raw = row.mvt as Uint8Array | undefined
        const tile = !raw || raw.length === 0
          ? new Uint8Array()
          : new Uint8Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength))
        setCachedTile(tileCacheKeyForRevision(rev, z, xtile, ytile), tile)
      }
      zoomBatchReady.add(bKey)
    }
  })()

  inflightZoomBatch.set(bKey, request)
  try {
    await request
  } finally {
    inflightZoomBatch.delete(bKey)
  }
}

async function generatePointTileMvt(z: number, x: number, y: number): Promise<Uint8Array> {
  if (!conn) return new Uint8Array()

  const key = tileCacheKey(z, x, y)
  if (isTileOutsideDataBounds(z, x, y)) return new Uint8Array()

  if (activeViewportZoom >= 16 && z <= 12) return new Uint8Array()

  const cached = getCachedTile(key)
  if (cached) return cached

  const inflight = inflightTileRequests.get(key)
  if (inflight) return inflight

  const baseQuery = effectiveBaseQuery(tileQuerySql)
  const { simplifyGridMeters } = adaptiveLodForTile(z, x, y)
  const bounds = tileBounds3857(z, x, y)
  const batchKey = zoomBatchKey(tileQueryRevision, z)

  if (shouldUseZoomBatch(z)) {
    await ensureZoomBatchTiles(z, baseQuery, simplifyGridMeters)
    const batched = getCachedTile(key)
    if (batched) return batched
    if (zoomBatchReady.has(batchKey)) {
      const emptyTile = new Uint8Array()
      setCachedTile(key, emptyTile)
      return new Uint8Array(emptyTile)
    }
  }

  if (simplifyGridMeters === 0 && z >= 15) {
    await ensurePreparedFeatureTableForZoom(z, baseQuery)
    await ensureNeighborhoodBatchTiles(z, x, y, baseQuery)
    const neighborBatched = getCachedTile(key)
    if (neighborBatched) return neighborBatched
  }

  const request = (async () => {
    const sql = simplifyGridMeters > 0 ? `
WITH base AS (
  ${baseQuery}
), pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    COALESCE(tf.tree_category, 'default') AS category,
    cm.display_color,
    COALESCE(base.diameter_at_breast_height, tf.dbh, 3) AS dbh
  FROM base
  INNER JOIN trees_fast tf
    ON base.tree_id = tf.tree_id
  INNER JOIN ${COLOR_MAP_TABLE} cm
    ON tf.tree_id = cm.tree_id
  WHERE tf.x_3857 BETWEEN ${bounds.minX} AND ${bounds.maxX}
    AND tf.y_3857 BETWEEN ${bounds.minY} AND ${bounds.maxY}
), agg AS (
  SELECT
    floor(x_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gx,
    floor(y_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gy,
    category,
    display_color,
    AVG(dbh) AS dbh,
    COUNT(*) AS point_count
  FROM pts
  GROUP BY 1, 2, 3, 4
), tiles AS (
  SELECT {
    'geometry': ST_AsMVTGeom(
      ST_Point(gx, gy),
      ST_Extent(ST_TileEnvelope(${z}, ${x}, ${y})),
      4096,
      64,
      true
    ),
    'id': -1,
    'dbh': TRY_CAST(dbh AS DOUBLE),
    'category': COALESCE(category, 'default'),
    'display_color': display_color,
    'rotation': 0,
    'point_count': TRY_CAST(point_count AS INTEGER),
    'grid_m': ${simplifyGridMeters}
  } AS feature
  FROM agg
)
SELECT ST_AsMVT(feature, 'trees', 4096, 'geometry') AS mvt
FROM tiles
WHERE feature.geometry IS NOT NULL AND NOT ST_IsEmpty(feature.geometry)
` : `
WITH base AS (
  ${baseQuery}
), points AS (
  SELECT
    COALESCE(base.tree_id, tf.tree_id, 'unkwn') AS id,
    COALESCE(base.diameter_at_breast_height, tf.dbh, 3) AS dbh,
    COALESCE(tf.tree_category, 'default') AS category,
    cm.display_color,
    0 AS rotation,
    1 AS point_count,
    32 AS grid_m,
    ST_AsMVTGeom(
      ST_Point(tf.x_3857, tf.y_3857),
      ST_Extent(ST_TileEnvelope(${z}, ${x}, ${y})),
      4096,
      64,
      true
    ) AS geom
  FROM base
  INNER JOIN trees_fast tf
    ON base.tree_id = tf.tree_id
  INNER JOIN ${COLOR_MAP_TABLE} cm
    ON tf.tree_id = cm.tree_id
  WHERE tf.x_3857 BETWEEN ${bounds.minX} AND ${bounds.maxX}
    AND tf.y_3857 BETWEEN ${bounds.minY} AND ${bounds.maxY}
  LIMIT 50000
)
SELECT ST_AsMVT(points, 'trees', 4096, 'geom') AS mvt
FROM points
WHERE geom IS NOT NULL
`

    const result = await conn.query(sql)
    const raw = result.get(0)?.mvt as Uint8Array | undefined
    const tile = !raw || raw.length === 0
      ? new Uint8Array()
      : new Uint8Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength))
    setCachedTile(key, tile)
    return new Uint8Array(tile)
  })()

  inflightTileRequests.set(key, request)
  try {
    return await request
  } finally {
    inflightTileRequests.delete(key)
  }
}

function invalidateTileCaches() {
  tileCache.clear()
  emptyTileKeys.clear()
  persistentTileCacheKeys.clear()
  inflightTileRequests.clear()
  zoomBatchReady.clear()
  inflightZoomBatch.clear()
  inflightNeighborhoodBatch.clear()
  preparedFeatureTablesReady.clear()
  inflightFeatureTableBuild.clear()
  visibleTileRangeByZoom.clear()
  prefetchedVisibleRangeSigByZoom.clear()
  prewarmDoneRevision = -1
  prewarmPromise = null
}

function setTileQuery(sql: string | null) {
  tileQuerySql = sql
  tileQueryRevision += 1
  invalidateTileCaches()
}

async function setPublishedTreeIdFilterSql(sql: string | null) {
  await ensureInit()
  if (!conn) return
  const normalized = sql?.trim() || null

  await conn.query(`DROP TABLE IF EXISTS ${PUBLISHED_TREE_FILTER_TABLE}`)

  if (normalized) {
    await conn.query(`
CREATE TEMP TABLE ${PUBLISHED_TREE_FILTER_TABLE} AS
SELECT DISTINCT CAST(tree_id AS VARCHAR) AS tree_id
FROM (
  ${normalized}
) __published_ids
WHERE tree_id IS NOT NULL
`)

    const countResult = await conn.query(`SELECT COUNT(*) AS cnt FROM ${PUBLISHED_TREE_FILTER_TABLE}`)
    const count = Number(countResult.get(0)?.cnt ?? 0)

    if (Number.isFinite(count) && count > 0) {
      publishedTreeIdFilterSql = normalized
      publishedTreeIdFilterSignature = `sql-${hashText(normalizeSql(normalized))}`
    } else {
      await conn.query(`DROP TABLE IF EXISTS ${PUBLISHED_TREE_FILTER_TABLE}`)
      publishedTreeIdFilterSql = null
      publishedTreeIdFilterSignature = 'all'
    }
  } else {
    publishedTreeIdFilterSql = null
    publishedTreeIdFilterSignature = 'all'
  }

  invalidateTileCaches()
}

async function setColorOverrideSql(sql: string | null) {
  await ensureInit()
  if (!conn) return
  const normalized = sql?.trim() || null

  if (normalized) {
    // Agent provided a color override SQL returning (tree_id, override_color).
    // Rebuild __tree_color_map: use agent colors where provided, fall back to
    // category defaults for unmatched trees.
    await conn.query(`DROP TABLE IF EXISTS __agent_color_override`)
    await conn.query(`
CREATE TEMP TABLE __agent_color_override AS
SELECT DISTINCT CAST(tree_id AS VARCHAR) AS tree_id, CAST(override_color AS VARCHAR) AS display_color
FROM (
  ${normalized}
) __color_ids
WHERE tree_id IS NOT NULL AND override_color IS NOT NULL
`)
    const countResult = await conn.query(`SELECT COUNT(*) AS cnt FROM __agent_color_override`)
    const count = Number(countResult.get(0)?.cnt ?? 0)

    if (Number.isFinite(count) && count > 0) {
      // Rebuild color map: agent colors override category defaults
      await conn.query(`
CREATE OR REPLACE TABLE ${COLOR_MAP_TABLE} AS
SELECT
  tf.tree_id,
  COALESCE(aco.display_color,
    CASE COALESCE(tf.tree_category, 'default')
      WHEN 'palm'        THEN '${DEFAULT_CATEGORY_COLORS.palm}'
      WHEN 'broadleaf'   THEN '${DEFAULT_CATEGORY_COLORS.broadleaf}'
      WHEN 'spreading'   THEN '${DEFAULT_CATEGORY_COLORS.spreading}'
      WHEN 'coniferous'  THEN '${DEFAULT_CATEGORY_COLORS.coniferous}'
      WHEN 'columnar'    THEN '${DEFAULT_CATEGORY_COLORS.columnar}'
      WHEN 'ornamental'  THEN '${DEFAULT_CATEGORY_COLORS.ornamental}'
      ELSE '${DEFAULT_CATEGORY_COLORS.default}'
    END
  ) AS display_color
FROM trees_fast tf
LEFT JOIN __agent_color_override aco ON tf.tree_id = aco.tree_id
`)
      // Compute distinct colors from the rebuilt map
      const distinctResult = await conn.query(`SELECT DISTINCT display_color FROM ${COLOR_MAP_TABLE}`)
      activeDistinctColors = distinctResult.toArray().map((r: Record<string, unknown>) => String(r.display_color))
      colorMapSignature = `color-${hashText(normalizeSql(normalized))}`
    } else {
      // No valid override rows — reset to defaults
      await buildDefaultColorMap()
    }
    await conn.query(`DROP TABLE IF EXISTS __agent_color_override`)
  } else {
    // No override — reset to category defaults
    await buildDefaultColorMap()
  }

  await rebuildAggCaches()
  invalidateTileCaches()
  postColorMapUpdate()
}

async function rebuildCityBounds(cityCode: string) {
  if (!conn) return
  dataTileBoundsByZoom.clear()
  const boundsResult = await conn.query(`
    SELECT
      MIN(xtile_z13) AS min_x13, MAX(xtile_z13) AS max_x13, MIN(ytile_z13) AS min_y13, MAX(ytile_z13) AS max_y13,
      MIN(xtile_z14) AS min_x14, MAX(xtile_z14) AS max_x14, MIN(ytile_z14) AS min_y14, MAX(ytile_z14) AS max_y14,
      MIN(xtile_z15) AS min_x15, MAX(xtile_z15) AS max_x15, MIN(ytile_z15) AS min_y15, MAX(ytile_z15) AS max_y15,
      MIN(xtile_z16) AS min_x16, MAX(xtile_z16) AS max_x16, MIN(ytile_z16) AS min_y16, MAX(ytile_z16) AS max_y16,
      MIN(xtile_z17) AS min_x17, MAX(xtile_z17) AS max_x17, MIN(ytile_z17) AS min_y17, MAX(ytile_z17) AS max_y17,
      MIN(xtile_z18) AS min_x18, MAX(xtile_z18) AS max_x18, MIN(ytile_z18) AS min_y18, MAX(ytile_z18) AS max_y18,
      MIN(xtile_z19) AS min_x19, MAX(xtile_z19) AS max_x19, MIN(ytile_z19) AS min_y19, MAX(ytile_z19) AS max_y19,
      MIN(xtile_z20) AS min_x20, MAX(xtile_z20) AS max_x20, MIN(ytile_z20) AS min_y20, MAX(ytile_z20) AS max_y20
    FROM trees_fast
    WHERE city = '${cityCode}'
  `)
  const boundsRow = boundsResult.toArray()[0] as Record<string, unknown> | undefined
  if (boundsRow) {
    for (let z = 13; z <= 20; z += 1) {
      const minX = Number(boundsRow[`min_x${z}`])
      const maxX = Number(boundsRow[`max_x${z}`])
      const minY = Number(boundsRow[`min_y${z}`])
      const maxY = Number(boundsRow[`max_y${z}`])
      if ([minX, maxX, minY, maxY].every((v) => Number.isFinite(v))) {
        dataTileBoundsByZoom.set(z, { minX, maxX, minY, maxY })
      }
    }
  }
}

async function setCityContext(city: string) {
  // Pass city to ensureInit so that if init hasn't started yet it loads this
  // city's data directly (avoids a redundant full-dataset load).
  await ensureInit(city)
  if (!conn) return

  // If trees/trees_fast are loaded for a different city (or not yet loaded),
  // reload them for the requested city before rebuilding caches.
  // Reset the gate first so in-flight tile/query requests queue up while we load.
  if (loadedCity !== city) {
    resetCityReadyGate()
    await loadCityTrees(city)
    invalidateTileCaches()
  }

  activeCityDefaultQuery = `
SELECT
  tree_id,
  species,
  latitude,
  longitude,
  diameter_at_breast_height
FROM trees
WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND city = '${city}'
`
  await buildDefaultColorMap()
  await rebuildAggCaches()
  await rebuildCityBounds(city)
  invalidateTileCaches()
  postColorMapUpdate()
  // All city tables are ready — unblock tile generation and queries.
  signalCityReady()
}

function setViewportZoom(zoom: number) {
  if (!Number.isFinite(zoom)) return
  activeViewportZoom = zoom
}

function setViewportCenter(lng: number, lat: number) {
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return
  activeViewportCenter = { lng, lat }
}

function setVisibleTileRange(z: number, minX: number, maxX: number, minY: number, maxY: number) {
  if (![z, minX, maxX, minY, maxY].every((v) => Number.isFinite(v))) return
  visibleTileRangeByZoom.set(z, {
    minX: Math.floor(Math.min(minX, maxX)),
    maxX: Math.floor(Math.max(minX, maxX)),
    minY: Math.floor(Math.min(minY, maxY)),
    maxY: Math.floor(Math.max(minY, maxY)),
  })
}

async function prefetchVisibleDetailTilesAtZoom(z: number, rangeOverride?: TileBounds): Promise<PrefetchStatus> {
  await ensureInit()
  if (!conn || !spatialExtensionReady || z < 15) return 'skipped'

  if (rangeOverride) {
    setVisibleTileRange(z, rangeOverride.minX, rangeOverride.maxX, rangeOverride.minY, rangeOverride.maxY)
  }

  const range = getVisibleTileRange(z)
  if (!range) return 'skipped'

  const sig = `${tileQueryRevision}:${treeFilterSignature()}:${z}:${range.minX}-${range.maxX}:${range.minY}-${range.maxY}`
  if (prefetchedVisibleRangeSigByZoom.get(z) === sig) return 'deduped'

  const baseQuery = effectiveBaseQuery(tileQuerySql)
  if (z === 15) {
    await ensurePreparedFeatureTableForZoom(15, baseQuery)
  }

  const centerX = Math.floor((range.minX + range.maxX) / 2)
  const centerY = Math.floor((range.minY + range.maxY) / 2)
  await ensureNeighborhoodBatchTiles(z, centerX, centerY, baseQuery)
  prefetchedVisibleRangeSigByZoom.set(z, sig)
  return 'executed'
}

async function prewarmLodCaches(): Promise<void> {
  await ensureInit()
  if (!conn || !spatialExtensionReady) return

  const rev = tileQueryRevision
  if (prewarmDoneRevision === rev) return
  if (prewarmPromise) {
    await prewarmPromise
    return
  }

  const baseQuery = effectiveBaseQuery(tileQuerySql)
  prewarmPromise = (async () => {
    const viewport = activeViewportCenter
    const focusZoom = Math.max(15, Math.min(19, Math.round(activeViewportZoom)))

    if (viewport) {
      const cFocus = lngLatToTile(viewport.lng, viewport.lat, focusZoom)
      await ensureNeighborhoodBatchTiles(focusZoom, cFocus.x, cFocus.y, baseQuery)

      const zMinusOne = focusZoom - 1
      if (zMinusOne >= 15) {
        const cMinusOne = lngLatToTile(viewport.lng, viewport.lat, zMinusOne)
        await ensureNeighborhoodBatchTiles(zMinusOne, cMinusOne.x, cMinusOne.y, baseQuery)
      }
    }

    await ensurePreparedFeatureTableForZoom(15, baseQuery)
    if (viewport) {
      const c15 = lngLatToTile(viewport.lng, viewport.lat, 15)
      await ensureNeighborhoodBatchTiles(15, c15.x, c15.y, baseQuery)
    }

    await ensureZoomBatchTiles(14, baseQuery, 32)
    await ensureZoomBatchTiles(13, baseQuery, 32)

    if (tileQueryRevision === rev) {
      prewarmDoneRevision = rev
    }
  })()

  try {
    await prewarmPromise
  } finally {
    prewarmPromise = null
  }
}

function setAutoTileFetchEnabled(enabled: boolean) {
  autoTileFetchEnabled = !!enabled
}

function normalizeValue(v: unknown): unknown {
  if (typeof v === 'bigint') {
    const n = Number(v)
    return Number.isSafeInteger(n) ? n : v.toString()
  }
  if (v instanceof Uint8Array) {
    return Array.from(v)
  }
  return v
}

async function runQuery(sql: string): Promise<{ columns: string[]; rows: Record<string, unknown>[] }> {
  await ensureInit()
  await waitForCityContext()
  if (!conn) throw new Error('DuckDB not initialized')

  const result = await conn.query(sql)
  const columns = result.schema.fields.map((f) => f.name)
  const rows = result.toArray().map((row) => {
    const obj: Record<string, unknown> = {}
    for (const col of columns) {
      obj[col] = normalizeValue(row[col])
    }
    return obj
  })
  return { columns, rows }
}

async function getTile(z: number, x: number, y: number): Promise<Uint8Array> {
  await ensureInit()
  await waitForCityContext()
  if (!autoTileFetchEnabled) {
    const cached = getCachedTile(tileCacheKey(z, x, y))
    return cached ?? new Uint8Array()
  }
  return queueTileRequest(z, x, y)
}

type WorkerMethodMap = {
  ensureInit: { params: { city?: string }; result: { ready: boolean; initError: string | null } }
  setTileQuery: { params: { sql: string | null }; result: void }
  setPublishedTreeIdFilterSql: { params: { sql: string | null }; result: void }
  setColorOverrideSql: { params: { sql: string | null }; result: void }
  setCityContext: { params: { city: string }; result: void }
  setViewportZoom: { params: { zoom: number }; result: void }
  setViewportCenter: { params: { lng: number; lat: number }; result: void }
  setVisibleTileRange: { params: { z: number; minX: number; maxX: number; minY: number; maxY: number }; result: void }
  prefetchVisibleDetailTilesAtZoom: {
    params: { z: number; range?: { minX: number; maxX: number; minY: number; maxY: number } }
    result: PrefetchStatus
  }
  prewarmLodCaches: { params: Record<string, never>; result: void }
  setAutoTileFetchEnabled: { params: { enabled: boolean }; result: void }
  invalidateTileCaches: { params: Record<string, never>; result: void }
  query: { params: { sql: string }; result: { columns: string[]; rows: Record<string, unknown>[] } }
  getTile: { params: { z: number; x: number; y: number }; result: { tileBuffer: ArrayBuffer } }
}

type WorkerRequest = {
  type: 'request'
  requestId: number
  method: keyof WorkerMethodMap
  params: unknown
}

type WorkerResponse = {
  type: 'response'
  requestId: number
  ok: boolean
  result?: unknown
  error?: string
}

type WorkerContext = {
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null
  postMessage: (message: unknown, transfer?: Transferable[]) => void
}

const ctx = self as unknown as WorkerContext

ctx.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const msg = event.data
  if (!msg || msg.type !== 'request') return

  const send = (payload: WorkerResponse, transfer?: Transferable[]) => {
    if (transfer && transfer.length > 0) {
      ctx.postMessage(payload, transfer)
    } else {
      ctx.postMessage(payload)
    }
  }

  try {
    switch (msg.method) {
      case 'ensureInit': {
        const { city } = msg.params as WorkerMethodMap['ensureInit']['params']
        await ensureInit(city)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: { ready, initError } })
        break
      }
      case 'setTileQuery': {
        const { sql } = msg.params as WorkerMethodMap['setTileQuery']['params']
        setTileQuery(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setPublishedTreeIdFilterSql': {
        const { sql } = msg.params as WorkerMethodMap['setPublishedTreeIdFilterSql']['params']
        await setPublishedTreeIdFilterSql(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setColorOverrideSql': {
        const { sql } = msg.params as WorkerMethodMap['setColorOverrideSql']['params']
        await setColorOverrideSql(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setCityContext': {
        const { city } = msg.params as WorkerMethodMap['setCityContext']['params']
        await setCityContext(city)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setViewportZoom': {
        const { zoom } = msg.params as WorkerMethodMap['setViewportZoom']['params']
        setViewportZoom(zoom)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setViewportCenter': {
        const { lng, lat } = msg.params as WorkerMethodMap['setViewportCenter']['params']
        setViewportCenter(lng, lat)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setVisibleTileRange': {
        const { z, minX, maxX, minY, maxY } = msg.params as WorkerMethodMap['setVisibleTileRange']['params']
        setVisibleTileRange(z, minX, maxX, minY, maxY)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'prefetchVisibleDetailTilesAtZoom': {
        const { z, range } = msg.params as WorkerMethodMap['prefetchVisibleDetailTilesAtZoom']['params']
        const status = await prefetchVisibleDetailTilesAtZoom(z, range)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: status })
        break
      }
      case 'prewarmLodCaches': {
        await prewarmLodCaches()
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setAutoTileFetchEnabled': {
        const { enabled } = msg.params as WorkerMethodMap['setAutoTileFetchEnabled']['params']
        setAutoTileFetchEnabled(enabled)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'invalidateTileCaches': {
        invalidateTileCaches()
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'query': {
        const { sql } = msg.params as WorkerMethodMap['query']['params']
        const result = await runQuery(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result })
        break
      }
      case 'getTile': {
        const { z, x, y } = msg.params as WorkerMethodMap['getTile']['params']
        const tile = await getTile(z, x, y)
        const clone = new Uint8Array(tile)
        send(
          { type: 'response', requestId: msg.requestId, ok: true, result: { tileBuffer: clone.buffer } },
          [clone.buffer],
        )
        break
      }
      default:
        send({ type: 'response', requestId: msg.requestId, ok: false, error: `Unknown method: ${String(msg.method)}` })
    }
  } catch (e) {
    const err = e as Error
    send({
      type: 'response',
      requestId: msg.requestId,
      ok: false,
      error: err?.message ?? String(e),
    })
  }
}

export { }
