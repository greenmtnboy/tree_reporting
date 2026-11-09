/**
 * Which part of the world the worker holds in memory.
 *
 * Nothing holds a city: the worker loads the trees of the ground near the
 * camera from the world's block files (worldBlocks.ts), in cells, and drops
 * the farthest once it holds more rows than its budget. What follows is the
 * phone's case, the tight one; a desktop runs the same rules with a block as
 * its cell and a budget of millions.
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

/** The zoom whose tiles are a phone's load and eviction unit. */
export const CELL_ZOOM = 14

/**
 * What the main thread decides for its layout. A phone holds z14 cells under
 * a small budget and a zoom floor; a desktop holds whole world blocks (z12,
 * `cellZoom`), since its resident layer reads them a block at a time.
 */
export type WindowPolicy = {
  /** The zoom of the cells held; CELL_ZOOM when absent. Never coarser than a world block. */
  cellZoom?: number
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
export function cellKey(x: number, y: number, cellZoom = CELL_ZOOM): number {
  return x * 2 ** cellZoom + y
}

export function cellFromKey(key: number, cellZoom = CELL_ZOOM): Cell {
  const n = 2 ** cellZoom
  return { x: Math.floor(key / n), y: key % n }
}

/**
 * The cells a tile at (z, x, y) draws from. A tile at or above CELL_ZOOM
 * sits inside one cell; a coarser tile spans 4^(CELL_ZOOM - z) of them.
 */
export function cellsForTile(z: number, x: number, y: number, cellZoom = CELL_ZOOM): number[] {
  if (z >= cellZoom) {
    const shift = z - cellZoom
    return [cellKey(x >> shift, y >> shift, cellZoom)]
  }
  const span = 2 ** (cellZoom - z)
  const keys: number[] = []
  for (let cx = x * span; cx < (x + 1) * span; cx += 1) {
    for (let cy = y * span; cy < (y + 1) * span; cy += 1) keys.push(cellKey(cx, cy, cellZoom))
  }
  return keys
}

/** The cell at `cellZoom` that holds a cell of a finer (or the same) zoom. */
export function parentCell(key: number, fromZoom: number, cellZoom: number): number {
  const { x, y } = cellFromKey(key, fromZoom)
  const shift = fromZoom - cellZoom
  return cellKey(x >> shift, y >> shift, cellZoom)
}

/** The cells a longitude and latitude box touches, capped at `max` (null past it: too wide to enumerate). */
export function cellsInBox(
  box: { west: number; east: number; south: number; north: number }, cellZoom = CELL_ZOOM, max = 256,
): number[] | null {
  const nw = cellAt(Math.max(-180, box.west), box.north, cellZoom)
  const se = cellAt(Math.min(180, box.east), box.south, cellZoom)
  if (se.x < nw.x || se.y < nw.y || (se.x - nw.x + 1) * (se.y - nw.y + 1) > max) return null
  const keys: number[] = []
  for (let x = nw.x; x <= se.x; x += 1) for (let y = nw.y; y <= se.y; y += 1) keys.push(cellKey(x, y, cellZoom))
  return keys
}

/** The cell a longitude and latitude fall in. */
export function cellAt(lng: number, lat: number, cellZoom = CELL_ZOOM): Cell {
  const n = 2 ** cellZoom
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
export function cellsBounds(keys: number[], cellZoom = CELL_ZOOM): { west: number; east: number; south: number; north: number } {
  const n = 2 ** cellZoom
  const lng = (x: number) => (x / n) * 360 - 180
  const lat = (y: number) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (const key of keys) {
    const { x, y } = cellFromKey(key, cellZoom)
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
export function chunksToEvict(chunks: Chunk[], camera: Cell | null, budgetRows: number, keepCells: number, cellZoom = CELL_ZOOM): string[] {
  let held = chunks.reduce((sum, chunk) => sum + chunk.rows, 0)
  if (held <= budgetRows || !camera) return []
  const nearest = (chunk: Chunk) => Math.min(...chunk.cells.map((key) => cellDistance(cellFromKey(key, cellZoom), camera)))
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
