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

/** Broad, overlapping masses read at marker scale without tiny leaf details. */
export function treeArtwork(form: TreeForm, appearance: TreeAppearance = 'leafy'): TreeArtwork {
  if (appearance === 'leafless') {
    // Retain the leafy envelope so a seasonal swap does not rescale the tree.
    const summer = treeArtwork(form)
    return { ...summer, trunk: leaflessUpright(form), foliage: [], blooms: [] }
  }
  let trunk = stem(0.49, 0.43)
  let foliage: TreeShape[]
  const blooms: TreeShape[] = []
  switch (form) {
    case 'palm': {
      // Slightly bowed, tapered trunk; fronds have distinct drooping/ascending angles.
      const left: [number, number][] = [], right: [number, number][] = []
      const width = 0.058, ry = width * 0.22
      for (let i = 0; i <= 12; i++) {
        const t = i / 12, x = 0.5 + 0.048 * Math.sin(t * Math.PI), y = 0.31 + (0.95 - ry - 0.31) * t
        const half = (0.027 + (width - 0.027) * t) / 2
        left.push([x - half, y]); right.push([x + half, y])
      }
      trunk = [polygon([...left, ...right.reverse()]), ellipse(0.5, 0.95 - ry, width / 2, ry)]
      // The upper and lower edges bow by different amounts: broad blades
      // taper to pendulous tips, with open sky between successive fronds.
      const frond = (cx: number, cy: number, x: number, y: number, belly: number) =>
        contour(50, 31, cx, cy, x, y, cx, cy + belly, 50, 31)
      foliage = palmFolds.map(([cx, cy, x, y, belly]) => frond(cx, cy, x, y, belly))
      break
    }
    case 'broadleaf':
      trunk.push(...stem(0.34, 0.47, 0.5, 0.75, 0.043), ...stem(0.66, 0.45, 0.5, 0.72, 0.039))
      foliage = [contour(12,38, 12,30,23,26, 21,17,34,17, 37,6,48,10, 54,4,61,14,
        72,11,75,23, 86,24,87,38, 87,47,78,50, 80,59,65,59,
        59,66,53,58, 48,51,43,58, 37,65,30,58, 16,60,18,50, 12,47,12,38)]
      break
    case 'spreading':
      trunk = [...stem(0.49, 0.43, 0.5, 0.95, 0.092), ...stem(0.26, 0.42, 0.5, 0.73, 0.043), ...stem(0.74, 0.4, 0.5, 0.7, 0.04)]
      foliage = [contour(4,43, 6,32,19,31, 22,21,35,24, 40,13,52,18,
        65,11,70,23, 82,19,85,30, 95,30,96,40, 98,47,86,48,
        80,54,70,49, 63,44,57,52, 51,56,46,51, 35,43,30,53, 22,57,17,51, 6,53,4,43)]
      break
    case 'conifer':
      trunk = stem(0.5, 0.58, 0.5, 0.95, 0.066)
      // Swept tiers create a fir silhouette, with a clear leader and broad skirt.
      foliage = [polygon([[.49,.04],[.53,.20],[.59,.27],[.55,.26],[.60,.36],[.67,.40],[.60,.39],
        [.67,.49],[.72,.52],[.65,.52],[.70,.60],[.79,.66],[.72,.66],[.82,.74],[.69,.72],[.63,.76],
        [.57,.73],[.48,.77],[.39,.74],[.28,.76],[.30,.72],[.18,.73],[.31,.62],[.24,.63],
        [.36,.51],[.30,.52],[.42,.38],[.35,.40],[.45,.27],[.40,.29],[.47,.18]])]
      break
    case 'columnar':
      trunk = stem(0.5, 0.54, 0.5, 0.95, 0.066)
      foliage = [contour(50,5, 57,14,58,23, 65,26,62,38, 68,49,63,58,
        65,71,54,75, 45,82,38,70, 31,65,35,56, 30,48,36,38, 34,28,41,24, 43,12,50,5)]
      break
    case 'ornamental':
      trunk = [...stem(0.5, 0.47, 0.5, 0.95, 0.069), ...stem(0.36, 0.48, 0.5, 0.73, 0.035), ...stem(0.65, 0.45, 0.5, 0.7, 0.032)]
      foliage = [contour(14,39, 15,30,24,31, 24,23,35,25, 36,16,48,19,
        53,10,64,15, 74,13,77,24, 89,27,81,36, 86,44,73,45,
        67,52,61,46, 56,43,50,49, 44,53,39,47, 32,44,29,53, 18,55,20,47, 11,47,14,39)]
      for (const [x, y, radius] of [[.24,.41,.027],[.31,.35,.016],[.40,.32,.026],[.49,.26,.014],[.66,.23,.027],[.73,.29,.017],[.61,.40,.023]]) {
        for (let petal = 0; petal < 5; petal++) {
          const angle = petal * Math.PI * 2 / 5 - Math.PI / 2
          blooms.push(ellipse(x + Math.cos(angle) * radius * 0.72, y + Math.sin(angle) * radius * 0.72, radius * 0.7))
        }
        blooms.push(ellipse(x, y, radius * 0.7))
      }
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
      trunk = [...stem(0.3, 0.43, 0.405, 0.9, 0.052), ...stem(0.5, 0.35, 0.51, 0.9, 0.059), ...stem(0.715, 0.45, 0.605, 0.9, 0.051)]
      foliage = [contour(11,41, 8,30,19,25, 20,16,31,21, 37,13,42,22,
        41,12,49,12, 56,4,63,12, 74,13,68,27, 77,22,82,31, 93,32,89,45,
        91,54,80,55, 74,62,65,52, 59,44,53,51, 49,57,43,50,
        37,45,34,53, 27,64,19,54, 9,55,11,41)]
      break
    default:
      trunk = [...stem(0.5, 0.46, 0.5, 0.95, 0.075), ...stem(0.63, 0.46, 0.5, 0.7, 0.032)]
      foliage = [ellipse(0.485, 0.345, 0.195, 0.255), ellipse(0.42, 0.475, 0.185, 0.17), ellipse(0.605, 0.46, 0.16, 0.18)]
  }
  // Broad crowns carry their weight on bent scaffold limbs and a flared
  // root collar. The gaps between limbs stay open even in a small marker.
  if (form === 'spreading' || form === 'broadleaf' || form === 'ornamental') {
    const wide = form === 'spreading'
    trunk = [
      contour(43,95, 47,90,47,78, 48,66,43,61, 35,57,wide ? 28 : 33,44,
        39,51,46,55, 49,58,50,61, 54,57,57,50, 60,41,65,39,
        61,47,60,54, 58,62,53,67, 50,85,56,95, 51,94,43,95),
      contour(47,67, 38,61,31,57, 23,53,20,44, 27,51,34,53, 42,57,48,60, 49,64,47,67),
      contour(54,62, 61,54,69,51, 75,48,79,41, 77,50,71,54, 63,57,55,67, 53,65,54,62),
    ]
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

// Shadow boundaries follow the boughs in the elevation drawing (100-unit board).
// Closed paths may extend beyond the foliage: its silhouette clips the washes.
const boughShadows: Record<Exclude<TreeForm, 'palm'>, string> = {
  broadleaf: 'M5 42 Q20 49 27 35 Q34 39 39 31 Q48 36 54 24 Q59 25 63 18 Q68 22 75 15 L100 0 V85 H0 Z',
  spreading: 'M0 42 Q12 46 21 37 Q28 39 33 34 Q42 37 47 29 Q57 33 64 25 Q72 29 78 23 Q89 25 95 20 L100 20 V70 H0 Z',
  ornamental: 'M8 41 Q22 47 29 37 Q37 39 43 31 Q50 35 56 26 Q64 29 70 21 Q76 24 85 15 L100 18 V70 H0 Z',
  columnar: 'M25 64 Q40 61 44 45 Q48 48 51 37 Q55 34 55 24 Q59 22 57 10 L78 0 V90 H25 Z',
  default: 'M20 47 Q32 55 41 42 Q51 45 56 32 Q63 31 63 20 L85 10 V80 H18 Z',
  multi_trunk: 'M5 43 Q19 52 28 39 Q35 39 39 26 Q39 37 46 38 Q57 37 61 24 Q65 23 68 15 L72 26 Q68 35 72 40 Q82 43 91 32 L96 70 H0 Z',
  weeping: 'M7 70 Q19 26 33 25 Q22 37 22 68 Q30 36 45 28 Q36 46 36 69 Q45 37 55 34 Q48 50 48 74 Q57 49 60 33 Q67 37 68 63 Q73 40 66 22 Q79 28 82 55 Q83 35 78 24 L98 37 V90 H6 Z',
  conifer: 'M32 25 L46 24 L50 18 L53 24 L60 25 L59 29 L48 27 L42 30 Z M26 40 L40 38 L46 32 L50 36 L54 32 L61 37 L71 38 L70 44 L56 40 L49 42 L44 39 L34 44 Z M22 53 L37 49 L43 44 L46 49 L52 45 L60 49 L71 50 L76 56 L65 54 L58 52 L51 55 L45 52 L32 57 Z M13 73 L31 65 L39 59 L41 64 L50 58 L55 63 L60 59 L69 65 L83 68 L88 81 H12 Z',
}

const palmFolds = [
  [47,14,54,4,17], [38,1,23,12,12], [58,-1,72,7,10], [22,10,7,33,13],
  [76,7,93,26,13], [22,24,16,53,14], [79,20,86,49,14], [37,34,34,58,11], [63,29,66,55,12],
]

/** Black washes over the current fill. Callers supply the foliage mask. */
export function paintUprightShade(ctx: CanvasRenderingContext2D, form: TreeForm, size: number) {
  ctx.save()
  ctx.scale(size / 100, size / 100)
  const wash = ctx.createLinearGradient(33, 16, 67, 60)
  wash.addColorStop(0, 'rgba(0,0,0,0)')
  wash.addColorStop(1, 'rgba(0,0,0,0.12)')
  ctx.fillStyle = wash
  ctx.fillRect(0, 0, 100, 100)
  ctx.fillStyle = 'rgba(0,0,0,0.16)'
  if (form === 'palm') {
    for (const [cx, cy, x, y, belly] of palmFolds) {
      ctx.beginPath()
      ctx.moveTo(50, 31)
      ctx.quadraticCurveTo(cx, cy + belly * 0.50, x, y)
      ctx.quadraticCurveTo(cx, cy + belly, 50, 31)
      ctx.fill()
    }
  } else {
    ctx.fill(new Path2D(boughShadows[form]))
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
