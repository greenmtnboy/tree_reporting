import type { TreeForm } from '../types'

/** Normalized vector artwork shared by native icons and close-range sprites. */
export type TreeShape =
  | { kind: 'ellipse'; x: number; y: number; rx: number; ry: number; angle?: number }
  | { kind: 'polygon'; points: [number, number][] }

export interface TreeArtwork { trunk: TreeShape[]; foliage: TreeShape[]; blooms: TreeShape[]; crownFraction: number }
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

/** Broad, overlapping masses read at marker scale without tiny leaf details. */
export function treeArtwork(form: TreeForm): TreeArtwork {
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
      foliage = [
        ellipse(0.5, 0.21, 0.035, 0.15, -0.06),
        ellipse(0.405, 0.235, 0.043, 0.18, -0.75),
        ellipse(0.6, 0.225, 0.04, 0.175, 0.82),
        ellipse(0.32, 0.31, 0.046, 0.215, -1.35),
        ellipse(0.69, 0.305, 0.046, 0.21, 1.36),
        ellipse(0.36, 0.385, 0.037, 0.165, -2.02),
        ellipse(0.645, 0.385, 0.037, 0.165, 2.02),
        ellipse(0.5, 0.315, 0.073, 0.056),
      ]
      break
    }
    case 'broadleaf':
      trunk.push(...stem(0.34, 0.47, 0.5, 0.75, 0.043), ...stem(0.66, 0.45, 0.5, 0.72, 0.039))
      foliage = [ellipse(0.49, 0.365, 0.255, 0.285), ellipse(0.315, 0.405, 0.155, 0.185),
        ellipse(0.675, 0.37, 0.165, 0.19), ellipse(0.45, 0.545, 0.205, 0.115), ellipse(0.62, 0.51, 0.16, 0.13)]
      break
    case 'spreading':
      trunk = [...stem(0.49, 0.43, 0.5, 0.95, 0.092), ...stem(0.26, 0.42, 0.5, 0.73, 0.043), ...stem(0.74, 0.4, 0.5, 0.7, 0.04)]
      foliage = [ellipse(0.475, 0.31, 0.265, 0.19), ellipse(0.235, 0.405, 0.175, 0.13),
        ellipse(0.74, 0.375, 0.195, 0.145), ellipse(0.47, 0.445, 0.31, 0.12)]
      break
    case 'conifer':
      trunk = stem(0.5, 0.58, 0.5, 0.95, 0.066)
      // Swept tiers create a fir silhouette, with a clear leader and broad skirt.
      foliage = [polygon([[0.5, 0.055], [0.635, 0.33], [0.588, 0.317], [0.704, 0.495],
        [0.644, 0.48], [0.79, 0.72], [0.635, 0.747], [0.5, 0.765], [0.36, 0.747],
        [0.21, 0.725], [0.353, 0.48], [0.298, 0.495], [0.414, 0.316], [0.365, 0.332]])]
      break
    case 'columnar':
      trunk = stem(0.5, 0.54, 0.5, 0.95, 0.066)
      foliage = [ellipse(0.505, 0.335, 0.125, 0.275), ellipse(0.465, 0.475, 0.145, 0.225),
        ellipse(0.565, 0.48, 0.108, 0.205), ellipse(0.51, 0.64, 0.106, 0.097)]
      break
    case 'ornamental':
      trunk = [...stem(0.5, 0.47, 0.5, 0.95, 0.069), ...stem(0.36, 0.48, 0.5, 0.73, 0.035), ...stem(0.65, 0.45, 0.5, 0.7, 0.032)]
      foliage = [ellipse(0.49, 0.335, 0.202, 0.225), ellipse(0.34, 0.435, 0.14, 0.16),
        ellipse(0.655, 0.407, 0.15, 0.17), ellipse(0.49, 0.525, 0.19, 0.105)]
      for (const [x, y, radius] of [[0.385, 0.335, 0.022], [0.6, 0.29, 0.02], [0.64, 0.46, 0.018], [0.455, 0.51, 0.021]]) {
        for (let petal = 0; petal < 5; petal++) {
          const angle = petal * Math.PI * 2 / 5 - Math.PI / 2
          blooms.push(ellipse(x + Math.cos(angle) * radius * 0.72, y + Math.sin(angle) * radius * 0.72, radius * 0.7))
        }
        blooms.push(ellipse(x, y, radius * 0.7))
      }
      break
    case 'weeping':
      trunk = [...stem(0.49, 0.34, 0.5, 0.95, 0.085), ...stem(0.32, 0.35, 0.5, 0.62, 0.035), ...stem(0.68, 0.35, 0.5, 0.6, 0.035)]
      foliage = [ellipse(0.49, 0.29, 0.26, 0.205),
        ellipse(0.25, 0.48, 0.085, 0.24, 0.12), ellipse(0.36, 0.515, 0.083, 0.255, 0.04),
        ellipse(0.49, 0.51, 0.091, 0.22), ellipse(0.62, 0.515, 0.084, 0.25, -0.06),
        ellipse(0.735, 0.47, 0.078, 0.23, -0.16)]
      break
    case 'multi_trunk':
      trunk = [...stem(0.3, 0.43, 0.405, 0.9, 0.052), ...stem(0.5, 0.35, 0.51, 0.9, 0.059), ...stem(0.715, 0.45, 0.605, 0.9, 0.051)]
      foliage = [ellipse(0.315, 0.38, 0.17, 0.215, -0.15), ellipse(0.515, 0.31, 0.195, 0.25, 0.08),
        ellipse(0.72, 0.395, 0.148, 0.18, 0.16), ellipse(0.49, 0.5, 0.235, 0.115)]
      break
    default:
      trunk = [...stem(0.5, 0.46, 0.5, 0.95, 0.075), ...stem(0.63, 0.46, 0.5, 0.7, 0.032)]
      foliage = [ellipse(0.485, 0.345, 0.195, 0.255), ellipse(0.42, 0.475, 0.185, 0.17), ellipse(0.605, 0.46, 0.16, 0.18)]
  }
  const bounds = foliage.map(shape => {
    if (shape.kind === 'polygon') return [Math.min(...shape.points.map(p => p[0])), Math.max(...shape.points.map(p => p[0]))]
    const radius = Math.hypot(shape.rx * Math.cos(shape.angle ?? 0), shape.ry * Math.sin(shape.angle ?? 0))
    return [shape.x - radius, shape.x + radius]
  })
  return { trunk, foliage, blooms, crownFraction: Math.max(...bounds.map(b => b[1])) - Math.min(...bounds.map(b => b[0])) }
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

/** Negative inside; normalized distance to the vector boundary. */
export function treeShapeDistance(shape: TreeShape, x: number, y: number): number {
  if (shape.kind === 'ellipse') {
    const c = Math.cos(shape.angle ?? 0), s = Math.sin(shape.angle ?? 0)
    const dx = c * (x - shape.x) + s * (y - shape.y), dy = -s * (x - shape.x) + c * (y - shape.y)
    const k0 = Math.hypot(dx / shape.rx, dy / shape.ry)
    const k1 = Math.hypot(dx / (shape.rx * shape.rx), dy / (shape.ry * shape.ry))
    // First-order distance is accurate near the zero contour, where the GPU
    // reconstructs the edge. Deep interiors/exteriors saturate in the atlas.
    return k1 ? k0 * (k0 - 1) / k1 : -Math.min(shape.rx, shape.ry)
  }
  let distance = Infinity, inside = false
  for (let i = 0; i < shape.points.length; i++) {
    const a = shape.points[i], b = shape.points[(i + 1) % shape.points.length]
    const dx = b[0] - a[0], dy = b[1] - a[1]
    const px = x - a[0], py = y - a[1]
    const t = Math.max(0, Math.min(1, (px * dx + py * dy) / (dx * dx + dy * dy)))
    distance = Math.min(distance, Math.hypot(px - t * dx, py - t * dy))
    // Even/odd crossing handles the concave fir tiers and either winding.
    if ((a[1] > y) !== (b[1] > y) && x < a[0] + dx * (y - a[1]) / dy) inside = !inside
  }
  return inside ? -distance : distance
}
