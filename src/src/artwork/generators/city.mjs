/** A small orthographic city. Every coordinate is projected from one ground
 * plane; z=0 is street level for foundations, walls, windows and curb lines. */
export const cityScene = {
  origin: [260, 170],
  buildings: [
    { id: 'left', x: -42, y: 68, width: 46, depth: 42, height: 80, floors: 3 },
    { id: 'background-tower', x: 30, y: 0, width: 54, depth: 44, height: 165, floors: 7 },
    { id: 'center', x: 30, y: 65, width: 60, depth: 50, height: 132, floors: 5 },
    { id: 'right', x: 125, y: 40, width: 56, depth: 42, height: 98, floors: 4 },
  ],
}

export function project([x, y, z = 0], origin = cityScene.origin) {
  return [origin[0] + (x - y) * Math.sqrt(3) / 2, origin[1] + (x + y) / 2 - z]
}

const cross = (a, b) => a[0] * b[1] - a[1] * b[0]
const minus = (a, b) => [a[0] - b[0], a[1] - b[1]]
const interpolate = (a, b, t) => a.map((n, i) => n + (b[i] - n) * t)
const edges = points => points.map((p, i) => [p, points[(i + 1) % points.length]])
const format = point => point.map(n => Number(n.toFixed(2))).join(' ')
const path = segments => segments.map(([a, b]) => `M${format(a)}L${format(b)}`).join(' ')

function inside(point, polygon) {
  const signs = edges(polygon).map(([a, b]) => cross(minus(b, a), minus(point, a)))
  return signs.every(n => n > 1e-6) || signs.every(n => n < -1e-6)
}

/** Remove portions behind nearer buildings, rather than paint opaque panels
 * over them. This keeps the exported SVG transparent and free of mask IDs. */
function visibleSegments(segment, occluders) {
  let segments = [segment]
  for (const polygon of occluders) {
    segments = segments.flatMap(([a, b]) => {
      const direction = minus(b, a)
      const cuts = [0, 1]
      for (const [c, d] of edges(polygon)) {
        const edge = minus(d, c)
        const denominator = cross(direction, edge)
        if (Math.abs(denominator) < 1e-9) continue
        const t = cross(minus(c, a), edge) / denominator
        const u = cross(minus(c, a), direction) / denominator
        if (t > 0 && t < 1 && u >= 0 && u <= 1) cuts.push(t)
      }
      cuts.sort((a, b) => a - b)
      return cuts.slice(1).flatMap((end, i) => {
        const start = cuts[i]
        if (end - start < 1e-8 || inside(interpolate(a, b, (start + end) / 2), polygon)) return []
        return [[interpolate(a, b, start), interpolate(a, b, end)]]
      })
    })
  }
  return segments
}

function buildingGeometry(building, origin) {
  const { x, y, width, depth, height, floors } = building
  if (![x, y, width, depth, height, floors].every(Number.isFinite)
    || width <= 0 || depth <= 0 || height <= 0 || !Number.isInteger(floors) || floors < 1) {
    throw new Error(`Invalid building dimensions: ${building.id}`)
  }
  const p = (u, v, z) => project([u, v, z], origin)
  const roof = [p(x, y, height), p(x + width, y, height), p(x + width, y + depth, height), p(x, y + depth, height)]
  const base = [p(x, y, 0), p(x + width, y, 0), p(x + width, y + depth, 0), p(x, y + depth, 0)]
  const silhouette = [roof[0], roof[1], base[1], base[2], base[3], roof[3]]
  const structure = [...edges(roof), ...[1, 2, 3].map(i => [roof[i], base[i]]), [base[1], base[2]], [base[2], base[3]]]
  const windows = []
  // Window rectangles live on the two visible wall planes, with margins at
  // each floor and corner. They cannot drift away from the facade geometry.
  for (const face of ['front', 'side']) {
    const span = face === 'front' ? width : depth
    const columns = Math.max(1, Math.floor(span / 19))
    const at = (u, z) => face === 'front' ? p(x + u, y + depth, z) : p(x + width, y + u, z)
    for (let row = 0; row < floors; row++) {
      const bottom = (row + .28) * height / floors
      const top = (row + .70) * height / floors
      for (let column = 0; column < columns; column++) {
        const left = (column + .27) * span / columns
        const right = (column + .65) * span / columns
        windows.push(...edges([at(left, bottom), at(right, bottom), at(right, top), at(left, top)]))
      }
    }
  }
  return { id: building.id, silhouette, structure, windows }
}

export function generateCitySvg(scene = cityScene) {
  // Painter order is derived from footprint depth, never from an arbitrary
  // path order. Footprints in this composition do not intersect.
  const ordered = [...scene.buildings].sort((a, b) =>
    (a.x + a.y + (a.width + a.depth) / 2) - (b.x + b.y + (b.width + b.depth) / 2))
  const buildings = ordered.map(building => buildingGeometry(building, scene.origin))
  const ground = (x, y) => project([x, y, 0], scene.origin)
  const survey = []
  for (const axis of [-90, -45, 0, 45, 90, 135, 180, 225]) {
    survey.push([ground(axis, -80), ground(axis, 185)], [ground(-115, axis), ground(245, axis)])
  }
  const curb = (x, y) => [ground(-60, y), ground(x, y), ground(x, -14)]
  const street = [curb(200, 128), curb(206, 134)].flatMap(points => [[points[0], points[1]], [points[1], points[2]]])
  const allSilhouettes = buildings.map(building => building.silhouette)
  const draw = (segments, occluders) => path(segments.flatMap(segment => visibleSegments(segment, occluders)))

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 360" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round">
  <!-- Generated by generators/city.mjs. Edit cityScene; run pnpm artwork:generate. -->
  <g class="survey-lines" opacity=".2" stroke-width=".65">
    <path d="${draw(survey, allSilhouettes)}" />
  </g>
  <g class="street" opacity=".55" stroke-width=".8">
    <path d="${draw(street, allSilhouettes)}" />
  </g>
${buildings.map((building, i) => {
    const occluders = buildings.slice(i + 1).map(building => building.silhouette)
    return `  <g data-building="${building.id}">
    <path class="structure" d="${draw(building.structure, occluders)}" />
    <path class="windows" opacity=".55" stroke-width=".65" d="${draw(building.windows, occluders)}" />
  </g>`
  }).join('\n')}
</svg>
`
}
