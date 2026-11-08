// Geometry for the desktop's resident tree layer (composables/treeDotsLayer.ts):
// the zoom curves the old per-colour heatmap and circle layers drew with, a
// level-of-detail pyramid for the heat, colour sums per level, a grid index
// for picking and the camera matrix relative to a local origin. Pure, so it
// is tested without WebGL.

const EARTH_RADIUS = 6378137
/** Web Mercator's world width in EPSG:3857 metres. */
export const MERCATOR_WORLD_M = 2 * Math.PI * EARTH_RADIUS
const HALF_WORLD_M = Math.PI * EARTH_RADIUS

/** EPSG:3857 metres per CSS pixel at `zoom` (512 px tiles). */
export function metresPerPixel(zoom: number): number {
  return MERCATOR_WORLD_M / (512 * 2 ** zoom)
}

/** Piecewise-linear interpolation over [zoom, value] stops, clamped at both ends (MapLibre's `interpolate linear`). */
export function interpolateStops(stops: readonly (readonly [number, number])[], x: number): number {
  if (x <= stops[0][0]) return stops[0][1]
  for (let i = 1; i < stops.length; i++) {
    const [x1, y1] = stops[i]
    if (x <= x1) {
      const [x0, y0] = stops[i - 1]
      return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0)
    }
  }
  return stops[stops.length - 1][1]
}

// --- Zoom curves (formerly useMapLayers' heatmap and circle paint) ---

export const HEAT_MIN_ZOOM = 6
export const HEAT_MAX_ZOOM = 15
export const CIRCLE_MIN_ZOOM = 12.8
export const CIRCLE_MAX_ZOOM = 15.5

/**
 * One heat layer stands where eighteen stacked ones (one per colour, 0.48
 * each) did, so it is drawn more opaque than any one of them was.
 * The heat fades in from z6 to z8 as the city markers fade out, and out by z15 as the circles and sprites take over. */
export const heatOpacity = (zoom: number) => interpolateStops([[6, 0], [8, 0.72], [10, 0.72], [13, 0.45], [15, 0]], zoom)
/** Kernel radius in CSS pixels. */
export const heatRadius = (zoom: number) => interpolateStops([[10, 10], [13, 14], [15, 22]], zoom)
export const heatIntensity = (zoom: number) => interpolateStops([[10, 0.85], [13, 1.2], [15, 1.9]], zoom)
export const circleOpacity = (zoom: number) => zoom < CIRCLE_MIN_ZOOM || zoom >= CIRCLE_MAX_ZOOM ? 0
  : interpolateStops([[12.8, 0], [12.9, 0.75], [15, 0.92], [15.4, 0.75], [15.5, 0]], zoom)

/** A circle's radius in CSS pixels runs from `min` (thin trunk) to `max` (42 in and over) at `zoom`. */
export function circleRadiusRange(zoom: number): [number, number] {
  return [
    interpolateStops([[12.8, 0.65], [15, 2.1], [18, 3.1], [21, 3.7]], zoom),
    interpolateStops([[12.8, 1], [15, 5.6], [18, 9.5], [21, 11.6]], zoom),
  ]
}

const SQRT_MAX_DBH = Math.sqrt(42)
export function circleRadius(dbh: number, [min, max]: readonly [number, number]): number {
  const s = Math.min(1, Math.max(0, Math.sqrt(dbh) / SQRT_MAX_DBH))
  return min + (max - min) * s
}

// --- Level of detail ---

/**
 * Cell sizes of the heat pyramid, metres; each level merges the one before
 * it. The finest is a third of the smallest kernel the heat draws (22 px at
 * z15 is 52 m); an 8 m level would hold nearly a cell per tree for nothing.
 */
export const DOT_LOD_CELLS = [16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192] as const

export interface DotLevel {
  cell: number
  count: number
  /** Weighted centroid of the cell's trees, metres from the origin. */
  x: Float32Array
  y: Float32Array
  /** Trees in the cell. */
  weight: Float32Array
  /** For each item of the level below (raw points for the first), its cell here. */
  parent: Uint32Array
}

/** Group items into square cells by their position; returns the cell of every item and per-cell sums. */
function groupCells(xs: Float32Array, ys: Float32Array, ws: Float32Array | null, n: number, cell: number): DotLevel {
  let cap = 16
  while (cap < n * 2) cap <<= 1
  const mask = cap - 1
  const keys = new Float64Array(cap).fill(-1)
  const slot = new Int32Array(cap)
  const parent = new Uint32Array(n)
  const sx = new Float64Array(n)
  const sy = new Float64Array(n)
  const sw = new Float64Array(n)
  let count = 0
  for (let i = 0; i < n; i++) {
    const cx = Math.floor(xs[i] / cell)
    const cy = Math.floor(ys[i] / cell)
    const key = cx * 4194304 + cy
    let h = (Math.imul(cx, 0x9E3779B1) ^ Math.imul(cy, 0x85EBCA77)) & mask
    while (keys[h] !== -1 && keys[h] !== key) h = (h + 1) & mask
    let c: number
    if (keys[h] === -1) { keys[h] = key; c = slot[h] = count++ } else c = slot[h]
    const w = ws ? ws[i] : 1
    parent[i] = c
    sx[c] += xs[i] * w
    sy[c] += ys[i] * w
    sw[c] += w
  }
  const x = new Float32Array(count)
  const y = new Float32Array(count)
  const weight = new Float32Array(count)
  for (let c = 0; c < count; c++) {
    x[c] = sx[c] / sw[c]
    y[c] = sy[c] / sw[c]
    weight[c] = sw[c]
  }
  return { cell, count, x, y, weight, parent }
}

/**
 * The heat pyramid: level 0 groups the raw points, each later level groups
 * the one before. A cell carries its trees' centroid and count, so a kernel
 * drawn at the cell sums to about what its trees' kernels would.
 */
export function buildDotLevels(x: Float32Array, y: Float32Array, count: number, cells: readonly number[] = DOT_LOD_CELLS): DotLevel[] {
  const levels: DotLevel[] = []
  let xs = x, ys = y, ws: Float32Array | null = null, n = count
  for (const cell of cells) {
    const level = groupCells(xs, ys, ws, n, cell)
    levels.push(level)
    xs = level.x; ys = level.y; ws = level.weight; n = level.count
  }
  return levels
}

/** The coarsest level whose cells are at most a third of the kernel radius (the finest when none is). */
export function levelForKernel(levels: readonly { cell: number }[], kernelMetres: number): number {
  let chosen = 0
  for (let i = 0; i < levels.length; i++) if (levels[i].cell <= kernelMetres / 3) chosen = i
  return chosen
}

/** Parse '#rrggbb' to [r, g, b] bytes; anything else is mid grey. */
export function hexRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return [128, 128, 128]
  const v = parseInt(m[1], 16)
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255]
}

/**
 * How much a colour counts in a cell's mean. A grey is "no recorded shade"
 * or "unknown trunk" in every scheme the map paints: it still counts as
 * trees (the density), but it should not wash out the recorded colours
 * beside it, so it carries a sixth of a colour's weight.
 */
export function colorWeight([r, g, b]: readonly [number, number, number]): number {
  return Math.max(r, g, b) - Math.min(r, g, b) < 16 ? 1 / 6 : 1
}

/**
 * Each level's colour per cell as RGBA bytes, from the points' palette
 * indices: RGB is the colour-weighted mean (colorWeight), A the cell's
 * colour weight per tree, so the heat can weigh cells against each other
 * the same way. Sums run up the pyramid, so a recolour is one pass over the
 * points and one over each level.
 */
export function levelColors(levels: readonly DotLevel[], color: Uint8Array, palette: readonly string[], count: number): Uint8Array[] {
  const rgb = palette.map(hexRgb)
  const cw = rgb.map(colorWeight)
  const out: Uint8Array[] = []
  let prev: Float64Array | null = null
  let prevCount = count
  for (const level of levels) {
    // Per cell: r·w, g·w, b·w, w.
    const sums = new Float64Array(level.count * 4)
    if (!prev) {
      for (let i = 0; i < count; i++) {
        const k = color[i] < rgb.length ? color[i] : 0
        const c = rgb[k], w = cw[k]
        const p = level.parent[i] * 4
        sums[p] += c[0] * w; sums[p + 1] += c[1] * w; sums[p + 2] += c[2] * w; sums[p + 3] += w
      }
    } else {
      for (let i = 0; i < prevCount; i++) {
        const p = level.parent[i] * 4
        sums[p] += prev[i * 4]; sums[p + 1] += prev[i * 4 + 1]; sums[p + 2] += prev[i * 4 + 2]; sums[p + 3] += prev[i * 4 + 3]
      }
    }
    const bytes = new Uint8Array(level.count * 4)
    for (let c = 0; c < level.count; c++) {
      const w = sums[c * 4 + 3]
      bytes[c * 4] = Math.round(sums[c * 4] / w)
      bytes[c * 4 + 1] = Math.round(sums[c * 4 + 1] / w)
      bytes[c * 4 + 2] = Math.round(sums[c * 4 + 2] / w)
      bytes[c * 4 + 3] = Math.round((255 * w) / level.weight[c])
    }
    out.push(bytes)
    prev = sums
    prevCount = level.count
  }
  return out
}

// --- Picking ---

export interface PickGrid {
  cell: number
  cols: number
  rows: number
  /** Points sorted by cell. */
  order: Uint32Array
  /** start[c]..start[c+1] is cell c's slice of `order`. */
  start: Uint32Array
}

/**
 * A counting-sort grid over the points, cells of at least `minCell` metres
 * and at most about `maxCells` of them. Points with negative coordinates
 * fall in the first row or column.
 */
export function buildPickGrid(x: Float32Array, y: Float32Array, count: number, maxCells = 1 << 20, minCell = 32): PickGrid {
  let maxX = 0, maxY = 0
  for (let i = 0; i < count; i++) { if (x[i] > maxX) maxX = x[i]; if (y[i] > maxY) maxY = y[i] }
  let cell = minCell
  while (Math.ceil((maxX + 1) / cell) * Math.ceil((maxY + 1) / cell) > maxCells) cell *= 2
  const cols = Math.max(1, Math.ceil((maxX + 1) / cell))
  const rows = Math.max(1, Math.ceil((maxY + 1) / cell))
  const cellOf = (i: number) => Math.min(rows - 1, Math.max(0, Math.floor(y[i] / cell))) * cols
    + Math.min(cols - 1, Math.max(0, Math.floor(x[i] / cell)))
  const start = new Uint32Array(cols * rows + 1)
  for (let i = 0; i < count; i++) start[cellOf(i) + 1]++
  for (let c = 0; c < cols * rows; c++) start[c + 1] += start[c]
  const fill = start.slice(0, cols * rows)
  const order = new Uint32Array(count)
  for (let i = 0; i < count; i++) order[fill[cellOf(i)]++] = i
  return { cell, cols, rows, order, start }
}

/** Every point within `radius` metres of (qx, qy) in the grid's cells; the caller tests them exactly. */
export function pointsNear(grid: PickGrid, qx: number, qy: number, radius: number, visit: (i: number) => void): void {
  const c0 = Math.max(0, Math.floor((qx - radius) / grid.cell))
  const c1 = Math.min(grid.cols - 1, Math.floor((qx + radius) / grid.cell))
  const r0 = Math.max(0, Math.floor((qy - radius) / grid.cell))
  const r1 = Math.min(grid.rows - 1, Math.floor((qy + radius) / grid.cell))
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      const cell = r * grid.cols + c
      for (let k = grid.start[cell]; k < grid.start[cell + 1]; k++) visit(grid.order[k])
    }
  }
}

/**
 * The index ranges of the cells a box overlaps, for items stored in cell
 * order (a grid's `order` applied): one range per grid row, merged where
 * rows run on. Returns [start, end) pairs.
 */
export function visibleRanges(grid: Pick<PickGrid, 'cell' | 'cols' | 'rows' | 'start'>, minX: number, minY: number, maxX: number, maxY: number): number[] {
  const out: number[] = []
  if (maxX < 0 || maxY < 0) return out
  const c0 = Math.max(0, Math.floor(minX / grid.cell))
  const c1 = Math.min(grid.cols - 1, Math.floor(maxX / grid.cell))
  const r0 = Math.max(0, Math.floor(minY / grid.cell))
  const r1 = Math.min(grid.rows - 1, Math.floor(maxY / grid.cell))
  if (c0 > c1 || r0 > r1) return out
  for (let r = r0; r <= r1; r++) {
    const start = grid.start[r * grid.cols + c0]
    const end = grid.start[r * grid.cols + c1 + 1]
    if (end <= start) continue
    if (out.length && out[out.length - 1] === start) out[out.length - 1] = end
    else out.push(start, end)
  }
  return out
}

/** Chunk grid cell for a draw: coarse, so a view is a few dozen ranges at most. */
export const DRAW_CHUNK_MIN_M = 512
export const DRAW_CHUNK_MAX_CELLS = 1 << 14

// --- Camera ---

/**
 * MapLibre's custom-layer matrix maps Mercator units ([0, 1], y down) to
 * clip space. Returns, in double precision, the matrix for positions given
 * in EPSG:3857 metres relative to (centreX, centreY): a vertex near the camera
 * then carries small numbers, and float32 keeps it steady at street zoom.
 */
export function relativeMatrix(matrix: ArrayLike<number>, centreX: number, centreY: number): Float64Array {
  const mx = (centreX + HALF_WORLD_M) / MERCATOR_WORLD_M
  const my = (HALF_WORLD_M - centreY) / MERCATOR_WORLD_M
  const out = new Float64Array(16)
  for (let row = 0; row < 4; row++) {
    out[row] = matrix[row] / MERCATOR_WORLD_M
    out[4 + row] = -matrix[4 + row] / MERCATOR_WORLD_M
    out[8 + row] = matrix[8 + row]
    out[12 + row] = matrix[row] * mx + matrix[4 + row] * my + matrix[12 + row]
  }
  return out
}

/** EPSG:3857 metres of a longitude/latitude. */
export function lngLatTo3857(lng: number, lat: number): [number, number] {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat))
  return [(lng * HALF_WORLD_M) / 180, EARTH_RADIUS * Math.log(Math.tan(Math.PI / 4 + (clamped * Math.PI) / 360))]
}

/** Longitude/latitude of EPSG:3857 metres. */
export function lngLatFrom3857(x: number, y: number): [number, number] {
  return [(x * 180) / HALF_WORLD_M, (360 / Math.PI) * Math.atan(Math.exp(y / EARTH_RADIUS)) - 90]
}
