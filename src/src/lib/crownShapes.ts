import type { TreeForm } from '../types'
import { leaflessCrown, type TreeAppearance } from './leaflessArtwork'

/**
 * A crown seen from overhead, drawn by its form: a palm's fronds, a conifer's star of whorls, a
 * broadleaf's billowing lobes. The shapes are baked once into a small distance-field texture
 * (composables/treeCrownShapeAtlas.ts) that the phone's ground rings and the desktop's overhead
 * crowns both read: one texture sample a pixel, as the desktop's side sprites already take. Each
 * tree carries its form and one of eight turns of the drawing in its colour's alpha (below), so
 * the vertex buffer gains nothing and neighbours of one form are not drawn alike.
 */

/** The forms, in the sprite atlases' order; an index into it is what a vertex carries. */
export const CROWN_FORMS: TreeForm[] = ['broadleaf', 'conifer', 'palm', 'columnar', 'ornamental', 'spreading', 'weeping', 'multi_trunk', 'default']
const DEFAULT_FORM = CROWN_FORMS.indexOf('default')
const FORM_INDEX = new Map<string, number>(CROWN_FORMS.map((form, i) => [form, i]))
/** Quarter turns and their mirror images: they map the crown's square onto itself, so no turn samples outside its cell. */
export const CROWN_TURNS = 8

/**
 * A bare (leafless) crown's shape index: its form's, past the leafy forms. A deciduous tree in
 * its winter draws from the leafless atlases (lib/leaflessArtwork) under the same index arithmetic.
 */
export const LEAFLESS_OFFSET = CROWN_FORMS.length

/**
 * The colour's alpha carries the tree's flags (1 measured, 2 notable) in its integer part and its
 * shape and turn in its fraction: fraction * 256 = shape * 8 + turn + 0.5, the shape being the
 * form index plus LEAFLESS_OFFSET when bare. A float holds that to well under a step, so the
 * vertex shader reads all three back exactly.
 */
const SHAPE_SLOTS = 256

export function crownFormIndex(category: string): number {
  return FORM_INDEX.get(category) ?? DEFAULT_FORM
}

/** One of CROWN_TURNS, fixed by where the tree stands, so a tree keeps its turn across batches. */
export function crownTurn(lng: number, lat: number): number {
  const x = Math.sin(lng * 12.9898 + lat * 78.233) * 43758.5453
  return Math.floor((x - Math.floor(x)) * CROWN_TURNS)
}

export function packCrownAlpha(flags: number, form: number, turn: number): number {
  return flags + (form * CROWN_TURNS + turn + 0.5) / SHAPE_SLOTS
}

export function unpackCrownAlpha(alpha: number): { flags: number; form: number; turn: number } {
  const flags = Math.floor(alpha), slot = Math.floor((alpha - flags) * SHAPE_SLOTS)
  return { flags, form: Math.floor(slot / CROWN_TURNS), turn: slot % CROWN_TURNS }
}

/** Vertex-shader GLSL: unpack the alpha into (flags, shape, turn); a shape at or past LEAFLESS_OFFSET is bare. */
export const CROWN_UNPACK_GLSL = `
      vec3 unpackCrown(float alpha) {
        float flags = floor(alpha);
        float slot = floor((alpha - flags) * ${SHAPE_SLOTS.toFixed(1)});
        float form = floor(slot / ${CROWN_TURNS.toFixed(1)});
        return vec3(flags, form, slot - form * ${CROWN_TURNS.toFixed(1)});
      }
      // The corner turned by one of the eight symmetries of the square: a mirror, then quarter turns.
      vec2 turnCrown(vec2 corner, float turn) {
        float quarters = floor(turn * 0.5);
        vec2 p = vec2(turn - quarters * 2.0 > 0.5 ? -corner.x : corner.x, corner.y);
        // cos and sin of the quarter turns 0..3: 1 0 -1 0 and 0 1 0 -1.
        float c = abs(quarters - 2.0) - 1.0, s = 1.0 - abs(quarters - 1.0);
        return vec2(c * p.x - s * p.y, s * p.x + c * p.y);
      }`

const TAU = Math.PI * 2
const abs = Math.abs
type Lobe = [angle: number, notch: number, crest: number]

/** Unequal boughs around the skyline. Each notch rolls up to a rounded shoulder,
 * then down to the next notch. Sample the periodic profile once, not per texel. */
function skyline(lobes: Lobe[]) {
  const n = 720, radii = new Float64Array(n + 1)
  let i = 0
  for (let j = 0; j < n; j++) {
    const deg = 360 * j / n
    if (i + 1 < lobes.length && deg >= lobes[i + 1][0]) i++
    const [start, low, high] = lobes[i], next = lobes[(i + 1) % lobes.length]
    const t = (deg - start) / ((next[0] || 360) - start)
    const floor = low + (next[1] - low) * t
    radii[j] = floor + (high - floor) * Math.sin(Math.PI * t) ** 0.72
  }
  radii[n] = radii[0]
  return (a: number) => {
    const u = (a / TAU - Math.floor(a / TAU)) * n, k = Math.floor(u)
    return radii[k] + (radii[k + 1] - radii[k]) * (u - k)
  }
}

const headProfile = skyline([[0,.90,1],[32,.85,.97],[70,.83,.96],[115,.89,1],[154,.83,.98],[191,.87,.96],[218,.77,.9],[263,.86,.99],[303,.82,.96],[334,.86,.97]])
const broadleaf = skyline([[0,.88,1],[40,.84,.96],[68,.87,.92],[88,.83,1],[132,.85,.97],[165,.82,.95],[180,.88,1],[220,.81,.91],[247,.85,.95],[268,.82,1],[312,.84,.98],[342,.87,.94]])
const poplar = skyline([[0,.91,.98],[58,.86,.93],[106,.88,1],[164,.90,.97],[231,.86,.96],[283,.88,.94]])
const cherry = skyline([[0,.84,.94],[25,.82,1],[53,.84,.90],[70,.62,.85],[88,.79,.98],[125,.81,.90],[147,.75,.98],[176,.87,1],[202,.83,.91],[224,.66,.83],[241,.76,.97],[279,.81,.93],[304,.74,.88],[320,.79,.97]])
const oakSpreading = skyline([[0,.91,.97],[34,.83,.87],[68,.80,.96],[102,.81,.93],[134,.89,1],[160,.96,.99],[192,.89,.91],[223,.78,.83],[258,.79,.96],[287,.83,.95],[318,.91,1],[348,.94,.96]])
const generic = skyline([[0,.92,.99],[73,.88,.96],[139,.91,1],[200,.86,.95],[267,.90,.98],[322,.89,.96]])


/**
 * Flat tones, as cut paper stacked by height: a crown is a few layers of one colour, the lowest
 * the deepest. `tone` is how far a layer sits below the top one (0 the top, 1 the lowest); it has
 * no light direction, so a crown reads the same under any of its turns.
 */
const TONE_MID = 0.36, TONE_DEEP = 0.72

/** One whorl of a conifer seen from above: straight-edged boughs, each a saw of forward twigs. */
type Whorl = { boughs: [cos: number, sin: number, length: number][]; width: number; core: number; teeth: number }
function whorl(lengths: number[], phase: number, width: number, core: number, teeth: number): Whorl {
  const lean = [0, .13, -.10, .08, -.07, .11, -.12, .05, -.04]
  return { width, core, teeth, boughs: lengths.map((length, i) => {
    const angle = TAU * (i + phase + lean[i % lean.length]) / lengths.length
    return [Math.cos(angle), Math.sin(angle), length]
  }) }
}
function whorlDistance(w: Whorl, x: number, y: number, r: number): number {
  let d = r - w.core
  for (const [c, s, length] of w.boughs) {
    const along = x*c + y*s
    if (along <= 0) continue
    const lateral = abs(y*c - x*s)
    if (along >= length) { d = Math.min(d, Math.hypot(along - length, lateral)); continue }
    // The width tapers straight to the tip; each tooth widens outward, then steps back.
    const t = along / length, tooth = t * w.teeth
    d = Math.min(d, lateral - w.width * (1 - t) * (.8 + .2 * (tooth - Math.floor(tooth))))
  }
  return d
}
const firWhorls = [
  whorl([.985,.88,.96,.90,.985,.86,.95,.91,.97], 0, .55, .62, 5),
  whorl([.70,.62,.68,.60,.69,.63,.66], .5, .43, .40, 5),
  whorl([.40,.34,.39,.33,.37], .25, .28, .19, 5),
]

// A bough mass seen from above is a scalloped ellipse lying along its bough:
// [x, y, half-length, half-breadth, direction, turn of the scalloped profile].
/** How much of a texel a layer covers, from its signed distance: a tone step a texel wide, not a stair. */
const cover = (d: number) => Math.max(0, Math.min(1, .5 - d * 64))
const layerTone = (mid: number, top: number, depth = 1) => depth * (TONE_DEEP + (TONE_MID - TONE_DEEP) * cover(mid)) * (1 - cover(top))
function pads(list: number[][], x: number, y: number): number {
  let d = 1
  for (const [cx, cy, long, broad, direction, turn] of list) {
    const dx = x - cx, dy = y - cy
    if (abs(dx) > long + .12 || abs(dy) > long + .12) continue
    const c = Math.cos(direction), s = Math.sin(direction)
    const u = (c*dx + s*dy) / long, v = (c*dy - s*dx) / broad, rho = Math.sqrt(u*u + v*v) || 1e-6
    // Scale the ellipse's own radius back to a distance in the disc, so a stroke keeps one width.
    d = Math.min(d, (rho - headProfile(Math.atan2(v, u) + turn)) * rho / Math.hypot(u / long, v / broad))
  }
  return d
}

// A spreading crown's masses along its long scaffold, then one oval at its peak, lying along it.
const oakMid = [[-.42,.42,.44,.34,2.36,.3], [.42,-.42,.44,.34,-.79,1.9], [0,0,.42,.36,.6,.9], [.30,.30,.30,.26,.8,2.6], [-.32,-.28,.30,.26,-2.3,4.1]]
const oakTop = [[0,0,.30,.20,2.36,.4]]

/**
 * A mound's two inner contours, as an elevation map draws them at equal steps of height. A crown
 * is a dome, steep at its rim and flat on top, so the middle contour lies near the outline and the
 * top one well in from it. The middle one is the outline itself shrunk (so it can never cross it);
 * the top one is turned a little. `mid` and `top` are the rings' sizes against the mound's radius.
 */
function mound(profile: (a: number) => number, dx: number, dy: number, radius = 1, turn = 0, mid = .8, top = .46): [number, number] {
  const r = Math.sqrt(dx*dx + dy*dy), a = Math.atan2(dy, dx) + turn
  return [r - radius * mid * profile(a), r - radius * top * profile(a + 2.3)]
}
const contoured = (edge: number, [mid, top]: [number, number]) => ({ edge, detail: Math.min(abs(mid), abs(top)), tone: layerTone(mid, top) })

// A palm's fronds fan straight out from the bud: [cos, sin, length], unevenly spaced. Every other
// one is of the lower tier, which the upper fronds lie over.
const palmFronds = [.99,.92,.97,.90,.985,.93,.96,.91].map((length, i) => {
  const angle = TAU * (i + [0,.12,-.08,.10,-.12,.06,-.10,.08][i]) / 8 + .2
  return [Math.cos(angle), Math.sin(angle), length]
})
const FROND_HALF_WIDTH = 0.19, LEAFLET_PITCH = 0.085, LEAFLET_SWEEP = 0.7
/**
 * One frond, a feather: leaflets packed along the midrib from just outside the bud, sweeping toward the
 * tip, longest two fifths of the way out. Each leaflet narrows to a point, so slits open between
 * them toward the frond's edge. Negative inside; `along` and `lateral` are the frond's own axes.
 */
function frond(along: number, lateral: number, length: number): number {
  // Where this point's leaflet leaves the midrib.
  const root = along - LEAFLET_SWEEP * lateral, t = root / length
  if (t < .05 || t > 1) return Math.hypot(Math.max(.05 * length - root, root - length, 0), lateral) + .01
  const reach = FROND_HALF_WIDTH * (t < .4 ? ((t - .05) / .35) ** .6 : 1 - ((t - .4) / .6) ** 1.6)
  const across = root / LEAFLET_PITCH, offset = abs(across - Math.floor(across) - .5)
  const out = lateral / reach
  return Math.max(lateral - reach, (offset - .5 * (1 - .8 * out ** 1.5)) * LEAFLET_PITCH * .8)
}

// A weeping crown: a dome whose rim hangs in rounded, uneven bulges (never points).
const mop = skyline([[0,.84,1],[31,.82,.96],[66,.85,.99],[97,.81,.95],[131,.84,1],[166,.83,.97],[199,.80,.96],[232,.85,1],[263,.82,.96],[296,.84,.99],[329,.81,.97]])

// A tree in flower: blossoms scattered over the whole crown, evenly but in no row or ring.
const halton = (i: number, base: number) => {
  let f = 1, value = 0
  for (; i > 0; i = Math.floor(i / base)) { f /= base; value += f * (i % base) }
  return value
}
const blossoms: number[][] = []
for (let i = 1; blossoms.length < 24 && i < 200; i++) {
  const x = 2 * halton(i, 2) - 1, y = 2 * halton(i, 3) - 1, radius = [.068, .05, .06, .046, .064][i % 5]
  // Wholly inside the crown, its notches included.
  const reach = Math.hypot(x, y) + radius + .05, at = Math.atan2(y, x)
  if ([-.2, -.1, 0, .1, .2].every(da => reach < cherry(at + da)) && blossoms.every(([bx, by, br]) => Math.hypot(x - bx, y - by) > (radius + br) * 1.35)) blossoms.push([x, y, radius])
}

export function crownShape(form: TreeForm, x: number, y: number, appearance: TreeAppearance = 'leafy'): { edge: number; detail: number; tone?: number } {
  if (appearance === 'leafless') return leaflessCrown(form,x,y)
  const r = Math.sqrt(x*x + y*y), a = Math.atan2(y, x)
  switch (form) {
    case 'conifer': {
      // Three whorls, the widest lowest; each casts no shadow, it is only a deeper tone.
      const low = whorlDistance(firWhorls[0], x, y, r), mid = whorlDistance(firWhorls[1], x, y, r), top = whorlDistance(firWhorls[2], x, y, r)
      return { edge: Math.min(low, mid, top), detail: Math.min(abs(mid), abs(top)), tone: layerTone(mid, top, .8) }
    }
    case 'palm': {
      // Two tiers of four fronds, the upper in the plain colour over the lower in a deeper one.
      // The midribs are the only strokes.
      let upper = r - .07, lower = 1, rib = 1
      palmFronds.forEach(([c, s, length], i) => {
        const along = x*c + y*s
        if (along <= 0) return
        const lateral = abs(y*c - x*s), d = frond(along, lateral, length)
        if (i % 2) lower = Math.min(lower, d); else upper = Math.min(upper, d)
        rib = Math.min(rib, Math.hypot(lateral, Math.max(0, along - length)))
      })
      return { edge: Math.min(upper, lower), detail: rib, tone: TONE_MID * (1 - cover(upper)) }
    }
    case 'columnar': return contoured(r - poplar(a), mound(poplar, x, y))
    case 'ornamental': {
      // The petals' signed distance is the detail: the desktop colours inside it, the phone inks its rim.
      let nearest = 1
      for (const [px, py, radius] of blossoms) {
        const dx = x - px, dy = y - py
        if (abs(dx) < .2 && abs(dy) < .2) nearest = Math.min(nearest, Math.sqrt(dx*dx + dy*dy) - radius)
      }
      const [mid, top] = mound(cherry, x, y, 1, 0, .78, .42)
      return { edge: r - cherry(a), detail: nearest, tone: layerTone(mid, top) }
    }
    case 'spreading':
      // The long scaffold extends northwest and southeast across the trunk.
      return clumps(r - oakSpreading(a) * (1 - .16 * Math.sin(a + Math.PI / 4) ** 2), oakMid, oakTop, x, y)
    case 'weeping': return contoured(r - mop(a), mound(mop, x, y))
    case 'multi_trunk': {
      // Three offset heads, each a mound with its own peak. Where two heads meet their middle
      // contours join, as contours do across a saddle; no rim is drawn where one head overlaps another.
      let edge = 1, mid = 1, top = 1
      for (const [cx, cy, radius, turn] of [[-.40,-.19,.555,.8], [.39,-.17,.57,-.9], [-.03,.36,.638,.764]]) {
        const dx = x - cx, dy = y - cy
        edge = Math.min(edge, Math.sqrt(dx*dx + dy*dy) - radius * headProfile(Math.atan2(dy,dx) + turn))
        const rings = mound(headProfile, dx, dy, radius, turn, .72, .36)
        mid = Math.min(mid, rings[0]); top = Math.min(top, rings[1])
      }
      return contoured(edge, [mid, top])
    }
    case 'broadleaf': return contoured(r - broadleaf(a + Math.PI / 9), mound(broadleaf, x, y, 1, Math.PI / 9))
    default: return contoured(r - generic(a), mound(generic, x, y))
  }
}

/** A crown in three flat tones: its outline, the masses lying on it, and the highest of those. */
function clumps(edge: number, mid: number[][], top: number[][], x: number, y: number) {
  const m = pads(mid, x, y), t = pads(top, x, y)
  return { edge, detail: Math.min(abs(m), abs(t)), tone: layerTone(m, t) }
}

/**
 * The outline's signed distance in disc units: the radial offset over its gradient, so the
 * outline drawn from it is one width round a lobe's steep flank too. At a cusp the finite step
 * straddles two lobes; past 4 the gradient is that, not the slope.
 */
export function edgeOverGradient(edge: number, gx: number, gy: number): number {
  return edge / Math.min(4, Math.max(1, Math.hypot(gx, gy)))
}

/** crownShape's edge as a distance, its gradient by central differences `h` apart. */
export function crownEdgeDistance(form: TreeForm, x: number, y: number, h = 1e-3): number {
  const e = (dx: number, dy: number) => crownShape(form, x + dx, y + dy).edge
  return edgeOverGradient(e(0, 0), (e(h, 0) - e(-h, 0)) / (2 * h), (e(0, h) - e(0, -h)) / (2 * h))
}
