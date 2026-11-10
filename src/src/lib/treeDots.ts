import { DEFAULT_CROWN_WIDTH, SPRITE_CROWN_ZOOM_START, SPRITE_OPAQUE_ZOOM } from './treeCrowns'
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

// --- Zoom curves (formerly useMapLayers' circle paint) ---

export const CIRCLE_MIN_ZOOM = 6
/** The dots are gone (bar the ground past the sprites' reach) once the sprites are opaque. */
export const CIRCLE_MAX_ZOOM = SPRITE_OPAQUE_ZOOM
/** The dots' opacity just before the sprites start to take over. */
export const CIRCLE_PEAK_OPACITY = 0.92
/** Dots past the sprites' reach, at zooms the sprites own. */
export const CIRCLE_FAR_OPACITY = 0.85
/** Below this a tap does not select a dot. */
export const CIRCLE_PICK_MIN_ZOOM = 12.8
export const circleOpacity = (zoom: number) => zoom < CIRCLE_MIN_ZOOM || zoom >= CIRCLE_MAX_ZOOM ? 0
  : interpolateStops([[6, 0], [8, 0.75], [SPRITE_CROWN_ZOOM_START, CIRCLE_PEAK_OPACITY], [SPRITE_OPAQUE_ZOOM, 0]], zoom)

/** Scales every dot radius below; the stops keep the shape of the curve. */
export const CIRCLE_RADIUS_SCALE = 0.7

/** A circle's radius in CSS pixels runs from `min` (thin trunk) to `max` (42 in and over) at `zoom`. */
export function circleRadiusRange(zoom: number): [number, number] {
  return [
    CIRCLE_RADIUS_SCALE * interpolateStops([[8, 0.4], [12.8, 0.5], [15, 1.5], [18, 2.2], [21, 2.6]], zoom),
    CIRCLE_RADIUS_SCALE * interpolateStops([[8, 0.5], [12.8, 0.8], [15, 4], [18, 6.7], [21, 8.2]], zoom),
  ]
}

const SQRT_MAX_DBH = Math.sqrt(42)
export function circleRadius(dbh: number, [min, max]: readonly [number, number]): number {
  const s = Math.min(1, Math.max(0, Math.sqrt(dbh) / SQRT_MAX_DBH))
  return min + (max - min) * s
}

/** A point chunk's crown byte per metre: a quarter metre each, so 60 m fits in a byte. */
export const CROWN_BYTE_PER_M = 4

/** A crown byte's width in metres; an unknown crown is the sprites' DEFAULT_CROWN_WIDTH. */
export function crownMetres(crown: number): number {
  return crown > 0 ? crown / CROWN_BYTE_PER_M : DEFAULT_CROWN_WIDTH
}

/** CSS pixels per real metre at the view's centre: what a sprite's crown is drawn at there. */
export function pixelsPerMetre(zoom: number, lat: number): number {
  return 1 / (metresPerPixel(zoom) * Math.cos((lat * Math.PI) / 180))
}

/**
 * A dot's radius in CSS pixels at the view's centre (the caller scales it for depth, as the
 * sprites shrink): the crown's own disc at metre scale, as the sprite over it draws it, so a big
 * tree hands over to a dot as wide as its crown; the trunk-sized circle is the floor, which is
 * all a dot is where crowns are under a pixel.
 */
export function dotRadius(dbh: number, crown: number, range: readonly [number, number], pxPerMetre: number): number {
  return Math.max(circleRadius(dbh, range), (crownMetres(crown) / 2) * pxPerMetre)
}

/** Parse '#rrggbb' to [r, g, b] bytes; anything else is mid grey. */
export function hexRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return [128, 128, 128]
  const v = parseInt(m[1], 16)
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255]
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

/**
 * Every point within `radius` metres of (qx, qy) in the grid's cells; the
 * caller tests them exactly. Without `order` the points are stored in the
 * grid's cell order, and a slot is the point's own index.
 */
export function pointsNear(grid: Pick<PickGrid, 'cell' | 'cols' | 'rows' | 'start'> & { order?: Uint32Array }, qx: number, qy: number, radius: number, visit: (i: number) => void): void {
  const c0 = Math.max(0, Math.floor((qx - radius) / grid.cell))
  const c1 = Math.min(grid.cols - 1, Math.floor((qx + radius) / grid.cell))
  const r0 = Math.max(0, Math.floor((qy - radius) / grid.cell))
  const r1 = Math.min(grid.rows - 1, Math.floor((qy + radius) / grid.cell))
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      const cell = r * grid.cols + c
      for (let k = grid.start[cell]; k < grid.start[cell + 1]; k++) visit(grid.order ? grid.order[k] : k)
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
