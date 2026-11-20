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
  REMOTE_FOLIAGE_DEFAULTS_PARQUET_URL,
  REMOTE_ECOREGION_PARQUET_URL,
  REMOTE_ECOREGION_BROWSER_PARQUET_URL,
  REMOTE_LANDMARKS_PARQUET_URL,
  cityBrowserParquetUrl,
  cityLandmarkParquetUrl,
  WorldFileError,
  fetchPublishedFile,
  fetchWorldFile,
  fetchWorldManifest,
  fetchWorldRange,
  parquetExtensionUrl,
  worldFileUrl,
} from './parquetUrls'
import { normalizeValue } from './normalizeValue'
import {
  CITY_ENUM_TYPES,
  COLOR_OVERRIDE_TABLE,
  DBH_COLORS,
  SHADE_TABLES,
  type ShadeTier,
  NOTABLE_TOKEN_PATTERN,
  TREES_AT_TOKEN,
  UNKNOWN_SHADE_COLOR,
  WINDOW_SHAPE_TABLE,
  treeKeySql,
  WEB_MERCATOR_MAX,
  WEB_MERCATOR_WORLD,
  blockCellSql,
  colorSql,
  dropWorldTablesSql,
  notableRelationSql,
  PUBLISHED_TREES_VIEW,
  publishedTreesViewSql,
  scopeSql,
  tileIndexSql,
  hexLiterals,
  treeRuleApplySql,
  treeRuleForgetSql,
  treeRuleRowsSql,
  treeRuleTablesSql,
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
/** The first zoom whose tiles hold a point per tree; below it a tile is an aggregate. */
const DETAIL_TILE_ZOOM = 15
const PUBLISHED_TREE_FILTER_TABLE = 'published_tree_filter_ids'

import { CATEGORY_COLORS as DEFAULT_CATEGORY_COLORS } from '../treeFormColors'
import { FoliageLookup, fineShadeMap, fittedShadeMap, foliageDefaultsSql, foliageSpeciesSql, OVERVIEW_SHADE_COUNT } from '../lib/foliageSeason'
import type { InitStage } from '../lib/loadProgress'
import { encodePointTiles, type PointProperty } from './pointTiles'
import { SparseBlock, type Box } from './sparseBlocks'
import {
  CHUNK_ZOOM, bucketByChunk, chunkLeafOff, flaggedRows, gridOrderChunks, pointChunkColorsSql, pointChunksSql, pointPalette,
  type PointChunkBatch, type PointChunkColors, type PointChunkDirectory, type PointKeys,
} from './cityPoints'

/** The published genus shares by cell, read once at start (lib/foliageSeason). */
let foliageDefaults: { cell: number; genus: string; deciduous: boolean }[] = []
/** Each tree's leaf-off months by its species code: rebuilt with each world, whose species ENUM it is keyed on. */
let foliage = FoliageLookup.none()

/** The source layer a tree with no colour lands in: the style's own fallback colour. */
const MVT_FALLBACK_LAYER = '#66BB6A'
/**
 * The palette a tile at `z` paints leaf shades from. Below DETAIL_TILE_ZOOM a
 * tile is drawn by a heatmap and a circle layer per colour, so a few merged
 * colours; from it a tile is one source layer with the colour on each feature
 * (pointTiles.ts `singleLayer`), drawn by one circle layer, so the fine
 * palette the desktop's dots paint.
 */
function tileShadeTier(z: number): ShadeTier {
  return z >= DETAIL_TILE_ZOOM ? 'fine' : 'overview'
}

/** How a tile at `z` is laid out: one source layer with a colour on each feature from DETAIL_TILE_ZOOM up. */
function tileLayout(z: number): { singleLayer: boolean } {
  return { singleLayer: z >= DETAIL_TILE_ZOOM }
}

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
function aggregatedDbhSql(z: number): { agg: string; select: string } {
  return z < CIRCLE_TILE_MIN_ZOOM
    ? { agg: 'NULL', select: '' }
    : { agg: 'round(AVG(dbh))', select: 'TRY_CAST(dbh AS INTEGER) AS dbh,' }
}

/** What a tree's own feature carries: a tap reads the id, the circles the diameter. */
const POINT_PROPERTIES: PointProperty[] = [
  { name: 'id', kind: 'string' },
  { name: 'dbh', kind: 'double' },
  // Written only on a notable tree.
  { name: 'notable', kind: 'flag' },
]

/** What an overview cell carries: the heatmaps read its count and grid size, the circles (z12 up) its diameter. */
function aggregateProperties(z: number, gridMeters: number): PointProperty[] {
  return [
    ...(z < CIRCLE_TILE_MIN_ZOOM ? [] : [{ name: 'dbh', kind: 'int' as const }]),
    { name: 'point_count', kind: 'int' },
    { name: 'grid_m', kind: 'int', value: gridMeters },
  ]
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
/**
 * The chat's standing rule (cityTables.ts `treeRuleRowsSql`): asked of every
 * cell as it loads, its answers in the filter and colour tables. `colors` is
 * every colour it has answered with or names as a literal, the palette's head.
 */
let treeRule: { sql: string; color: boolean; signature: string; colors: Set<string> } | null = null
/** One rule statement at a time: they share a staging table, and a publish must not interleave with a load's. */
let treeRuleQueue: Promise<unknown> = Promise.resolve()
/** True while the species shade tables exist (SHADE_TABLES): the leaf-shade scheme is the base colour. */
let speciesShadeActive = false
let speciesShadeSignature = 'none'
/** True while the base colour is the trunk diameter's band (`dbhColorSql`). */
let dbhColorsActive = false
/**
 * Every colour the overview tiles (below DETAIL_TILE_ZOOM) may draw: a heatmap
 * and a circle layer each (useMapLayers). The tree tiles carry their colour
 * on each feature and need no list.
 */
let activeDistinctColors: string[] = Object.values(DEFAULT_CATEGORY_COLORS)
/** Every colour the desktop's dots may draw: their one-byte palette (cityPoints pointPalette). Differs only for leaf shades. */
let pointDistinctColors: string[] = activeDistinctColors
let activeColorLabelMap: Record<string, string> = { ...DEFAULT_CATEGORY_LABELS }
let activeViewportZoom = 13
let activeViewportCenter: { lng: number; lat: number } | null = null
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
 * A block of this many rows or more is not fetched whole for a phone's load:
 * only its footer and the row groups the load's cells fall in are
 * (sparseBlocks.ts). Central Boston's block is 129,000 rows and 4.1 MB, and a
 * street view's two cells are five of its thirteen row groups, 1.6 MB. Below
 * this (a megabyte or so) the footer's extra round trip is not worth the
 * bytes; nine blocks in ten are smaller.
 */
const SPARSE_BLOCK_ROWS = 30_000
/** Blocks held in part, newest last, within the same allowance as the whole ones. */
const sparseBlocks = new Map<string, Promise<SparseBlock>>()
const sparseBlockSizes = new Map<string, number>()
/** Blocks whose partial read failed once: read whole from then on. */
const sparseRefused = new Set<string>()

/** Forget the oldest entries of `held` (newest last) until `sizes` fit the allowance; never `keep`. */
function trimHeldBlocks(held: Map<string, unknown>, sizes: Map<string, number>, keep: string): void {
  let total = 0
  for (const size of sizes.values()) total += size
  for (const name of [...held.keys()]) {
    if (total <= WHOLE_BLOCK_BYTES || name === keep) break
    total -= sizes.get(name) ?? 0
    held.delete(name)
    sizes.delete(name)
  }
}

/** A block held in part: the one kept, or a new one that is kept once its footer is read. */
function sparseBlock(file: string): Promise<SparseBlock> {
  const held = sparseBlocks.get(file)
  if (held) {
    sparseBlocks.delete(file)
    sparseBlocks.set(file, held)
    return held
  }
  const opened = SparseBlock.open(file, fetchWorldRange)
  sparseBlocks.set(file, opened)
  opened.then((block) => {
    if (sparseBlocks.get(file) !== opened) return
    sparseBlockSizes.set(file, block.total)
    trimHeldBlocks(sparseBlocks, sparseBlockSizes, file)
  }, () => {
    if (sparseBlocks.get(file) === opened) sparseBlocks.delete(file)
  })
  return opened
}

function refuseSparse(file: string): void {
  sparseRefused.add(file)
  sparseBlocks.delete(file)
  sparseBlockSizes.delete(file)
}

/**
 * The bytes a read of `box` needs from a block file, as a buffer the engine
 * can be handed as that file: the row groups under the box for a large block,
 * the whole file for a small one or one whose partial read has failed.
 */
async function blockBytes(file: string, rows: number, box: Box): Promise<{ bytes: Uint8Array; sparse: SparseBlock | null }> {
  if (rows >= SPARSE_BLOCK_ROWS && !sparseRefused.has(file) && !wholeBlocks.has(file)) {
    try {
      const block = await sparseBlock(file)
      await block.cover(box)
      return { bytes: block.bytes, sparse: block }
    } catch (error) {
      // A file that is gone is the load's to report; anything else, the file is read whole.
      if (error instanceof WorldFileError && error.status === 404) throw error
      console.warn('[duckdb-worker] a partial read of a block failed; reading it whole', file, error)
      refuseSparse(file)
    }
  }
  return { bytes: await wholeBlock(file), sparse: null }
}

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
    trimHeldBlocks(wholeBlocks, wholeBlockSizes, file)
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
  foliage: Promise<Uint8Array | null>
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
    foliage: fetchPublishedFile(REMOTE_FOLIAGE_DEFAULTS_PARQUET_URL),
  }
}

/** Half the side of the ground a phone's screen shows where Near Me opens (z17), in metres, with room to spare. */
const OPENING_VIEW_HALF_M = 200

/** The cells a phone's screen shows where Near Me opens on a point: the ones its first tiles will ask for. */
function openingCells(lng: number, lat: number, cz: number, manifest: WorldManifest): number[] {
  const dLat = OPENING_VIEW_HALF_M / 111_320
  const dLng = dLat / Math.max(0.01, Math.cos((lat * Math.PI) / 180))
  const cells = cellsInBox({ west: lng - dLng, east: lng + dLng, south: lat - dLat, north: lat + dLat }, cz, MAX_SCOPE_CELLS) ?? []
  return cells.filter((key) => manifest.blocks.has(blockOfCell(key, cz)))
}

/**
 * Fetch the trees under a point before anything can read them: Near Me knows
 * where the camera will open while the engine is still downloading, and those
 * bytes are what the first trees wait for after it. A phone asks for what its
 * opening view's cells need of their block (blockBytes); a desktop's cell is
 * the block.
 *
 * Then, as soon as the engine is up, a phone's cells under that view are cut
 * out of the block. The first tile used to ask for them, and it is asked for
 * only once the bookmark, the colour scheme and the map's layers are all in
 * place: a third of a second of loading that now runs beside that set-up
 * instead of after it.
 */
function prefetchBlockAt(lng: number, lat: number): void {
  const started = ensureInit()
  const manifest = world ? Promise.resolve(world.manifest) : earlyFiles?.manifest.then(parseWorldManifest)
  manifest?.then((m) => {
    const cz = cellZoom()
    if (cz === BLOCK_ZOOM) {
      const at = blockAt(lng, lat)
      const block = m.blocks.get(blockKey(at.x, at.y))
      if (block) wholeBlock(blockFile(block)).catch(() => {})
      return
    }
    // The same cells, and so the same box, as the load below asks for: it finds these bytes held.
    const cells = openingCells(lng, lat, cz, m)
    const blocks = [...new Set(cells.map((key) => blockOfCell(key, cz)))]
    if (cells.length === 0 || blocks.length > WHOLE_BLOCK_LOADS) return
    const box = cellsBounds(cells, cz)
    for (const key of blocks) {
      const block = m.blocks.get(key)!
      blockBytes(blockFile(block), block.rows, box).catch(() => {})
    }
  }).catch(() => {})
  started.then(() => {
    const win = world
    // A desktop's resident layer asks for the blocks it draws.
    if (!win || win.cellZoom === BLOCK_ZOOM) return
    return ensureWindowCells(openingCells(lng, lat, win.cellZoom, win.manifest))
  }).catch(() => {})
}
/** The most cells an intro's prefetch of a tile range loads ahead of its aggregate. */
const MAX_PREFETCH_CELLS = 400
/** The widest scoped read: past this many cells a read uses the whole view. */
const MAX_SCOPE_CELLS = 64
/** How long tile requests are gathered before their cells are loaded in one scan. */
const WINDOW_GATHER_MS = 40

/**
 * What the main thread is told about the worker's network: the init stage
 * reached (the Near Me overlay walks through them) and how many window loads
 * are fetching blocks (the corner leaf). Pushed, never asked for.
 */
let activeWindowLoads = 0
function postInitStage(stage: InitStage): void {
  ctx.postMessage({ type: 'initStage', stage })
}
function postWindowLoads(delta: number): void {
  activeWindowLoads = Math.max(0, activeWindowLoads + delta)
  ctx.postMessage({ type: 'windowLoads', active: activeWindowLoads })
}

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
  if (!publishedTreeIdFilterSql?.trim() && !treeRule) return baseQuery
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
  foliage = await buildFoliageLookup()
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
 * The leaf-habit lookup over the world's species ENUM: one row a species code, joined to the
 * enrichment table once here rather than in every crown and chunk read. With no enrichment
 * table (it failed to load) every tree keeps its leaves.
 */
async function buildFoliageLookup(): Promise<FoliageLookup> {
  try {
    const table = await conn!.query(foliageSpeciesSql(CITY_ENUM_TYPES.species))
    const code = table.getChild('code')!, deciduous = table.getChild('deciduous')!, genus = table.getChild('open_genus')!
    const species = Array.from({ length: table.numRows }, (_, i) => {
      const d = deciduous.get(i), g = genus.get(i)
      return { code: Number(code.get(i)), deciduous: d == null ? null : !!d, open_genus: g == null ? null : String(g) }
    })
    return new FoliageLookup(species, foliageDefaults)
  } catch (e) {
    console.warn('[Perf] duckdb-worker:foliage-lookup:failed', e)
    return FoliageLookup.none()
  }
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
      // The rule's answers went with the cells they were about; it is asked again of each cell that loads.
      if (treeRule) await onTreeRuleQueue(() => resetTreeRuleTables())
      // The view went with the world's tables; a standing query that reads it gets it back now.
      publishedViewCity = undefined
      await publishedView(tileQuerySql)
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
  postWindowLoads(1)
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
      //
      // A large block is not fetched whole either: only the row groups the
      // cells' box reaches (blockBytes), in a buffer the engine reads as the
      // file. Should the engine read past them it throws, and the load is
      // made again from the whole files.
      const blocks = [...byBlock.keys()].map((key) => win.manifest.blocks.get(key)!)
      const blockFiles = blocks.map(blockFile)
      const whole = blockFiles.length <= WHOLE_BLOCK_LOADS
      const registered: string[] = []
      const box = cellsBounds(cells, cz)
      const stage = async (): Promise<boolean> => {
        let files = blockFiles.map(worldFileUrl)
        const partial: string[] = []
        if (whole) {
          const fetched = await Promise.all(blocks.map((block) => blockBytes(blockFile(block), block.rows, box)))
          if (!live()) return false
          files = []
          bytes = 0
          for (const [i, file] of fetched.entries()) {
            // The name keeps the block's key where the staging SQL can be read for it.
            const name = `whole_${seq}_${registered.length}/${blockFiles[i]}`
            await db!.registerFileBuffer(name, new Uint8Array(file.bytes))
            registered.push(name)
            files.push(name)
            bytes += file.sparse ? file.sparse.fetched : file.bytes.byteLength
            if (file.sparse) partial.push(blockFiles[i])
          }
        }
        try {
          await conn!.query(windowStagingSql(staging, files, cells, box, cz).create)
        } catch (error) {
          if (partial.length === 0) throw error
          console.warn('[duckdb-worker] a block held in part could not be read; reading it whole', partial, error)
          partial.forEach(refuseSparse)
          await conn!.query(`DROP TABLE IF EXISTS ${staging}`)
          return stage()
        }
        return true
      }
      try {
        if (!await stage()) return
        const { counts: countsSql } = windowStagingSql(staging, blockFiles.map(worldFileUrl), cells, box, cz)
        for (const row of (await conn.query(countsSql)).toArray()) counts.set(Number(row.cell), Number(row.n))
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
    // The chat's standing rule is asked of these rows before anything can
    // read them: a tile drawn from a cell the rule had not reached would be
    // cached without its matches. Nothing may await between this and the
    // cells becoming readable just below, or a rule published in the gap
    // would miss them (setTreeRule lists the held cells after its own awaits).
    await applyTreeRule(added.map((chunk) => chunk.table))
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
      await forgetTreeRule(evict)
      for (const name of evict) await conn.query(`DROP TABLE IF EXISTS ${name}`)
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
    postWindowLoads(-1)
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

/**
 * The engine's own files, asked for before the engine is started: on the
 * entry screen, while the viewer is still choosing (prefetchEngine). The wasm
 * is 34 MB (about 9 MB on the wire) and was the longest single wait between a
 * Near Me tap and a tree; fetched from the entry screen, a viewer who takes a
 * few seconds to tap finds most of it already here.
 *
 * `module` is the wasm as a blob URL, which the engine instantiates from
 * instead of fetching again: not left to the HTTP cache, because a second
 * request for a response still arriving is a second download in some
 * browsers. It costs the compile's overlap with the download, so the engine
 * takes this path only when a prefetch was started ahead of it.
 */
type EngineFiles = {
  bundle: Promise<duckdb.DuckDBBundle>
  module: Promise<string | null>
  /** Settles when the parquet extension is in the browser's cache, or could not be fetched. */
  extension: Promise<void>
}
let engineFiles: EngineFiles | null = null

function selectEngineBundle(): Promise<duckdb.DuckDBBundle> {
  return duckdb.selectBundle({
    mvp: { mainModule: DUCKDB_ASSET_URLS.mvp.mainModule, mainWorker: DUCKDB_ASSET_URLS.mvp.mainWorker },
    eh: { mainModule: DUCKDB_ASSET_URLS.eh.mainModule, mainWorker: DUCKDB_ASSET_URLS.eh.mainWorker },
  })
}

/** Ask for the parquet extension the engine will load, so its own request finds it cached. */
function fetchParquetExtension(bundle: Promise<duckdb.DuckDBBundle>): Promise<void> {
  return bundle.then(async ({ mainModule }) => {
    const url = parquetExtensionUrl(mainModule)
    if (url) await (await fetch(url)).arrayBuffer()
  }).catch(() => {})
}

function prefetchEngine(): void {
  if (engineFiles || db || initPromise) return
  const t0 = nowMs()
  const bundle = selectEngineBundle()
  const module = bundle.then(async ({ mainModule, mainWorker }) => {
    // The engine's worker script: fetched here only to be in the cache when the worker is made.
    if (mainWorker) void fetch(mainWorker).then((response) => response.arrayBuffer()).catch(() => {})
    const response = await fetch(mainModule)
    if (!response.ok) return null
    // As a blob, never an ArrayBuffer: 34 MB that need not sit in this worker's heap.
    const body = await response.blob()
    console.info('[Perf] duckdb-worker:engine-prefetch', { mb: Math.round(body.size / 1e5) / 10, ms: Math.round(nowMs() - t0) })
    // Streaming compilation takes only a response typed as wasm, whatever the host called it.
    return URL.createObjectURL(new Blob([body], { type: 'application/wasm' }))
  }).catch(() => null)
  engineFiles = { bundle, module, extension: fetchParquetExtension(bundle) }
}

/**
 * How long a prefetched engine may take to compile before it is given up on.
 * The bytes are already here, so this is compile time alone; a browser that
 * would not compile from a blob might never answer, and the engine's own
 * fetch is the way that is known to work.
 */
const PREFETCHED_ENGINE_DEADLINE_MS = 20_000

/** Start the engine from `module` (a prefetched blob) or the bundle's own URL. */
async function startEngine(bundle: duckdb.DuckDBBundle, module: string | null): Promise<duckdb.AsyncDuckDB> {
  const worker = new Worker(bundle.mainWorker!)
  const engine = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker)
  let deadline: ReturnType<typeof setTimeout> | undefined
  try {
    const started = engine.instantiate(module ?? bundle.mainModule, bundle.pthreadWorker)
    await (module
      ? Promise.race([started, new Promise<never>((_, reject) => {
        deadline = setTimeout(() => reject(new Error('the prefetched engine did not start in time')), PREFETCHED_ENGINE_DEADLINE_MS)
      })])
      : started)
  } catch (error) {
    worker.terminate()
    throw error
  } finally {
    clearTimeout(deadline)
  }
  return engine
}

async function doInit() {
  if (db) return
  const t0 = nowMs()
  console.info('[Perf] duckdb-worker:init:start', { enginePrefetched: !!engineFiles })
  postInitStage('engine')
  const early = earlyFiles = startEarlyFiles()

  const files = engineFiles
  engineFiles = null
  const bundle = await (files?.bundle ?? selectEngineBundle())
  // With no prefetch ahead of it the engine fetches its own wasm, compiling as it arrives.
  const extension = files?.extension ?? fetchParquetExtension(Promise.resolve(bundle))
  const module = await files?.module ?? null
  try {
    db = await startEngine(bundle, module).catch((e) => {
      if (!module) throw e
      console.warn('[duckdb-worker] the prefetched engine did not start; fetching it again', e)
      return startEngine(bundle, null)
    })
  } finally {
    if (module) URL.revokeObjectURL(module)
  }
  console.info('[Perf] duckdb-worker:engine', { ms: Math.round(nowMs() - t0), prefetched: !!module })
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
  postInitStage('guide')
  const speciesBytes = await early.species
  // The first parquet read loads the extension: from the cache, once the fetch that put it there is done.
  await extension
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
  // The genus decides a generic tree's leaf habit where it stands (lib/foliageSeason).
  const genusColumn = await parquetHasColumn(speciesFile, 'genus') ? 'genus' : 'CAST(NULL AS VARCHAR) AS genus'
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
        ${genusColumn},
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
  // The published genus shares by cell (lib/foliageSeason): a generic tree's leaf habit where it
  // stands. Until the file is published, or if it fails, a species keeps the enrichment's answer.
  const foliageBytes = await early.foliage
  try {
    if (!foliageBytes) throw new Error('foliage defaults not published')
    await db.registerFileBuffer('foliage_defaults.parquet', foliageBytes)
    const table = await conn.query(foliageDefaultsSql('foliage_defaults.parquet'))
    const cell = table.getChild('cell')!, genus = table.getChild('genus')!, deciduous = table.getChild('deciduous')!
    foliageDefaults = Array.from({ length: table.numRows }, (_, i) => ({
      cell: Number(cell.get(i)), genus: String(genus.get(i)), deciduous: !!deciduous.get(i),
    }))
  } catch (e) {
    console.warn('[Perf] duckdb-worker:foliage-defaults:failed', e)
    foliageDefaults = []
  } finally {
    if (foliageBytes) await db.dropFile('foliage_defaults.parquet').catch(() => {})
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

  if (ecoregionBytes) await db.dropFile('ecoregion_browser.parquet').catch(() => {})

  // The world's manifest and vocabulary. No tree is loaded until a view asks.
  postInitStage('world')
  await loadWorld(early)
  postColorMapUpdate()

  ready = true
  initError = null
  postInitStage('ready')

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
async function baseDistinctColors(tier: ShadeTier = 'overview'): Promise<string[]> {
  if (dbhColorsActive) return Object.values(DBH_COLORS)
  if (!conn || !speciesShadeActive) return Object.values(DEFAULT_CATEGORY_COLORS)
  const result = await conn.query(`SELECT DISTINCT unnest(shades) AS display_color FROM ${SHADE_TABLES[tier]}`)
  return [...new Set([...result.toArray().map((r: Record<string, unknown>) => String(r.display_color)), UNKNOWN_SHADE_COLOR])]
}

/**
 * The colours each renderer may draw: a chat override's first, so the dots'
 * one-byte palette never drops one, then the base scheme's.
 */
async function setDistinctColors(overrides: string[] = []) {
  activeDistinctColors = [...new Set([...overrides, ...await baseDistinctColors('overview')])]
  pointDistinctColors = [...new Set([...overrides, ...await baseDistinctColors('fine')])]
}

/** The colours the active override may draw: a rule's (met or named), or every colour in a published list. */
async function overrideColors(): Promise<string[]> {
  if (treeRule) return [...treeRule.colors]
  const result = await conn!.query(`SELECT DISTINCT display_color FROM ${COLOR_OVERRIDE_TABLE}`)
  return result.toArray().map((r: Record<string, unknown>) => String(r.display_color))
}

/** Drop any chat colour override; every colour read falls back to the base scheme. */
async function clearColorOverride() {
  if (!conn) return
  await conn.query(`DROP TABLE IF EXISTS ${COLOR_OVERRIDE_TABLE}`)
  colorOverrideActive = false
  await setDistinctColors()
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
  const t0 = nowMs()
  for (const table of Object.values(SHADE_TABLES)) await conn.query(`DROP TABLE IF EXISTS ${table}`)
  if (normalized) {
    // Exact hexes first, then each painted two ways (lib/foliageSeason): a
    // few merged colours, fitted to the shades, for the overview tiles, where
    // each colour is a heatmap and a circle layer; and the fine palette for
    // everything that draws any colour from one layer: the tree tiles, the
    // desktop's dots and the crowns.
    await conn.query(`CREATE OR REPLACE TEMP TABLE __species_shade_exact AS SELECT species, shades FROM (
${normalized}
) __shades`)
    const exact = (await conn.query(`SELECT DISTINCT unnest(shades) AS shade FROM __species_shade_exact`))
      .toArray().map((r: Record<string, unknown>) => String(r.shade))
    const tiers: Record<ShadeTier, Map<string, string>> = {
      overview: fittedShadeMap(exact, OVERVIEW_SHADE_COUNT),
      fine: fineShadeMap(exact),
    }
    // Each map answers only for a bare #rrggbb, so the literals need no escaping.
    const pairs = exact.flatMap((shade) => tiers.overview.has(shade)
      ? [`('${shade}', '${tiers.overview.get(shade)}', '${tiers.fine.get(shade)}')`]
      : [])
    await conn.query(`CREATE OR REPLACE TEMP TABLE __shade_snap (shade VARCHAR, overview VARCHAR, fine VARCHAR)`)
    if (pairs.length) await conn.query(`INSERT INTO __shade_snap VALUES ${pairs.join(', ')}`)
    for (const [tier, table] of Object.entries(SHADE_TABLES)) {
      await conn.query(`CREATE TEMP TABLE ${table} AS
SELECT species, list(${tier} ORDER BY i) AS shades
FROM (
  SELECT species, unnest(shades) AS shade, generate_subscripts(shades, 1) AS i FROM __species_shade_exact
) JOIN __shade_snap USING (shade)
GROUP BY species`)
    }
    await conn.query('DROP TABLE __species_shade_exact')
    await conn.query('DROP TABLE __shade_snap')
  }
  speciesShadeActive = normalized !== null
  speciesShadeSignature = signature
  if (colorOverrideActive) {
    await setDistinctColors(await overrideColors())
  } else {
    await setDistinctColors()
    activeColorLabelMap = baseScheme() === 'form' ? { ...DEFAULT_CATEGORY_LABELS } : {}
  }
  console.info('[Perf] duckdb-worker:species-shade', { on: speciesShadeActive, colors: activeDistinctColors.length, pointColors: pointDistinctColors.length, ms: Math.round(nowMs() - t0) })
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

  const shades = tileShadeTier(z)
  const color = colorSql('tf', colorOverrideActive, baseScheme(), { shades })
  const src = tileRowSource(baseQuery, color)
  // Overview cells group by colour only (see aggCacheSql in cityTables.ts).
  const aggColor = colorSql('tf', colorOverrideActive, baseScheme(), { aggregated: true, shades })
  const aggSrc = tileRowSource(baseQuery, aggColor)
  const aggDbh = aggregatedDbhSql(z)
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
  const visibleRange = !explicitRange && z >= DETAIL_TILE_ZOOM ? getVisibleTileRange(z) : null
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
    // The query returns rows and pointTiles.ts encodes them: one row per
    // overview cell or per tree, each with the tile it falls in.
    const sql = simplifyGridMeters > 0 ? `
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
)
SELECT xtile, ytile, display_color AS layer, gx AS x, gy AS y, ${aggDbh.select} TRY_CAST(point_count AS INTEGER) AS point_count
FROM agg
` : `
${src.prelude} pts AS (
  SELECT
    ${tileXExpr('tf', z)} AS xtile,
    ${tileYExpr('tf', z)} AS ytile,
    ${color.expr} AS layer,
    tf.x_3857 AS x,
    tf.y_3857 AS y,
    ${src.id} AS id,
    TRY_CAST(${src.dbh} AS DOUBLE) AS dbh,
    tf.notable
  FROM ${src.from}
  WHERE ${src.where}${tileXExpr('tf', z)} BETWEEN ${minX} AND ${maxX}
    AND ${tileYExpr('tf', z)} BETWEEN ${minY} AND ${maxY}
)
SELECT * FROM pts
`

    const t0 = nowMs()
    const result = await conn.query(sql)
    const queried = nowMs()
    // One tile per (xtile, ytile); each colour is its own source layer.
    const tiles = encodePointTiles(result, z, simplifyGridMeters > 0 ? aggregateProperties(z, simplifyGridMeters) : POINT_PROPERTIES, MVT_FALLBACK_LAYER, tileLayout(z))
    console.info('[Perf] duckdb-worker:neighborhood-batch', {
      z,
      mode: simplifyGridMeters > 0 ? `agg${simplifyGridMeters}` : 'points',
      tiles: (maxX - minX + 1) * (maxY - minY + 1),
      nonEmpty: tiles.size,
      rows: result.numRows,
      ms: Math.round(nowMs() - t0),
      encodeMs: Math.round(nowMs() - queried),
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
  const shades = tileShadeTier(z)
  const color = colorSql('tf', colorOverrideActive, baseScheme(), { shades })
  const src = tileRowSource(baseQuery, color)
  const aggColor = colorSql('tf', colorOverrideActive, baseScheme(), { aggregated: true, shades })
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
)
SELECT ${x} AS xtile, ${y} AS ytile, display_color AS layer, gx AS x, gy AS y, ${aggDbh.select} TRY_CAST(point_count AS INTEGER) AS point_count
FROM agg
` : `
${src.prelude} points AS (
  SELECT
    ${x} AS xtile,
    ${y} AS ytile,
    ${color.expr} AS layer,
    tf.x_3857 AS x,
    tf.y_3857 AS y,
    ${src.id} AS id,
    TRY_CAST(${src.dbh} AS DOUBLE) AS dbh,
    tf.notable
  FROM ${src.from}
  WHERE ${src.where}tf.x_3857 BETWEEN ${bounds.minX} AND ${bounds.maxX}
    AND tf.y_3857 BETWEEN ${bounds.minY} AND ${bounds.maxY}
  LIMIT 50000
)
SELECT * FROM points
`

    const result = await conn.query(sql)
    const properties = simplifyGridMeters > 0 ? aggregateProperties(z, simplifyGridMeters) : POINT_PROPERTIES
    const tile = encodePointTiles(result, z, properties, MVT_FALLBACK_LAYER, tileLayout(z)).get(`${x}/${y}`)?.mvt ?? new Uint8Array()
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
  visibleTileRangeByZoom.clear()
  prefetchedVisibleRangeSigByZoom.clear()
  prewarmDoneRevision = -1
  prewarmPromise = null
}

function setTileQuery(sql: string | null) {
  // The map republishes its query on every configuration pass, most of them
  // with the same SQL. Bumping the revision then throws away every tile and
  // batch for rows that did not change.
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
  // often: none, as before). Rebuilding the table would invalidate every
  // tile for the same rows.
  if ((normalized ?? '') === (publishedTreeIdFilterSql ?? '')) return
  // A published id list replaces a standing rule; they share the table.
  if (treeRule) await onTreeRuleQueue(() => dropTreeRule())

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
  // A standing rule owns the colour table; "no published colours" is not about it.
  if (treeRule) {
    if (!normalized) return
    await onTreeRuleQueue(() => dropTreeRule())
  }

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
      await setDistinctColors(distinctResult.toArray().map((r: Record<string, unknown>) => String(r.display_color)))
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

function onTreeRuleQueue<T>(work: () => Promise<T>): Promise<T> {
  const run = treeRuleQueue.then(work, work)
  treeRuleQueue = run.catch(() => {})
  return run
}

/** The rule's two tables, empty, and the colour reads joined to them if it colours. */
async function resetTreeRuleTables() {
  if (!conn || !treeRule) return
  for (const sql of treeRuleTablesSql(PUBLISHED_TREE_FILTER_TABLE)) await conn.query(sql)
  colorOverrideActive = treeRule.color
}

/** Stop applying the rule and drop its answers. Runs on the rule queue. */
async function dropTreeRule() {
  if (!conn || !treeRule) return
  treeRule = null
  publishedTreeIdFilterSignature = 'all'
  await conn.query(`DROP TABLE IF EXISTS ${PUBLISHED_TREE_FILTER_TABLE}`)
  await clearColorOverride()
}

/** Ask the rule of `tables` and keep its answers. Runs on the rule queue. */
async function askTreeRule(tables: string[]) {
  const rule = treeRule
  if (!conn || !rule || tables.length === 0) return
  const apply = treeRuleApplySql(rule.sql, scopeSql(tables), rule.color, PUBLISHED_TREE_FILTER_TABLE)
  try {
    await conn.query(apply.stage)
    for (const sql of apply.add) await conn.query(sql)
    if (!rule.color) return
    const met = (await conn.query(apply.colors)).toArray().map((r: Record<string, unknown>) => String(r.display_color))
    const fresh = met.filter((color) => !rule.colors.has(color))
    if (fresh.length === 0) return
    // A colour the rule computes rather than names (a species' own leaf
    // shade), first met here: every renderer's palette grows by it.
    for (const color of fresh) rule.colors.add(color)
    await setDistinctColors([...rule.colors])
    invalidateTileCaches('color-override')
    postColorMapUpdate()
  } finally {
    await conn.query(apply.drop).catch(() => {})
  }
}

/**
 * The standing rule, asked of cells that have just loaded and are not yet
 * readable. A rule that fails on these rows (a cast its first cells never
 * exercised) leaves them unmatched; the load is not the place to fail.
 */
function applyTreeRule(tables: string[]): Promise<void> {
  if (!treeRule || tables.length === 0) return Promise.resolve()
  return onTreeRuleQueue(() => askTreeRule(tables)).catch((error) => {
    console.warn('[duckdb-worker] the chat rule failed on newly loaded cells', error)
  })
}

/** Drop the rule's answers for cells about to be dropped, so its tables stay the size of what is held. */
function forgetTreeRule(tables: string[]): Promise<void> {
  if (!treeRule || tables.length === 0) return Promise.resolve()
  return onTreeRuleQueue(async () => {
    if (!conn || !treeRule) return
    for (const sql of treeRuleForgetSql(scopeSql(tables), PUBLISHED_TREE_FILTER_TABLE)) await conn.query(sql)
  }).catch((error) => {
    console.warn('[duckdb-worker] could not drop the chat rule answers for evicted cells', error)
  })
}

/**
 * The species dimension a rule joins is the file `species_enrichment` was
 * loaded from: read from the table where the rule's columns are in it, so a
 * cell's load does not wait on a fetch. A column only the file has (family,
 * soil preferences) keeps the remote read.
 */
async function localTreeRuleSql(sql: string): Promise<string> {
  const remote = `read_parquet('${REMOTE_SPECIES_PARQUET_URL}')`
  if (!conn || !sql.includes(remote)) return sql
  const local = sql.split(remote).join('species_enrichment')
  try {
    await conn.query(`SELECT * FROM (${treeRuleRowsSql(local, WINDOW_SHAPE_TABLE, false)}) __probe LIMIT 0`)
    return local
  } catch {
    return sql
  }
}

/**
 * Publish the chat's standing rule, or with null return to every tree. The
 * rule is asked of the cells held now and of each cell that loads after, so
 * what it filters and colours follows the camera.
 */
async function setTreeRule(rule: { sql: string; color: boolean } | null) {
  await ensureInit()
  if (!conn) return
  const text = rule?.sql.trim() || null
  const signature = text ? `rule-${hashText(normalizeSql(text))}-${rule!.color ? 'c' : 'f'}` : null
  // Every configuration pass republishes it, usually unchanged.
  if (signature === (treeRule?.signature ?? null)) return
  await onTreeRuleQueue(async () => {
    if (!text || !signature) return dropTreeRule()
    const color = !!rule!.color
    const sql = await localTreeRuleSql(text)
    // A rule replaces a published id list: the tables are the same two.
    publishedTreeIdFilterSql = null
    treeRule = { sql, color, signature, colors: new Set(color ? hexLiterals(text) : []) }
    try {
      await resetTreeRuleTables()
      publishedTreeIdFilterSignature = signature
      colorMapSignature = color ? `color-${signature}` : 'default'
      await setDistinctColors([...treeRule.colors])
      if (!color) activeColorLabelMap = baseScheme() === 'form' ? { ...DEFAULT_CATEGORY_LABELS } : {}
      // Listed after the awaits above: a load that asked the old rule (or
      // none) of its cells has made them readable by now, and is in here.
      await askTreeRule(world ? [...world.chunks.keys()] : [])
    } catch (error) {
      await dropTreeRule()
      throw error
    }
  })
  invalidateTileCaches('tree-rule')
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
    await setDistinctColors(await overrideColors())
  } else {
    await setDistinctColors()
    activeColorLabelMap = baseScheme() === 'form' ? { ...DEFAULT_CATEGORY_LABELS } : {}
  }
  invalidateTileCaches('color-scheme')
  postColorMapUpdate()
}

function landmarkUrlFor(city: string): string {
  return cityLandmarkParquetUrl(city) ?? REMOTE_LANDMARKS_PARQUET_URL
}

/** A city's landmark file, whole, or null when it cannot be fetched; asked for once however often it is named. */
const landmarkFiles = new Map<string, Promise<Uint8Array | null>>()
function landmarkFile(city: string): Promise<Uint8Array | null> {
  let file = landmarkFiles.get(city)
  if (!file) landmarkFiles.set(city, file = fetchPublishedFile(landmarkUrlFor(city)))
  return file
}

/** The city `trees_published` reads; undefined until a read has needed it since the bookmark moved. */
let publishedViewCity: string | null | undefined

/**
 * Point `trees_published` at the bookmark's projection, if `sql` reads it and
 * it does not already. Binding the view reads the file's footer: eight
 * requests and 0.6 MB for Boston, a second and a half on a phone's link. That
 * used to happen for every bookmark, ahead of the first tree; two reads use
 * the view (a deep-linked tree's position, the check-in's species count), and
 * now only they wait for it.
 */
async function publishedView(sql: string | null): Promise<void> {
  if (!conn || !sql?.includes(PUBLISHED_TREES_VIEW) || publishedViewCity === contextCity) return
  const city = contextCity
  try {
    await conn.query(publishedTreesViewSql(city ? cityBrowserParquetUrl(city) : null))
  } catch (e) {
    // A city whose projection is not out yet: its reads find no rows.
    console.warn('[duckdb-worker] city projection unavailable', city, e)
    await conn.query(publishedTreesViewSql(null))
  }
  publishedViewCity = city
}

/**
 * Make `city` the bookmark in context: its landmarks, its default query, and
 * the projection `trees_published` will read (publishedView). It loads no
 * trees and drops none: what is held follows the camera, so moving the
 * bookmark as the camera crosses from one inventory into the next costs one
 * small file and redraws nothing.
 */
async function setCityContext(city: string) {
  await ensureInit()
  if (!conn) return
  const startedAt = nowMs()
  // A layout that asks for another cell size (a phone's window turned on or off) starts the world over.
  if (world && world.cellZoom !== cellZoom()) await resetWorld('the window\'s cell size changed')

  if (contextCity !== city) {
    // The file whole, asked for when the engine was (landmarkFile): a few
    // kilobytes that DuckDB's own read of the bucket made five requests in a
    // row for, each a round trip the first tree waited behind.
    const bytes = await landmarkFile(city)
    landmarkFiles.delete(city)
    const name = bytes ? `landmarks_${city}.parquet` : landmarkUrlFor(city)
    try {
      if (bytes) await db!.registerFileBuffer(name, bytes)
      await conn.query(`
        CREATE OR REPLACE TABLE landmarks AS
        SELECT landmark_id, city, name, latitude, longitude
        FROM read_parquet('${name}')
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
      `)
    } catch (e) {
      console.warn('[duckdb-worker] landmarks-load:failed', e)
    } finally {
      if (bytes) await db!.dropFile(name).catch(() => {})
    }
    contextCity = city
    // `trees_published` follows on the first read that names it (publishedView).
    publishedViewCity = undefined
    await publishedView(tileQuerySql)
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
  if (!conn || !shouldUseZoomBatch(z) && z < DETAIL_TILE_ZOOM) return 'skipped'

  // An overview zoom's tile requests batch fixed 4x4 blocks, and each block's
  // aggregate scans the city: the intro's footprint at z14 spans six of them
  // (7.3 s on Boston). The range is warmed as one aggregate instead, and it
  // is passed in, never stored, because the main thread's range reports for
  // these zooms drive nothing else. The intro holds its camera on this.
  if (z < DETAIL_TILE_ZOOM) {
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

  // The visible range only: the intro gates its motion on this.
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
  if (!conn) return

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
    // The tiles around the camera at its zoom, the one above it and the first
    // detail zoom, so a first zoom out or in draws from the cache.
    const focusZoom = Math.max(DETAIL_TILE_ZOOM, Math.min(19, Math.floor(activeViewportZoom)))
    if (viewport) {
      for (const z of new Set([focusZoom, Math.max(DETAIL_TILE_ZOOM, focusZoom - 1), DETAIL_TILE_ZOOM])) {
        const c = lngLatToTile(viewport.lng, viewport.lat, z)
        await ensureNeighborhoodBatchTiles(z, c.x, c.y, baseQuery)
      }
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
  await publishedView(sql)
  sql = sql.replace(NOTABLE_TOKEN_PATTERN, (_, city: string) => {
    const file = world?.manifest.notable.get(city)
    return notableRelationSql(file ? worldFileUrl(file) : null)
  })

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
  // A crown is painted per vertex, so it takes the fine shades on either
  // renderer, as do the desktop's dots and a phone's tree tiles (tileShadeTier).
  const color = colorSql('tf', colorOverrideActive, baseScheme(), { shades: 'fine' })
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
      return conn!.query(crownQuery(camera, filter, color, { from: scope, trees: filter ? treesSelectSql(scope) : null, speciesCode: true }))
    })
  } else {
    await viewIsCurrent()
    result = await conn.query(crownQuery(camera, filter, color, { from: 'trees_fast', speciesCode: true }))
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
  const flags = (name: string) => {
    const col = result.getChild(name)!, out = new Uint8Array(n)
    for (let i = 0; i < n; i++) out[i] = col.get(i) ? 1 : 0
    return out
  }
  const columns: TreeCrownColumns = {
    id: strings('id'), lng: numbers('lng'), lat: numbers('lat'), width: numbers('width'), measured: flags('measured'),
    color: strings('color'), category: strings('category'), dbh: numbers('dbh'), height: numbers('height'), notable: flags('notable'),
  }
  // Each tree's leaf-off months, from its species code and where it stands (lib/foliageSeason).
  const lookup = foliage, code = result.getChild('species_code')!, leafOff = new Uint16Array(n)
  for (let i = 0; i < n; i++) leafOff[i] = lookup.leafOff(Number(code.get(i) ?? -1), columns.lat[i], columns.lng[i])
  columns.leafOff = leafOff
  return columns
}

// --- Point chunks (the desktop's TreeDotsLayer) ---

/** What the drawn rows are: the world's build, and the query and filter over it. Not the bookmark. */
function pointRowsKey(): string {
  return `${world?.manifest.id}|${world?.generation}|${tileQueryRevision}|${publishedTreeIdFilterSignature}`
}

function pointColorKey(): string {
  return `${colorMapSignature}|${colorSchemeSignature()}|${pointDistinctColors.join(',')}`
}

/** Copy an Arrow column into its own buffer, so it can be transferred. */
function columnArray<T extends Float32Array | Uint8Array | Uint16Array | Uint32Array | Int32Array>(table: { getChild(name: string): { toArray(): unknown } | null }, name: string, Ctor: { new(source: ArrayLike<number>): T }): T {
  const column = table.getChild(name)
  return new Ctor((column ? column.toArray() : []) as ArrayLike<number>)
}

/** The rows source and colour a point read over `tables` uses. */
function pointSource(tables: string[]) {
  const color = colorSql('tf', colorOverrideActive, baseScheme(), { shades: 'fine' })
  return { src: tileRowSource(effectiveBaseQuery(tileQuerySql), color, scopeSql(tables)), color, palette: pointPalette(pointDistinctColors) }
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
    const table = await conn!.query(pointChunksSql(src, keys, color.expr, palette, true))
    if (pointRowsKey() !== rowsKey || pointColorKey() !== colorKey) return null
    const { offsets, columns } = bucketByChunk(columnArray(table, 'chunk', Uint32Array), keys, {
      x: columnArray(table, 'x', Float32Array),
      y: columnArray(table, 'y', Float32Array),
      dbh: columnArray(table, 'dbh', Uint8Array),
      crown: columnArray(table, 'crown', Uint8Array),
      color: columnArray(table, 'color', Uint8Array),
      rowid: columnArray(table, 'rowid', Uint32Array),
      notable: columnArray(table, 'notable', Uint8Array),
      species: columnArray(table, 'species_code', Int32Array),
    })
    const { notable: flags, species, ...drawn } = columns
    const leafOff = chunkLeafOff(foliage, keys, offsets, drawn.x, drawn.y, species)
    const grids = gridOrderChunks(offsets, drawn.x, drawn.y, { dbh: drawn.dbh, crown: drawn.crown, color: drawn.color, rowid: drawn.rowid, leafOff, notable: flags })
    const notable = flaggedRows(flags)
    console.info('[Perf] duckdb-worker:point-chunks', { chunks: keys.length, rows: table.numRows, notable: notable.length, ms: Math.round(nowMs() - t0) })
    return { rowsKey, colorKey, palette, keys, offsets, ...drawn, leafOff, notable, grids }
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
  setTreeRule: { params: { rule: { sql: string; color: boolean } | null }; result: void }
  setSpeciesShadeSql: { params: { sql: string | null }; result: void }
  setDbhColors: { params: { enabled: boolean }; result: void }
  setCityContext: { params: { city: string; window?: WindowPolicy | null }; result: void }
  setViewportZoom: { params: { zoom: number }; result: void }
  setViewportCenter: { params: { lng: number; lat: number }; result: void }
  ensureWindowAt: { params: { lng: number; lat: number }; result: void }
  prefetchWindowAt: { params: { lng: number; lat: number; city?: string }; result: void }
  prefetchEngine: { params: Record<string, never>; result: void }
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
          result ? [result.offsets.buffer, result.x.buffer, result.y.buffer, result.dbh.buffer, result.crown.buffer, result.color.buffer, result.rowid.buffer, result.notable.buffer, result.leafOff.buffer,
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
        const { window, city } = msg.params as WorkerMethodMap['ensureInit']['params']
        if (window !== undefined) windowPolicy = window ?? DESKTOP_POLICY
        // The bookmark's landmarks download beside the engine.
        if (city) void landmarkFile(city)
        await ensureInit()
        send({ type: 'response', requestId: msg.requestId, ok: true, result: { ready, initError } })
        break
      }
      case 'setTileQuery': {
        const { sql } = msg.params as WorkerMethodMap['setTileQuery']['params']
        await publishedView(sql)
        setTileQuery(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setPublishedTreeIdFilterSql': {
        const { sql } = msg.params as WorkerMethodMap['setPublishedTreeIdFilterSql']['params']
        await publishedView(sql)
        await setPublishedTreeIdFilterSql(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setTreeRule': {
        const { rule } = msg.params as WorkerMethodMap['setTreeRule']['params']
        await setTreeRule(rule)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setColorOverrideSql': {
        const { sql } = msg.params as WorkerMethodMap['setColorOverrideSql']['params']
        await publishedView(sql)
        await setColorOverrideSql(sql)
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'setSpeciesShadeSql': {
        const { sql } = msg.params as WorkerMethodMap['setSpeciesShadeSql']['params']
        await publishedView(sql)
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
      case 'prefetchEngine': {
        prefetchEngine()
        send({ type: 'response', requestId: msg.requestId, ok: true, result: undefined })
        break
      }
      case 'prefetchWindowAt': {
        const { lng, lat, city } = msg.params as WorkerMethodMap['prefetchWindowAt']['params']
        prefetchBlockAt(lng, lat)
        if (city) void landmarkFile(city)
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
