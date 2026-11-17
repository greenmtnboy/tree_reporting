import type { TreeForm } from '../types'

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
 * The colour's alpha carries the tree's flags (1 measured, 2 notable) in its integer part and its
 * form and turn in its fraction: fraction * 128 = form * 8 + turn + 0.5. A float holds that to
 * well under a step, so the vertex shader reads all three back exactly.
 */
const SHAPE_SLOTS = 128

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

/** Vertex-shader GLSL: unpack the alpha into (flags, form, turn). */
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
const tri = (t: number) => 1 - Math.abs(2 * (t - Math.floor(t)) - 1)
const fract = (t: number) => t - Math.floor(t)

/**
 * The crown of `form` at (x, y) in its unit disc: `edge` is negative inside the outline (a radial
 * offset, not yet a true distance), `detail` the distance to the nearest interior stroke (fronds'
 * midribs, whorls, clumps). Every outline stays inside the unit disc and reaches it, so the crown
 * is still as wide as it is measured.
 */
export function crownShape(form: TreeForm, x: number, y: number): { edge: number; detail: number } {
  const r = Math.hypot(x, y), a = Math.atan2(y, x)
  const cos = Math.cos, abs = Math.abs
  switch (form) {
    case 'conifer': {
      // A straight-edged star of needle tips, a smaller whorl inside it a half step turned, the leader at its heart.
      const tip = 1 - tri(a * 11 / TAU), inner = 1 - tri(a * 11 / TAU + 0.5)
      return { edge: r - (0.8 + 0.2 * tip), detail: Math.min(abs(r - 0.52 * (0.78 + 0.22 * inner)), abs(r - 0.1)) }
    }
    case 'palm': {
      // Seven pointed fronds from a hub, each with its midrib.
      const t = fract(a * 7 / TAU)
      return { edge: r - (0.24 + 0.76 * tri(t) ** 1.2), detail: r > 0.1 ? r * Math.sin(abs(t - 0.5) * TAU / 7) : abs(r - 0.1) }
    }
    case 'columnar':
      // A tight round top, the column's crown a ring within it.
      return { edge: r - (0.94 + 0.06 * abs(cos(4.5 * a))), detail: abs(r - 0.46) }
    case 'ornamental': {
      // Flowering branches: an airy crown of round bunches about a centre, five branches out from
      // the trunk and blossoms along them.
      let edge = r - 0.5, detail = Infinity
      for (let i = 0; i < 7; i++) {
        const b = i * TAU / 7
        edge = Math.min(edge, Math.hypot(x - 0.64 * cos(b), y - 0.64 * Math.sin(b)) - 0.36)
      }
      for (let i = 0; i < 5; i++) {
        const b = i * TAU / 5 + 0.3, ux = cos(b), uy = Math.sin(b)
        // The branch: a segment from the trunk to 0.72 out.
        const t = Math.max(0, Math.min(0.72, x * ux + y * uy))
        detail = Math.min(detail, Math.hypot(x - t * ux, y - t * uy))
        // Blossoms: small rings at the branch's tip, along it, and off it either side.
        for (const [along, off] of [[0.74, 0], [0.45, 0.13], [0.5, -0.15], [0.25, 0.1]]) {
          const bx = along * ux - off * uy, by = along * uy + off * ux
          detail = Math.min(detail, abs(Math.hypot(x - bx, y - by) - 0.055))
        }
      }
      return { edge, detail }
    }
    case 'spreading':
      // Broad uneven lobes, inner masses of foliage in arcs.
      return {
        edge: r - (0.84 + 0.1 * abs(cos(1.5 * a + 0.4)) + 0.06 * abs(cos(4.5 * a))),
        detail: cos(2 * a + 0.8) > 0.2 ? abs(r - 0.6 * (0.86 + 0.14 * abs(cos(3.5 * a)))) : 1,
      }
    case 'weeping': {
      // A willow from above: a shaggy fringe, tiers of drooping branches hanging in scallops inside it.
      return {
        edge: r - (0.88 + 0.07 * abs(cos(11.5 * a)) + 0.05 * abs(cos(17.5 * a + 1))),
        detail: Math.min(abs(r - 0.66 * (0.93 + 0.07 * abs(cos(9.5 * a + 0.4)))), abs(r - 0.38 * (0.9 + 0.1 * abs(cos(5.5 * a + 1.1))))),
      }
    }
    case 'multi_trunk': {
      // Three leafy crowns overlapping, each one's lobed outline showing through, a stem under each.
      let edge = Infinity, detail = Infinity
      for (let i = 0; i < 3; i++) {
        const b = i * TAU / 3 + 0.5, cx = 0.44 * cos(b), cy = 0.44 * Math.sin(b)
        const rho = Math.hypot(x - cx, y - cy), phi = Math.atan2(y - cy, x - cx)
        const d = rho - 0.56 * (0.88 + 0.12 * abs(cos(3.5 * phi + i)))
        edge = Math.min(edge, d)
        detail = Math.min(detail, abs(d), abs(rho - 0.05))
      }
      return { edge, detail }
    }
    default:
      // Broadleaf and the rest: a billowing cloud of seven lobes, broken arcs of inner clumps.
      return {
        edge: r - (0.9 + 0.1 * abs(cos(3.5 * a))),
        detail: cos(3 * a + 0.5) > 0 ? abs(r - 0.55 * (0.88 + 0.12 * abs(cos(2.5 * a + 0.7)))) : 1,
      }
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
