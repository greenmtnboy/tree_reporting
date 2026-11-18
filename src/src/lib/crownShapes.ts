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
const willow = skyline([[0,.83,.98],[32,.81,.92],[55,.78,.96],[95,.8,.92],[126,.76,.98],[164,.83,1],[190,.8,.95],[213,.78,.96],[243,.85,.94],[269,.76,1],[303,.8,.93],[329,.75,.95]])
const generic = skyline([[0,.92,.99],[73,.88,.96],[139,.91,1],[200,.86,.95],[267,.90,.98],[322,.89,.96]])

// Open quadratic strokes in disc coordinates: start, control, end, then control/end
// pairs for a continuing line. Share short chords between nearby atlas texels.
function ink(...paths: number[][]) {
  type Chord = { x: number; y: number; dx: number; dy: number; inv: number }
  const bins = Array.from({ length: 64 }, (): Chord[] => [])
  const bin = (v: number) => Math.min(7, Math.max(0, Math.floor((v + 1) * 4)))
  const range = 0.14 // wider than the stroke channel's encoded distance
  for (const path of paths) {
    for (let i = 0; i + 5 < path.length; i += 4) {
      const [ax, ay, bx, by, cx, cy] = path.slice(i, i + 6)
      let x = ax, y = ay
      for (let j = 1; j <= 5; j++) {
        const t = j / 5, s = 1 - t
        const nx = s*s*ax + 2*s*t*bx + t*t*cx, ny = s*s*ay + 2*s*t*by + t*t*cy
        const dx = nx - x, dy = ny - y, chord = { x, y, dx, dy, inv: 1 / (dx*dx + dy*dy) }
        for (let row = bin(Math.min(y, ny) - range); row <= bin(Math.max(y, ny) + range); row++) {
          for (let col = bin(Math.min(x, nx) - range); col <= bin(Math.max(x, nx) + range); col++) bins[row*8 + col].push(chord)
        }
        x = nx; y = ny
      }
    }
  }
  return (x: number, y: number) => {
    let best = range * range
    for (const c of bins[bin(y)*8 + bin(x)]) {
      const px = x - c.x, py = y - c.y, t = Math.max(0, Math.min(1, (px*c.dx + py*c.dy)*c.inv))
      best = Math.min(best, (px - t*c.dx)**2 + (py - t*c.dy)**2)
    }
    return best < range * range ? Math.sqrt(best) : 1
  }
}

// Each quadratic starts at the trunk. The controls bend the bough as it
// grows outward. Secondary forks start on a parent limb.
const oakInk = ink([0,0, -.16,.26,-.53,.51], [0,0, .19,.16,.58,.26],
  [0,0, .11,-.23,.35,-.54], [0,0, -.26,-.05,-.59,-.30], [-.20,.27,-.33,.31,-.47,.30])
const poplarInk = ink([0,0,-.11,.17,-.18,.49], [0,0,.16,.08,.36,.34],
  [0,0,.11,-.20,.13,-.49], [0,0,-.18,-.08,-.34,-.31])
const spreadingInk = ink([0,0,-.39,.04,-.77,.28], [-.36,.09,-.53,.02,-.66,-.09],
  [0,0,.38,-.02,.77,-.31], [.36,-.08,.57,-.06,.68,.04],
  [-.36,.09,-.42,.28,-.47,.47], [0,0,-.05,-.28,-.27,-.53])
const willowInk = ink([0,0,-.43,.04,-.72,.32], [0,0,-.28,.25,-.28,.67],
  [0,0,.05,.34,.34,.61], [0,0,.37,.20,.70,.11],
  [0,0,.38,-.07,.58,-.42], [0,0,.15,-.32,.01,-.68], [0,0,-.25,-.24,-.64,-.30])
const multiInk = ink([0,0,-.04,.28,-.15,.68], [0,0,.33,-.20,.69,-.18],
  [0,0,-.23,-.25,-.64,-.34], [-.05,.35,.06,.48,.15,.55])
const genericInk = ink([0,0,-.13,.22,-.42,.39], [0,0,.19,.09,.50,.08], [0,0,-.13,-.17,-.16,-.42])

// A signed distance inside each five-petalled blossom gives the desktop its
// coloured petals and lets the phone draw the same flowers in one ink.
const blossomPetals = [
  [-.49,.22,.15], [-.13,.53,.11], [.35,.40,.15], [.53,-.09,.10],
  [.07,-.31,.16], [-.44,-.44,.11],
].flatMap(([x, y, radius], index) => Array.from({ length: 5 }, (_, petal) => {
  const angle = TAU * petal / 5 + index * .8
  return [x + radius * .60 * Math.cos(angle), y + radius * .60 * Math.sin(angle), radius * .44]
}))

const frondLengths = [.99,.94,1,.90,.98,.94,.98]
const firLengths = [1,.88,.96,.99,.89,.98,.92,1,.91,.96]

export function crownShape(form: TreeForm, x: number, y: number, appearance: TreeAppearance = 'leafy'): { edge: number; detail: number } {
  if (appearance === 'leafless') return leaflessCrown(form,x,y)
  const r = Math.sqrt(x*x + y*y), a = Math.atan2(y, x)
  switch (form) {
    case 'conifer': {
      // Long tapering sprays with narrow tips and daylight between boughs.
      const u = (a / TAU + 1) * 10 + .08 * Math.sin(3*a)
      const i = Math.round(u), flank = abs(u - i) * 2
      const length = firLengths[i % 10]
      const edge = r - (.49 + (length - .49) * (1 - flank) ** 1.45)
      const lateral = r * Math.sin((u - i) * TAU / 10)
      const rib = Math.hypot(lateral, Math.max(0, r - length + .11))
      return { edge, detail: rib }
    }
    case 'palm': {
      // Bow each midrib as it leaves the bud; a shallow cut or two breaks
      // the leaf's margin, without turning the compound frond into a comb.
      const u = (a / TAU + 1) * 7 - .46 - .25*r + .065*Math.sin(a*3)
      const i = Math.round(u), t = abs(u - i)*2, length = frondLengths[i % 7]
      const teeth = .045 * (1 - Math.cos(r * 29)) * Math.sin(Math.PI * Math.min(1, r))**2
      const edge = r - (.21 + (length - .21) * (1 - t) ** 1.12 - teeth * t)
      const rib = r * Math.sin((u - i) * TAU / 7)
      return { edge, detail: Math.hypot(rib, Math.max(.065 - r, r - length + .09, 0)) }
    }
    case 'columnar': return { edge: r - poplar(a), detail: poplarInk(x,y) }
    case 'ornamental': {
      let nearest = 1
      for (const [px, py, radius] of blossomPetals) {
        const dx = x - px, dy = y - py
        if (abs(dx) < .23 && abs(dy) < .23) nearest = Math.min(nearest, Math.sqrt(dx*dx + dy*dy) - radius)
      }
      return { edge: r - cherry(a), detail: nearest }
    }
    case 'spreading': {
      // The long scaffold extends northwest and southeast across the trunk.
      const radius = oakSpreading(a) * (1 - .16 * Math.sin(a + Math.PI / 4) ** 2)
      return { edge: r - radius, detail: spreadingInk(x,y) }
    }
    case 'weeping': return { edge: r - willow(a + .5*r) + .035 * (1 - Math.cos(19*a + 4*Math.sin(3*a) - 4*r)), detail: willowInk(x,y) }
    case 'multi_trunk': {
      // Three offset heads at different heights. The front head hides the
      // backs' contact line; there are no full circles drawn through foliage.
      const head = (cx: number, cy: number, radius: number, turn: number) => {
        const dx = x - cx, dy = y - cy
        return Math.sqrt(dx*dx + dy*dy) - radius * headProfile(Math.atan2(dy,dx) + turn)
      }
      const left = head(-.40,-.19,.555,.8), right = head(.39,-.17,.57,-.9), front = head(-.03,.36,.638,.764)
      const back = Math.min(left,right)
      return { edge: Math.min(front,back), detail: multiInk(x,y) }
    }
    case 'broadleaf': return { edge: r - broadleaf(a + Math.PI / 9), detail: oakInk(x,y) }
    default: return { edge: r - generic(a), detail: genericInk(x,y) }
  }
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
