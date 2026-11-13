import * as duckdb from '@duckdb/duckdb-wasm'
import { DUCKDB_ASSET_URLS } from '../duckdbAssetUrls'
import { crownQuery } from './crownQuery'
import { CROWN_DISTANCE_MAX, crownColumnBuffers, type TreeRenderView, type TreeCrownColumns } from '../lib/treeCrowns'
import { buildCrownBatch, crownBatchBuffers, type CrownBatch, type CrownBatchOptions } from '../lib/crownBatch'

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
  REMOTE_SPECIES_PARQUET_URL,
  REMOTE_ECOREGION_PARQUET_URL,
  REMOTE_ECOREGION_BROWSER_PARQUET_URL,
  REMOTE_LANDMARKS_PARQUET_URL,
  cityBrowserParquetUrl,
  cityLandmarkParquetUrl,
  WorldFileError,
  fetchPublishedFile,
  fetchWorldFile,
  fetchWorldManifest,
  worldFileUrl,
} from './parquetUrls'
import { normalizeValue } from './normalizeValue'
import {
  COLOR_OVERRIDE_TABLE,
  DBH_COLORS,
  SPECIES_SHADE_TABLE,
  TREES_AT_TOKEN,
  UNKNOWN_SHADE_COLOR,
  WINDOW_SHAPE_TABLE,
  treeKeySql,
  WEB_MERCATOR_MAX,
  WEB_MERCATOR_WORLD,
  blockCellSql,
  colorSql,
  dropWorldTablesSql,
  publishedTreesViewSql,
  scopeSql,
  tileIndexSql,
  treesSelectSql,
  windowCellSplitSql,
  windowCellTable,
  windowStagingSql,
  windowViewSql,
  worldTablesSql,
  type BaseColorScheme,
} from './cityTables'
import {
  CELL_ZOOM,
  cellAt,
  cellDistance,
  cellFromKey,
  cellKey,
  cellsBounds,
  cellsForTile,
  cellsInBox,
  chunksToEvict,
  type Chunk,
  type WindowPolicy,
} from './cityWindow'
import {
  BLOCK_ZOOM, blockAt, blockFile, blockKey, blockOfCell, parseWorldManifest, tileHasBlocks, worldDirectory,
  type WorldDirectory, type WorldManifest,
} from './worldBlocks'

const MAX_TILE_CACHE_ENTRIES = 1536
const MAX_PARALLEL_TILE_WORK = 3
const PREPARED_FEATURE_TABLE_ZOOM = 15
const PUBLISHED_TREE_FILTER_TABLE = 'published_tree_filter_ids'

import { CATEGORY_COLORS as DEFAULT_CATEGORY_COLORS } from '../treeFormColors'
import { snapShade } from '../lib/foliageSeason'
import { assembleLayeredTiles } from './mvtLayers'
import {
  CHUNK_ZOOM, bucketByChunk, gridOrderChunks, pointChunkColorsSql, pointChunksSql, pointPalette,
  type PointChunkBatch, type PointChunkColors, type PointChunkDirectory, type PointKeys,
} from './cityPoints'

/** The source layer a tree with no colour lands in: the style's own fallback colour. */
const MVT_FALLBACK_LAYER = '#66BB6A'

/**
 * The lowest tile zoom whose features the circle layer draws (useMapLayers'
 * CIRCLE_ZOOM_MIN is 12.8, and MapLibre buckets a layer only on tiles at or
 * above its floored minzoom). Below it only the heatmaps read a cell, and
 * they read its count, not its size.
 */
const CIRCLE_TILE_MIN_ZOOM = 12

/**
 * An overview cell's dbh: none where no circle reads it, and a whole inch
 * where one does, so the tile's value table holds a few dozen sizes rather
 * than one float per cell.
 */
function aggregatedDbhSql(z: number): { agg: string; property: string } {
  return z < CIRCLE_TILE_MIN_ZOOM
    ? { agg: 'NULL', property: '' }
    : { agg: 'round(AVG(dbh))', property: "\n      'dbh': TRY_CAST(dbh AS INTEGER)," }
}

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
/**
 * Bumped whenever the rows under trees_fast change (a city load, a window
 * move): a feature-table build that started before stops between slices and
 * is never marked ready. Its key alone cannot tell, since every city's
 * default query has the same one.
 */
let featureTableGeneration = 0

function resetFeatureTables() {
  preparedFeatureTablesReady.clear()
  featureTableGeneration += 1
}
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
/** True while `__species_shade` exists: the leaf-shade scheme is the base colour. */
let speciesShadeActive = false
let speciesShadeSignature = 'none'
/** True while the base colour is the trunk diameter's band (`dbhColorSql`). */
let dbhColorsActive = false
let activeDistinctColors: string[] = Object.values(DEFAULT_CATEGORY_COLORS)
let activeColorLabelMap: Record<string, string> = { ...DEFAULT_CATEGORY_LABELS }
let activeViewportZoom = 13
let activeViewportCenter: { lng: number; lat: number } | null = null
let spatialExtensionReady = false
let prewarmDoneRevision = -1
let prewarmPromise: Promise<void> | null = null
let autoTileFetchEnabled = true
let activeQueuedWorkers = 0
/**
 * The bookmark: the city whose projection (`trees_published`), landmarks and
 * default query are in context. It decides nothing about which trees are
 * held; the camera does.
 */
let contextCity: string | null = null

/**
 * A desktop's window: whole blocks, a budget its heap can carry (about
 * 125 bytes a row), and the blocks around the camera never dropped. The
 * resident layer keeps its own, wider set as typed arrays; what is held here
 * is what the sprites, the tree card and a filter read.
 */
const DESKTOP_POLICY: WindowPolicy = { cellZoom: BLOCK_ZOOM, floorZoom: 9, budgetRows: 3_000_000, radiusCells: 2 }
/** What the main thread asked for (cityWindow.ts): a phone's tight window, or the desktop's. */
let windowPolicy: WindowPolicy = DESKTOP_POLICY

/** The zoom of the cells the window holds. */
function cellZoom(): number {
  return Math.max(BLOCK_ZOOM, windowPolicy.cellZoom ?? CELL_ZOOM)
}

// The world: the manifest, and the cells of it held (cityWindow.ts). Set once
// the manifest and the vocabulary are read; reset only when the manifest
// turns out to be stale or the cell zoom changes.
type World = {
  manifest: WorldManifest
  cellZoom: number
  /** Bumped on every reset, so a load that lands after one is dropped. */
  generation: number
  chunks: Map<string, Chunk>
  /** Every loaded cell, with the table holding it ('' for a cell with no trees). */
  cellTable: Map<number, string>
  /** Cells whose load is queued or running. */
  cellLoads: Map<number, Promise<void>>
  pending: Set<number>
  flush: Promise<void> | null
  /** Cells a read is using right now, by how many reads; never dropped. */
  pins: Map<number, number>
  /** False once cells were added or dropped and `trees_fast` has not been redefined over them. */
  viewCurrent: boolean
}
let world: World | null = null
let windowGeneration = 0
let windowChunkSeq = 0
/** Block files fetched at once: enough to fill a connection, few enough to hold in memory. */
const BLOCK_FETCH_PARALLEL = 6
/**
 * A phone's load that touches this many blocks or fewer fetches them whole
 * (WHOLE_BLOCK_BYTES). Measured on Boston's South End block (3.6 MB) over a
 * 9 Mbps link: DuckDB-wasm's range reads of two of its sixteen cells were 22
 * requests in a row and 4.6 MB, its read-ahead doubling to a megabyte past
 * every seek, 6.5 s; the file whole is one request. A wider first view (a
 * city opened at the floor zoom) spans four blocks and a third of their rows,
 * and keeps the range reads.
 */
const WHOLE_BLOCK_LOADS = 2
/** Whole block files kept as bytes, newest last, so the next cell of a block is no request at all. */
const WHOLE_BLOCK_BYTES = 16 * 1024 * 1024
const wholeBlocks = new Map<string, Promise<Uint8Array>>()
const wholeBlockSizes = new Map<string, number>()

/**
 * A block file's bytes: the copy kept, or a fetch that is kept. A fetch that
 * fails is forgotten, so the next ask tries again.
 */
function wholeBlock(file: string): Promise<Uint8Array> {
  const held = wholeBlocks.get(file)
  if (held) {
    // Newest last.
    wholeBlocks.delete(file)
    wholeBlocks.set(file, held)
    return held
  }
  const fetched = fetchWorldFile(file)
  wholeBlocks.set(file, fetched)
  fetched.then((bytes) => {
    if (wholeBlocks.get(file) !== fetched) return
    wholeBlockSizes.set(file, bytes.byteLength)
    let total = 0
    for (const size of wholeBlockSizes.values()) total += size
    for (const name of [...wholeBlocks.keys()]) {
      if (total <= WHOLE_BLOCK_BYTES || name === file) break
      total -= wholeBlockSizes.get(name) ?? 0
      wholeBlocks.delete(name)
      wholeBlockSizes.delete(name)
    }
  }, () => {
    if (wholeBlocks.get(file) === fetched) wholeBlocks.delete(file)
  })
  return fetched
}

/**
 * What the start reads besides the engine, asked for before the engine is:
 * the manifest, the vocabulary, the species table and the ecoregions. They
 * download beside the 8 MB of DuckDB instead of after it, each in one request.
 */
type EarlyFiles = {
  manifest: Promise<unknown>
  vocab: Promise<Uint8Array>
  species: Promise<Uint8Array | null>
  ecoregions: Promise<Uint8Array | null>
}
let earlyFiles: EarlyFiles | null = null

function startEarlyFiles(): EarlyFiles {
  const manifest = fetchWorldManifest()
  const vocab = manifest.then((json) => fetchWorldFile(parseWorldManifest(json).vocab))
  // loadWorld reports a failure of either; nothing else may.
  manifest.catch(() => {})
  vocab.catch(() => {})
  return {
    manifest,
    vocab,
    species: fetchPublishedFile(REMOTE_SPECIES_PARQUET_URL),
    ecoregions: fetchPublishedFile(REMOTE_ECOREGION_BROWSER_PARQUET_URL),
  }
}

/**
 * Fetch the block under a point before anything can read it: Near Me knows
 * where the camera will open while the engine is still downloading, and the
 * block's bytes are what the first trees wait for after it.
 */
function prefetchBlockAt(lng: number, lat: number): void {
  void ensureInit().catch(() => {})
  const manifest = world ? Promise.resolve(world.manifest) : earlyFiles?.manifest.then(parseWorldManifest)
  manifest?.then((m) => {
    const at = blockAt(lng, lat)
    const block = m.blocks.get(blockKey(at.x, at.y))
    if (block) wholeBlock(blockFile(block)).catch(() => {})
  }).catch(() => {})
}
/** The most cells an intro's prefetch of a tile range loads ahead of its aggregate. */
const MAX_PREFETCH_CELLS = 400
/** The widest scoped read: past this many cells a read uses the whole view. */
const MAX_SCOPE_CELLS = 64
/** How long tile requests are gathered before their cells are loaded in one scan. */
const WINDOW_GATHER_MS = 40

// Context gate: tile generation and queries must not run until the first
// setCityContext has completed (the world's tables and a bookmark exist).
// It opens once and stays open: a later bookmark changes no table a read uses.
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

/** The base colour rule every colour read uses (`colorSql`). */
function baseScheme(): BaseColorScheme {
  return dbhColorsActive ? 'dbh' : speciesShadeActive ? 'shade' : 'form'
}

function colorSchemeSignature(): string {
  return dbhColorsActive ? 'dbh' : speciesShadeSignature
}

function treeFilterSignature(): string {
  return `${publishedTreeIdFilterSignature}|${colorMapSignature}|${colorSchemeSignature()}`
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
 * The rows a tile query reads. When the published query is the default (no
 * chat scope, no URL filter) the held rows are read as they stand; the join
 * back through the `trees` view cost a third to a half of every overview
 * batch for nothing. Any other query is joined to the rows by id.
 *
 * `scope` is the relation the rows come from: `trees_fast`, or the held
 * cells a read is about (`scopeSql`). With a scope the query's own
 * `FROM trees` is pointed at the same cells, so a filter runs over the
 * ground being read and not over everything held.
 */
function tileRowSource(baseQuery: string, color: { join: string }, scope = 'trees_fast'): { prelude: string; from: string; where: string; dbh: string; id: string } {
  if (isDefaultBaseQuery(baseQuery)) {
    return {
      prelude: 'WITH',
      from: `${scope} tf\n  ${color.join}`,
      where: 'tf.latitude IS NOT NULL AND tf.longitude IS NOT NULL\n    AND ',
      dbh: 'COALESCE(tf.dbh, 3)',
      id: 'tf.tree_id',
    }
  }
  const trees = scope === 'trees_fast' ? '' : ` trees AS (${treesSelectSql(scope)}\n),`
  return {
    prelude: `WITH${trees} base AS (\n  ${baseQuery}\n),`,
    from: `base\n  INNER JOIN ${scope} tf\n    ON base.tree_id = tf.tree_id\n  ${color.join}`,
    where: '',
    dbh: 'COALESCE(base.diameter_at_breast_height, tf.dbh, 3)',
    id: "COALESCE(base.tree_id, tf.tree_id, 'unkwn')",
  }
}

function normalizeSql(sql: string): string {
  return sql.replace(/\s+/g, ' ').trim().toLowerCase()
}

/**
 * True for the default query, with or without a city clause, whichever city
 * it names: the map draws every tree held, so a bookmark's default and the
 * next bookmark's are the same rows.
 */
function isDefaultBaseQuery(sql: string): boolean {
  const normalized = normalizeSql(sql)
  const plain = normalizeSql(DEFAULT_BASE_QUERY_SQL)
  return normalized === plain || normalized.replace(/ and city = '[a-z0-9]+'$/, '') === plain
}

/** What a published query is, for telling a change from a republish. */
function queryIdentity(sql: string | null): string {
  const text = sql?.trim() ? sql : DEFAULT_BASE_QUERY_SQL
  return isDefaultBaseQuery(text) ? 'default' : normalizeSql(text)
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
  return !isTileOutsideData(parsed.z, parsed.x, parsed.y)
}

function neighborhoodBatchKey(rev: number, z: number, minX: number, maxX: number, minY: number, maxY: number): string {
  return `${rev}:${treeFilterSignature()}:${z}:${minX}-${maxX}:${minY}-${maxY}`
}

/**
 * What a feature table's rows depend on: the query and the published filter.
 * Not the colours: the table holds each tree's species, form and id and the
 * read resolves the colour (`colorSql`), so a colour scheme or chat override
 * change keeps the table (7 s to build on Boston). Not the query revision
 * either: a republish of the same SQL must find the table it already has,
 * and a build that started a moment before the first publish must still
 * count.
 */
function featureTableBuildKey(baseQuery: string, z: number): string {
  const query = isDefaultBaseQuery(baseQuery) ? 'default' : hashText(normalizeSql(baseQuery))
  return `${query}:${publishedTreeIdFilterSignature}:${z}`
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

function getVisibleTileRange(z: number): TileBounds | null {
  return visibleTileRangeByZoom.get(z) ?? null
}

/** True for a tile no block of the world reaches: it is empty without a read. */
function isTileOutsideData(z: number, x: number, y: number): boolean {
  return world ? !tileHasBlocks(world.manifest, z, x, y) : false
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

/** A file's bytes under a name DuckDB can read, for as long as `use` runs. */
async function withRegisteredFile<T>(name: string, bytes: Uint8Array, use: () => Promise<T>): Promise<T> {
  await db!.registerFileBuffer(name, bytes)
  try {
    return await use()
  } finally {
    await db!.dropFile(name).catch(() => {})
  }
}

/**
 * Read the manifest and set the world's tables up: the vocabulary's ENUM
 * types, an empty `trees_fast` and the `trees` view (cityTables.ts). No tree
 * is loaded here. Memory is the constraint: the DuckDB-wasm heap only ever
 * grows, and on an iPhone it sits in the same process budget as the map, so
 * rows arrive only as cells near the camera are asked for (cityWindow.ts).
 */
async function loadWorld(early?: EarlyFiles): Promise<void> {
  if (!conn || !db) return
  const t0 = nowMs()
  const manifest = parseWorldManifest(await (early?.manifest ?? fetchWorldManifest()))
  const vocab = await (early?.vocab ?? fetchWorldFile(manifest.vocab))
  const vocabKb = Math.round(vocab.byteLength / 1024)
  const timings: Array<[string, number]> = []
  await withRegisteredFile('world_vocab.parquet', vocab, async () => {
    for (const sql of worldTablesSql('world_vocab.parquet')) {
      const started = nowMs()
      await conn!.query(sql)
      timings.push([sql.trim().slice(0, 40).replace(/\s+/g, ' '), Math.round(nowMs() - started)])
    }
  })
  windowGeneration += 1
  world = {
    manifest,
    cellZoom: cellZoom(),
    generation: windowGeneration,
    chunks: new Map(),
    cellTable: new Map(),
    cellLoads: new Map(),
    pending: new Set(),
    flush: null,
    pins: new Map(),
    viewCurrent: true,
  }
  console.info('[Perf] duckdb-worker:world-load', {
    trees: manifest.trees,
    blocks: manifest.blocks.size,
    cities: manifest.cities.length,
    cellZoom: world.cellZoom,
    vocabKb,
    ms: Math.round(nowMs() - t0),
    steps: timings.map(([label, ms]) => `${ms} ${label}`).join(' | '),
  })
}

/**
 * Drop every held cell and read the manifest again: the one it opened with
 * named a block that is gone (a build replaced it more than the retention
 * ago), or the layout asked for a different cell size. Whatever was drawn
 * from the old world is redrawn; the resident layer sees a new rows key.
 */
let worldReset: Promise<void> | null = null
function resetWorld(reason: string): Promise<void> {
  worldReset ??= (async () => {
    console.info('[duckdb-worker] reloading the world:', reason)
    try {
      const old = world
      if (old) {
        await windowLoadsSettled()
        world = null
        for (const sql of dropWorldTablesSql([...old.chunks.keys()])) await conn!.query(sql)
        colorOverrideActive = false
      }
      await loadWorld()
      if (contextCity) await conn!.query(publishedTreesViewSql(cityBrowserParquetUrl(contextCity)))
      resetFeatureTables()
      invalidateTileCaches('world-reset')
    } finally {
      worldReset = null
    }
  })()
  return worldReset
}

/** True for a cell whose rows are held, or that has none: its block is not in the world. */
function cellHeld(win: World, key: number): boolean {
  return win.cellTable.has(key) || !win.manifest.blocks.has(blockOfCell(key, win.cellZoom))
}

/** The cells of a tile that can hold trees: those inside a block the world lists. */
function windowCellsForTile(z: number, x: number, y: number): number[] {
  const win = world
  if (!win) return []
  // A tile far coarser than a cell spans thousands of them; the caller's floor keeps it from here.
  if (z < win.cellZoom - 4) return []
  return cellsForTile(z, x, y, win.cellZoom).filter((key) => win.manifest.blocks.has(blockOfCell(key, win.cellZoom)))
}

/** True when every row a tile could draw is loaded, so its result may be cached. */
function windowCoversTile(z: number, x: number, y: number): boolean {
  const win = world
  if (!win) return true
  if (z < win.cellZoom - 4) return !tileHasBlocks(win.manifest, z, x, y)
  return windowCellsForTile(z, x, y).every((key) => win.cellTable.has(key))
}

/**
 * Whether a tile is near enough the camera to pull its trees in: within the
 * policy's radius of the camera's cell. A tile MapLibre asks for mid-flight,
 * or below the floor, is answered empty and transient instead.
 */
function windowAllowsTile(z: number, x: number, y: number): boolean {
  const win = world
  if (!win) return true
  if (z < Math.floor(windowPolicy.floorZoom)) return false
  if (!activeViewportCenter) return true
  const cz = win.cellZoom
  const camera = cellAt(activeViewportCenter.lng, activeViewportCenter.lat, cz)
  const span = z >= cz ? 1 : 2 ** (cz - z)
  const middle = z >= cz
    ? cellFromKey(cellsForTile(z, x, y, cz)[0], cz)
    : { x: x * span + span / 2, y: y * span + span / 2 }
  return cellDistance(middle, camera) <= windowPolicy.radiusCells + span / 2
}

/** Load whichever of `keys` are not held, gathering concurrent requests into one load. */
async function ensureWindowCells(keys: number[]): Promise<void> {
  const win = world
  if (!win) return
  const waits: Promise<void>[] = []
  for (const key of keys) {
    if (cellHeld(win, key)) continue
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

/** Run `work` over `items`, `parallel` at a time. */
async function inParallel<T>(items: T[], parallel: number, work: (item: T) => Promise<void>): Promise<void> {
  let next = 0
  await Promise.all(Array.from({ length: Math.min(parallel, items.length) }, async () => {
    while (next < items.length) await work(items[next++])
  }))
}

/**
 * One load: the block files that hold `cells`, fetched side by side, their
 * rows into a table per cell, then the farthest cells dropped while the held
 * rows exceed the budget. A desktop's cell is a whole block and becomes a
 * table straight from its file; a phone's finer cells are cut out of their
 * blocks in one scan. A load that lands after a world reset is dropped unseen.
 */
async function loadWindowCells(win: World, cells: number[]): Promise<void> {
  if (!conn || !db || cells.length === 0) return
  const t0 = nowMs()
  const cz = win.cellZoom
  const seq = (windowChunkSeq += 1)
  const staging = `trees_window_staging_${seq}`
  const added: Chunk[] = []
  const live = () => world === win && win.generation === windowGeneration
  const byBlock = new Map<number, number[]>()
  for (const key of cells) {
    const block = blockOfCell(key, cz)
    byBlock.set(block, [...(byBlock.get(block) ?? []), key])
  }
  let bytes = 0
  const counts = new Map<number, number>()
  try {
    if (cz === BLOCK_ZOOM) {
      // One file, one table: only a block's bytes are in the heap at a time.
      await inParallel([...byBlock.keys()], BLOCK_FETCH_PARALLEL, async (key) => {
        const block = win.manifest.blocks.get(key)!
        const file = await fetchWorldFile(blockFile(block))
        bytes += file.byteLength
        if (!live()) return
        const name = `world_block_${key}_${seq}.parquet`
        await withRegisteredFile(name, file, () => conn!.query(blockCellSql(key, name)))
        added.push({ table: windowCellTable(key), cells: [key], rows: block.rows })
        counts.set(key, block.rows)
      })
    } else {
      // A phone keeps a sixteenth of a block, so it does not download the
      // block: DuckDB range-reads the row groups its cells fall in (the rows
      // are in cell order) straight from the published file, as it read a
      // window out of a city's projection. San Francisco's first phone view
      // is 24 cells of four 3.6 MB blocks.
      //
      // A street's view is a cell or two of one block, and there the range
      // reads cost more than the file (WHOLE_BLOCK_LOADS): it is fetched
      // whole, or was already when Near Me said where it would open.
      const blockFiles = [...byBlock.keys()].map((key) => blockFile(win.manifest.blocks.get(key)!))
      const whole = blockFiles.length <= WHOLE_BLOCK_LOADS
      const registered: string[] = []
      let files = blockFiles.map(worldFileUrl)
      try {
        if (whole) {
          const fetched = await Promise.all(blockFiles.map(wholeBlock))
          if (!live()) return
          files = []
          for (const [i, file] of fetched.entries()) {
            // The name keeps the block's key where the staging SQL can be read for it.
            const name = `whole_${seq}/${blockFiles[i]}`
            await db.registerFileBuffer(name, new Uint8Array(file))
            registered.push(name)
            files.push(name)
            bytes += file.byteLength
          }
        }
        const sql = windowStagingSql(staging, files, cells, cellsBounds(cells, cz), cz)
        await conn.query(sql.create)
        for (const row of (await conn.query(sql.counts)).toArray()) counts.set(Number(row.cell), Number(row.n))
        if (!live()) return
        for (const key of cells) {
          const rows = counts.get(key) ?? 0
          if (rows > 0) {
            await conn.query(windowCellSplitSql(staging, key, cz))
            added.push({ table: windowCellTable(key), cells: [key], rows })
          }
        }
      } finally {
        await conn.query(`DROP TABLE IF EXISTS ${staging}`).catch(() => {})
        for (const name of registered) await db.dropFile(name).catch(() => {})
      }
    }
    if (!live()) return
    for (const key of cells) win.cellTable.set(key, counts.get(key) ? windowCellTable(key) : '')
    for (const chunk of added) win.chunks.set(chunk.table, chunk)

    // The cells just loaded are what the view is waiting to draw, and a
    // pinned cell is being read: never the ones dropped to make room.
    const camera = activeViewportCenter ? cellAt(activeViewportCenter.lng, activeViewportCenter.lat, cz) : null
    const fresh = new Set(added.map((chunk) => chunk.table))
    const droppable = [...win.chunks.values()].filter((chunk) => !fresh.has(chunk.table) && !chunk.cells.some((key) => win.pins.has(key)))
    const evict = chunksToEvict(droppable, camera,
      windowPolicy.budgetRows - [...win.chunks.values()].filter((chunk) => !droppable.includes(chunk)).reduce((sum, chunk) => sum + chunk.rows, 0),
      windowPolicy.radiusCells, cz)
    for (const name of evict) {
      for (const key of win.chunks.get(name)?.cells ?? []) win.cellTable.delete(key)
      win.chunks.delete(name)
    }
    if (added.length > 0 || evict.length > 0) {
      // `trees_fast` is redefined when something next reads it (viewIsCurrent):
      // a desktop loading a metro's blocks in twenty batches would otherwise
      // replan a several-hundred-table union after each one.
      win.viewCurrent = false
      for (const name of evict) await conn.query(`DROP TABLE IF EXISTS ${name}`)
      resetFeatureTables()
    }

    console.info('[Perf] duckdb-worker:window-load', {
      cells: cells.length,
      blocks: byBlock.size,
      ...(bytes ? { kb: Math.round(bytes / 1024) } : {}),
      rows: added.reduce((sum, chunk) => sum + chunk.rows, 0),
      held: [...win.chunks.values()].reduce((sum, chunk) => sum + chunk.rows, 0),
      tables: win.chunks.size,
      evicted: evict.length,
      ms: Math.round(nowMs() - t0),
    })
  } catch (error) {
    // A block the manifest names is gone: the manifest is from an older build.
    const gone = error instanceof WorldFileError ? error.status === 404 : /HTTP 404|404 \(Not Found\)|No files found/i.test(String((error as Error)?.message))
    if (gone && live()) void resetWorld('a block the manifest names is no longer published')
    throw error
  } finally {
    // A load that failed or was superseded leaves nothing behind.
    if (!live() || added.some((chunk) => !win.chunks.has(chunk.table))) {
      for (const chunk of added) {
        if (!live() || !win.chunks.has(chunk.table)) await conn.query(`DROP TABLE IF EXISTS ${chunk.table}`).catch(() => {})
      }
    }
    for (const key of cells) win.cellLoads.delete(key)
  }
}

/** Redefine `trees_fast` over the cells now held, if any were added or dropped since it last was. */
async function viewIsCurrent(): Promise<void> {
  const win = world
  if (!win || !conn || win.viewCurrent) return
  win.viewCurrent = true
  await conn.query(windowViewSql([...win.chunks.keys()]))
}

/** Wait for every window load queued or running, so a read sees the ground it asked about. */
async function windowLoadsSettled(): Promise<void> {
  const win = world
  if (!win) return
  // A failed load has already left cellLoads; the read goes ahead on what is held.
  while (win.cellLoads.size > 0) await Promise.all([...new Set(win.cellLoads.values())].map((load) => load.catch(() => {})))
}

/**
 * Run a read over the tables of `cells`, loading the ones not held and
 * keeping all of them from being dropped while it runs. `read` gets the
 * tables of the cells that hold any trees.
 */
async function withCells<T>(cells: number[], read: (tables: string[]) => Promise<T>): Promise<T> {
  const win = world
  if (!win) return read([])
  for (;;) {
    await ensureWindowCells(cells)
    if (world !== win) throw new Error('The world was reloaded during a read')
    if (cells.every((key) => cellHeld(win, key))) break
  }
  for (const key of cells) win.pins.set(key, (win.pins.get(key) ?? 0) + 1)
  try {
    return await read(cells.flatMap((key) => win.cellTable.get(key) || []))
  } finally {
    for (const key of cells) {
      const n = (win.pins.get(key) ?? 1) - 1
      if (n > 0) win.pins.set(key, n)
      else win.pins.delete(key)
    }
  }
}

async function doInit() {
  if (db) return
  const t0 = nowMs()
  console.info('[Perf] duckdb-worker:init:start')
  const early = earlyFiles = startEarlyFiles()

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
  // Since duckdb-wasm 1.31 an unset forceFullHTTPReads means true: every
  // remote parquet was downloaded whole on first touch, so a window load
  // fetched the full projection and its row groups pruned nothing. Range
  // reads need it off; the ranged-HEAD sizing (reliableHeadRequests false)
  // is the one studio uses for the dashboards (trilogy-studio-core #277).
  await db.open({
    filesystem: { reliableHeadRequests: false, allowFullHTTPReads: true, forceFullHTTPReads: false },
  })

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
  // The table is read whole, so its file is fetched whole (startEarlyFiles)
  // and read from memory; from the bucket only if that fetch failed.
  const speciesBytes = await early.species
  const speciesFile = speciesBytes ? 'species_enrichment.parquet' : REMOTE_SPECIES_PARQUET_URL
  if (speciesBytes) await db.registerFileBuffer(speciesFile, speciesBytes)
  const hasTrunkPhoto = await parquetHasColumn(speciesFile, 'trunk_photo_url')
  const trunkPhotoColumns = hasTrunkPhoto
    ? 'trunk_photo_url, trunk_photo_license, trunk_photo_attribution'
    : 'CAST(NULL AS VARCHAR) AS trunk_photo_url, CAST(NULL AS VARCHAR) AS trunk_photo_license, CAST(NULL AS VARCHAR) AS trunk_photo_attribution'
  // The seasonal shades (November 2026), read the same way until the job
  // republishes with them; the "Leaf shade" colour mode and the tree card
  // read nulls and fall back to the tree-form colour meanwhile.
  const hasShades = await parquetHasColumn(speciesFile, 'foliage_colors')
  const shadeColumns = hasShades
    ? 'foliage_colors, fall_foliage_colors, trunk_color'
    : 'CAST(NULL AS VARCHAR[]) AS foliage_colors, CAST(NULL AS VARCHAR[]) AS fall_foliage_colors, CAST(NULL AS VARCHAR) AS trunk_color'
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
        ${trunkPhotoColumns},
        ${shadeColumns}
      FROM read_parquet('${speciesFile}')
    `)
  } catch (e) {
    console.error('[duckdb-worker] species-enrichment load failed — parquet schema may be out of date', e)
  } finally {
    if (speciesBytes) await db.dropFile(speciesFile).catch(() => {})
  }

  // The three columns the worker keeps, from the projection the pipeline
  // publishes for it; the full dimension (5.75 MB, almost all geometry) only
  // while the projection is not out yet.
  const ecoregionBytes = await early.ecoregions
  if (ecoregionBytes) await db.registerFileBuffer('ecoregion_browser.parquet', ecoregionBytes)
  for (const url of [...(ecoregionBytes ? ['ecoregion_browser.parquet'] : []), REMOTE_ECOREGION_BROWSER_PARQUET_URL, REMOTE_ECOREGION_PARQUET_URL]) {
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

  if (ecoregionBytes) await db.dropFile('ecoregion_browser.parquet').catch(() => {})

  // The world's manifest and vocabulary. No tree is loaded until a view asks.
  await loadWorld(early)
  postColorMapUpdate()

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

/** The colours the base scheme can draw: the trunk bands, every species shade and grey, or the tree-form categories. */
async function baseDistinctColors(): Promise<string[]> {
  if (dbhColorsActive) return Object.values(DBH_COLORS)
  if (!conn || !speciesShadeActive) return Object.values(DEFAULT_CATEGORY_COLORS)
  const result = await conn.query(`SELECT DISTINCT unnest(shades) AS display_color FROM ${SPECIES_SHADE_TABLE}`)
  return [...new Set([...result.toArray().map((r: Record<string, unknown>) => String(r.display_color)), UNKNOWN_SHADE_COLOR])]
}

/** Drop any chat colour override; every colour read falls back to the base scheme. */
async function clearColorOverride() {
  if (!conn) return
  await conn.query(`DROP TABLE IF EXISTS ${COLOR_OVERRIDE_TABLE}`)
  colorOverrideActive = false
  activeDistinctColors = await baseDistinctColors()
  activeColorLabelMap = baseScheme() === 'form' ? { ...DEFAULT_CATEGORY_LABELS } : {}
  colorMapSignature = 'default'
}

/**
 * The base colour scheme: a query returning (species, shades) turns the
 * leaf-shade scheme on, null returns to the tree-form colours. The table is
 * per species, so it survives a city switch.
 */
async function setSpeciesShadeSql(sql: string | null) {
  await ensureInit()
  if (!conn) return
  const normalized = sql?.trim() || null
  const signature = normalized ? `shade-${hashText(normalizeSql(normalized))}` : 'none'
  if (signature === speciesShadeSignature) return
  await conn.query(`DROP TABLE IF EXISTS ${SPECIES_SHADE_TABLE}`)
  if (normalized) {
    // Exact hexes first, then each snapped to the paint palette (snapShade):
    // a few thousand distinct shades would be as many heatmap layers.
    await conn.query(`CREATE OR REPLACE TEMP TABLE __species_shade_exact AS SELECT species, shades FROM (
${normalized}
) __shades`)
    const exact = await conn.query(`SELECT DISTINCT unnest(shades) AS shade FROM __species_shade_exact`)
    const pairs = exact.toArray().flatMap((r: Record<string, unknown>) => {
      const shade = String(r.shade)
      const snapped = snapShade(shade)
      // snapShade answers only for a bare #rrggbb, so the literal needs no escaping.
      return snapped ? [`('${shade}', '${snapped}')`] : []
    })
    await conn.query(`CREATE OR REPLACE TEMP TABLE __shade_snap (shade VARCHAR, snapped VARCHAR)`)
    if (pairs.length) await conn.query(`INSERT INTO __shade_snap VALUES ${pairs.join(', ')}`)
    await conn.query(`CREATE TEMP TABLE ${SPECIES_SHADE_TABLE} AS
SELECT species, list(snapped ORDER BY i) AS shades
FROM (
  SELECT species, unnest(shades) AS shade, generate_subscripts(shades, 1) AS i FROM __species_shade_exact
) JOIN __shade_snap USING (shade)
GROUP BY species`)
    await conn.query('DROP TABLE __species_shade_exact')
    await conn.query('DROP TABLE __shade_snap')
  }
  speciesShadeActive = normalized !== null
  speciesShadeSignature = signature
  if (colorOverrideActive) {
    const result = await conn.query(`SELECT DISTINCT display_color FROM ${COLOR_OVERRIDE_TABLE}`)
    const overrides = result.toArray().map((r: Record<string, unknown>) => String(r.display_color))
    activeDistinctColors = [...new Set([...overrides, ...await baseDistinctColors()])]
  } else {
    activeDistinctColors = await baseDistinctColors()
    activeColorLabelMap = baseScheme() === 'form' ? { ...DEFAULT_CATEGORY_LABELS } : {}
  }
  invalidateTileCaches('color-scheme')
  postColorMapUpdate()
}

async function ensureInit(): Promise<void> {
  if (ready) return
  if (!initPromise) {
    initPromise = doInit().catch((e) => {
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

  const generation = featureTableGeneration
  const inflightKey = `${generation}:${key}`
  const inflight = inflightFeatureTableBuild.get(inflightKey)
  if (inflight) {
    await inflight
    return
  }

  const xExpr = tileXExpr('tf', z)
  const yExpr = tileYExpr('tf', z)
  const table = featureTableName(z)
  const src = tileRowSource(baseQuery, { join: '' })

  const request = (async () => {
    // trees_fast is a view over the held cells, with no rowid to slice on:
    // the table is built a cell table at a time instead, each one a slice.
    await viewIsCurrent()
    const tables = world ? [...world.chunks.keys()] : []
    const slices = Math.max(1, tables.length)
    const sliceSource = (i: number) => tileRowSource(baseQuery, { join: '' }, tables.length ? tables[i] : WINDOW_SHAPE_TABLE)
    // The colour is not stored: the read joins it (featureTableBuildKey).
    // The id is a column too, so it is held once, and goes back into the
    // struct at read time.
    const sliceSql = (i: number) => `
${i === 0 ? `CREATE OR REPLACE TEMP TABLE ${table} AS` : `INSERT INTO ${table}`}
${sliceSource(i).prelude} rows AS (
  SELECT
    ${xExpr} AS xtile,
    ${yExpr} AS ytile,
    ${src.id} AS tree_id,
    tf.species,
    tf.tree_form,
    {
      'geom': ST_AsMVTGeom(
        ST_Point(tf.x_3857, tf.y_3857),
        ST_Extent(ST_TileEnvelope(${z}, ${xExpr}, ${yExpr})),
        4096,
        64,
        true
      ),
      'dbh': TRY_CAST(${src.dbh} AS DOUBLE)
    } AS feature
  FROM ${sliceSource(i).from}
)
SELECT xtile, ytile, tree_id, species, tree_form, feature
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
    // Readers check this key; the first slice replaces the table under it.
    preparedFeatureTablesReady.delete(z)
    let longestMs = 0
    const current = () => generation === featureTableGeneration && key === featureTableBuildKey(effectiveBaseQuery(tileQuerySql), z)
    for (let i = 0; i < slices; i += 1) {
      // Rows or query changed while an earlier slice ran: this table is for neither.
      if (!current()) {
        console.info('[Perf] duckdb-worker:feature-table:abandoned', { z, slice: i, slices })
        return
      }
      const ts = nowMs()
      await conn!.query(sliceSql(i))
      longestMs = Math.max(longestMs, nowMs() - ts)
    }
    console.info('[Perf] duckdb-worker:feature-table', { z, ms: Math.round(nowMs() - t0), slices, longestSliceMs: Math.round(longestMs) })
    if (!current()) return
    // CREATE OR REPLACE swapped the table under whatever key was there.
    preparedFeatureTablesReady.set(z, key)
  })()

  inflightFeatureTableBuild.set(inflightKey, request)
  try {
    await request
  } finally {
    inflightFeatureTableBuild.delete(inflightKey)
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

  const color = colorSql('tf', colorOverrideActive, baseScheme())
  const src = tileRowSource(baseQuery, color)
  // Overview cells group by colour only (see aggCacheSql in cityTables.ts).
  const aggColor = colorSql('tf', colorOverrideActive, baseScheme(), { aggregated: true })
  const aggSrc = tileRowSource(baseQuery, aggColor)
  const aggDbh = aggregatedDbhSql(z)
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
    // The feature table has no colour: it is resolved here over the range's
    // rows (a few tens of thousands), so a colour change never rebuilds it.
    const tableColor = colorSql('f', colorOverrideActive, baseScheme())
    const sql = useFeatureTable ? `
WITH rows AS (
  SELECT
    f.xtile,
    f.ytile,
    ${tableColor.expr} AS display_color,
    struct_insert(f.feature, id := f.tree_id) AS feature
  FROM ${featureTableName(PREPARED_FEATURE_TABLE_ZOOM)} f
  ${tableColor.join}
  WHERE f.xtile BETWEEN ${minX} AND ${maxX}
    AND f.ytile BETWEEN ${minY} AND ${maxY}
)
SELECT
  xtile,
  ytile,
  display_color AS layer,
  ST_AsMVT(feature, 'trees', 4096, 'geom') FILTER (WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)) AS mvt
FROM rows
GROUP BY xtile, ytile, display_color
` : simplifyGridMeters > 0 ? `
${aggSrc.prelude} pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    ${aggColor.expr} AS display_color,
    ${aggSrc.dbh} AS dbh,
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile
  FROM ${aggSrc.from}
  WHERE ${aggSrc.where}${tileXExpr('tf', z)} BETWEEN ${minX} AND ${maxX}
    AND ${tileYExpr('tf', z)} BETWEEN ${minY} AND ${maxY}
), agg AS (
  SELECT
    xtile,
    ytile,
    floor(x_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gx,
    floor(y_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gy,
    display_color,
    ${aggDbh.agg} AS dbh,
    COUNT(*) AS point_count
  FROM pts
  GROUP BY 1, 2, 3, 4, 5
), rows AS (
  SELECT
    xtile,
    ytile,
    display_color,
    {
      'geometry': ST_AsMVTGeom(
        ST_Point(gx, gy),
        ST_Extent(ST_TileEnvelope(${z}, xtile, ytile)),
        4096,
        64,
        true
      ),${aggDbh.property}
      'point_count': TRY_CAST(point_count AS INTEGER),
      'grid_m': ${simplifyGridMeters}
    } AS feature
  FROM agg
)
SELECT
  xtile,
  ytile,
  display_color AS layer,
  ST_AsMVT(feature, 'trees', 4096, 'geometry') AS mvt
FROM rows
WHERE feature.geometry IS NOT NULL AND NOT ST_IsEmpty(feature.geometry)
GROUP BY xtile, ytile, display_color
` : `
${src.prelude} pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    ${src.id} AS id,
    ${src.dbh} AS dbh,
    ${color.expr} AS display_color,
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile
  FROM ${src.from}
  WHERE ${src.where}${tileXExpr('tf', z)} BETWEEN ${minX} AND ${maxX}
    AND ${tileYExpr('tf', z)} BETWEEN ${minY} AND ${maxY}
), rows AS (
  SELECT
    xtile,
    ytile,
    display_color,
    {
      'geom': ST_AsMVTGeom(
        ST_Point(x_3857, y_3857),
        ST_Extent(ST_TileEnvelope(${z}, xtile, ytile)),
        4096,
        64,
        true
      ),
      'id': id,
      'dbh': TRY_CAST(dbh AS DOUBLE)
    } AS feature
  FROM pts
)
SELECT
  xtile,
  ytile,
  display_color AS layer,
  -- The geometry guard is the aggregate's FILTER, not a WHERE: DuckDB-WASM
  -- pushes a WHERE on feature.geom into the scan ahead of the tile range and
  -- runs ST_AsMVTGeom on every tree in the city (8.3 s against 32 ms for a
  -- four-tile z16 batch on Boston).
  ST_AsMVT(feature, 'trees', 4096, 'geom') FILTER (WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)) AS mvt
FROM rows
GROUP BY xtile, ytile, display_color
`

    const t0 = nowMs()
    const result = await conn.query(sql)
    // One row per tile and colour; each colour becomes its own source layer.
    const tiles = assembleLayeredTiles(result.toArray(), MVT_FALLBACK_LAYER)
    console.info('[Perf] duckdb-worker:neighborhood-batch', {
      z,
      mode: useFeatureTable ? 'feature-table' : simplifyGridMeters > 0 ? `agg${simplifyGridMeters}` : 'points',
      tiles: (maxX - minX + 1) * (maxY - minY + 1),
      nonEmpty: tiles.size,
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
      for (const { xtile, ytile, mvt } of tiles.values()) {
        if (!windowCoversTile(z, xtile, ytile)) continue
        setCachedTile(tileCacheKeyForRevision(rev, z, xtile, ytile), mvt)
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
  if (isTileOutsideData(z, x, y)) return new Uint8Array()
  await viewIsCurrent()

  const cached = getCachedTile(key)
  if (cached) return cached

  const inflight = inflightTileRequests.get(key)
  if (inflight) return inflight

  const baseQuery = effectiveBaseQuery(tileQuerySql)
  const { simplifyGridMeters } = adaptiveLodForTile(z, x, y)
  const bounds = tileBounds3857(z, x, y)
  const color = colorSql('tf', colorOverrideActive, baseScheme())
  const src = tileRowSource(baseQuery, color)
  const aggColor = colorSql('tf', colorOverrideActive, baseScheme(), { aggregated: true })
  const aggSrc = tileRowSource(baseQuery, aggColor)
  const aggDbh = aggregatedDbhSql(z)

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
${aggSrc.prelude} pts AS (
  SELECT
    tf.x_3857,
    tf.y_3857,
    ${aggColor.expr} AS display_color,
    ${aggSrc.dbh} AS dbh
  FROM ${aggSrc.from}
  WHERE ${aggSrc.where}tf.x_3857 BETWEEN ${bounds.minX} AND ${bounds.maxX}
    AND tf.y_3857 BETWEEN ${bounds.minY} AND ${bounds.maxY}
), agg AS (
  SELECT
    floor(x_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gx,
    floor(y_3857 / ${simplifyGridMeters}) * ${simplifyGridMeters} + ${simplifyGridMeters} / 2.0 AS gy,
    display_color,
    ${aggDbh.agg} AS dbh,
    COUNT(*) AS point_count
  FROM pts
  GROUP BY 1, 2, 3
), tiles AS (
  SELECT display_color, {
    'geometry': ST_AsMVTGeom(
      ST_Point(gx, gy),
      ST_Extent(ST_TileEnvelope(${z}, ${x}, ${y})),
      4096,
      64,
      true
    ),${aggDbh.property}
    'point_count': TRY_CAST(point_count AS INTEGER),
    'grid_m': ${simplifyGridMeters}
  } AS feature
  FROM agg
)
SELECT ${x} AS xtile, ${y} AS ytile, display_color AS layer, ST_AsMVT(feature, 'trees', 4096, 'geometry') AS mvt
FROM tiles
WHERE feature.geometry IS NOT NULL AND NOT ST_IsEmpty(feature.geometry)
GROUP BY display_color
` : `
${src.prelude} points AS (
  SELECT
    ${src.id} AS id,
    ${src.dbh} AS dbh,
    ${color.expr} AS display_color,
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
SELECT ${x} AS xtile, ${y} AS ytile, display_color AS layer, ST_AsMVT({'geom': geom, 'id': id, 'dbh': TRY_CAST(dbh AS DOUBLE)}, 'trees', 4096, 'geom') AS mvt
FROM points
WHERE geom IS NOT NULL
GROUP BY display_color
`

    const result = await conn.query(sql)
    const tile = assembleLayeredTiles(result.toArray(), MVT_FALLBACK_LAYER).get(`${x}/${y}`)?.mvt ?? new Uint8Array()
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
  if (queryIdentity(sql) === queryIdentity(tileQuerySql)) return
  tileQuerySql = sql
  tileQueryRevision += 1
  invalidateTileCaches('tile-query')
}

async function setPublishedTreeIdFilterSql(sql: string | null) {
  await ensureInit()
  if (!conn) return
  await viewIsCurrent()
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
  await viewIsCurrent()
  const normalized = sql?.trim() || null
  // No override before and none now: nothing to rebuild, no colours to push.
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
      // The colours the map draws a layer or a palette entry for. Only the
      // ground near the camera is held, and a colour first met in a cell
      // loaded later would have none, so this is every override colour and
      // every colour the base scheme can draw, not what the held rows show.
      const distinctResult = await conn.query(`SELECT DISTINCT display_color FROM ${COLOR_OVERRIDE_TABLE}`)
      const colors = distinctResult.toArray().map((r: Record<string, unknown>) => String(r.display_color))
      activeDistinctColors = [...new Set([...colors, ...await baseDistinctColors()])]
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

/**
 * The trunk-diameter scheme on or off. An expression over the row, so it
 * colours a tree paged in later by the same rule (cityTables.ts `dbhColorSql`).
 */
async function setDbhColors(enabled: boolean) {
  await ensureInit()
  if (dbhColorsActive === enabled) return
  dbhColorsActive = enabled
  if (colorOverrideActive && conn) {
    const result = await conn.query(`SELECT DISTINCT display_color FROM ${COLOR_OVERRIDE_TABLE}`)
    const overrides = result.toArray().map((r: Record<string, unknown>) => String(r.display_color))
    activeDistinctColors = [...new Set([...overrides, ...await baseDistinctColors()])]
  } else {
    activeDistinctColors = await baseDistinctColors()
    activeColorLabelMap = baseScheme() === 'form' ? { ...DEFAULT_CATEGORY_LABELS } : {}
  }
  invalidateTileCaches('color-scheme')
  postColorMapUpdate()
}

/**
 * Make `city` the bookmark in context: its projection behind
 * `trees_published`, its landmarks, its default query. It loads no trees and
 * drops none: what is held follows the camera, so moving the bookmark as the
 * camera crosses from one inventory into the next costs two small reads and
 * redraws nothing.
 */
async function setCityContext(city: string) {
  await ensureInit()
  if (!conn) return
  const startedAt = nowMs()
  // A layout that asks for another cell size (a phone's window turned on or off) starts the world over.
  if (world && world.cellZoom !== cellZoom()) await resetWorld('the window\'s cell size changed')

  if (contextCity !== city) {
    try {
      await conn.query(publishedTreesViewSql(cityBrowserParquetUrl(city)))
    } catch (e) {
      // A city whose projection is not out yet: its reads find no rows.
      console.warn('[duckdb-worker] city projection unavailable', city, e)
      await conn.query(publishedTreesViewSql(null))
    }
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
    contextCity = city
  }

  postColorMapUpdate()
  // The world's tables exist: unblock tile generation and queries.
  signalCityReady()
  const treeCount = world?.manifest.cities.find((c) => c.code === city)?.trees ?? 0
  ctx.postMessage({ type: 'cityContextReady', loadMs: Math.round(nowMs() - startedAt), treeCount })
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
    // The range's trees have to be held to be aggregated: nothing loaded the city ahead of this.
    const cells = new Set<number>()
    for (let x = rangeOverride.minX; x <= rangeOverride.maxX && cells.size <= MAX_PREFETCH_CELLS; x += 1) {
      for (let y = rangeOverride.minY; y <= rangeOverride.maxY; y += 1) for (const key of windowCellsForTile(z, x, y)) cells.add(key)
    }
    if (cells.size > 0 && cells.size <= MAX_PREFETCH_CELLS) await ensureWindowCells([...cells])
    await viewIsCurrent()
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
 * rest. A chat query reads the published files, not the cells held, and
 * one that lists a city's rows would otherwise build the whole result here
 * and again on the main thread: Boston's 1.4M rows took a phone tab from
 * 0.8 to 2.4 GB (docs/MOBILE_LOAD_BUDGET.md). Streamed, only one batch is
 * held at a time, in the query's own order. The stream runs on its own
 * connection: a tile query on the shared one would close it mid-read.
 *
 * With `at`, the read is about one tree: the cell under that point is loaded
 * if it is not held, and TREES_AT_TOKEN in the SQL becomes its table, so the
 * read plans one table and not the union of every cell held.
 */
async function runQuery(sql: string, maxRows?: number, at?: { lng: number; lat: number }): Promise<QueryResult> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !db) throw new Error('DuckDB not initialized')

  if (at) {
    const cz = cellZoom()
    const cell = cellAt(at.lng, at.lat, cz)
    return withCells([cellKey(cell.x, cell.y, cz)], async (tables) => {
      const result = await conn!.query(sql.split(TREES_AT_TOKEN).join(tables[0] ?? WINDOW_SHAPE_TABLE))
      const columns = result.schema.fields.map((f) => f.name)
      const rows = rowObjects(columns, result.toArray())
      return { columns, rows, totalRows: rows.length }
    })
  }

  // A read sees the cells held when it runs. It does not wait for loads in
  // flight: a desktop is fetching the rest of a metro in the background for
  // a minute after it opens, and the landmarks list is not waiting for that.
  await viewIsCurrent()

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

/** The longitude and latitude box a sprite query reads: the view's, or a square around the camera. */
function crownBox(camera: TreeRenderView): { west: number; east: number; south: number; north: number } {
  if (camera.bounds) return camera.bounds
  const dLat = CROWN_DISTANCE_MAX / 111_320
  const dLng = dLat / Math.max(0.01, Math.cos((camera.lat * Math.PI) / 180))
  return { west: camera.lng - dLng, east: camera.lng + dLng, south: camera.lat - dLat, north: camera.lat + dLat }
}

async function getMapTrees(camera: TreeRenderView): Promise<TreeCrownColumns> {
  const empty: TreeCrownColumns = {
    id: [], lng: new Float64Array(), lat: new Float64Array(), width: new Float64Array(), measured: new Uint8Array(),
    color: [], category: [], dbh: new Float64Array(), height: new Float64Array(),
  }
  await ensureInit()
  await waitForCityContext()
  // The intro pauses vector-tile work, but still needs the independent sprite
  // renderer to draw and satisfy its readiness gate. This reads local tables.
  const win = world
  if (!conn || !win || (!camera.bounds && camera.altitude >= CROWN_DISTANCE_MAX)) return empty
  const revision = tileQueryRevision
  const signature = treeFilterSignature()
  const current = () => cityContextReady && world === win && tileQueryRevision === revision && treeFilterSignature() === signature
  if (!current()) return empty
  const baseQuery = effectiveBaseQuery(tileQuerySql)
  const filter = isDefaultBaseQuery(baseQuery) ? null : baseQuery
  const color = colorSql('tf', colorOverrideActive, baseScheme())
  // The cells under the view: the ones near the camera are loaded for the
  // read, the rest are read if they happen to be held. A view too wide to
  // list (a pitched horizon) reads the whole view of what is held.
  const cz = win.cellZoom
  const cells = cellsInBox(crownBox(camera), cz, MAX_SCOPE_CELLS)?.filter((key) => win.manifest.blocks.has(blockOfCell(key, cz))) ?? null
  let result
  if (cells) {
    const at = cellAt(camera.lng, camera.lat, cz)
    // A phone's crowns reach CROWN_DISTANCE_MAX, nine z14 cells and most of a
    // block; its screen at the zoom they draw is a few hundred metres. Only
    // the cells on screen are loaded for the read (the tiles ask for the same
    // ones); a crown past them is drawn if its cell is held.
    const onScreen = camera.view && !camera.bounds ? new Set(cellsInBox(camera.view, cz, MAX_SCOPE_CELLS) ?? cells) : null
    const near = cells.filter((key) => (!onScreen || onScreen.has(key)) && cellDistance(cellFromKey(key, cz), at) <= windowPolicy.radiusCells)
    result = await withCells(near, async () => {
      const tables = cells.flatMap((key) => win.cellTable.get(key) || [])
      const scope = scopeSql(tables)
      return conn!.query(crownQuery(camera, filter, color, { from: scope, trees: filter ? treesSelectSql(scope) : null }))
    })
  } else {
    await viewIsCurrent()
    result = await conn.query(crownQuery(camera, filter, color))
  }
  if (!current()) return empty
  // Read column by column: no per-row proxy objects.
  const n = result.numRows
  const numbers = (name: string) => {
    const col = result.getChild(name)!, out = new Float64Array(n)
    for (let i = 0; i < n; i++) { const v = col.get(i); out[i] = v == null ? NaN : Number(v) }
    return out
  }
  const strings = (name: string) => {
    const col = result.getChild(name)!, out: string[] = new Array(n)
    for (let i = 0; i < n; i++) out[i] = String(col.get(i))
    return out
  }
  const measuredCol = result.getChild('measured')!
  const measured = new Uint8Array(n)
  for (let i = 0; i < n; i++) measured[i] = measuredCol.get(i) ? 1 : 0
  const columns: TreeCrownColumns = {
    id: strings('id'), lng: numbers('lng'), lat: numbers('lat'), width: numbers('width'), measured,
    color: strings('color'), category: strings('category'), dbh: numbers('dbh'), height: numbers('height'),
  }
  return columns
}

// --- Point chunks (the desktop's TreeDotsLayer) ---

/** What the drawn rows are: the world's build, and the query and filter over it. Not the bookmark. */
function pointRowsKey(): string {
  return `${world?.manifest.id}|${world?.generation}|${tileQueryRevision}|${publishedTreeIdFilterSignature}`
}

function pointColorKey(): string {
  return `${colorMapSignature}|${colorSchemeSignature()}|${activeDistinctColors.join(',')}`
}

/** Copy an Arrow column into its own buffer, so it can be transferred. */
function columnArray<T extends Float32Array | Uint8Array | Uint32Array>(table: { getChild(name: string): { toArray(): unknown } | null }, name: string, Ctor: { new(source: ArrayLike<number>): T }): T {
  const column = table.getChild(name)
  return new Ctor((column ? column.toArray() : []) as ArrayLike<number>)
}

/** The rows source and colour a point read over `tables` uses. */
function pointSource(tables: string[]) {
  const color = colorSql('tf', colorOverrideActive, baseScheme())
  return { src: tileRowSource(effectiveBaseQuery(tileQuerySql), color, scopeSql(tables)), color, palette: pointPalette(activeDistinctColors) }
}

/** The cells that hold the rows of some chunks: the chunks themselves on a desktop, their sixteenths on a phone. */
function cellsOfChunks(keys: number[]): number[] {
  const cz = cellZoom()
  const span = 2 ** (cz - CHUNK_ZOOM)
  if (span === 1) return keys
  const perAxis = 2 ** CHUNK_ZOOM
  return keys.flatMap((key) => {
    const x = Math.floor(key / perAxis) * span, y = (key % perAxis) * span
    return Array.from({ length: span * span }, (_, i) => cellKey(x + Math.floor(i / span), y + (i % span), cz))
  })
}

/** Which rows and colours a point read would answer with now, without reading: null while the world is not up. */
async function getPointKeys(): Promise<PointKeys | null> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !world) return null
  return { rowsKey: pointRowsKey(), colorKey: pointColorKey() }
}

/** Every chunk that may hold a tree: the manifest's blocks. No read; the world's directory is a file. */
async function getPointChunkDirectory(): Promise<PointChunkDirectory | null> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !world) return null
  const keys: number[] = [], counts: number[] = []
  for (const block of world.manifest.blocks.values()) { keys.push(block.key); counts.push(block.rows) }
  return {
    rowsKey: pointRowsKey(), colorKey: pointColorKey(), keys, counts,
    exact: isDefaultBaseQuery(effectiveBaseQuery(tileQuerySql)),
  }
}

/** The rows of `keys`, grouped by chunk, their blocks loaded for the read; null when `rowsKey` is no longer the current rows. */
async function getPointChunks(rowsKey: string, keys: number[]): Promise<PointChunkBatch | null> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !world || pointRowsKey() !== rowsKey || !keys.length) return null
  const colorKey = pointColorKey()
  const t0 = nowMs()
  return withCells(cellsOfChunks(keys), async (tables) => {
    if (pointRowsKey() !== rowsKey || pointColorKey() !== colorKey) return null
    const { src, color, palette } = pointSource(tables)
    const table = await conn!.query(pointChunksSql(src, keys, color.expr, palette))
    if (pointRowsKey() !== rowsKey || pointColorKey() !== colorKey) return null
    const { offsets, columns } = bucketByChunk(columnArray(table, 'chunk', Uint32Array), keys, {
      x: columnArray(table, 'x', Float32Array),
      y: columnArray(table, 'y', Float32Array),
      dbh: columnArray(table, 'dbh', Uint8Array),
      crown: columnArray(table, 'crown', Uint8Array),
      color: columnArray(table, 'color', Uint8Array),
      rowid: columnArray(table, 'rowid', Uint32Array),
    })
    const grids = gridOrderChunks(offsets, columns.x, columns.y, { dbh: columns.dbh, crown: columns.crown, color: columns.color, rowid: columns.rowid })
    console.info('[Perf] duckdb-worker:point-chunks', { chunks: keys.length, rows: table.numRows, ms: Math.round(nowMs() - t0) })
    return { rowsKey, colorKey, palette, keys, offsets, ...columns, grids }
  })
}

/** The current colour of the rows of `keys`, grouped by chunk; null when `rowsKey` is no longer current. */
async function getPointChunkColors(rowsKey: string, keys: number[]): Promise<PointChunkColors | null> {
  await ensureInit()
  await waitForCityContext()
  if (!conn || !world || pointRowsKey() !== rowsKey || !keys.length) return null
  const colorKey = pointColorKey()
  const t0 = nowMs()
  return withCells(cellsOfChunks(keys), async (tables) => {
    if (pointRowsKey() !== rowsKey || pointColorKey() !== colorKey) return null
    const { src, color, palette } = pointSource(tables)
    const table = await conn!.query(pointChunkColorsSql(src, keys, color.expr, palette))
    if (pointRowsKey() !== rowsKey || pointColorKey() !== colorKey) return null
    const { offsets, columns } = bucketByChunk(columnArray(table, 'chunk', Uint32Array), keys, {
      color: columnArray(table, 'color', Uint8Array),
      rowid: columnArray(table, 'rowid', Uint32Array),
    })
    console.info('[Perf] duckdb-worker:point-chunk-colors', { chunks: keys.length, rows: table.numRows, ms: Math.round(nowMs() - t0) })
    return { rowsKey, colorKey, palette, keys, offsets, ...columns }
  })
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
  // A tile is drawn only from loaded cells. The cells are loaded
  // here, before the tile queue, so every tile of a view asks at once and the
  // gather in ensureWindowCells makes one scan of them.
  if (world && !getCachedTile(tileCacheKey(z, x, y)) && !windowCoversTile(z, x, y)) {
    if (!windowAllowsTile(z, x, y)) return { tile: new Uint8Array(), transient: true }
    await ensureWindowCells(windowCellsForTile(z, x, y))
  }
  return { tile: await queueTileRequest(z, x, y), transient: false }
}

async function getWorldDirectory(): Promise<WorldDirectory | null> {
  await ensureInit()
  return world ? worldDirectory(world.manifest) : null
}

type WorkerMethodMap = {
  getMapTrees: { params: TreeRenderView; result: TreeCrownColumns }
  getCrownBatch: { params: { camera: TreeRenderView; options: CrownBatchOptions }; result: CrownBatch }
  getPointKeys: { params: Record<string, never>; result: PointKeys | null }
  getPointChunkDirectory: { params: Record<string, never>; result: PointChunkDirectory | null }
  getPointChunks: { params: { rowsKey: string; keys: number[] }; result: PointChunkBatch | null }
  getPointChunkColors: { params: { rowsKey: string; keys: number[] }; result: PointChunkColors | null }
  getWorldDirectory: { params: Record<string, never>; result: WorldDirectory | null }
  ensureInit: { params: { city?: string; window?: WindowPolicy | null }; result: { ready: boolean; initError: string | null } }
  setTileQuery: { params: { sql: string | null }; result: void }
  setPublishedTreeIdFilterSql: { params: { sql: string | null }; result: void }
  setColorOverrideSql: { params: { sql: string | null }; result: void }
  setSpeciesShadeSql: { params: { sql: string | null }; result: void }
  setDbhColors: { params: { enabled: boolean }; result: void }
  setCityContext: { params: { city: string; window?: WindowPolicy | null }; result: void }
  setViewportZoom: { params: { zoom: number }; result: void }
  setViewportCenter: { params: { lng: number; lat: number }; result: void }
  ensureWindowAt: { params: { lng: number; lat: number }; result: void }
  prefetchWindowAt: { params: { lng: number; lat: number }; result: void }
  setVisibleTileRange: { params: { z: number; minX: number; maxX: number; minY: number; maxY: number }; result: void }
  prefetchVisibleTilesAtZoom: {
    params: { z: number; range?: { minX: number; maxX: number; minY: number; maxY: number } }
    result: PrefetchStatus
  }
  prewarmLodCaches: { params: Record<string, never>; result: void }
  setAutoTileFetchEnabled: { params: { enabled: boolean }; result: void }
  invalidateTileCaches: { params: Record<string, never>; result: void }
  query: { params: { sql: string; maxRows?: number; at?: { lng: number; lat: number } }; result: QueryResult }
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
        send({ type: 'response', requestId: msg.requestId, ok: true, result }, crownColumnBuffers(result))
        break
      }
      case 'getCrownBatch': {
        // The sort and vertex fill run here, off the main thread; the GPU buffer is transferred.
        const { camera, options } = msg.params as { camera: TreeRenderView; options: CrownBatchOptions }
        const started = performance.now()
        const columns = await getMapTrees(camera)
        const built = performance.now()
        const result = buildCrownBatch(columns, camera, options)
        result.timing = { sqlMs: built - started, buildMs: performance.now() - built }
        send({ type: 'response', requestId: msg.requestId, ok: true, result }, crownBatchBuffers(result))
        break
      }
      case 'getPointKeys': {
        send({ type: 'response', requestId: msg.requestId, ok: true, result: await getPointKeys() })
        break
      }
      case 'getPointChunkDirectory': {
        send({ type: 'response', requestId: msg.requestId, ok: true, result: await getPointChunkDirectory() })
        break
      }
      case 'getPointChunks': {
        const { rowsKey, keys } = msg.params as WorkerMethodMap['getPointChunks']['params']
        const result = await getPointChunks(rowsKey, keys)
        send({ type: 'response', requestId: msg.requestId, ok: true, result },
          result ? [result.offsets.buffer, result.x.buffer, result.y.buffer, result.dbh.buffer, result.crown.buffer, result.color.buffer, result.rowid.buffer,
            ...result.grids.map((g) => g.start.buffer)] : undefined)
        break
      }
      case 'getPointChunkColors': {
        const { rowsKey, keys } = msg.params as WorkerMethodMap['getPointChunkColors']['params']
        const result = await getPointChunkColors(rowsKey, keys)
        send({ type: 'response', requestId: msg.requestId, ok: true, result },
          result ? [result.offsets.buffer, result.color.buffer, result.rowid.buffer] : undefined)
        break
      }
      case 'getWorldDirectory': {
        send({ type: 'response', requestId: msg.requestId, ok: true, result: await getWorldDirectory() })
        break
      }
      case 'ensureInit': {
        const { window } = msg.params as WorkerMethodMap['ensureInit']['params']
        if (window !== undefined) windowPolicy = window ?? DESKTOP_POLICY
        await ensureInit()
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
      case 'setSpeciesShadeSql': {
        const { sql } = msg.params as WorkerMethodMap['setSpeciesShadeSql']['params']
        await setSpeciesShadeSql(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setDbhColors': {
        const { enabled } = msg.params as WorkerMethodMap['setDbhColors']['params']
        await setDbhColors(enabled)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setCityContext': {
        const { city, window } = msg.params as WorkerMethodMap['setCityContext']['params']
        if (window !== undefined) windowPolicy = window ?? DESKTOP_POLICY
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
        const cell = cellAt(lng, lat, cellZoom())
        await ensureWindowCells([cellKey(cell.x, cell.y, cellZoom())])
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'prefetchWindowAt': {
        const { lng, lat } = msg.params as WorkerMethodMap['prefetchWindowAt']['params']
        prefetchBlockAt(lng, lat)
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
        const { sql, maxRows, at } = msg.params as WorkerMethodMap['query']['params']
        const result = await runQuery(sql, maxRows, at)
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
