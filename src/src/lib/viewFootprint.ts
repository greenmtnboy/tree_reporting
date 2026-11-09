/**
 * The ground a pitched, rotated camera sees, as a convex polygon in EPSG:3857
 * metres, padded on every side and behind the camera itself, so a pan, a
 * reverse or a spin starts from trees already loaded. An axis-aligned lng/lat box around
 * the same view holds large off-screen corners at any bearing but the four
 * cardinal ones, and spends the sprite budget there.
 */
export type Point = [number, number]

/** Of the view's depth, how far the fetch area reaches past each edge, and behind the camera (or the near edge, if that is further back). */
export const FOOTPRINT_PAD = 0.25
/** Real metres; the most that padding adds, so a long pitched view does not load a deep strip behind the camera. */
export const FOOTPRINT_PAD_MAX_M = 500

/** Twice the signed area: positive when counter-clockwise (y north). */
function cross(o: Point, a: Point, b: Point): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
}

/** Convex hull, counter-clockwise, of a handful of points (monotone chain). */
export function convexHull(points: Point[]): Point[] {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1])
  if (sorted.length < 3) return sorted
  const half = (list: Point[]) => {
    const out: Point[] = []
    for (const p of list) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop()
      out.push(p)
    }
    out.pop()
    return out
  }
  return [...half(sorted), ...half([...sorted].reverse())]
}

/**
 * The fetch polygon for a view. `ground` holds the screen's bottom-left,
 * bottom-right, top-right and top-left corners on the ground; `eye` is the
 * camera's ground position; `metres` converts real metres to EPSG:3857 ones
 * at this latitude (1 / cos lat).
 */
export function viewFootprint(eye: Point, bearing: number, ground: Point[], pad = FOOTPRINT_PAD, metres = 1): Point[] {
  const b = bearing * Math.PI / 180
  const f: Point = [Math.sin(b), Math.cos(b)], r: Point = [Math.cos(b), -Math.sin(b)]
  // Camera frame: u forward, v to the right.
  const uv = ground.map(([x, y]): Point => {
    const dx = x - eye[0], dy = y - eye[1]
    return [dx * f[0] + dy * f[1], dx * r[0] + dy * r[1]]
  })
  const depth = Math.max(...uv.map(p => p[0])) - Math.min(...uv.map(p => p[0]))
  const p = Math.min(pad * depth, FOOTPRINT_PAD_MAX_M * metres)
  const [bl, br, tr, tl] = uv
  const padded: Point[] = p > 0
    // A pitched view's near edge is ahead of the camera: reach behind the camera itself. Its far
    // edge is at the horizon, where nearest-first never spends the budget, but a flat view's is not.
    ? [[Math.min(bl[0] - p, -p), bl[1] - p], [Math.min(br[0] - p, -p), br[1] + p], [tr[0] + p, tr[1] + p], [tl[0] + p, tl[1] - p]]
    : uv
  return convexHull(padded.map(([u, v]): Point => [eye[0] + u * f[0] + v * r[0], eye[1] + u * f[1] + v * r[1]]))
}

/** Inside or on a counter-clockwise convex polygon. */
export function insidePolygon(polygon: Point[], [x, y]: Point): boolean {
  for (let i = 0; i < polygon.length; i++) {
    if (cross(polygon[i], polygon[(i + 1) % polygon.length], [x, y]) < 0) return false
  }
  return polygon.length >= 3
}

/** The SQL test for a counter-clockwise convex polygon over two coordinate columns. */
export function polygonPredicate(polygon: Point[], x: string, y: string): string {
  if (polygon.length < 3 || !polygon.flat().every(Number.isFinite)) throw new Error('Invalid sprite footprint')
  return polygon.map((a, i) => {
    const b = polygon[(i + 1) % polygon.length]
    return `((${b[0] - a[0]}) * (${y} - (${a[1]})) - (${b[1] - a[1]}) * (${x} - (${a[0]})) >= 0)`
  }).join(' AND ')
}
