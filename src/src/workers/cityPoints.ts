// The desktop map's trees as chunks of typed arrays, read on demand
// (composables/treeDotsLayer.ts draws them).
//
// The chunk grid is global: Web Mercator tiles at CHUNK_ZOOM, the same
// grid at every latitude and longitude, so nothing about it depends on
// which city's rows happen to be loaded. A chunk is the same size on screen
// anywhere (its ground size scales with cos(latitude): 7.2 km across in
// Boston, 9.8 km at the equator), which is what bounds the chunks a view
// draws. Each row's chunk is its stored z20 tile index shifted, so a chunk
// is exactly 4 x 4 of the phone's window cells (workers/cityWindow.ts) and
// a Hilbert-sorted projection keeps a chunk's rows in few row groups.

import { tileIndexSql, WEB_MERCATOR_MAX, WEB_MERCATOR_WORLD } from './cityTables'
import { DRAW_CHUNK_MAX_CELLS, DRAW_CHUNK_MIN_M, buildPickGrid, type PickGrid } from '../lib/treeDots'

/** A chunk's draw grid without its order: the points are already stored in it. */
export type ChunkGrid = Pick<PickGrid, 'cell' | 'cols' | 'rows' | 'start'>

export const CHUNK_ZOOM = 12
const CHUNKS_PER_AXIS = 2 ** CHUNK_ZOOM
/** A chunk's side in EPSG:3857 metres. */
export const CHUNK_SIZE_M = WEB_MERCATOR_WORLD / CHUNKS_PER_AXIS

/**
 * The heat pyramid's cell sizes within a chunk, finest first: the chunk's
 * side halved nine times (19 m) up to the chunk itself, so every level's
 * cells tile the chunk exactly and never straddle two.
 */
export const CHUNK_LOD_CELLS: readonly number[] = Array.from({ length: 10 }, (_, k) => CHUNK_SIZE_M / 2 ** (9 - k))

/** Index 0 of every palette: a colour the palette does not list. */
export const POINT_FALLBACK_COLOR = '#66BB6A'
/** Palette entries a one-byte index can address after the fallback. */
export const MAX_POINT_PALETTE = 255

/** One integer per chunk, the same expression the SQL groups on. */
export function chunkKey(x: number, y: number): number {
  return x * CHUNKS_PER_AXIS + y
}

export function chunkFromKey(key: number): { x: number; y: number } {
  return { x: Math.floor(key / CHUNKS_PER_AXIS), y: key % CHUNKS_PER_AXIS }
}

/** A chunk's south-west corner in EPSG:3857 metres: the origin its positions are relative to. */
export function chunkOrigin(key: number): [number, number] {
  const { x, y } = chunkFromKey(key)
  return [x * CHUNK_SIZE_M - WEB_MERCATOR_MAX, WEB_MERCATOR_MAX - (y + 1) * CHUNK_SIZE_M]
}

/** The chunk keys a box in EPSG:3857 metres touches, nearest its centre first. */
export function chunksInBox(minX: number, minY: number, maxX: number, maxY: number): number[] {
  const clampX = (v: number) => Math.max(0, Math.min(CHUNKS_PER_AXIS - 1, v))
  const x0 = clampX(Math.floor((minX + WEB_MERCATOR_MAX) / CHUNK_SIZE_M))
  const x1 = clampX(Math.floor((maxX + WEB_MERCATOR_MAX) / CHUNK_SIZE_M))
  const y0 = clampX(Math.floor((WEB_MERCATOR_MAX - maxY) / CHUNK_SIZE_M))
  const y1 = clampX(Math.floor((WEB_MERCATOR_MAX - minY) / CHUNK_SIZE_M))
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2
  const keys: { key: number; d: number }[] = []
  for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) keys.push({ key: chunkKey(x, y), d: (x - cx) ** 2 + (y - cy) ** 2 })
  return keys.sort((a, b) => a.d - b.d).map((k) => k.key)
}

/** The non-empty chunks of the current rows, with their tree counts. */
export interface PointKeys {
  /** The row set's identity: a colour change keeps it, a query, filter or city change does not. */
  rowsKey: string
  /** The colouring's identity: scheme, override and palette. */
  colorKey: string
}

export interface PointChunkDirectory extends PointKeys {
  keys: number[]
  counts: number[]
  /** The largest rowid in the rows, to size the rowid index a colour refresh scatters by. */
  maxRowid: number
}

/**
 * Chunks of rows, back to back: chunk `keys[i]` is rows offsets[i] to
 * offsets[i + 1]. Positions are float32 metres from the chunk's origin.
 */
export interface PointChunkBatch {
  rowsKey: string
  colorKey: string
  palette: string[]
  keys: number[]
  offsets: Uint32Array
  x: Float32Array
  y: Float32Array
  /** Trunk diameter, whole inches, 1 to 255 (3 where unknown). */
  dbh: Uint8Array
  /** Index into `palette`. */
  color: Uint8Array
  /** trees_fast rowid: the tree card's lookup, and how a colour refresh finds its tree. */
  rowid: Uint32Array
  /**
   * Each chunk's 512 m draw grid. A chunk's rows are stored in its grid's
   * cell order, so a frame draws the ranges of the cells in view and a pick
   * scans a few cells.
   */
  grids: ChunkGrid[]
}

/** A colour refresh for some chunks of `rowsKey`, rows in any order within a chunk. */
export interface PointChunkColors {
  rowsKey: string
  colorKey: string
  palette: string[]
  keys: number[]
  offsets: Uint32Array
  color: Uint8Array
  rowid: Uint32Array
}

const sqlString = (value: string) => `'${value.replace(/'/g, "''")}'`
/** A literal DuckDB reads as a DOUBLE: a long decimal literal is otherwise a DECIMAL, and overflows. */
const sqlDouble = (value: number) => `CAST(${value} AS DOUBLE)`

/** Fallback first, then the scheme's colours, at most one byte's worth. */
export function pointPalette(colors: readonly string[]): string[] {
  const unique = [...new Set(colors.filter((c) => c && c !== POINT_FALLBACK_COLOR))]
  return [POINT_FALLBACK_COLOR, ...unique.slice(0, MAX_POINT_PALETTE)]
}

/** A colour expression's palette index; anything unlisted is 0, the fallback. */
export function paletteIndexSql(colorExpr: string, palette: readonly string[]): string {
  const listed = palette.slice(1)
  if (!listed.length) return 'CAST(0 AS UTINYINT)'
  return `CAST(COALESCE(list_position([${listed.map(sqlString).join(', ')}], ${colorExpr}), 0) AS UTINYINT)`
}

export interface PointRowSource {
  prelude: string
  from: string
  where: string
  dbh: string
}

/** A row's chunk key, from its stored z20 tile index. */
export function chunkKeySql(alias = 'tf'): string {
  return `(${tileIndexSql(alias, 'x', CHUNK_ZOOM)} * ${CHUNKS_PER_AXIS} + ${tileIndexSql(alias, 'y', CHUNK_ZOOM)})`
}

/** The chunks' bounding box, a metre wider: a filter the table's zone maps can prune row groups on. */
function chunkBoxSql(keys: readonly number[], alias = 'tf'): string {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const key of keys) {
    const [x, y] = chunkOrigin(key)
    minX = Math.min(minX, x); minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + CHUNK_SIZE_M); maxY = Math.max(maxY, y + CHUNK_SIZE_M)
  }
  return `${alias}.x_3857 BETWEEN ${sqlDouble(minX - 1)} AND ${sqlDouble(maxX + 1)} AND ${alias}.y_3857 BETWEEN ${sqlDouble(minY - 1)} AND ${sqlDouble(maxY + 1)}`
}

function chunkFilterSql(keys: readonly number[]): string {
  return `${chunkBoxSql(keys)} AND ${chunkKeySql()} IN (${keys.join(', ')})`
}

/** Tree counts per non-empty chunk of the current rows. */
export function pointChunkDirectorySql(src: PointRowSource): string {
  return `${src.prelude} __unused AS (SELECT 1)
SELECT ${chunkKeySql()} AS chunk, COUNT(*) AS n, MAX(tf.rowid) AS max_rowid
FROM ${src.from}
WHERE ${src.where}TRUE
GROUP BY 1`
}

/** Every drawn tree of the chunks: its chunk, rowid, position from the chunk's origin, size and colour. */
export function pointChunksSql(src: PointRowSource, keys: readonly number[], colorExpr: string, palette: readonly string[]): string {
  const chunk = chunkKeySql()
  const cx = `${tileIndexSql('tf', 'x', CHUNK_ZOOM)}`
  const cy = `${tileIndexSql('tf', 'y', CHUNK_ZOOM)}`
  return `${src.prelude} __unused AS (SELECT 1)
SELECT
  ${chunk} AS chunk,
  CAST(tf.rowid AS UINTEGER) AS rowid,
  CAST(tf.x_3857 - (${cx} * ${sqlDouble(CHUNK_SIZE_M)} - ${sqlDouble(WEB_MERCATOR_MAX)}) AS FLOAT) AS x,
  CAST(tf.y_3857 - (${sqlDouble(WEB_MERCATOR_MAX)} - (${cy} + 1) * ${sqlDouble(CHUNK_SIZE_M)}) AS FLOAT) AS y,
  CAST(LEAST(255, GREATEST(1, round(${src.dbh}))) AS UTINYINT) AS dbh,
  ${paletteIndexSql(colorExpr, palette)} AS color
FROM ${src.from}
WHERE ${src.where}${chunkFilterSql(keys)}`
}

/** The same chunks' colours alone. */
export function pointChunkColorsSql(src: PointRowSource, keys: readonly number[], colorExpr: string, palette: readonly string[]): string {
  return `${src.prelude} __unused AS (SELECT 1)
SELECT ${chunkKeySql()} AS chunk, CAST(tf.rowid AS UINTEGER) AS rowid, ${paletteIndexSql(colorExpr, palette)} AS color
FROM ${src.from}
WHERE ${src.where}${chunkFilterSql(keys)}`
}

/**
 * Group rows by chunk without a sort: a counting pass, then a scatter.
 * Returns the order of `keys` (as given, empty chunks included), the
 * offsets of each chunk's rows, and each column permuted to match.
 */
export function bucketByChunk<T extends Record<string, Float32Array | Uint8Array | Uint32Array>>(
  chunk: ArrayLike<number>, keys: readonly number[], columns: T,
): { offsets: Uint32Array; columns: T } {
  const slot = new Map<number, number>()
  keys.forEach((k, i) => slot.set(k, i))
  const n = chunk.length
  const offsets = new Uint32Array(keys.length + 1)
  const slots = new Int32Array(n)
  for (let i = 0; i < n; i++) {
    const s = slot.get(Number(chunk[i]))
    slots[i] = s ?? -1
    if (s !== undefined) offsets[s + 1]++
  }
  for (let s = 0; s < keys.length; s++) offsets[s + 1] += offsets[s]
  const fill = offsets.slice(0, keys.length)
  const order = new Uint32Array(offsets[keys.length])
  for (let i = 0; i < n; i++) if (slots[i] >= 0) order[fill[slots[i]]++] = i
  const out = {} as Record<string, Float32Array | Uint8Array | Uint32Array>
  for (const [name, values] of Object.entries(columns)) {
    const Ctor = values.constructor as { new(length: number): Float32Array | Uint8Array | Uint32Array }
    const permuted = new Ctor(order.length)
    for (let k = 0; k < order.length; k++) permuted[k] = values[order[k]]
    out[name] = permuted
  }
  return { offsets, columns: out as T }
}

/**
 * Reorder each chunk's rows (offsets[i] to offsets[i + 1]) into its draw
 * grid's cell order, in place, and return the grids. Runs in the DuckDB
 * worker, so the page only uploads what arrives.
 */
export function gridOrderChunks<T extends Record<string, Float32Array | Uint8Array | Uint32Array>>(
  offsets: Uint32Array, x: Float32Array, y: Float32Array, columns: T,
): ChunkGrid[] {
  const grids: ChunkGrid[] = []
  const all: (Float32Array | Uint8Array | Uint32Array)[] = [x, y, ...Object.values(columns)]
  for (let s = 0; s + 1 < offsets.length; s++) {
    const from = offsets[s], to = offsets[s + 1]
    const grid = buildPickGrid(x.subarray(from, to), y.subarray(from, to), to - from, DRAW_CHUNK_MAX_CELLS, DRAW_CHUNK_MIN_M)
    for (const values of all) {
      const slice = values.slice(from, to)
      for (let k = 0; k < grid.order.length; k++) values[from + k] = slice[grid.order[k]]
    }
    grids.push({ cell: grid.cell, cols: grid.cols, rows: grid.rows, start: grid.start })
  }
  return grids
}
