/**
 * Which part of a city a phone holds in memory.
 *
 * A phone cannot hold a dense city whole: Boston with its predicted trees is
 * 1.57M rows, and the load alone takes the tab past Safari's ~1 GB budget
 * (docs/MOBILE_LOAD_BUDGET.md). A phone instead holds the trees of the
 * ground it is looking at, in cells: web-mercator tiles at CELL_ZOOM, about
 * 1.8 km across at Boston's latitude. The map cannot zoom out past a floor,
 * so the ground a view can show is bounded, and the cells a tile request
 * covers are loaded before the tile is drawn. Once more rows are held than
 * the budget allows, the cells farthest from the camera are dropped.
 *
 * Every tree belongs to exactly one cell (its own z20 tile index, shifted),
 * so cells loaded at different times never overlap. Nothing here touches
 * DuckDB; the worker owns the tables and asks this module what to do.
 */

/** The zoom whose tiles are the load and eviction unit. */
export const CELL_ZOOM = 14

/** What the main thread decides for a phone; null means hold the whole city. */
export type WindowPolicy = {
  /** The map's zoom-out floor. No tile below it is drawn from the window. */
  floorZoom: number
  /** Rows held at most before the farthest cells are dropped. */
  budgetRows: number
  /**
   * How far from the camera, in cells, a tile request may pull trees in.
   * Covers the screen at the floor zoom with a margin; a request beyond it
   * (a tile MapLibre asks for mid-flight) is answered empty and transient.
   */
  radiusCells: number
}

export type Cell = { x: number; y: number }

/** One integer per cell, the same expression the load's SQL filters on. */
export function cellKey(x: number, y: number): number {
  return x * 2 ** CELL_ZOOM + y
}

export function cellFromKey(key: number): Cell {
  const n = 2 ** CELL_ZOOM
  return { x: Math.floor(key / n), y: key % n }
}

/**
 * The cells a tile at (z, x, y) draws from. A tile at or above CELL_ZOOM
 * sits inside one cell; a coarser tile spans 4^(CELL_ZOOM - z) of them.
 */
export function cellsForTile(z: number, x: number, y: number): number[] {
  if (z >= CELL_ZOOM) {
    const shift = z - CELL_ZOOM
    return [cellKey(x >> shift, y >> shift)]
  }
  const span = 2 ** (CELL_ZOOM - z)
  const keys: number[] = []
  for (let cx = x * span; cx < (x + 1) * span; cx += 1) {
    for (let cy = y * span; cy < (y + 1) * span; cy += 1) keys.push(cellKey(cx, cy))
  }
  return keys
}

/** The cell a longitude and latitude fall in. */
export function cellAt(lng: number, lat: number): Cell {
  const n = 2 ** CELL_ZOOM
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat))
  const rad = (clamped * Math.PI) / 180
  return {
    x: Math.max(0, Math.min(n - 1, Math.floor(((lng + 180) / 360) * n))),
    y: Math.max(0, Math.min(n - 1, Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n))),
  }
}

/** Chebyshev distance between two cells, in cells. */
export function cellDistance(a: Cell, b: Cell): number {
  return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y))
}

/** The longitude and latitude box of a set of cells, for the scan's pushdown. */
export function cellsBounds(keys: number[]): { west: number; east: number; south: number; north: number } {
  const n = 2 ** CELL_ZOOM
  const lng = (x: number) => (x / n) * 360 - 180
  const lat = (y: number) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (const key of keys) {
    const { x, y } = cellFromKey(key)
    minX = Math.min(minX, x); maxX = Math.max(maxX, x)
    minY = Math.min(minY, y); maxY = Math.max(maxY, y)
  }
  return { west: lng(minX), east: lng(maxX + 1), north: lat(minY), south: lat(maxY + 1) }
}

/** A loaded cell's table and its row count. */
export type Chunk = { table: string; cells: number[]; rows: number }

/**
 * The tables to drop so the held rows fit the budget: farthest from the
 * camera first. A table with a cell inside `keepCells` of the camera is
 * never dropped, so a budget too small for the ground on screen holds more
 * than the budget rather than dropping what is being drawn. The caller
 * leaves the load that just landed out of `chunks` and takes its rows off
 * the budget.
 */
export function chunksToEvict(chunks: Chunk[], camera: Cell | null, budgetRows: number, keepCells: number): string[] {
  let held = chunks.reduce((sum, chunk) => sum + chunk.rows, 0)
  if (held <= budgetRows || !camera) return []
  const nearest = (chunk: Chunk) => Math.min(...chunk.cells.map((key) => cellDistance(cellFromKey(key), camera)))
  const candidates = chunks
    .filter((chunk) => nearest(chunk) > keepCells)
    .sort((a, b) => nearest(b) - nearest(a) || b.rows - a.rows)
  const evict: string[] = []
  for (const chunk of candidates) {
    if (held <= budgetRows) break
    evict.push(chunk.table)
    held -= chunk.rows
  }
  return evict
}
