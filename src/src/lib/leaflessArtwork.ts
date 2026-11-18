import type { TreeForm } from '../types'
import type { TreeShape } from './treeArtwork'

export type TreeAppearance = 'leafy' | 'leafless'
type Point = [number, number]
/** Quadratic branch on a 100-unit drawing board, with total widths at its ends. */
type Branch = { start: Point; control: Point; end: Point; width: number; tip: number }
const branch = (start: Point, control: Point, end: Point, width = 1.6, tip = .28): Branch =>
  ({ start, control, end, width, tip })

function pointAt(b: Branch, t: number): Point {
  const u = 1 - t
  return [u*u*b.start[0] + 2*u*t*b.control[0] + t*t*b.end[0],
    u*u*b.start[1] + 2*u*t*b.control[1] + t*t*b.end[1]]
}

/** Tapered ribbons preserve the silhouette at large scales and antialias as
 * single strokes at marker scale. Each is a single polygon in the existing atlas. */
function ribbon(b: Branch): TreeShape {
  const left: Point[] = [], right: Point[] = []
  for (let i = 0; i <= 8; i++) {
    const t = i / 8, p = pointAt(b, t)
    const dx = (1-t)*(b.control[0]-b.start[0]) + t*(b.end[0]-b.control[0])
    const dy = (1-t)*(b.control[1]-b.start[1]) + t*(b.end[1]-b.control[1])
    const half = (b.width * (1-t) + b.tip*t) / 2 / Math.hypot(dx, dy)
    left.push([(p[0]-dy*half)/100, (p[1]+dx*half)/100])
    right.push([(p[0]+dy*half)/100, (p[1]-dx*half)/100])
  }
  return { kind: 'polygon', points: [...left, ...right.reverse()] }
}

// Deliberate scaffold poses. The short lateral shoots are generated from these
// living growth directions, with deterministic unequal spacing on each bough.
const upright: Record<TreeForm, Branch[]> = {
  broadleaf: [
    branch([50,95],[52,68],[47,40],6.8,2.4),
    branch([48,65],[37,52],[26,37],3.3,1.1), branch([49,59],[61,52],[70,35],3,1),
    branch([47,43],[38,28],[37,11],2.2), branch([47,43],[55,26],[58,8],2.2),
    branch([26,37],[19,29],[16,23],1.3), branch([26,37],[24,23],[27,16],1.5),
    branch([70,35],[73,22],[70,17],1.4), branch([70,35],[82,29],[86,30],1.4),
    branch([42,55],[25,48],[13,46],1.8), branch([57,55],[72,50],[85,42],1.7),
  ],
  spreading: [
    branch([50,95],[49,73],[48,58],7.7,3.2),
    branch([48,58],[34,43],[20,43],3.4,1.5), branch([48,58],[61,43],[80,36],3.2,1.4),
    branch([20,43],[11,42],[5,35],1.5), branch([20,43],[19,31],[26,26],1.5),
    branch([37,48],[32,31],[39,21],2), branch([50,56],[57,34],[55,19],2.3),
    branch([65,42],[66,29],[73,23],1.8), branch([80,36],[89,29],[94,32],1.4),
    branch([66,42],[80,46],[94,43],1.8), branch([30,47],[16,51],[8,48],1.5),
  ],
  ornamental: [
    branch([50,95],[49,69],[49,58],5.4,2.4),
    branch([49,58],[34,49],[29,32],2.8,1.0), branch([49,58],[61,46],[69,32],2.7,1),
    branch([29,32],[21,29],[16,29],1.2), branch([29,32],[29,21],[36,19],1.3),
    branch([44,52],[47,30],[43,23],1.8), branch([57,48],[55,29],[61,16],1.9),
    branch([69,32],[75,22],[75,18],1.2), branch([69,32],[80,32],[83,39],1.3),
    branch([33,43],[22,44],[17,42],1.1),
  ],
  columnar: [
    branch([50,95],[50,53],[50,6],5.0,.4),
    branch([50,70],[38,52],[39,33],1.8), branch([50,59],[58,44],[59,27],1.8),
    branch([50,50],[43,32],[45,17],1.4), branch([50,38],[54,24],[53,13],1.2),
    branch([50,77],[62,63],[63,49],1.8), branch([49,62],[35,54],[35,43],1.5),
  ],
  default: [
    branch([50,95],[50,62],[48,32],5.8,1.4),
    branch([49,59],[36,43],[30,31],2.4), branch([49,53],[62,41],[69,24],2.3),
    branch([48,34],[41,21],[44,10],1.7), branch([48,34],[55,22],[56,15],1.5),
    branch([39,47],[29,45],[24,39],1.5), branch([59,43],[70,43],[75,37],1.4),
  ],
  multi_trunk: [
    branch([40,90],[34,58],[29,28],4.2,.8), branch([51,90],[54,53],[53,11],4.6,.5),
    branch([61,90],[66,63],[77,33],4.0,.7),
    branch([30,37],[19,27],[16,22],1.7), branch([29,29],[33,20],[36,18],1.5),
    branch([36,58],[20,46],[13,39],1.8), branch([54,45],[43,32],[42,23],1.9),
    branch([54,32],[65,20],[64,13],1.8), branch([68,57],[82,51],[88,43],1.9),
    branch([74,42],[70,29],[72,22],1.6),
  ],
  weeping: [
    branch([50,95],[48,63],[49,33],6.5,2.8),
    branch([49,42],[31,14],[20,35],3.1,.5), branch([49,33],[35,3],[29,23],2.5,.4),
    branch([49,33],[63,5],[72,28],2.6,.4), branch([50,45],[77,17],[83,39],2.9,.4),
    branch([48,36],[52,12],[58,19],2.1,.4),
  ],
  conifer: [branch([50,95],[50,53],[49,5],5.1,.25)],
  palm: [branch([50,95],[58,56],[50,31],5.6,2.2)],
}

function sideTwigs(b: Branch, index: number, form: TreeForm): Branch[] {
  if (form === 'palm' || form === 'weeping') return []
  const dx = b.end[0] - b.start[0], dy = b.end[1] - b.start[1]
  const length = Math.hypot(dx, dy)
  const shoots: Branch[] = []
  for (const t of [.48, .75]) {
    const start = pointAt(b, t)
    const sign = (index + (t > .5 ? 1 : 0)) % 2 ? 1 : -1
    const outward = form === 'columnar' ? 2.8 : 4.5
    const end: Point = [start[0] + dx*.24 + sign*outward, start[1] + dy*.26 - 4.5]
    if (length > 15) shoots.push(branch(start,
      [start[0] + dx*.10 + sign*outward, start[1]+dy*.12-1], end, .75, .16))
  }
  return shoots
}

function makeUpright(form: TreeForm): TreeShape[] {
  const scaffold = [...upright[form]]
  if (form === 'conifer') {
    for (let i = 0; i < 6; i++) {
      const y = 25+i*8.4, spread = 6+i*4.5
      for (const side of [-1,1]) scaffold.push(branch([50,y-3],
        [50+side*spread*.65,y+4], [50+side*spread,y-1.5],1.6,.2))
    }
  }
  if (form === 'palm') {
    for (const [cx,cy,x,y] of [[38,4,23,12],[61,2,72,7],[22,11,8,33],[76,9,92,26],
      [22,25,16,53],[79,22,86,49],[37,37,34,58],[63,32,66,55],[48,16,54,4]]) {
      scaffold.push(branch([50,31],[cx,cy],[x,y],1.3,.18))
    }
  }
  const twigs = scaffold.flatMap((b,i) => i === 0 ? [] : sideTwigs(b,i,form))
  if (form === 'weeping') {
    for (let i=0; i<13; i++) {
      const x = 16+i*5.6
      const y = 16 + Math.abs(x-50)*.55
      const end: Point = [x + (x<50 ? -3 : 3), 65 + 13*Math.sin(i*1.8)**2]
      const b = branch([x,y],[x+(x-50)*.16,42],end,.95,.13)
      // Connect the hanging spray to a scaffold, then let its tip fall freely.
      scaffold.push(branch([49,34],[(49+x)/2,8], [x,y],1.3,.75))
      scaffold.push(b)
      const start = pointAt(b,.40)
      twigs.push(branch(start,[start[0]+3,57],[end[0]+4,end[1]-5],.5,.12))
    }
  }
  const shapes = [...scaffold, ...twigs].map(ribbon)
  if (form === 'columnar') {
    for (const shape of shapes) {
      if (shape.kind === 'polygon') {
        shape.points = shape.points.map(([x, y]) => [.5 + (x - .5) * .68, y])
      }
    }
  }
  return shapes
}

type Segment = {
  ax: number; ay: number; dx: number; dy: number
  inverseLength: number; width: number; taper: number
  left: number; right: number; top: number; bottom: number
}

/**
 * Build a hierarchy of scaffold limbs, secondary boughs and terminal twigs.
 * Angles and lengths are deliberately unequal; every fork grows from its parent.
 */
function makePlan(form: TreeForm) {
  const branches: Branch[] = []
  const directions: Record<TreeForm, number[]> = {
    broadleaf: [0, 31, 73, 109, 143, 181, 218, 261, 298, 333],
    conifer: [0, 36, 72, 108, 144, 180, 216, 252, 288, 324],
    palm: [0, 39, 77, 118, 165, 207, 249, 288, 327],
    columnar: [90, 121, 159, 209, 270, 301, 338, 29],
    ornamental: [0, 42, 89, 131, 178, 221, 269, 318],
    spreading: [0, 23, 67, 119, 179, 207, 253, 310],
    weeping: [0, 36, 80, 123, 168, 204, 251, 291, 329],
    multi_trunk: [90, 149, 213, 269, 331, 29, 197, 257, 317, 17, 77, 137, 325, 25, 85, 145, 205, 265],
    default: [0, 43, 86, 128, 176, 215, 258, 305, 336],
  }

  function pointOnPlan(b: Branch, t: number): Point {
    const p = pointAt(b, t)
    if (form === 'palm' || form === 'conifer') return p
    const index = branches.indexOf(b)
    const dx = b.end[0] - b.start[0], dy = b.end[1] - b.start[1]
    const length = Math.hypot(dx, dy)
    // Endpoints stay fixed. Forks attach to these same knuckles.
    const bend = Math.sin(t * Math.PI) * Math.min(1.7, length * .035)
      * (Math.sin(t * 18 + index * 2.1) + .35 * Math.sin(t * 31 + index))
    const x = p[0] - dy / length * bend, y = p[1] + dx / length * bend
    const radius = Math.hypot(x, y)
    const fit = radius > 98.8 ? 98.8 / radius : 1
    return [x * fit, y * fit]
  }

  function growFork(parent: Branch, t: number, angle: number, length: number, width: number, bend: number) {
    const start = pointOnPlan(parent, t)
    const end: Point = [start[0] + length * Math.cos(angle), start[1] + length * Math.sin(angle)]
    const control: Point = [
      start[0] + length * .53 * Math.cos(angle + bend),
      start[1] + length * .53 * Math.sin(angle + bend),
    ]
    const fork = branch(start, control, end, width, .12)
    branches.push(fork)
    return fork
  }

  if (form === 'multi_trunk') {
    // Three splayed stems rise from one close stool, then each develops its own crown.
    branches.push(
      branch([0, 0], [1, 12], [5, 24], 4.2, 2.4),
      branch([0, 0], [-12, -1], [-22, -8], 4.0, 2.4),
      branch([0, 0], [13, -4], [24, -15], 3.8, 2.4),
    )
  }
  for (const [i, degrees] of directions[form].entries()) {
    const angle = degrees * Math.PI / 180
    const radius = form === 'multi_trunk'
      ? i === 0 ? 74.5 : 47 + 13 * Math.sin(i * 1.9 + .3) ** 2
      : i === 0 ? 98.8 : 79 + 17 * Math.sin(i * 1.9 + .3) ** 2
    const spread = form === 'spreading' && i > 0 ? 1 - .24 * Math.sin(angle) ** 2 : 1
    const turn = form === 'weeping' ? .12 * Math.sin(i * 2.3) : form === 'conifer' ? 0
      : form === 'palm' ? .18 : .24 * Math.sin(i * 2.6 + .5)
    const start: Point = form === 'multi_trunk'
      ? i < 6 ? [5, 24] : i < 12 ? [-22, -8] : [24, -15]
      : [0, 0]
    const end: Point = [start[0] + radius * spread * Math.cos(angle), start[1] + radius * spread * Math.sin(angle)]
    const control: Point = [
      start[0] + (end[0] - start[0]) * .46 - radius * .12 * Math.sin(turn),
      start[1] + (end[1] - start[1]) * .46 + radius * .12 * Math.cos(angle) * Math.sin(turn),
    ]
    const main = branch(start, control, end, form === 'palm' ? 1.7 : form === 'multi_trunk' ? 2.4 : 3.1, .22)
    branches.push(main)
    if (form === 'palm') continue

    const boughs: Array<{ branch: Branch; angle: number; length: number }> = [
      { branch: main, angle, length: radius },
    ]
    if (form !== 'conifer') {
      // A long early fork breaks up the radial spoke before the smaller shoots.
      const forkAngle = angle + (i % 2 ? -.28 : .31)
      const length = radius * (.68 + .12 * Math.sin(i * 1.6) ** 2)
      const fork = growFork(main, .16, forkAngle, length, 1.7, -.12)
      boughs.push({ branch: fork, angle: forkAngle, length })
    }
    for (const bough of boughs) {
      for (const [j, t] of [.25, .42, .60, .77].entries()) {
        const side = (i + j) % 2 === 0 ? -1 : 1
        const forkAngle = bough.angle + side * (.38 + .19 * Math.sin(i * 2 + j) ** 2)
        const length = (1 - t) * bough.length * (form === 'columnar' ? .52 : .69)
        const fork = growFork(bough.branch, t, forkAngle, length, 1.25 - t * .7, side * .16)
        if (form !== 'conifer') {
          const twig = growFork(fork, .43, forkAngle - side * .48, length * .49, .43, -.12 * side)
          growFork(fork, .70, forkAngle + side * .39, length * .34, .32, .08 * side)
          growFork(twig, .55, forkAngle + side * .16, length * .22, .24, -.1)
        }
      }
    }
  }
  const segments: Segment[] = []
  for (const b of branches) {
    const steps = b.width > 2 ? 12 : 6
    for (let i = 0; i < steps; i++) {
      const start = pointOnPlan(b, i / steps)
      const end = pointOnPlan(b, (i + 1) / steps)
      const scaleX = form === 'columnar' ? .62 : 1
      const dx = (end[0] - start[0]) / 100 * scaleX
      const dy = (end[1] - start[1]) / 100
      const ax = start[0] / 100 * scaleX, ay = start[1] / 100
      const width = (b.width + (b.tip - b.width) * i / steps) / 200
      segments.push({
        left: Math.min(ax, ax + dx) - width,
        right: Math.max(ax, ax + dx) + width,
        top: Math.min(ay, ay + dy) - width,
        bottom: Math.max(ay, ay + dy) + width,
        ax: start[0] / 100 * scaleX,
        ay: start[1] / 100,
        dx, dy,
        inverseLength: 1 / (dx * dx + dy * dy),
        width,
        taper: (b.tip - b.width) / (200 * steps),
      })
    }
  }
  // Only segments within the encoded distance range can contribute to a texel.
  const bins = Array.from({ length: 256 }, (): Segment[] => [])
  const bin = (v: number) => Math.max(0, Math.min(15, Math.floor((v + 1) * 8)))
  for (const segment of segments) {
    const pad = .15
    const left = bin(Math.min(segment.ax, segment.ax + segment.dx) - pad)
    const right = bin(Math.max(segment.ax, segment.ax + segment.dx) + pad)
    const top = bin(Math.min(segment.ay, segment.ay + segment.dy) - pad)
    const bottom = bin(Math.max(segment.ay, segment.ay + segment.dy) + pad)
    for (let y = top; y <= bottom; y++) {
      for (let x = left; x <= right; x++) bins[y * 16 + x].push(segment)
    }
  }
  return (x: number, y: number) => {
    let edge = .125
    for (const segment of bins[bin(y) * 16 + bin(x)]) {
      if (x < segment.left - edge || x > segment.right + edge
        || y < segment.top - edge || y > segment.bottom + edge) continue
      const px = x - segment.ax, py = y - segment.ay
      const t = Math.max(0, Math.min(1, (px * segment.dx + py * segment.dy) * segment.inverseLength))
      const dx = px - t * segment.dx
      const dy = py - t * segment.dy
      const width = segment.width + t * segment.taper
      const squared = dx * dx + dy * dy
      if (squared < (edge + width) ** 2) edge = Math.min(edge, Math.sqrt(squared) - width)
    }
    return { edge, detail: 1 }
  }
}

const uprightCache = new Map<TreeForm, TreeShape[]>()
const planCache = new Map<TreeForm, ReturnType<typeof makePlan>>()

export function leaflessUpright(form: TreeForm): TreeShape[] {
  let shapes = uprightCache.get(form)
  if (!shapes) {
    shapes = makeUpright(form)
    uprightCache.set(form, shapes)
  }
  return shapes
}

export function leaflessCrown(form: TreeForm, x: number, y: number) {
  let sample = planCache.get(form)
  if (!sample) {
    sample = makePlan(form)
    planCache.set(form, sample)
  }
  return sample(x, y)
}
