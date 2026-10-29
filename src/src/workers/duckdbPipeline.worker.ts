import * as duckdb from '@duckdb/duckdb-wasm'
import { DUCKDB_ASSET_URLS } from '../duckdbAssetUrls'
import { crownQuery } from './crownQuery'
import { CROWN_DISTANCE_MAX, type TreeRenderView, type TreeCrown } from '../lib/treeCrowns'

let db: duckdb.AsyncDuckDB | null = null
let conn: duckdb.AsyncDuckDBConnection | null = null
/** The connection capped chat reads stream on (runQuery). */
let streamConn: duckdb.AsyncDuckDBConnection | null = null
let ready = false
let initError: string | null = null
let initPromise: Promise<void> | null = null


const DEFAULT_BASE_QUERY_SQL = `
SELECT tree_id, species, latitude, longitude, diameter_at_breast_height
FROM trees
WHERE latitude IS NOT NULL AND longitude IS NOT NULL
`

import {
  REMOTE_TREES_PARQUET_URL,
  REMOTE_SPECIES_PARQUET_URL,
  REMOTE_PREDICTIONS_PARQUET_URL,
  REMOTE_ECOREGION_PARQUET_URL,
  REMOTE_ECOREGION_BROWSER_PARQUET_URL,
  REMOTE_LANDMARKS_PARQUET_URL,
  cityTreeParquetUrl,
  cityBrowserParquetUrl,
  cityLandmarkParquetUrl,
  cityPredictionsParquetUrl,
} from './parquetUrls'
import { normalizeValue } from './normalizeValue'
import {
  COLOR_OVERRIDE_TABLE,
  treeKeySql,
  WEB_MERCATOR_MAX,
  WEB_MERCATOR_WORLD,
  cityTileBoundsSql,
  colorSql,
  dropCityTablesSql,
  loadCityTablesSql,
  tileIndexSql,
  windowCellSplitSql,
  windowCellTable,
  windowStagingSql,
  windowViewSql,
  windowedCityTablesSql,
  type CityTableOptions,
} from './cityTables'
import {
  CELL_ZOOM,
  cellAt,
  cellDistance,
  cellFromKey,
  cellKey,
  cellsBounds,
  cellsForTile,
  chunksToEvict,
  type Chunk,
  type WindowPolicy,
} from './cityWindow'

const MAX_TILE_CACHE_ENTRIES = 1536
const MAX_PARALLEL_TILE_WORK = 3
const PREPARED_FEATURE_TABLE_ZOOM = 15
const PUBLISHED_TREE_FILTER_TABLE = 'published_tree_filter_ids'

import { CATEGORY_COLORS as DEFAULT_CATEGORY_COLORS } from '../treeFormColors'

const DEFAULT_CATEGORY_LABELS: Record<string, string> = {
  '#e6a835': 'Palm',
  '#4CAF50': 'Broadleaf',
  '#2E7D32': 'Conifer',
  '#43A047': 'Columnar',
  '#E91E63': 'Ornamental',
  '#8BC34A': 'Spreading',
  '#26A69A': 'Weeping',
  '#8D6E63': 'Multi-trunk',
  '#81C784': 'Other',
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
const inflightNeighborhoodBatch = new Map<string, Promise<void>>()
/** The ground each in-flight neighbourhood batch covers, by its key. */
const inflightNeighborhoodRanges = new Map<string, { rev: number; z: number } & TileBounds>()
/** The content key of the feature table each zoom currently holds. */
const preparedFeatureTablesReady = new Map<number, string>()
const inflightFeatureTableBuild = new Map<string, Promise<void>>()
const dataTileBoundsByZoom = new Map<number, TileBounds>()
const visibleTileRangeByZoom = new Map<number, TileBounds>()
const prefetchedVisibleRangeSigByZoom = new Map<number, string>()
const pendingTileQueue: QueuedTileRequest[] = []

let tileQuerySql: string | null = null
let tileQueryRevision = 0
let publishedTreeIdFilterSql: string | null = null
let publishedTreeIdFilterSignature = 'all'
let colorMapSignature = 'default'
/** True while `__tree_color_override` exists; every colour read joins it then. */
let colorOverrideActive = false
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

// The phone's window (cityWindow.ts). `windowPolicy` is what the main thread
// asked for; `cityWindow` is set while the loaded city is held in windows,
// and is null for a city held whole (desktop, or a city with no projection).
let windowPolicy: WindowPolicy | null = null
let cityWindow: {
  options: CityTableOptions
  /** Bumped on every city load, so a load that lands after a switch is dropped. */
  generation: number
  /** The city's extent from the parquet footer, since no table holds all of it. */
  bounds: { west: number; east: number; south: number; north: number } | null
  treeCount: number
  chunks: Map<string, Chunk>
  /** Every loaded cell, with the table holding it ('' for a cell with no trees). */
  cellTable: Map<number, string>
  /** Cells whose load is queued or running. */
  cellLoads: Map<number, Promise<void>>
  pending: Set<number>
  flush: Promise<void> | null
} | null = null
let windowGeneration = 0
/** Whether the loaded city was loaded with a window policy (it may still be whole: no projection). */
let loadedWithWindowPolicy = false
let windowChunkSeq = 0
/** How long tile requests are gathered before their cells are loaded in one scan. */
const WINDOW_GATHER_MS = 40

// City-context gate: tile generation and city-table queries must not run until
// setCityContext has fully completed (color map, agg caches, bounds all built).
// Uses a resolver array so that resetCityReadyGate() never orphans existing waiters.
let cityContextReady = false
const cityContextPendingResolvers: Array<() => void> = []

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

/** Block tile/query work until setCityContext has fully completed. */
async function waitForCityContext(): Promise<void> {
  if (cityContextReady) return
  await new Promise<void>((r) => { cityContextPendingResolvers.push(r) })
}

/** Mark city as not-ready during a city switch; existing waiters stay in the queue. */
function resetCityReadyGate(): void {
  cityContextReady = false
}

/** Open the gate and unblock all current waiters. */
function signalCityReady(): void {
  cityContextReady = true
  const resolvers = cityContextPendingResolvers.splice(0)
  for (const r of resolvers) r()
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
  ON ${treeKeySql('__base.tree_id')} = __filter_ids.tree_key
`
}

function effectiveBaseQuery(sql: string | null): string {
  return applyTreeFilterToBaseQuery(sanitizeBaseQuery(sql))
}

/**
 * The rows a tile query reads. When the published query is the city default
 * (no chat scope, no URL filter) the city table is read as it stands; the
 * join back through the `trees` view cost a third to a half of every
 * overview batch for nothing. Any other query is joined to the table by id,
 * as before.
 */
function tileRowSource(baseQuery: string, color: { join: string }): { prelude: string; from: string; where: string; dbh: string; id: string } {
  if (isDefaultBaseQuery(baseQuery)) {
    return {
      prelude: 'WITH',
      from: `trees_fast tf\n  ${color.join}`,
      where: 'tf.latitude IS NOT NULL AND tf.longitude IS NOT NULL\n    AND ',
      dbh: 'COALESCE(tf.dbh, 3)',
      id: 'tf.tree_id',
    }
  }
  return {
    prelude: `WITH base AS (\n  ${baseQuery}\n),`,
    from: `base\n  INNER JOIN trees_fast tf\n    ON base.tree_id = tf.tree_id\n  ${color.join}`,
    where: '',
    dbh: 'COALESCE(base.diameter_at_breast_height, tf.dbh, 3)',
    id: "COALESCE(base.tree_id, tf.tree_id, 'unkwn')",
  }
}

function normalizeSql(sql: string): string {
  return sql.replace(/\s+/g, ' ').trim().toLowerCase()
}

/** True for the city's default query, with or without its city clause: one city is loaded, so both read every row. */
function isDefaultBaseQuery(sql: string): boolean {
  const normalized = normalizeSql(sql)
  return normalized === normalizeSql(activeCityDefaultQuery) || normalized === normalizeSql(DEFAULT_BASE_QUERY_SQL)
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

function neighborhoodBatchKey(rev: number, z: number, minX: number, maxX: number, minY: number, maxY: number): string {
  return `${rev}:${treeFilterSignature()}:${z}:${minX}-${maxX}:${minY}-${maxY}`
}

/**
 * What a feature table's rows depend on: the query, the published filter and
 * the colours baked into it. Not the query revision: a republish of the same
 * SQL must find the table it already has, and a build that started a moment
 * before the first publish must still count.
 */
function featureTableBuildKey(baseQuery: string, z: number): string {
  const query = isDefaultBaseQuery(baseQuery) ? 'default' : hashText(normalizeSql(baseQuery))
  return `${query}:${treeFilterSignature()}:${z}`
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
  // Overview zooms: a 4x4 block of z13 tiles is about 20 km across at a
  // city's latitude, more than a phone shows, so one aggregate covers a pan.
  if (z >= 11) return 4
  return 1
}

function tileXExpr(alias: string, z: number): string {
  return tileIndexSql(alias, 'x', z)
}

function tileYExpr(alias: string, z: number): string {
  return tileIndexSql(alias, 'y', z)
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

/**
 * Aggregate cell size for the overview zooms. Below the circle layer's
 * minimum (z12.8) only the heatmap draws, and its weight is normalised by
 * `grid_m`, so the cell can follow the pixel: a 32 m cell at z11 is half a
 * pixel at a mid-latitude city, and aggregating a million of them to draw
 * one overview was the cost of the far zooms.
 */
function baseSimplifyGridMetersForZoom(z: number): number {
  if (z <= 11) return 128
  if (z === 12) return 64
  if (z === 13) return 32
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
  // floor, not round: MapLibre floors the camera zoom to choose the displayed
  // tile level, so e.g. zoom 15.7 renders z15 tiles and those must win priority.
  const viewportZoom = Math.floor(activeViewportZoom)
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
 * True when `url`'s parquet has `column`. Reads only the file footer, so this
 * is a metadata round trip rather than a scan. Returns false on any error: the
 * caller uses it to decide whether to add an optional predicate, and guessing
 * "absent" degrades where guessing "present" would break the load.
 */
async function parquetHasColumn(url: string, column: string): Promise<boolean> {
  if (!conn) return false
  try {
    const result = await conn.query(`
      SELECT 1 FROM (DESCRIBE SELECT * FROM read_parquet('${url}'))
      WHERE column_name = '${column}'
      LIMIT 1
    `)
    return result.numRows > 0
  } catch {
    return false
  }
}

/**
 * Load (or reload) `trees_fast`, and the `trees` view over it, for `city`
 * from the per-city parquet. Pass undefined to load all cities from the full
 * file (only as a last-resort fallback when city is genuinely unknown).
 *
 * Memory is the constraint. The DuckDB-wasm heap only ever grows, and on an
 * iPhone it sits in the same process budget as the map. Desktop materialises
 * the whole city; a phone (`windowPolicy`) loads only the vocabularies here
 * and its rows cell by cell as tiles ask (cityWindow.ts). The row shape lives
 * in cityTables.ts, with the numbers; what this function decides is which
 * files to read.
 */
async function loadCityTrees(city?: string): Promise<void> {
  if (!conn) return
  // Already loaded, in the shape asked for: a phone that turns its window off
  // (or on) reloads the same city.
  if (city && loadedCity === city && loadedWithWindowPolicy === (windowPolicy !== null)) return

  if (!city) {
    throw new Error('City must be specified to load trees, to avoid accidentally loading all cities into memory.')
  }

  preparedFeatureTablesReady.clear()
  dataTileBoundsByZoom.clear()
  colorOverrideActive = false

  const cityUrl = city ? cityTreeParquetUrl(city) : null
  const parquetUrl = cityUrl ?? REMOTE_TREES_PARQUET_URL

  // Cross-source duplicates are dropped at the source now -- every city's
  // published target carries `where tree_id = cluster_id`, so the parquet holds
  // one row per tree (see data/raw/tree_dedup.preql, "The prune").
  //
  // This filter is what that replaced, and it is kept only for parquets built
  // before it. The probe is what makes the two safe to mix: a pruned parquet has
  // no `is_duplicate` column, the probe returns false, no filter is applied, and
  // "everything" already is the survivors; a parquet still awaiting its rebuild
  // has the column and gets filtered exactly as before. So cities can be
  // refreshed one at a time, in any order, with no window where the map is
  // wrong -- which is the whole reason the column was not simply dropped in the
  // same change.
  //
  // It also still covers two cases that are not transitional: the cross-city
  // full_tree_info fallback cannot carry the column at all (merging the
  // per-city keys makes the planner resolve a city's grid aggregate over every
  // city, and it fails to plan), and a brand-new city has no parquet until its
  // first credentialed build.
  //
  // Delete this once every city has been rebuilt past the prune.
  // The browser projection, when the pipeline has published it: the display
  // name, the growth form and the crown prediction are columns, so the load
  // is one scan and none of the probes or joins below run. A city whose
  // projection is not out yet (a new city before the next core tick) takes
  // the assembled path, as every city did before the projection existed.
  const projectionCandidate = cityBrowserParquetUrl(city)
  const projectionUrl = projectionCandidate && await parquetHasColumn(projectionCandidate, 'display_name')
    ? projectionCandidate
    : null
  if (!projectionUrl) console.info('[duckdb-worker] browser projection unavailable; assembling the city from its published files', projectionCandidate)

  const hasDedupFlag = !projectionUrl && await parquetHasColumn(parquetUrl, 'is_duplicate')

  // `cultivar` is the tree's cultivated selection ('Tina' in Malus sargentii
  // 'Tina'), kept on the tree row by the ingest since September 2026. A
  // parquet built before that has no such column, and selecting a missing
  // column fails the whole load -- so probe for it the same way, and read a
  // null until the city is rebuilt. Fold this into the plain select once every
  // city has been refreshed past the change.
  const hasCultivar = !projectionUrl && await parquetHasColumn(parquetUrl, 'cultivar')
  // `crown_width_m`, the observed crown spread in metres (null for most
  // trees), arrived the same way in September 2026; probe it likewise.
  const hasCrownWidth = !projectionUrl && await parquetHasColumn(parquetUrl, 'crown_width_m')

  // The assembled path folds the published crown prediction in at load, so
  // the first sprite batch is sized correctly and the city switch has no
  // second table. The per-city slices the retired slice job left are a few
  // MB; the all-city file is ~170 MB and DuckDB-wasm downloads a whole
  // object on first read, so it is only the fallback for a city with no
  // slice. A city with neither reachable keeps recorded crowns only; reload
  // to retry.
  let predictionsUrl: string | null = null
  const citySliceUrl = cityPredictionsParquetUrl(city)
  if (!projectionUrl) {
    for (const url of [citySliceUrl, REMOTE_PREDICTIONS_PARQUET_URL]) {
      if (url && await parquetHasColumn(url, 'predicted_crown_width_m')) {
        predictionsUrl = url
        break
      }
      console.info('[TreeCrowns] predictions source unavailable', url)
    }
    if (!predictionsUrl) console.warn('[TreeCrowns] predictions unavailable; using recorded crowns')
  }
  // The slice is one row per tree by its model's grain; the all-city file is
  // aggregated to be sure (see CityTableOptions.predictionsAreUnique).
  const predictionsAreUnique = predictionsUrl !== null && predictionsUrl === citySliceUrl

  // A window load still running holds a staging table over the city's types.
  await windowLoadsSettled()
  for (const sql of dropCityTablesSql(cityWindow ? { chunkTables: [...cityWindow.chunks.keys()] } : null)) await conn.query(sql)
  cityWindow = null
  windowGeneration += 1
  loadedWithWindowPolicy = windowPolicy !== null

  const options: CityTableOptions = { projectionUrl, parquetUrl, predictionsUrl, predictionsAreUnique, city, hasDedupFlag, hasCultivar, hasCrownWidth }
  // A phone holds the city in windows when the projection is published; the
  // assembled fallback joins three files and is held whole, as before.
  const windowed = windowPolicy !== null && projectionUrl !== null
  const timings: Array<[string, number]> = []
  for (const sql of windowed ? windowedCityTablesSql(options) : loadCityTablesSql(options)) {
    const started = nowMs()
    await conn.query(sql)
    timings.push([sql.trim().slice(0, 48).replace(/\s+/g, ' '), Math.round(nowMs() - started)])
  }
  if (windowed && projectionUrl) {
    cityWindow = {
      options,
      generation: windowGeneration,
      bounds: null,
      treeCount: 0,
      chunks: new Map(),
      cellTable: new Map(),
      cellLoads: new Map(),
      pending: new Set(),
      flush: null,
    }
    // The extent and the count come from the footer: no table holds the city.
    const stats = (await conn.query(`
      SELECT path_in_schema AS col,
        min(TRY_CAST(COALESCE(stats_min_value, stats_min) AS DOUBLE)) AS lo,
        max(TRY_CAST(COALESCE(stats_max_value, stats_max) AS DOUBLE)) AS hi
      FROM parquet_metadata('${projectionUrl}')
      WHERE path_in_schema IN ('latitude', 'longitude')
      GROUP BY 1`)).toArray()
    const range = (col: string) => stats.find((row) => row.col === col)
    const lat = range('latitude'), lng = range('longitude')
    if (lat && lng && [lat.lo, lat.hi, lng.lo, lng.hi].every((v) => Number.isFinite(Number(v)))) {
      cityWindow.bounds = { south: Number(lat.lo), north: Number(lat.hi), west: Number(lng.lo), east: Number(lng.hi) }
    }
    const count = await conn.query(`SELECT sum(num_rows) AS n FROM parquet_file_metadata('${projectionUrl}')`)
    cityWindow.treeCount = Number(count.toArray()[0]?.n ?? 0)
  }
  const memory = await conn.query(`
    SELECT tag, CAST(round(sum(memory_usage_bytes) / 1048576) AS INTEGER) AS mb
    FROM duckdb_memory() WHERE memory_usage_bytes > 0 GROUP BY tag ORDER BY mb DESC`)
  console.info('[Perf] duckdb-worker:city-load', {
    city,
    source: projectionUrl ? 'projection' : 'assembled',
    windowed,
    ms: timings.map(([label, ms]) => `${ms} ${label}`).join(' | '),
    duckdbMb: memory.toArray().map((row) => `${row.tag}=${row.mb}`).join(' '),
  })

  loadedCity = city ?? null

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

/** The cells of a tile that hold any of the city, by the footer's extent. */
function windowCellsForTile(z: number, x: number, y: number): number[] {
  const keys = cellsForTile(z, x, y)
  const bounds = getDataTileBounds(CELL_ZOOM)
  if (!bounds) return keys
  return keys.filter((key) => {
    const cell = cellFromKey(key)
    return cell.x >= bounds.minX && cell.x <= bounds.maxX && cell.y >= bounds.minY && cell.y <= bounds.maxY
  })
}

/** True when every row a tile could draw is loaded, so its result may be cached. */
function windowCoversTile(z: number, x: number, y: number): boolean {
  if (!cityWindow) return true
  const held = cityWindow.cellTable
  return windowCellsForTile(z, x, y).every((key) => held.has(key))
}

/**
 * Whether a tile is near enough the camera to pull its trees in: within the
 * policy's radius of the camera's cell. A tile MapLibre asks for mid-flight,
 * or below the floor, is answered empty and transient instead.
 */
function windowAllowsTile(z: number, x: number, y: number): boolean {
  if (!cityWindow || !windowPolicy) return true
  if (z < Math.floor(windowPolicy.floorZoom)) return false
  if (!activeViewportCenter) return true
  const camera = cellAt(activeViewportCenter.lng, activeViewportCenter.lat)
  const span = z >= CELL_ZOOM ? 1 : 2 ** (CELL_ZOOM - z)
  const middle = z >= CELL_ZOOM
    ? cellFromKey(cellsForTile(z, x, y)[0])
    : { x: x * span + span / 2, y: y * span + span / 2 }
  return cellDistance(middle, camera) <= windowPolicy.radiusCells + span / 2
}

/** Load whichever of `keys` are not held, gathering concurrent requests into one scan. */
async function ensureWindowCells(keys: number[]): Promise<void> {
  const win = cityWindow
  if (!win) return
  const waits: Promise<void>[] = []
  for (const key of keys) {
    if (win.cellTable.has(key)) continue
    const inflight = win.cellLoads.get(key)
    if (inflight) {
      waits.push(inflight)
      continue
    }
    win.pending.add(key)
    if (!win.flush) {
      win.flush = new Promise<void>((resolve) => setTimeout(resolve, WINDOW_GATHER_MS)).then(() => {
        const cells = [...win.pending]
        win.pending.clear()
        win.flush = null
        return loadWindowCells(win, cells)
      })
    }
    win.cellLoads.set(key, win.flush)
    waits.push(win.flush)
  }
  await Promise.all(waits)
}

/**
 * One load: the rows of `cells` in one scan, split into a table per cell,
 * `trees_fast` redefined over them, then the farthest cells dropped while the
 * held rows exceed the budget. A load that lands after a city switch is
 * dropped unseen.
 */
async function loadWindowCells(win: NonNullable<typeof cityWindow>, cells: number[]): Promise<void> {
  if (!conn || cells.length === 0) return
  const t0 = nowMs()
  const staging = `trees_window_staging_${(windowChunkSeq += 1)}`
  const added: Chunk[] = []
  try {
    const sql = windowStagingSql(staging, win.options, cells, cellsBounds(cells), CELL_ZOOM)
    await conn.query(sql.create)
    const counts = new Map((await conn.query(sql.counts)).toArray().map((row) => [Number(row.cell), Number(row.n)]))
    if (cityWindow !== win || win.generation !== windowGeneration) return
    for (const key of cells) {
      const rows = counts.get(key) ?? 0
      if (rows > 0) {
        await conn.query(windowCellSplitSql(staging, key, CELL_ZOOM))
        added.push({ table: windowCellTable(key), cells: [key], rows })
      }
    }
    await conn.query(`DROP TABLE IF EXISTS ${staging}`)
    for (const key of cells) win.cellTable.set(key, counts.get(key) ? windowCellTable(key) : '')
    for (const chunk of added) win.chunks.set(chunk.table, chunk)

    // The cells just loaded are what the view is waiting to draw: never the
    // ones dropped to make room for them.
    const camera = activeViewportCenter ? cellAt(activeViewportCenter.lng, activeViewportCenter.lat) : null
    const fresh = new Set(added.map((chunk) => chunk.table))
    const evict = windowPolicy
      ? chunksToEvict([...win.chunks.values()].filter((chunk) => !fresh.has(chunk.table)), camera,
        windowPolicy.budgetRows - added.reduce((sum, chunk) => sum + chunk.rows, 0), windowPolicy.radiusCells)
      : []
    for (const name of evict) {
      for (const key of win.chunks.get(name)?.cells ?? []) win.cellTable.delete(key)
      win.chunks.delete(name)
    }
    // The view moves off the dropped tables before they go.
    if (added.length > 0 || evict.length > 0) await conn.query(windowViewSql([...win.chunks.keys()]))
    for (const name of evict) await conn.query(`DROP TABLE IF EXISTS ${name}`)
    if (added.length > 0 || evict.length > 0) preparedFeatureTablesReady.clear()

    console.info('[Perf] duckdb-worker:window-load', {
      cells: cells.length,
      rows: added.reduce((sum, chunk) => sum + chunk.rows, 0),
      held: [...win.chunks.values()].reduce((sum, chunk) => sum + chunk.rows, 0),
      tables: win.chunks.size,
      evicted: evict.length,
      ms: Math.round(nowMs() - t0),
    })
  } finally {
    // A load that failed or was superseded leaves nothing behind.
    if (cityWindow !== win || win.generation !== windowGeneration) {
      for (const chunk of added) await conn.query(`DROP TABLE IF EXISTS ${chunk.table}`).catch(() => {})
    }
    await conn.query(`DROP TABLE IF EXISTS ${staging}`).catch(() => {})
    for (const key of cells) win.cellLoads.delete(key)
  }
}

/** Wait for every window load queued or running, so a read sees the ground it asked about. */
async function windowLoadsSettled(): Promise<void> {
  const win = cityWindow
  if (!win) return
  // A failed load has already left cellLoads; the read goes ahead on what is held.
  while (win.cellLoads.size > 0) await Promise.all([...new Set(win.cellLoads.values())].map((load) => load.catch(() => {})))
}

async function doInit(city?: string) {
  if (db) return
  const t0 = nowMs()
  console.info('[Perf] duckdb-worker:init:start', { city: city ?? '(deferred)' })

  const bundles: duckdb.DuckDBBundles = {
    mvp: {
      mainModule: DUCKDB_ASSET_URLS.mvp.mainModule,
      mainWorker: DUCKDB_ASSET_URLS.mvp.mainWorker,
    },
    eh: {
      mainModule: DUCKDB_ASSET_URLS.eh.mainModule,
      mainWorker: DUCKDB_ASSET_URLS.eh.mainWorker,
    },
  }
  const bundle = await duckdb.selectBundle(bundles)

  const logger = new duckdb.ConsoleLogger()
  const worker = new Worker(bundle.mainWorker!)
  db = new duckdb.AsyncDuckDB(logger, worker)
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker)

  conn = await db.connect()

  // Nothing here depends on row order, and keeping it makes every CREATE
  // TABLE AS buffer its whole input before writing. The wasm heap never
  // shrinks, so the peak during the city load is what the tab keeps.
  await conn.query('SET preserve_insertion_order = false')

  // Load species enrichment and landmarks once — these are city-agnostic / small.
  //
  // The trunk photo columns were added in September 2026 and reach the
  // published table only when it is next republished; naming a column the
  // parquet does not have fails this whole load, and with it the tree card.
  // Probe, and read nulls until then. Fold into the plain select afterwards.
  const hasTrunkPhoto = await parquetHasColumn(REMOTE_SPECIES_PARQUET_URL, 'trunk_photo_url')
  const trunkPhotoColumns = hasTrunkPhoto
    ? 'trunk_photo_url, trunk_photo_license, trunk_photo_attribution'
    : 'CAST(NULL AS VARCHAR) AS trunk_photo_url, CAST(NULL AS VARCHAR) AS trunk_photo_license, CAST(NULL AS VARCHAR) AS trunk_photo_attribution'
  try {
    await conn.query(`
      CREATE TABLE species_enrichment AS
      SELECT
        species,
        common_names,
        description,
        tree_form,
        native_ecoregions,
        is_evergreen,
        mature_height_min_ft,
        mature_height_max_ft,
        canopy_spread_min_ft,
        canopy_spread_max_ft,
        growth_rate,
        lifespan_min_years,
        lifespan_max_years,
        drought_tolerance,
        water_needs,
        sun_exposure,
        bloom_months,
        wildlife_value,
        fire_risk,
        usda_zone_min,
        usda_zone_max,
        photo_url,
        photo_license,
        photo_attribution,
        ${trunkPhotoColumns}
      FROM read_parquet('${REMOTE_SPECIES_PARQUET_URL}')
    `)
  } catch (e) {
    console.error('[duckdb-worker] species-enrichment load failed — parquet schema may be out of date', e)
  }

  // The three columns the worker keeps, from the projection the pipeline
  // publishes for it; the full dimension (5.75 MB, almost all geometry) only
  // while the projection is not out yet.
  for (const url of [REMOTE_ECOREGION_BROWSER_PARQUET_URL, REMOTE_ECOREGION_PARQUET_URL]) {
    try {
      await conn.query(`
        CREATE OR REPLACE TABLE ecoregion_info AS
        SELECT ecoregion_id, biome, realm
        FROM read_parquet('${url}')
      `)
      break
    } catch (e) {
      console.warn('[Perf] duckdb-worker:ecoregion-load:failed', url, e)
    }
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

/** Drop any chat colour override; every colour read falls back to the tree-form CASE. */
async function clearColorOverride() {
  if (!conn) return
  await conn.query(`DROP TABLE IF EXISTS ${COLOR_OVERRIDE_TABLE}`)
  colorOverrideActive = false
  activeDistinctColors = Object.values(DEFAULT_CATEGORY_COLORS)
  activeColorLabelMap = { ...DEFAULT_CATEGORY_LABELS }
  colorMapSignature = 'default'
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
  if (!conn || z !== PREPARED_FEATURE_TABLE_ZOOM) return
  if (baseQuery !== effectiveBaseQuery(tileQuerySql)) return

  const key = featureTableBuildKey(baseQuery, z)
  if (preparedFeatureTablesReady.get(z) === key) return

  const inflight = inflightFeatureTableBuild.get(key)
  if (inflight) {
    await inflight
    return
  }

  const xExpr = tileXExpr('tf', z)
  const yExpr = tileYExpr('tf', z)
  const table = featureTableName(z)
  const color = colorSql('tf', colorOverrideActive)
  const src = tileRowSource(baseQuery, color)

  const request = (async () => {
    const sql = `
CREATE OR REPLACE TEMP TABLE ${table} AS
${src.prelude} rows AS (
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
      'id': ${src.id},
      'dbh': TRY_CAST(${src.dbh} AS DOUBLE),
      'category': COALESCE(tf.tree_form, 'default'),
      'display_color': ${color.expr},
      'rotation': 0,
      'point_count': 1,
      'grid_m': 32
    } AS feature
  FROM ${src.from}
)
SELECT xtile, ytile, feature
FROM rows
WHERE xtile >= 0
  AND ytile >= 0
  AND xtile < CAST(pow(2, ${z}) AS INTEGER)
  AND ytile < CAST(pow(2, ${z}) AS INTEGER)
`
    // No geometry filter here: DuckDB-WASM pushes one into the scan and runs
    // ST_AsMVTGeom twice per tree (11.4 s against 3.6 s on Boston). A point
    // clipped to its own tile is never empty; the readers' ST_AsMVT FILTER
    // keeps the guard.
    const t0 = nowMs()
    await conn.query(sql)
    console.info('[Perf] duckdb-worker:feature-table', { z, ms: Math.round(nowMs() - t0) })
    // CREATE OR REPLACE swapped the table under whatever key was there.
    preparedFeatureTablesReady.set(z, key)
  })()

  inflightFeatureTableBuild.set(key, request)
  try {
    await request
  } finally {
    inflightFeatureTableBuild.delete(key)
  }
}

async function ensureNeighborhoodBatchTiles(z: number, x: number, y: number, baseQuery: string, simplifyGridMeters = 0, explicitRange?: TileBounds): Promise<void> {
  if (!conn) return
  if (baseQuery !== effectiveBaseQuery(tileQuerySql)) return
  const blockSize = neighborhoodBlockSizeForZoom(z)
  if (blockSize <= 1) return

  const rev = tileQueryRevision

  // A view's tiles arrive in two waves: the first before the main thread has
  // reported the visible range (batched by block), the rest after (batched by
  // range). A second aggregate over ground the first is already covering must
  // wait for it, not start beside it: at z11 each one is most of the city.
  // An explicit range is the intro's whole footprint and runs regardless.
  for (const [key, range] of explicitRange ? [] : inflightNeighborhoodRanges) {
    if (range.rev !== rev || range.z !== z) continue
    if (x < range.minX || x > range.maxX || y < range.minY || y > range.maxY) continue
    const pending = inflightNeighborhoodBatch.get(key)
    if (pending) {
      await pending
      return
    }
  }

  const color = colorSql('tf', colorOverrideActive)
  const src = tileRowSource(baseQuery, color)
  // The z15 feature table is a whole-city materialisation (one MVT geometry
  // per tree) that the desktop's LOD prewarm builds after the intro lands. A
  // tile request reads it when it is there and runs the direct join
  // otherwise; it never builds it, so a phone, which never prewarms, never
  // pays for it.
  const useFeatureTable = z === PREPARED_FEATURE_TABLE_ZOOM
    && preparedFeatureTablesReady.get(PREPARED_FEATURE_TABLE_ZOOM) === featureTableBuildKey(baseQuery, PREPARED_FEATURE_TABLE_ZOOM)
  const tileCount = Math.pow(2, z)
  const blockX = Math.floor(x / blockSize)
  const blockY = Math.floor(y / blockSize)
  let minX = Math.max(0, explicitRange ? explicitRange.minX : blockX * blockSize)
  let maxX = Math.min(tileCount - 1, explicitRange ? explicitRange.maxX : minX + blockSize - 1)
  let minY = Math.max(0, explicitRange ? explicitRange.minY : blockY * blockSize)
  let maxY = Math.min(tileCount - 1, explicitRange ? explicitRange.maxY : minY + blockSize - 1)

  // At the detail zooms, batch over the reported visible range when it
  // contains the requested tile — a stale range (pan or zoom drift since the
  // last report) would otherwise batch the wrong area and leave the requested
  // tile to a slow single-tile fallback query. The overview zooms keep the
  // fixed block: a view's first tiles arrive before its range is reported,
  // and a second aggregate over the range would redo most of the block.
  const visibleRange = !explicitRange && z >= PREPARED_FEATURE_TABLE_ZOOM ? getVisibleTileRange(z) : null
  if (visibleRange
    && x >= visibleRange.minX && x <= visibleRange.maxX
    && y >= visibleRange.minY && y <= visibleRange.maxY) {
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
    const sql = useFeatureTable ? `
WITH rows AS (
  SELECT
    xtile,
    ytile,
    feature
  FROM ${featureTableName(PREPARED_FEATURE_TABLE_ZOOM)}
  WHERE xtile BETWEEN ${minX} AND ${maxX}
    AND ytile BETWEEN ${minY} AND ${maxY}
)
SELECT
  xtile,
  ytile,
  ST_AsMVT(feature, 'trees', 4096, 'geom') FILTER (WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)) AS mvt
FROM rows
GROUP BY xtile, ytile
` : simplifyGridMeters > 0 ? `
${src.prelude} pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    COALESCE(tf.tree_form, 'default') AS category,
    ${color.expr} AS display_color,
    ${src.dbh} AS dbh,
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile
  FROM ${src.from}
  WHERE ${src.where}${tileXExpr('tf', z)} BETWEEN ${minX} AND ${maxX}
    AND ${tileYExpr('tf', z)} BETWEEN ${minY} AND ${maxY}
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
  FROM pts
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
${src.prelude} pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    ${src.id} AS id,
    ${src.dbh} AS dbh,
    COALESCE(tf.tree_form, 'default') AS category,
    ${color.expr} AS display_color,
    0 AS rotation,
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile
  FROM ${src.from}
  WHERE ${src.where}${tileXExpr('tf', z)} BETWEEN ${minX} AND ${maxX}
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
  -- The geometry guard is the aggregate's FILTER, not a WHERE: DuckDB-WASM
  -- pushes a WHERE on feature.geom into the scan ahead of the tile range and
  -- runs ST_AsMVTGeom on every tree in the city (8.3 s against 32 ms for a
  -- four-tile z16 batch on Boston).
  ST_AsMVT(feature, 'trees', 4096, 'geom') FILTER (WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)) AS mvt
FROM rows
GROUP BY xtile, ytile
`

    const t0 = nowMs()
    const result = await conn.query(sql)
    const rows = result.toArray()
    console.info('[Perf] duckdb-worker:neighborhood-batch', {
      z,
      mode: useFeatureTable ? 'feature-table' : simplifyGridMeters > 0 ? `agg${simplifyGridMeters}` : 'points',
      tiles: (maxX - minX + 1) * (maxY - minY + 1),
      nonEmpty: rows.length,
      ms: Math.round(nowMs() - t0),
    })

    if (rev === tileQueryRevision) {
      // A windowed city's batch covers whatever cells are held; a tile reaching
      // past them is drawn but not cached, so it is asked for again once they load.
      for (let tx = minX; tx <= maxX; tx += 1) {
        for (let ty = minY; ty <= maxY; ty += 1) {
          if (windowCoversTile(z, tx, ty)) setCachedTile(tileCacheKeyForRevision(rev, z, tx, ty), new Uint8Array())
        }
      }
      for (const row of rows) {
        const xtile = Number(row.xtile)
        const ytile = Number(row.ytile)
        if (!windowCoversTile(z, xtile, ytile)) continue
        const raw = row.mvt as Uint8Array | undefined
        const tile = !raw || raw.length === 0
          ? new Uint8Array()
          : new Uint8Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength))
        setCachedTile(tileCacheKeyForRevision(rev, z, xtile, ytile), tile)
      }
    }
  })()

  inflightNeighborhoodBatch.set(bKey, request)
  inflightNeighborhoodRanges.set(bKey, { rev, z, minX, maxX, minY, maxY })
  try {
    await request
  } finally {
    inflightNeighborhoodBatch.delete(bKey)
    inflightNeighborhoodRanges.delete(bKey)
  }
}

async function generatePointTileMvt(z: number, x: number, y: number): Promise<Uint8Array> {
  if (!conn) return new Uint8Array()
  await waitForCityContext()

  const key = tileCacheKey(z, x, y)
  if (isTileOutsideDataBounds(z, x, y)) return new Uint8Array()

  const cached = getCachedTile(key)
  if (cached) return cached

  const inflight = inflightTileRequests.get(key)
  if (inflight) return inflight

  const baseQuery = effectiveBaseQuery(tileQuerySql)
  const { simplifyGridMeters } = adaptiveLodForTile(z, x, y)
  const bounds = tileBounds3857(z, x, y)
  const color = colorSql('tf', colorOverrideActive)
  const src = tileRowSource(baseQuery, color)

  // An overview tile (z11 to z14) is served from an aggregate over the
  // visible neighbourhood, never the whole city: the transient is
  // proportional to the viewport, and the cell size follows the pixel.
  if (shouldUseZoomBatch(z) || (simplifyGridMeters === 0 && z >= 15)) {
    await ensureNeighborhoodBatchTiles(z, x, y, baseQuery, simplifyGridMeters)
    const neighborBatched = getCachedTile(key)
    if (neighborBatched) return neighborBatched
  }

  const request = (async () => {
    const sql = simplifyGridMeters > 0 ? `
${src.prelude} pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    COALESCE(tf.tree_form, 'default') AS category,
    ${color.expr} AS display_color,
    ${src.dbh} AS dbh
  FROM ${src.from}
  WHERE ${src.where}tf.x_3857 BETWEEN ${bounds.minX} AND ${bounds.maxX}
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
${src.prelude} points AS (
  SELECT
    ${src.id} AS id,
    ${src.dbh} AS dbh,
    COALESCE(tf.tree_form, 'default') AS category,
    ${color.expr} AS display_color,
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
  FROM ${src.from}
  WHERE ${src.where}tf.x_3857 BETWEEN ${bounds.minX} AND ${bounds.maxX}
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
    if (windowCoversTile(z, x, y)) setCachedTile(key, tile)
    return new Uint8Array(tile)
  })()

  inflightTileRequests.set(key, request)
  try {
    return await request
  } finally {
    inflightTileRequests.delete(key)
  }
}

function invalidateTileCaches(reason: string) {
  console.info('[Perf] duckdb-worker:invalidate', { reason, revision: tileQueryRevision })
  tileCache.clear()
  emptyTileKeys.clear()
  persistentTileCacheKeys.clear()
  inflightTileRequests.clear()
  inflightNeighborhoodBatch.clear()
  inflightNeighborhoodRanges.clear()
  // Feature tables are keyed by content, not revision: a table built for
  // this query, filter and colour set is still right after a republish.
  visibleTileRangeByZoom.clear()
  prefetchedVisibleRangeSigByZoom.clear()
  prewarmDoneRevision = -1
  prewarmPromise = null
}

function setTileQuery(sql: string | null) {
  // The map republishes its query on every configuration pass, most of them
  // with the same SQL. Bumping the revision then throws away every tile and
  // batch and rebuilds the z15 feature table (20 s for a 1.5M-row city) for
  // rows that did not change.
  if (normalizeSql(sql ?? '') === normalizeSql(tileQuerySql ?? '')) return
  tileQuerySql = sql
  tileQueryRevision += 1
  invalidateTileCaches('tile-query')
}

async function setPublishedTreeIdFilterSql(sql: string | null) {
  await ensureInit()
  if (!conn) return
  const normalized = sql?.trim() || null
  // Every configuration pass republishes the filter, usually unchanged (most
  // often: none, as before). Rebuilding the table then invalidating every
  // tile for the same rows cost the desktop a second z15 feature table.
  if ((normalized ?? '') === (publishedTreeIdFilterSql ?? '')) return

  await conn.query(`DROP TABLE IF EXISTS ${PUBLISHED_TREE_FILTER_TABLE}`)

  if (normalized) {
    await conn.query(`
CREATE TEMP TABLE ${PUBLISHED_TREE_FILTER_TABLE} AS
SELECT DISTINCT ${treeKeySql('tree_id')} AS tree_key
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

  invalidateTileCaches('tree-filter')
}

async function setColorOverrideSql(sql: string | null) {
  await ensureInit()
  if (!conn) return
  const normalized = sql?.trim() || null
  // City setup already cleared the override and built the aggregate table.
  // Ordinary filter changes need neither a city-wide rebuild nor another
  // color push.
  if (!normalized && colorMapSignature === 'default') return

  if (normalized) {
    // Agent provided a color override SQL returning (tree_id, override_color).
    // Keep it as a sparse table: the colour reads COALESCE it over the
    // tree-form default, so unmatched trees keep their category colour and no
    // per-tree table exists for the city's other rows.
    await conn.query(`DROP TABLE IF EXISTS ${COLOR_OVERRIDE_TABLE}`)
    await conn.query(`
CREATE TEMP TABLE ${COLOR_OVERRIDE_TABLE} AS
SELECT DISTINCT ${treeKeySql('tree_id')} AS tree_key, CAST(override_color AS VARCHAR) AS display_color
FROM (
  ${normalized}
) __color_ids
WHERE tree_id IS NOT NULL AND override_color IS NOT NULL
`)
    const countResult = await conn.query(`SELECT COUNT(*) AS cnt FROM ${COLOR_OVERRIDE_TABLE}`)
    const count = Number(countResult.get(0)?.cnt ?? 0)

    if (Number.isFinite(count) && count > 0) {
      colorOverrideActive = true
      // The colours the map draws a heatmap layer for. A whole city reads
      // them off its rows. A window holds only the ground near the camera,
      // and a colour first met in a cell loaded later would have no layer, so
      // it lists every override colour and every category default instead.
      const color = colorSql('tf', true)
      const distinctResult = await conn.query(cityWindow
        ? `SELECT DISTINCT display_color FROM ${COLOR_OVERRIDE_TABLE}`
        : `SELECT DISTINCT ${color.expr} AS display_color FROM trees_fast tf ${color.join}`)
      const colors = distinctResult.toArray().map((r: Record<string, unknown>) => String(r.display_color))
      activeDistinctColors = cityWindow ? [...new Set([...colors, ...Object.values(DEFAULT_CATEGORY_COLORS)])] : colors
      colorMapSignature = `color-${hashText(normalizeSql(normalized))}`
    } else {
      // No valid override rows — reset to defaults
      await clearColorOverride()
    }
  } else {
    // No override — reset to category defaults
    await clearColorOverride()
  }

  invalidateTileCaches('color-override')
  postColorMapUpdate()
}

async function rebuildCityBounds() {
  if (!conn) return
  dataTileBoundsByZoom.clear()
  // A windowed city's extent is the footer's; the table holds only the window.
  if (cityWindow) {
    const { bounds } = cityWindow
    if (!bounds) return
    for (let z = CELL_ZOOM - 1; z <= 20; z += 1) {
      const nw = lngLatToTile(bounds.west, bounds.north, z)
      const se = lngLatToTile(bounds.east, bounds.south, z)
      dataTileBoundsByZoom.set(z, { minX: nw.x, maxX: se.x, minY: nw.y, maxY: se.y })
    }
    return
  }
  const zooms = Array.from({ length: 8 }, (_, i) => 13 + i)
  const boundsResult = await conn.query(cityTileBoundsSql(zooms))
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

  const cityLoadStartAt = nowMs()

  // If trees/trees_fast are loaded for a different city (or not yet loaded),
  // reload them for the requested city before rebuilding caches.
  // Reset the gate first so in-flight tile/query requests queue up while we load.
  if (loadedCity !== city || loadedWithWindowPolicy !== (windowPolicy !== null)) {
    resetCityReadyGate()
    await loadCityTrees(city)
    invalidateTileCaches('city-load')
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
  await clearColorOverride()
  await rebuildCityBounds()
  invalidateTileCaches('city-context')
  postColorMapUpdate()
  // All city tables are ready — unblock tile generation and queries.
  signalCityReady()
  const treeCount = cityWindow
    ? cityWindow.treeCount
    : Number((await conn.query(`SELECT COUNT(*) AS cnt FROM trees WHERE city = '${city}'`)).toArray()[0]?.cnt ?? 0)
  ctx.postMessage({ type: 'cityContextReady', loadMs: Math.round(nowMs() - cityLoadStartAt), treeCount })
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

async function prefetchVisibleTilesAtZoom(z: number, rangeOverride?: TileBounds): Promise<PrefetchStatus> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !spatialExtensionReady || !shouldUseZoomBatch(z) && z < PREPARED_FEATURE_TABLE_ZOOM) return 'skipped'

  // An overview zoom's tile requests batch fixed 4x4 blocks, and each block's
  // aggregate scans the city: the intro's footprint at z14 spans six of them
  // (7.3 s on Boston). The range is warmed as one aggregate instead, and it
  // is passed in, never stored, because the main thread's range reports for
  // these zooms drive nothing else. The intro holds its camera on this.
  if (z < PREPARED_FEATURE_TABLE_ZOOM) {
    if (!rangeOverride) return 'skipped'
    const sig = `${tileQueryRevision}:${treeFilterSignature()}:${z}:${rangeOverride.minX}-${rangeOverride.maxX}:${rangeOverride.minY}-${rangeOverride.maxY}`
    if (prefetchedVisibleRangeSigByZoom.get(z) === sig) return 'deduped'
    const { simplifyGridMeters } = adaptiveLodForTile(z, rangeOverride.minX, rangeOverride.minY)
    await ensureNeighborhoodBatchTiles(z, rangeOverride.minX, rangeOverride.minY, effectiveBaseQuery(tileQuerySql), simplifyGridMeters, rangeOverride)
    prefetchedVisibleRangeSigByZoom.set(z, sig)
    return 'executed'
  }

  if (rangeOverride) {
    setVisibleTileRange(z, rangeOverride.minX, rangeOverride.maxX, rangeOverride.minY, rangeOverride.maxY)
  }

  const range = getVisibleTileRange(z)
  if (!range) return 'skipped'

  const sig = `${tileQueryRevision}:${treeFilterSignature()}:${z}:${range.minX}-${range.maxX}:${range.minY}-${range.maxY}`
  if (prefetchedVisibleRangeSigByZoom.get(z) === sig) return 'deduped'

  // The visible range only: the intro gates its motion on this, and the
  // whole-city z15 feature table (20 to 100 s of wasm on Boston) held the
  // camera at the start pose until it finished. The prewarm builds it once
  // the intro has landed; until then the range is a points batch.
  const baseQuery = effectiveBaseQuery(tileQuerySql)

  const centerX = Math.floor((range.minX + range.maxX) / 2)
  const centerY = Math.floor((range.minY + range.maxY) / 2)
  await ensureNeighborhoodBatchTiles(z, centerX, centerY, baseQuery)
  prefetchedVisibleRangeSigByZoom.set(z, sig)
  return 'executed'
}

async function prewarmLodCaches(): Promise<void> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !spatialExtensionReady) return

  const rev = tileQueryRevision
  if (prewarmDoneRevision === rev) return
  if (prewarmPromise) {
    await prewarmPromise
    return
  }

  const baseQuery = effectiveBaseQuery(tileQuerySql)
  const prewarmStartedAt = nowMs()
  console.info('[Perf] duckdb-worker:prewarm:start', { zoom: Math.floor(activeViewportZoom) })
  prewarmPromise = (async () => {
    const viewport = activeViewportCenter
    const focusZoom = Math.max(PREPARED_FEATURE_TABLE_ZOOM, Math.min(19, Math.floor(activeViewportZoom)))

    if (focusZoom === PREPARED_FEATURE_TABLE_ZOOM) {
      await ensurePreparedFeatureTableForZoom(PREPARED_FEATURE_TABLE_ZOOM, baseQuery)
    }

    if (viewport) {
      const cFocus = lngLatToTile(viewport.lng, viewport.lat, focusZoom)
      await ensureNeighborhoodBatchTiles(focusZoom, cFocus.x, cFocus.y, baseQuery)

      const zMinusOne = focusZoom - 1
      if (zMinusOne >= PREPARED_FEATURE_TABLE_ZOOM) {
        if (zMinusOne === PREPARED_FEATURE_TABLE_ZOOM) {
          await ensurePreparedFeatureTableForZoom(PREPARED_FEATURE_TABLE_ZOOM, baseQuery)
        }
        const cMinusOne = lngLatToTile(viewport.lng, viewport.lat, zMinusOne)
        await ensureNeighborhoodBatchTiles(zMinusOne, cMinusOne.x, cMinusOne.y, baseQuery)
      }
    }

    await ensurePreparedFeatureTableForZoom(PREPARED_FEATURE_TABLE_ZOOM, baseQuery)
    if (viewport) {
      const c15 = lngLatToTile(viewport.lng, viewport.lat, PREPARED_FEATURE_TABLE_ZOOM)
      await ensureNeighborhoodBatchTiles(PREPARED_FEATURE_TABLE_ZOOM, c15.x, c15.y, baseQuery)
    }


    if (tileQueryRevision === rev) {
      prewarmDoneRevision = rev
    }
    console.info('[Perf] duckdb-worker:prewarm:done', { ms: Math.round(nowMs() - prewarmStartedAt), stale: tileQueryRevision !== rev })
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

type QueryResult = { columns: string[]; rows: Record<string, unknown>[]; totalRows: number }

function rowObjects(columns: string[], rows: Iterable<Record<string, unknown>>): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = []
  for (const row of rows) {
    const obj: Record<string, unknown> = {}
    for (const col of columns) obj[col] = normalizeValue(row[col])
    out.push(obj)
  }
  return out
}

/**
 * Run `sql`; with `maxRows`, return at most that many rows and count the
 * rest. A chat query reads the published files, not the phone's window, and
 * one that lists a city's rows would otherwise build the whole result here
 * and again on the main thread: Boston's 1.4M rows took a phone tab from
 * 0.8 to 2.4 GB (docs/MOBILE_LOAD_BUDGET.md). Streamed, only one batch is
 * held at a time, in the query's own order. The stream runs on its own
 * connection: a tile query on the shared one would close it mid-read.
 */
async function runQuery(sql: string, maxRows?: number): Promise<QueryResult> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !db) throw new Error('DuckDB not initialized')
  await windowLoadsSettled()

  if (maxRows === undefined) {
    const result = await conn.query(sql)
    const columns = result.schema.fields.map((f) => f.name)
    const rows = rowObjects(columns, result.toArray())
    return { columns, rows, totalRows: rows.length }
  }

  streamConn ??= await db.connect()
  const reader = await streamConn.send(sql, true)
  const kept: Record<string, unknown>[] = []
  let totalRows = 0
  // The browser's reader drops its schema once read to the end; take it from a batch.
  let columns: string[] | null = null
  for await (const batch of reader) {
    columns ??= batch.schema.fields.map((f) => f.name)
    if (kept.length < maxRows) {
      const take = Math.min(batch.numRows, maxRows - kept.length)
      for (let i = 0; i < take; i += 1) kept.push(batch.get(i)!.toJSON())
    }
    totalRows += batch.numRows
  }
  columns ??= reader.schema?.fields.map((f) => f.name) ?? []
  return { columns, rows: rowObjects(columns, kept), totalRows }
}

async function getMapTrees(camera: TreeRenderView): Promise<TreeCrown[]> {
  await ensureInit()
  await waitForCityContext()
  // The intro pauses vector-tile work, but still needs the independent sprite
  // renderer to draw and satisfy its readiness gate. This reads local tables.
  if (!conn || !loadedCity || (!camera.bounds && camera.altitude >= CROWN_DISTANCE_MAX)) return []
  const city = loadedCity
  const revision = tileQueryRevision
  const signature = treeFilterSignature()
  const current = () => cityContextReady && loadedCity === city && tileQueryRevision === revision && treeFilterSignature() === signature
  if (!current()) return []
  const result = await conn.query(crownQuery(camera, effectiveBaseQuery(tileQuerySql), colorSql('tf', colorOverrideActive)))
  if (!current()) return []
  return result.toArray().map(row => ({
    id: String(row.id), lng: Number(row.lng), lat: Number(row.lat),
    width: row.width == null ? null : Number(row.width), measured: Boolean(row.measured), color: String(row.color),
    category: String(row.category), dbh: Number(row.dbh),
  }))
}

async function getTile(z: number, x: number, y: number): Promise<{ tile: Uint8Array; transient: boolean }> {
  await ensureInit()
  await waitForCityContext()
  if (!autoTileFetchEnabled) {
    // Tile fetching is paused (intro/swoop). Serve from cache when possible;
    // otherwise return an empty tile flagged transient so the client can give
    // it a short expiry in MapLibre's tile cache rather than letting the empty
    // response persist as if the tile had no trees.
    const cached = getCachedTile(tileCacheKey(z, x, y))
    return { tile: cached ?? new Uint8Array(), transient: cached == null }
  }
  // A windowed city draws a tile only from loaded cells. The cells are loaded
  // here, before the tile queue, so every tile of a view asks at once and the
  // gather in ensureWindowCells makes one scan of them.
  if (cityWindow && !getCachedTile(tileCacheKey(z, x, y)) && !windowCoversTile(z, x, y)) {
    if (!windowAllowsTile(z, x, y)) return { tile: new Uint8Array(), transient: true }
    await ensureWindowCells(windowCellsForTile(z, x, y))
  }
  return { tile: await queueTileRequest(z, x, y), transient: false }
}

type WorkerMethodMap = {
  getMapTrees: { params: TreeRenderView; result: TreeCrown[] }
  ensureInit: { params: { city?: string; window?: WindowPolicy | null }; result: { ready: boolean; initError: string | null } }
  setTileQuery: { params: { sql: string | null }; result: void }
  setPublishedTreeIdFilterSql: { params: { sql: string | null }; result: void }
  setColorOverrideSql: { params: { sql: string | null }; result: void }
  setCityContext: { params: { city: string; window?: WindowPolicy | null }; result: void }
  setViewportZoom: { params: { zoom: number }; result: void }
  setViewportCenter: { params: { lng: number; lat: number }; result: void }
  ensureWindowAt: { params: { lng: number; lat: number }; result: void }
  setVisibleTileRange: { params: { z: number; minX: number; maxX: number; minY: number; maxY: number }; result: void }
  prefetchVisibleTilesAtZoom: {
    params: { z: number; range?: { minX: number; maxX: number; minY: number; maxY: number } }
    result: PrefetchStatus
  }
  prewarmLodCaches: { params: Record<string, never>; result: void }
  setAutoTileFetchEnabled: { params: { enabled: boolean }; result: void }
  invalidateTileCaches: { params: Record<string, never>; result: void }
  query: { params: { sql: string; maxRows?: number }; result: QueryResult }
  getTile: { params: { z: number; x: number; y: number }; result: { tileBuffer: ArrayBuffer; transient: boolean } }
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
      case 'getMapTrees': {
        const result = await getMapTrees(msg.params as TreeRenderView)
        send({ type: 'response', requestId: msg.requestId, ok: true, result })
        break
      }
      case 'ensureInit': {
        const { city, window } = msg.params as WorkerMethodMap['ensureInit']['params']
        if (window !== undefined) windowPolicy = window
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
        const { city, window } = msg.params as WorkerMethodMap['setCityContext']['params']
        if (window !== undefined) windowPolicy = window
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
      case 'ensureWindowAt': {
        // A read about one tree (a deep link's card) before its tile is asked for.
        const { lng, lat } = msg.params as WorkerMethodMap['ensureWindowAt']['params']
        await waitForCityContext()
        const cell = cellAt(lng, lat)
        await ensureWindowCells([cellKey(cell.x, cell.y)])
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setVisibleTileRange': {
        const { z, minX, maxX, minY, maxY } = msg.params as WorkerMethodMap['setVisibleTileRange']['params']
        setVisibleTileRange(z, minX, maxX, minY, maxY)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'prefetchVisibleTilesAtZoom': {
        const { z, range } = msg.params as WorkerMethodMap['prefetchVisibleTilesAtZoom']['params']
        const status = await prefetchVisibleTilesAtZoom(z, range)
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
        invalidateTileCaches('rpc')
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'query': {
        const { sql, maxRows } = msg.params as WorkerMethodMap['query']['params']
        const result = await runQuery(sql, maxRows)
        send({ type: 'response', requestId: msg.requestId, ok: true, result })
        break
      }
      case 'getTile': {
        const { z, x, y } = msg.params as WorkerMethodMap['getTile']['params']
        const { tile, transient } = await getTile(z, x, y)
        const clone = new Uint8Array(tile)
        send(
          { type: 'response', requestId: msg.requestId, ok: true, result: { tileBuffer: clone.buffer, transient } },
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
