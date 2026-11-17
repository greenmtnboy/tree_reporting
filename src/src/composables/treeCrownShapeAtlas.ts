import { CROWN_FORMS, crownShape, edgeOverGradient } from '../lib/crownShapes'

/** Texels across a crown's unit disc (its diameter). */
const IMAGE_SIZE = 128
const GUTTER = 4
const CELL = IMAGE_SIZE + GUTTER * 2
export const CROWN_ATLAS_COLUMNS = 3
export const CROWN_ATLAS_ROWS = Math.ceil(CROWN_FORMS.length / CROWN_ATLAS_COLUMNS)
/**
 * A power of two each way, so WebGL 1 can mipmap it too: a crown a few pixels across reads a
 * level its own size, not 128 texels scattered through the cache. Mipmapping blurs the cells into
 * each other only at their corners, outside every crown, where all of them are blank.
 */
const ATLAS_SIZE = 512
/** Texels per disc unit: the shader's level of detail from its pixels' size in disc units. */
export const CROWN_TEXELS_PER_UNIT = IMAGE_SIZE / 2
/**
 * Disc units each channel spans either side of its 0.5: the outline (R) and the strokes (G) fine,
 * for edges a pixel or two wide; the outline again (B) coarse, for a notable crown's rim a sixth
 * of the crown deep and for the inside of a crown too small on screen for the fine range.
 */
export const CROWN_FINE_RANGE = 0.125
export const CROWN_COARSE_RANGE = 0.5

/**
 * Where form `index`'s cell is: the texture coordinate of its disc's centre and of one disc unit,
 * so a vertex's coordinate is centre + corner * unit.
 */
export function crownCellGLSL(): string {
  const width = ATLAS_SIZE, height = ATLAS_SIZE
  return `
      vec2 crownCellUV(float form, vec2 corner) {
        float row = floor(form / ${CROWN_ATLAS_COLUMNS.toFixed(1)});
        vec2 centre = (vec2(form - row * ${CROWN_ATLAS_COLUMNS.toFixed(1)}, row) * ${CELL.toFixed(1)} + ${(GUTTER + IMAGE_SIZE / 2).toFixed(1)}) / vec2(${width.toFixed(1)}, ${height.toFixed(1)});
        return centre + corner * vec2(${(IMAGE_SIZE / 2 / width).toFixed(8)}, ${(-IMAGE_SIZE / 2 / height).toFixed(8)});
      }`
}

let cached: ImageData | undefined
let pending: Promise<ImageData> | undefined

/**
 * The overhead crowns, one cell a form: RGB distance channels and, in alpha, the desktop canopy's
 * foliage-mass shading (see FOLIAGE_SHADE). Raw pixels, never a canvas: a canvas would premultiply
 * the colour channels by that alpha and spoil the distances.
 */
export function createCrownShapeAtlas(): ImageData {
  return cached ??= build()
}

/** The atlas once built, else undefined. */
export function crownShapeAtlas(): ImageData | undefined {
  return cached
}

/**
 * The atlas, built when the page is next idle: tens of milliseconds of a phone's main thread
 * (a few on a desktop) that stay off the map's first load. Until then the crowns are plain discs.
 */
export function loadCrownShapeAtlas(): Promise<ImageData> {
  return pending ??= new Promise(resolve => {
    const run = () => resolve(createCrownShapeAtlas())
    if (typeof requestIdleCallback === 'function') requestIdleCallback(run, { timeout: 2000 })
    else setTimeout(run, 0)
  })
}

/** The deepest the foliage-mass shading darkens the canopy; alpha 255 is this much. */
export const FOLIAGE_SHADE = 0.08

const smoothstep = (a: number, b: number, x: number) => {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

/**
 * Overlapping foliage masses that break up a large crown of one colour: two offset scales, so no
 * row of tufts repeats. A function of the atlas coordinate alone, so it is baked here rather than
 * evaluated (four trigonometric calls) for every pixel of every overhead crown.
 */
function foliageShade(u: number, v: number): number {
  const px = u * 32, py = v * 32
  const qx = px + Math.sin(py * 1.4 + px) * 0.55, qy = py + Math.sin(px * 1.7 - py) * 0.55
  const mass = Math.cos(qx * 1.8 + qy) + Math.cos(qy * 2.2 - qx * 0.6)
  const leafPad = smoothstep(-0.45, 0.25, mass)
  const under = smoothstep(-0.85, -0.45, mass) * (1 - leafPad)
  return 0.055 * (1 - leafPad) + 0.025 * under
}

function encode(distance: number, range: number): number {
  return Math.round(255 * Math.max(0, Math.min(1, 0.5 - distance / (2 * range))))
}

function build(): ImageData {
  const pixels = new ImageData(ATLAS_SIZE, ATLAS_SIZE)
  // One shape evaluation a texel, on a grid a texel wider each way than the cell and its gutters,
  // so the outline's gradient comes from its neighbours.
  const span = IMAGE_SIZE + GUTTER * 2 + 2, step = 2 / IMAGE_SIZE
  const edge = new Float64Array(span * span), detail = new Float64Array(span * span)
  CROWN_FORMS.forEach((form, index) => {
    for (let j = 0; j < span; j++) {
      for (let i = 0; i < span; i++) {
        // Disc coordinates of the texel's centre, y up as the ground's north.
        const shape = crownShape(form, (i - GUTTER - 1 + 0.5) * step - 1, 1 - (j - GUTTER - 1 + 0.5) * step)
        edge[j * span + i] = shape.edge
        detail[j * span + i] = shape.detail
      }
    }
    const left = (index % CROWN_ATLAS_COLUMNS) * CELL, top = Math.floor(index / CROWN_ATLAS_COLUMNS) * CELL
    // Texel centres, gutters included, so bilinear sampling at a cell's edge reads its own crown.
    for (let y = 0; y < CELL; y++) {
      for (let x = 0; x < CELL; x++) {
        const k = (y + 1) * span + x + 1
        const distance = edgeOverGradient(edge[k], (edge[k + 1] - edge[k - 1]) / (2 * step), (edge[k - span] - edge[k + span]) / (2 * step))
        const offset = ((top + y) * ATLAS_SIZE + left + x) * 4
        pixels.data[offset] = encode(distance, CROWN_FINE_RANGE)
        pixels.data[offset + 1] = encode(detail[k], CROWN_FINE_RANGE)
        pixels.data[offset + 2] = encode(distance, CROWN_COARSE_RANGE)
        pixels.data[offset + 3] = Math.round(255 * foliageShade((left + x + 0.5) / ATLAS_SIZE, (top + y + 0.5) / ATLAS_SIZE) / FOLIAGE_SHADE)
      }
    }
  })
  return pixels
}
