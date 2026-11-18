import type { TreeForm } from '../types'
import { leaflessUpright, type TreeAppearance } from './leaflessArtwork'

/** Normalized vector artwork shared by native icons and close-range sprites. */
export type TreeShape =
  | { kind: 'ellipse'; x: number; y: number; rx: number; ry: number; angle?: number }
  | { kind: 'polygon'; points: [number, number][] }

/**
 * `crownFraction` is the foliage's width as a fraction of the cell; `crownTop`
 * the foliage's highest point, as a y in the cell (0 is the top). With the
 * foot's y, the latter gives the drawn tree's height for metre scaling.
 */
export interface TreeArtwork { trunk: TreeShape[]; foliage: TreeShape[]; blooms: TreeShape[]; crownFraction: number; crownTop: number }
export const TREE_TRUNK_COLOR = '#5D4037'
export const TREE_BLOOM_COLOR = '#F8BBD0'
/** How much a layer's flat shadow darkens the foliage under it. */
const UPRIGHT_SHADOW = 0.24

const ellipse = (x: number, y: number, rx: number, ry = rx, angle = 0): TreeShape => ({ kind: 'ellipse', x, y, rx, ry, angle })
const polygon = (points: [number, number][]): TreeShape => ({ kind: 'polygon', points })

/** A tapered stem with a shallow elliptical foot, suggesting a round cylinder. */
function stem(topX: number, topY: number, footX = 0.5, base = 0.95, width = 0.08, tipWidth = width * 0.55): TreeShape[] {
  const ry = width * 0.22, footY = base - ry
  return [polygon([[topX - tipWidth / 2, topY], [topX + tipWidth / 2, topY], [footX + width / 2, footY], [footX - width / 2, footY]]),
    ellipse(footX, footY, width / 2, ry)]
}

/** Closed quadratic contour on a 100-unit drawing board. Controls can pull
 * a shoulder outward or cut a pocket of sky between two limbs. */
function contour(x: number, y: number, ...arcs: number[]): TreeShape {
  const points: [number, number][] = [[x / 100, y / 100]]
  for (let i = 0; i < arcs.length; i += 4) {
    const [cx, cy, ex, ey] = arcs.slice(i, i + 4)
    for (const t of [0.25, 0.5, 0.75, 1]) {
      const u = 1 - t
      points.push([(u*u*x + 2*u*t*cx + t*t*ex) / 100, (u*u*y + 2*u*t*cy + t*t*ey) / 100])
    }
    x = ex; y = ey
  }
  // The implicit closing edge completes the final arc.
  if (points[points.length - 1][0] === points[0][0] && points[points.length - 1][1] === points[0][1]) points.pop()
  return polygon(points)
}

type Points = [number, number][]

/**
 * The row (a y in the cell) where a broadleaf's crown is widest, clear of its shaded underside: the
 * renderer's tests measure the sprite's width and edge sharpness along it.
 */
export const LEAF_WAIST = 0.42

/**
 * A fir's tiers, top first: each a straight-edged skirt hanging from the leader, its lower edge
 * cut into drooping bough tips. Left and right differ, so the tree is not a stamped symbol.
 */
const firTiers: Points[] = ([
  // top, bottom, half-width left, half-width right, where the mid bough tip falls on each side
  [4, 18, 5, 5.5, .5, .5], [11, 30, 11, 12.5, .52, .40], [21, 43, 18, 16, .38, .55],
  [32, 57, 24, 26, .56, .42], [45, 73, 32, 32, .44, .58],
] as const).map(([top, bottom, left, right, l, r], i) => {
  const lift = i % 2 ? 1.4 : -0.8
  const side = (w: number, dir: number, skew: number, mid: number): Points => [
    [50 + dir * w, bottom - 2 + skew],
    [50 + dir * w * (mid + .2), bottom - 3.4 + skew], [50 + dir * w * mid, bottom + 2.2],
    [50 + dir * w * .1, bottom - 1 - skew],
  ]
  return [[49.6, top], ...side(right, 1, lift, r), ...side(left, -1, -lift, l).reverse()].map(([x, y]) => [x / 100, y / 100] as [number, number])
})

/** A scalloped foliage mass: rounded lobes of unequal breadth round an ellipse. */
function leafPad(cx: number, cy: number, rx: number, ry: number, lobes: number, phase: number): Points {
  const n = lobes * 6, points: Points = []
  for (let i = 0; i < n; i++) {
    const angle = Math.PI * 2 * i / n, swung = angle + .22 * Math.sin(2 * angle + phase * 1.7)
    const k = .91 + .09 * Math.abs(Math.sin(lobes * swung / 2 + phase)) ** .6
    points.push([cx + rx * k * Math.cos(angle), cy + ry * k * Math.sin(angle)])
  }
  return points
}

/**
 * Fit a drawing's foliage to the crown width and top its form is registered at, and set the two
 * masses that reach furthest out so the crown is widest at `waist`: the renderer sizes a sprite
 * from that width, and its tests measure it there.
 */
function register(shapes: Points[], left: number, right: number, top: number, waist?: number): Points[] {
  const xs = shapes.flat().map(p => p[0]), ys = shapes.flat().map(p => p[1])
  const x0 = Math.min(...xs), y0 = Math.min(...ys), scale = (right - left) / (Math.max(...xs) - x0)
  const fitted = shapes.map(points => points.map(([x, y]) => [left + (x - x0) * scale, top + (y - y0) * scale] as [number, number]))
  if (waist === undefined) return fitted
  for (const pick of [Math.min, Math.max]) {
    const reach = (points: Points) => pick(...points.map(p => p[0]))
    const mass = fitted.find(points => reach(points) === pick(...fitted.map(reach)))!
    const lift = waist - mass.find(p => p[0] === reach(mass))![1]
    for (const point of mass) point[1] += lift
  }
  return fitted
}

// A broadleaf: a full, round head about as wide as the tree is tall, on a short stout bole. Each
// flank is one tall mass (two stacked ones read as a double bulge), and the flanks hang lower than the middle, so the limbs show where they fork into the crown. The widest
// row (LEAF_WAIST) is above the middle, clear of the shaded underside.
const leafPads = register([
  leafPad(25, 51, 18, 26, 12, .4), leafPad(75, 50, 18, 26, 12, 2.1), leafPad(50, 46, 20, 16, 9, 1.2),
  leafPad(37, 36, 23, 20, 11, 1.7), leafPad(63, 35, 23, 20, 11, .2), leafPad(50, 22, 24, 17, 11, 2.9),
], .11, .89, .06, LEAF_WAIST)

// A spreading tree is wider than it is tall: a low crown, starting well down the cell.
const spreadPads = register([
  leafPad(20, 62, 18, 13, 9, .4), leafPad(80, 61, 18, 13, 9, 2.1), leafPad(50, 66, 28, 10, 10, 1.2),
  leafPad(32, 50, 22, 15, 10, 1.7), leafPad(68, 49, 22, 15, 10, .2), leafPad(50, 43, 22, 10, 10, 2.9),
], .04, .9613, .28)
const columnPads = register([
  leafPad(50, 63, 11, 8.5, 6, .4), leafPad(51, 53, 14, 12, 7, 2.1), leafPad(49, 40, 13.5, 13, 7, 1.2),
  leafPad(51, 27, 11, 12, 6, 1.7), leafPad(50, 14, 7, 9, 5, .2),
], .3375, .6625, .05)
const cherryPads = register([
  leafPad(32, 46, 18, 13, 8, .4), leafPad(68, 45, 18, 13, 8, 2.1), leafPad(50, 50, 20, 11, 8, 1.2),
  leafPad(36, 31, 19, 15, 9, 1.7), leafPad(64, 30, 19, 15, 9, .2), leafPad(50, 20, 17, 12, 8, 2.9),
], .1325, .84, .1338)
const copsePads = register([
  leafPad(27, 42, 17, 13, 8, .4), leafPad(74, 44, 16, 12, 8, 2.1), leafPad(50, 47, 20, 11, 8, 1.2),
  leafPad(35, 28, 17, 14, 9, 1.7), leafPad(66, 30, 16, 13, 9, .2), leafPad(51, 18, 15, 12, 8, 2.9), leafPad(50, 34, 14, 12, 7, .7),
], .1037, .9006, .08)
// The plain ovoid tree.
const plainPads = register([
  leafPad(43, 48, 15, 13, 8, .4), leafPad(59, 47, 14, 13, 8, 2.1), leafPad(50, 52, 13, 10, 7, 1.2),
  leafPad(50, 34, 18, 18, 9, 1.7), leafPad(50, 19, 13, 11, 7, .2),
], .235, .765, .09)

const inside = (points: Points, x: number, y: number) => {
  let hit = false
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [ax, ay] = points[i], [bx, by] = points[j]
    if ((ay > y) !== (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)) hit = !hit
  }
  return hit
}
const halton = (i: number, base: number) => {
  let f = 1, value = 0
  for (; i > 0; i = Math.floor(i / base)) { f /= base; value += f * (i % base) }
  return value
}
/**
 * A tree in flower: blossoms scattered over the whole head, evenly but in no row (a low-discrepancy
 * sequence, so the scatter is the same every bake). One sits at (0.40, 0.32), where the renderer's
 * tests sample a bloom.
 */
const cherryBlooms: [number, number, number][] = [[0.40, 0.32, 0.019]]
for (let i = 1; cherryBlooms.length < 30 && i < 200; i++) {
  const x = 0.14 + 0.70 * halton(i, 2), y = 0.14 + 0.52 * halton(i, 3), radius = [0.019, 0.013, 0.016, 0.012, 0.017][i % 5]
  // Clear of the head's edge and of the blossoms already placed.
  const room = [[0, 0], [radius * 1.6, 0], [-radius * 1.6, 0], [0, radius * 1.6], [0, -radius * 1.6]]
    .every(([dx, dy]) => cherryPads.some(pad => inside(pad, x + dx, y + dy)))
  if (room && cherryBlooms.every(([bx, by, br]) => Math.hypot(x - bx, y - by) > (radius + br) * 1.5)) cherryBlooms.push([x, y, radius])
}

/**
 * The forms drawn as stacked flat layers, lowest first; a layer is one or more shapes taken as one
 * mass. `cast` drops each layer's outline onto the layers below it (a fir's skirts); otherwise
 * each mass keeps its own underside in shade, `drop` deep.
 */
const layered: Partial<Record<TreeForm, { layers: Points[][]; drop: number; cast?: boolean }>> = {
  conifer: { layers: [...firTiers].reverse().map(tier => [tier]), drop: 0.03, cast: true },
  // One crown, one shadow: its underside. A band part-way up reads as a second tree behind.
  broadleaf: { layers: [leafPads], drop: 0.07 },
  spreading: { layers: [spreadPads], drop: 0.06 },
  columnar: { layers: [columnPads], drop: 0.06 },
  ornamental: { layers: [cherryPads], drop: 0.06 },
  multi_trunk: { layers: [copsePads], drop: 0.07 },
  default: { layers: [plainPads], drop: 0.07 },
}

/** Broad, overlapping masses read at marker scale without tiny leaf details. */
export function treeArtwork(form: TreeForm, appearance: TreeAppearance = 'leafy'): TreeArtwork {
  if (appearance === 'leafless') {
    // Retain the leafy envelope so a seasonal swap does not rescale the tree.
    const summer = treeArtwork(form)
    return { ...summer, trunk: leaflessUpright(form), foliage: [], blooms: [] }
  }
  let trunk: TreeShape[]
  let foliage: TreeShape[]
  const blooms: TreeShape[] = []
  switch (form) {
    case 'palm': {
      // Slightly bowed, tapered trunk; fronds have distinct drooping/ascending angles.
      const left: [number, number][] = [], right: [number, number][] = []
      const width = 0.058, ry = width * 0.22
      for (let i = 0; i <= 12; i++) {
        const t = i / 12, x = 0.5 + 0.048 * Math.sin(t * Math.PI), y = PALM_BUD + (0.95 - ry - PALM_BUD) * t
        const half = (0.027 + (width - 0.027) * t) / 2
        left.push([x - half, y]); right.push([x + half, y])
      }
      trunk = [polygon([...left, ...right.reverse()]), ellipse(0.5, 0.95 - ry, width / 2, ry)]
      // The upper and lower edges bow by different amounts: broad blades
      // taper to pendulous tips, with open sky between successive fronds.
      const frond = (cx: number, cy: number, x: number, y: number, belly: number) =>
        contour(50, PALM_BUD * 100, cx, cy, x, y, cx, cy + belly, 50, PALM_BUD * 100)
      // The fronds leave a solid heart, so none hangs off the trunk by a thread.
      foliage = [...palmFolds.map(([cx, cy, x, y, belly]) => frond(cx, cy, x, y, belly)), ellipse(0.5, PALM_BUD - 0.02, 0.058, 0.03)]
      break
    }
    case 'broadleaf':
      // The bole and both limbs run well up into the head, so none stops short of it.
      trunk = [...stem(0.5, 0.45, 0.5, 0.95, 0.125, 0.06), ...stem(0.37, 0.47, 0.5, 0.8, 0.045, 0.02), ...stem(0.63, 0.46, 0.5, 0.79, 0.043, 0.02)]
      foliage = leafPads.map(polygon)
      break
    case 'spreading':
      // A short bole; the limbs leave it low and run out almost level to the crown's ends.
      trunk = [...stem(0.5, 0.52, 0.5, 0.95, 0.11, 0.05),
        ...stem(0.19, 0.64, 0.5, 0.89, 0.05, 0.016), ...stem(0.81, 0.63, 0.5, 0.88, 0.048, 0.016),
        ...stem(0.35, 0.62, 0.5, 0.84, 0.036, 0.012), ...stem(0.65, 0.62, 0.5, 0.83, 0.034, 0.012)]
      foliage = spreadPads.map(polygon)
      break
    case 'conifer':
      trunk = stem(0.5, 0.58, 0.5, 0.95, 0.066)
      foliage = firTiers.map(polygon)
      break
    case 'columnar':
      trunk = stem(0.5, 0.54, 0.5, 0.95, 0.066)
      foliage = columnPads.map(polygon)
      break
    case 'ornamental':
      trunk = [...stem(0.5, 0.5, 0.5, 0.95, 0.062, 0.026), ...stem(0.37, 0.58, 0.5, 0.80, 0.03, 0.012), ...stem(0.63, 0.57, 0.5, 0.78, 0.028, 0.012)]
      foliage = cherryPads.map(polygon)
      for (const [x, y, radius] of cherryBlooms) blooms.push(ellipse(x, y, radius))
      break
    case 'weeping':
      trunk = [...stem(0.49, 0.34, 0.5, 0.95, 0.085), ...stem(0.32, 0.35, 0.5, 0.62, 0.035), ...stem(0.68, 0.35, 0.5, 0.6, 0.035)]
      // A rounded pollard and a curtain of switches: leave daylight
      // under the lifted centre, and vary each tip's fall and curl.
      foliage = [contour(15,46, 14,28,26,23, 27,11,40,13, 50,4,60,13, 74,10,78,24,
        89,29,87,49, 85,64,91,68, 82,68,80,52, 79,70,84,77,
        73,74,72,53, 70,69,73,81, 63,77,64,55, 60,61,58,68,
        52,63,55,48, 45,52,43,71, 39,68,40,56, 32,65,32,80,
        24,73,30,52, 22,60,19,76, 15,67,19,52, 13,58,11,64, 10,57,15,46)]
      break
    case 'multi_trunk':
      // The stems rise from one stool and lean apart.
      trunk = [...stem(0.3, 0.43, 0.458, 0.9, 0.05), ...stem(0.5, 0.35, 0.505, 0.9, 0.056), ...stem(0.715, 0.45, 0.552, 0.9, 0.049)]
      foliage = copsePads.map(polygon)
      break
    default:
      trunk = [...stem(0.5, 0.46, 0.5, 0.95, 0.075), ...stem(0.63, 0.46, 0.5, 0.7, 0.032)]
      foliage = plainPads.map(polygon)
  }
  const bounds = foliage.map(shape => {
    if (shape.kind === 'polygon') return [Math.min(...shape.points.map(p => p[0])), Math.max(...shape.points.map(p => p[0]))]
    const radius = Math.hypot(shape.rx * Math.cos(shape.angle ?? 0), shape.ry * Math.sin(shape.angle ?? 0))
    return [shape.x - radius, shape.x + radius]
  })
  const tops = foliage.map(shape => shape.kind === 'polygon'
    ? Math.min(...shape.points.map(p => p[1]))
    : shape.y - Math.hypot(shape.rx * Math.sin(shape.angle ?? 0), shape.ry * Math.cos(shape.angle ?? 0)))
  return { trunk, foliage, blooms, crownFraction: Math.max(...bounds.map(b => b[1])) - Math.min(...bounds.map(b => b[0])), crownTop: Math.min(...tops) }
}

export function fillTreeShape(ctx: CanvasRenderingContext2D, shape: TreeShape, size: number) {
  ctx.beginPath()
  if (shape.kind === 'ellipse') ctx.ellipse(shape.x * size, shape.y * size, shape.rx * size, shape.ry * size, shape.angle ?? 0, 0, Math.PI * 2)
  else {
    ctx.moveTo(shape.points[0][0] * size, shape.points[0][1] * size)
    for (const [x, y] of shape.points.slice(1)) ctx.lineTo(x * size, y * size)
    ctx.closePath()
  }
  ctx.fill()
}

// The shade between a willow's switches (100-unit board); the silhouette clips it.
const weepingShadow = 'M7 70 Q19 26 33 25 Q22 37 22 68 Q30 36 45 28 Q36 46 36 69 Q45 37 55 34 Q48 50 48 74 Q57 49 60 33 Q67 37 68 63 Q73 40 66 22 Q79 28 82 55 Q83 35 78 24 L98 37 V90 H6 Z'

/** Where a palm's fronds leave its trunk, as a y in the cell. */
const PALM_BUD = 0.24
// Each frond: control and tip of its upper edge, and how far its lower edge bellies below.
const palmFolds = [
  [47,9,56,4,16], [38,3,23,8,13], [58,2,72,5,12], [22,5,7,27,14],
  [76,2,93,20,14], [22,17,16,46,15], [79,13,86,42,15], [37,27,35,50,12], [63,22,65,47,13],
]

/** Black washes over the current fill. Callers supply the foliage mask. */
export function paintUprightShade(ctx: CanvasRenderingContext2D, form: TreeForm, size: number) {
  const flat = layered[form]
  if (flat) {
    // One flat tone, no gradient: each layer's outline dropped onto whatever lies below it.
    const scratch = document.createElement('canvas')
    scratch.width = scratch.height = size
    const ink = scratch.getContext('2d')!
    const mass = (shapes: Points[], dy: number) => {
      const path = new Path2D()
      for (const points of shapes) {
        points.forEach(([x, y], i) => i ? path.lineTo(x * size, (y + dy) * size) : path.moveTo(x * size, (y + dy) * size))
        path.closePath()
      }
      return path
    }
    for (const layer of flat.layers) {
      ink.save()
      if (!flat.cast) {
        // The mass hides what is behind it, then shades its own lower edge.
        ink.globalCompositeOperation = 'destination-out'
        ink.fill(mass(layer, 0))
        ink.clip(mass(layer, 0))
      }
      ink.globalCompositeOperation = 'source-over'
      ink.fill(mass(layer, flat.cast ? flat.drop : 0))
      ink.globalCompositeOperation = 'destination-out'
      ink.fill(mass(layer, flat.cast ? 0 : -flat.drop))
      ink.restore()
    }
    ctx.save()
    ctx.globalAlpha = UPRIGHT_SHADOW
    ctx.drawImage(scratch, 0, 0)
    ctx.restore()
    return
  }
  ctx.save()
  ctx.scale(size / 100, size / 100)
  ctx.fillStyle = `rgba(0,0,0,${UPRIGHT_SHADOW})`
  if (form === 'palm') {
    // The half of each frond below its midrib.
    const fronds = new Path2D()
    for (const [cx, cy, x, y, belly] of palmFolds) {
      fronds.moveTo(50, PALM_BUD * 100)
      fronds.quadraticCurveTo(cx, cy + belly * 0.50, x, y)
      fronds.quadraticCurveTo(cx, cy + belly, 50, PALM_BUD * 100)
    }
    ctx.fill(fronds)
  } else {
    ctx.fill(new Path2D(weepingShadow))
  }
  ctx.restore()
}

// The sprite atlas visits a row left to right. Reuse the crossings and
// candidate edges for every texel on that row of the same polygon.
type Scan = { y: number; range: number; crossings: number[]; edges: number[] }
const scans = new WeakMap<TreeShape, Scan>()

/** Negative inside; normalized distance to the vector boundary. */
export function treeShapeDistance(shape: TreeShape, x: number, y: number, range = Infinity): number {
  if (shape.kind === 'ellipse') {
    const c = Math.cos(shape.angle ?? 0), s = Math.sin(shape.angle ?? 0)
    const dx = c * (x - shape.x) + s * (y - shape.y), dy = -s * (x - shape.x) + c * (y - shape.y)
    const k0 = Math.hypot(dx / shape.rx, dy / shape.ry)
    const k1 = Math.hypot(dx / (shape.rx * shape.rx), dy / (shape.ry * shape.ry))
    // First-order distance is accurate near the zero contour, where the GPU
    // reconstructs the edge. Deep interiors/exteriors saturate in the atlas.
    return k1 ? k0 * (k0 - 1) / k1 : -Math.min(shape.rx, shape.ry)
  }
  let scan = scans.get(shape)
  if (!scan || scan.y !== y || scan.range !== range) {
    if (!scan) { scan = { y, range, crossings: [], edges: [] }; scans.set(shape, scan) }
    scan.y = y; scan.range = range; scan.crossings.length = scan.edges.length = 0
    for (let i = 0; i < shape.points.length; i++) {
      const a = shape.points[i], b = shape.points[(i + 1) % shape.points.length]
      if ((a[1] > y) !== (b[1] > y)) scan.crossings.push(a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]))
      if (y >= Math.min(a[1], b[1]) - range && y <= Math.max(a[1], b[1]) + range) scan.edges.push(i)
    }
  }
  let distance = range * range, inside = false
  for (const crossing of scan.crossings) if (x < crossing) inside = !inside
  for (const i of scan.edges) {
    const a = shape.points[i], b = shape.points[(i + 1) % shape.points.length]
    if (x < Math.min(a[0], b[0]) - range || x > Math.max(a[0], b[0]) + range) continue
    const dx = b[0] - a[0], dy = b[1] - a[1], px = x - a[0], py = y - a[1]
    const t = Math.max(0, Math.min(1, (px * dx + py * dy) / (dx * dx + dy * dy)))
    distance = Math.min(distance, (px - t * dx) ** 2 + (py - t * dy) ** 2)
  }
  return (inside ? -1 : 1) * Math.sqrt(distance)
}
