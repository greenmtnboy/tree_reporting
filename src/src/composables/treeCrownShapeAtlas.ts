import type { TreeAppearance } from '../lib/leaflessArtwork'
import { CROWN_FORMS, crownShape, edgeOverGradient } from '../lib/crownShapes'

/** Texels across a crown's unit disc (its diameter). */
const IMAGE_SIZE = 128
const GUTTER = 4
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
/** The same for an appearance's atlas: the leafless one is twice as fine. */
export function crownTexelsPerUnit(appearance: TreeAppearance): number {
  return crownAtlasLayout(appearance).imageSize / 2
}
/**
 * Disc units each channel spans either side of its 0.5: the outline (R) and the strokes (G) fine,
 * for edges a pixel or two wide; the outline again (B) coarse, for a notable crown's rim a sixth
 * of the crown deep and for the inside of a crown too small on screen for the fine range.
 */
export const CROWN_FINE_RANGE = 0.125
export const CROWN_COARSE_RANGE = 0.5

function crownAtlasLayout(appearance: TreeAppearance) {
  // Fine bare twigs need twice the linear resolution of a filled canopy.
  const scale = appearance === 'leafless' ? 2 : 1
  const imageSize = IMAGE_SIZE * scale
  return { imageSize, cellSize: imageSize + GUTTER * 2, atlasSize: ATLAS_SIZE * scale }
}

/**
 * Where form `index`'s cell is: the texture coordinate of its disc's centre and of one disc unit,
 * so a vertex's coordinate is centre + corner * unit.
 */
export function crownCellGLSL(appearance: TreeAppearance = 'leafy', name = 'crownCellUV'): string {
  const { imageSize, cellSize, atlasSize } = crownAtlasLayout(appearance)
  const width = atlasSize, height = atlasSize
  return `
      vec2 ${name}(float form, vec2 corner) {
        float row = floor(form / ${CROWN_ATLAS_COLUMNS.toFixed(1)});
        vec2 centre = (vec2(form - row * ${CROWN_ATLAS_COLUMNS.toFixed(1)}, row) * ${cellSize.toFixed(1)} + ${(GUTTER + imageSize / 2).toFixed(1)}) / vec2(${width.toFixed(1)}, ${height.toFixed(1)});
        return centre + corner * vec2(${(imageSize / 2 / width).toFixed(8)}, ${(-imageSize / 2 / height).toFixed(8)});
      }`
}

const atlases = new Map<TreeAppearance, ImageData>()
const pending = new Map<TreeAppearance, Promise<ImageData>>()

/**
 * The overhead crowns, one cell a form: RGB distance channels and, in alpha, the desktop canopy's
 * foliage-mass shading (see FOLIAGE_SHADE). Raw pixels, never a canvas: a canvas would premultiply
 * the colour channels by that alpha and spoil the distances.
 */
export function createCrownShapeAtlas(appearance: TreeAppearance = 'leafy'): ImageData {
  let atlas = atlases.get(appearance)
  if (!atlas) {
    const bake = baker(appearance)
    drain(bake.rows())
    atlas = bake.pixels
    atlases.set(appearance, atlas)
  }
  return atlas
}

/** The atlas once built, else undefined. */
export function crownShapeAtlas(appearance: TreeAppearance = 'leafy'): ImageData | undefined {
  return atlases.get(appearance)
}

/**
 * The milliseconds the next idle moment offers. A page that is never idle (a map kept moving, a
 * slow software GL) still gets a short slice every few frames, so the bake finishes.
 */
function idle(): Promise<number> {
  return new Promise(resolve => {
    if (typeof requestIdleCallback === 'function') {
      requestIdleCallback(deadline => resolve(deadline.didTimeout ? 8 : Math.max(deadline.timeRemaining(), 4)), { timeout: 40 })
    } else setTimeout(() => resolve(8), 0)
  })
}

function drain(work: Iterator<unknown>) {
  while (!work.next().done);
}

/**
 * Run a bake a row at a time in the page's idle moments, each slice within the time the browser
 * offers: a leafless atlas is a second of a slow phone's main thread, which in one piece (or a
 * form's worth) would hold up a tap.
 */
export async function bakeWhenIdle(work: Iterator<unknown>): Promise<void> {
  for (;;) {
    const end = performance.now() + await idle()
    do if (work.next().done) return
    while (performance.now() < end)
  }
}

/**
 * The atlas, built when the page is idle, one form a slice: the leafy one is tens of milliseconds
 * of a phone's main thread (a few on a desktop), the leafless one at twice the resolution about
 * four times that, and neither should land in one frame or on the map's first load. Until then
 * the crowns are plain discs (bare ones, their leafy shape).
 */
export function loadCrownShapeAtlas(appearance: TreeAppearance = 'leafy'): Promise<ImageData> {
  let request = pending.get(appearance)
  if (!request) {
    request = (async () => {
      const done = atlases.get(appearance)
      if (done) return done
      const bake = baker(appearance)
      await bakeWhenIdle(bake.rows())
      if (!atlases.has(appearance)) atlases.set(appearance, bake.pixels)
      return atlases.get(appearance)!
    })()
    pending.set(appearance, request)
  }
  return request
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

/** An atlas's pixels, and how to bake one form's cell into them. */
function baker(appearance: TreeAppearance) {
  const { imageSize, cellSize, atlasSize } = crownAtlasLayout(appearance)
  const pixels = new ImageData(atlasSize, atlasSize)
  // One shape evaluation a texel, on a grid a texel wider each way than the cell and its gutters,
  // so the outline's gradient comes from its neighbours.
  const span = imageSize + GUTTER * 2 + 2, step = 2 / imageSize
  const edge = new Float64Array(span * span), detail = new Float64Array(span * span)
  // Every form's cell, a row of shape evaluations a step.
  function* rows() {
    for (let index = 0; index < CROWN_FORMS.length; index++) yield* cell(index)
  }
  function* cell(index: number) {
    const form = CROWN_FORMS[index]
    for (let j = 0; j < span; j++) {
      yield
      for (let i = 0; i < span; i++) {
        // Disc coordinates of the texel's centre, y up as the ground's north.
        const shape = crownShape(form, (i - GUTTER - 1 + 0.5) * step - 1, 1 - (j - GUTTER - 1 + 0.5) * step, appearance)
        edge[j * span + i] = shape.edge
        detail[j * span + i] = shape.detail
      }
    }
    const left = (index % CROWN_ATLAS_COLUMNS) * cellSize, top = Math.floor(index / CROWN_ATLAS_COLUMNS) * cellSize
    // Texel centres, gutters included, so bilinear sampling at a cell's edge reads its own crown.
    for (let y = 0; y < cellSize; y++) {
      for (let x = 0; x < cellSize; x++) {
        const k = (y + 1) * span + x + 1
        const distance = edgeOverGradient(edge[k], (edge[k + 1] - edge[k - 1]) / (2 * step), (edge[k - span] - edge[k + span]) / (2 * step))
        const offset = ((top + y) * atlasSize + left + x) * 4
        pixels.data[offset] = encode(distance, CROWN_FINE_RANGE)
        pixels.data[offset + 1] = encode(detail[k], CROWN_FINE_RANGE)
        pixels.data[offset + 2] = encode(distance, CROWN_COARSE_RANGE)
        pixels.data[offset + 3] = Math.round(255 * foliageShade((left + x + 0.5) / atlasSize, (top + y + 0.5) / atlasSize) / FOLIAGE_SHADE)
      }
    }
  }
  return { pixels, rows }
}
