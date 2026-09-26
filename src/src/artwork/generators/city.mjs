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
  street: {
    cornerRadius: 22,
    gardens: [{ x: -62, y: 123, radius: 11 }, { x: 191, y: 14, radius: 9 }],
  },
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
const chain = points => points.slice(1).map((point, i) => [points[i], point])

function streetGeometry(scene) {
  const ground = (x, y) => project([x, y, 0], scene.origin)
  // Round the street corner on the same ground plane as the foundations.
  // A slight bow in the long runs gives the road a drawn, unhurried character.
  const promenade = (x, y, radius) => {
    const points = []
    for (let i = 0; i <= 40; i++) {
      const t = i / 40
      points.push(ground(-105 + (x - radius + 105) * t, y + 2.5 * Math.sin(t * Math.PI * 2)))
    }
    for (let i = 1; i <= 24; i++) {
      const angle = i / 24 * Math.PI / 2
      points.push(ground(x - radius + radius * Math.sin(angle), y - radius + radius * Math.cos(angle)))
    }
    for (let i = 1; i <= 32; i++) {
      const t = i / 32
      points.push(ground(x + 2 * Math.sin(t * Math.PI * 2), (y - radius) * (1 - t) - 70 * t))
    }
    return points
  }
  const radius = scene.street.cornerRadius
  const inner = promenade(200, 128, radius)
  const outer = promenade(209, 137, radius + 9)
  const road = promenade(222, 150, radius + 22)
  const pencil = chain(outer.map(([x, y], i) => [x + .9 * Math.sin(i * .32), y + 1.5]))
    .filter((_, i) => i % 19 < 12)
  const paving = []
  for (const x of [-72, -48, -24, 0, 24, 48, 72, 96, 120, 144]) {
    const t = (x + 105) / (200 - radius + 105)
    const bow = 2.5 * Math.sin(t * Math.PI * 2)
    paving.push([ground(x, 129 + bow), ground(x, 135 + bow)])
  }
  // A short zebra crossing, set into the road instead of the building block.
  const crossing = []
  for (let i = 0; i < 5; i++) {
    const x = -58 + i * 7
    crossing.push(...edges([ground(x, 142), ground(x + 3, 142), ground(x + 3, 159), ground(x, 159)]))
  }
  const gardens = scene.street.gardens.map(({ x, y, radius }, index) => {
    const base = ground(x, y)
    const ring = Array.from({ length: 40 }, (_, i) => {
      const angle = i / 40 * Math.PI * 2
      return ground(x + Math.cos(angle) * radius, y + Math.sin(angle) * radius)
    })
    const crown = Array.from({ length: 60 }, (_, i) => {
      const angle = i / 60 * Math.PI * 2
      const scallop = 1 + .09 * Math.sin(5 * angle + index) + .035 * Math.sin(9 * angle)
      return [base[0] + Math.cos(angle) * 12 * scallop, base[1] - 24 + Math.sin(angle) * 14 * scallop]
    })
    const branches = [
      [base, [base[0] + 1, base[1] - 29]],
      [[base[0] + .6, base[1] - 17.4], [base[0] - 6, base[1] - 24]],
      [[base[0] + .8, base[1] - 23.2], [base[0] + 6, base[1] - 30]],
    ]
    return { crown, outline: [...edges(ring), ...edges(crown), ...branches] }
  })
  return {
    curbs: [...chain(inner), ...chain(outer)],
    lane: chain(road).filter((_, i) => i % 6 < 3),
    pencil, paving, crossing, gardens,
  }
}

function inside(point, polygon) {
  // Ray casting also supports the gently scalloped (non-convex) tree crowns.
  let contained = false
  for (const [a, b] of edges(polygon)) {
    if (Math.abs(cross(minus(b, a), minus(point, a))) < 1e-6
      && point[0] >= Math.min(a[0], b[0]) && point[0] <= Math.max(a[0], b[0])
      && point[1] >= Math.min(a[1], b[1]) && point[1] <= Math.max(a[1], b[1])) return false
    if ((a[1] > point[1]) !== (b[1] > point[1])
      && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) {
      contained = !contained
    }
  }
  return contained
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
  const street = streetGeometry(scene)
  const allSilhouettes = buildings.map(building => building.silhouette)
  const groundOccluders = [...allSilhouettes, ...street.gardens.map(garden => garden.crown)]
  const draw = (segments, occluders) => path(segments.flatMap(segment => visibleSegments(segment, occluders)))

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 360" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round">
  <!-- Generated by generators/city.mjs. Edit cityScene; run pnpm artwork:generate. -->
  <g class="survey-lines" opacity=".1" stroke-width=".65">
    <path d="${draw(survey, groundOccluders)}" />
  </g>
  <g class="street" opacity=".65" stroke-width=".85">
    <path d="${draw(street.curbs, groundOccluders)}" />
    <path class="lane-markings" opacity=".65" stroke-width="1.1" d="${draw(street.lane, groundOccluders)}" />
    <path class="paving" opacity=".45" stroke-width=".65" d="${draw(street.paving, groundOccluders)}" />
    <path class="crossing" opacity=".7" stroke-width=".65" d="${draw(street.crossing, groundOccluders)}" />
    <path class="pencil-pass" opacity=".3" stroke-width=".55" d="${draw(street.pencil, groundOccluders)}" />
  </g>
  <g class="pocket-gardens" opacity=".8" stroke-width=".85">
    <path d="${draw(street.gardens.flatMap(garden => garden.outline), allSilhouettes)}" />
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
