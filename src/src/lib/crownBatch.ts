import {
  CROWN_DISTANCE_MAX, DEFAULT_CROWN_WIDTH, MAX_CROWNS, MAX_TREE_SPRITES,
  mercatorPoint, metersPerMercatorUnit, spriteAspect, validCrownWidth,
  type TreeCrownColumns, type TreeRenderView,
} from './treeCrowns'

/** Floats per vertex; six vertices (two triangles) per tree. */
export const CROWN_STRIDE = 15
const CORNERS = [-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]

/** A sprite atlas cell's geometry: what the vertex builder needs from the atlas, without its pixels. */
export interface SpriteCellGeometry {
  left: number; right: number; top: number; bottom: number
  crownFraction: number; heightFraction: number; base: number
}

export interface CrownBatchOptions {
  /** Phones draw ground-plane rings for measured crowns near the camera, not sprites. */
  simplified: boolean
  /** Degrees; sprites are ordered back to front along it. */
  bearing: number
  /** The atlas cells by tree form; desktop only. */
  cells?: Record<string, SpriteCellGeometry>
}

/**
 * A sprite batch ready for the GPU, built in the worker so a batch landing
 * costs the main thread one buffer upload instead of a sort and a vertex
 * fill (~1 ms per thousand sprites).
 */
export interface CrownBatch {
  vertices: Float32Array
  /** The drawn trees, in draw order (back to front on the desktop). */
  id: string[]
  lng: Float64Array
  lat: Float64Array
  category: string[]
  /** Mercator position of the query camera; vertices are relative to it. */
  origin: [number, number]
  /** Metres from the query camera within which the phone's crown budget holds. */
  budgetDistance: number
  /** Metres from the query camera of the farthest sprite when the batch hit its cap; Infinity when nothing was cut. */
  reach: number
  /** Rows the query returned, drawn or not. */
  rows: number
  /** Worker-side milliseconds: waiting for and running the query, and building this batch. */
  timing: { sqlMs: number; buildMs: number }
}

export function emptyCrownBatch(): CrownBatch {
  return {
    vertices: new Float32Array(), id: [], lng: new Float64Array(), lat: new Float64Array(), category: [],
    origin: [0, 0], budgetDistance: CROWN_DISTANCE_MAX, reach: Infinity, rows: 0, timing: { sqlMs: 0, buildMs: 0 },
  }
}

export function crownBatchBuffers(b: CrownBatch): ArrayBuffer[] {
  return [b.vertices, b.lng, b.lat].map(a => a.buffer as ArrayBuffer)
}

function rgb(hex: string, cache: Map<string, number[]>): number[] {
  let value = cache.get(hex)
  if (!value) {
    const valid = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#66BB6A'
    value = [1, 3, 5].map(offset => parseInt(valid.slice(offset, offset + 2), 16) / 255)
    cache.set(hex, value)
  }
  return value
}

export function buildCrownBatch(c: TreeCrownColumns, camera: TreeRenderView, options: CrownBatchOptions): CrownBatch {
  const { simplified, cells } = options
  const limit = simplified ? MAX_CROWNS : camera.spriteLimit ?? MAX_TREE_SPRITES
  const rows = c.id.length
  const n = Math.min(rows, limit)
  const origin = mercatorPoint(camera.lng, camera.lat)
  const meters = metersPerMercatorUnit(camera.lat)

  // Project once: Mercator units relative to the query camera (local, to
  // avoid float32 jitter at street-level zoom).
  const dx = new Float64Array(n), dy = new Float64Array(n)
  for (let i = 0; i < n; i++) {
    const [x, y] = mercatorPoint(c.lng[i], c.lat[i])
    dx[i] = x - origin[0]
    dy[i] = y - origin[1]
  }
  // Back-to-front for overlapping upright sprites. Ground-plane depth order
  // depends on bearing, not perspective divide, so one axis sorts them.
  const order = new Uint32Array(n)
  for (let i = 0; i < n; i++) order[i] = i
  if (!simplified) {
    const bearing = options.bearing * Math.PI / 180
    const sin = Math.sin(bearing), cos = Math.cos(bearing)
    const depth = new Float64Array(n)
    for (let i = 0; i < n; i++) depth[i] = -dx[i] * sin + dy[i] * cos
    order.sort((a, b) => depth[a] - depth[b])
  }
  const altitude2 = camera.altitude ** 2

  const vertices = new Float32Array(n * 6 * CROWN_STRIDE)
  const id: string[] = [], category: string[] = []
  const lng = new Float64Array(n), lat = new Float64Array(n)
  const nearbyDistances: number[] = []
  const colors = new Map<string, number[]>()
  let cursor = 0, drawn = 0, farthest = 0
  for (let k = 0; k < n; k++) {
    const i = order[k]
    const width = c.width[i]
    if ((simplified && !validCrownWidth(width)) || !Number.isFinite(c.lng[i]) || !Number.isFinite(c.lat[i])) continue
    const x = dx[i] * meters, y = dy[i] * meters
    const distance = Math.sqrt(x * x + y * y + altitude2)
    if (simplified && distance >= CROWN_DISTANCE_MAX) continue
    if (validCrownWidth(width) && distance < CROWN_DISTANCE_MAX) nearbyDistances.push(distance)
    farthest = Math.max(farthest, distance)
    // Desktop sprites are always drawn at metre scale; a tree with no width gets a typical crown.
    const drawnWidth = validCrownWidth(width) ? width : simplified ? 0 : DEFAULT_CROWN_WIDTH
    const radius = drawnWidth / 2 / metersPerMercatorUnit(c.lat[i])
    const [r, g, b] = rgb(c.color[i], colors)
    const cell = cells?.[c.category[i]] ?? cells?.default
    const dbh = Number.isFinite(c.dbh[i]) && c.dbh[i] > 0 ? c.dbh[i] : 3
    const trunk = Math.min(1, Math.sqrt(dbh / 42))
    const aspect = cell ? spriteAspect(width, c.height[i], cell.crownFraction, cell.heightFraction) : 1
    for (let v = 0; v < CORNERS.length; v += 2) {
      const cx = CORNERS[v], cy = CORNERS[v + 1]
      vertices[cursor++] = dx[i]
      vertices[cursor++] = dy[i]
      vertices[cursor++] = cx
      vertices[cursor++] = cy
      vertices[cursor++] = radius
      vertices[cursor++] = r
      vertices[cursor++] = g
      vertices[cursor++] = b
      vertices[cursor++] = c.measured[i] ? 1 : 0
      vertices[cursor++] = cell ? (cx < 0 ? cell.left : cell.right) : 0
      vertices[cursor++] = cell ? (cy > 0 ? cell.top : cell.bottom) : 0
      vertices[cursor++] = cell?.crownFraction ?? 1
      vertices[cursor++] = trunk
      vertices[cursor++] = cell?.base ?? 0.95
      vertices[cursor++] = aspect
    }
    id.push(c.id[i])
    category.push(c.category[i])
    lng[drawn] = c.lng[i]
    lat[drawn] = c.lat[i]
    drawn++
  }
  // If density exhausts the phone's budget, fade the outer band instead of
  // showing an arbitrary hard edge through a dense park.
  let budgetDistance = CROWN_DISTANCE_MAX
  if (nearbyDistances.length >= MAX_CROWNS) {
    nearbyDistances.sort((a, b) => a - b)
    budgetDistance = nearbyDistances[MAX_CROWNS - 1]
  }
  return {
    // Copy the used part so the transfer sends only what is drawn.
    vertices: vertices.slice(0, cursor), id, category,
    lng: lng.slice(0, drawn), lat: lat.slice(0, drawn),
    origin, budgetDistance, reach: !simplified && rows >= limit ? farthest : Infinity, rows, timing: { sqlMs: 0, buildMs: 0 },
  }
}
