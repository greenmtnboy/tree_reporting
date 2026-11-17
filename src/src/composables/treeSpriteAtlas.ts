import { paintUprightShade, treeArtwork, treeShapeDistance, type TreeShape } from '../lib/treeArtwork'
import { CROWN_FORMS } from '../lib/crownShapes'
import type { TreeForm } from '../types'

const IMAGE_SIZE = 192
const GUTTER = 4
const CELL = IMAGE_SIZE + GUTTER * 2
const COLUMNS = 3
const DISTANCE_RANGE = 8 / IMAGE_SIZE
/** The atlas cells' order is the vertex's form index (lib/crownShapes). */
export const SPRITE_FORMS: TreeForm[] = CROWN_FORMS

export interface SpriteCell {
  left: number
  right: number
  top: number
  bottom: number
  crownFraction: number
  /** Foot to crown top, as a fraction of the cell's height. */
  heightFraction: number
  base: number
  alpha: Uint8Array
  size: number
}

let cachedAtlas: ReturnType<typeof buildAtlas> | undefined

/** One atlas stores trunk, foliage and bloom distances in RGB and the foliage value in alpha. */
export function createTreeSpriteAtlas() {
  return cachedAtlas ??= buildAtlas()
}

function buildAtlas() {
  const distance = new ImageData(CELL * COLUMNS, CELL * Math.ceil(SPRITE_FORMS.length / COLUMNS))
  const cells = new Map<string, SpriteCell>()
  const wash = document.createElement('canvas')
  wash.width = wash.height = IMAGE_SIZE
  const ctx = wash.getContext('2d', { willReadFrequently: true })!
  const union = (shapes: TreeShape[], x: number, y: number) => {
    let d = Infinity
    for (const shape of shapes) d = Math.min(d, treeShapeDistance(shape, x, y, DISTANCE_RANGE))
    return d
  }
  SPRITE_FORMS.forEach((form, index) => {
    const artwork = treeArtwork(form)
    ctx.fillStyle = 'white'
    ctx.fillRect(0, 0, IMAGE_SIZE, IMAGE_SIZE)
    paintUprightShade(ctx, form, IMAGE_SIZE)
    const shade = ctx.getImageData(0, 0, IMAGE_SIZE, IMAGE_SIZE).data
    const left = (index % COLUMNS) * CELL + GUTTER
    const top = Math.floor(index / COLUMNS) * CELL + GUTTER
    const alpha = new Uint8Array(IMAGE_SIZE * IMAGE_SIZE)
    // Evaluate vector distances at texel centers, including the gutters, so
    // bilinear sampling cannot pull in a neighbouring sprite at the cell edge.
    for (let y = -GUTTER; y < IMAGE_SIZE + GUTTER; y++) {
      for (let x = -GUTTER; x < IMAGE_SIZE + GUTTER; x++) {
        const px = (x + 0.5) / IMAGE_SIZE, py = (y + 0.5) / IMAGE_SIZE
        const distances = [union(artwork.trunk, px, py), union(artwork.foliage, px, py), union(artwork.blooms, px, py)]
        const offset = ((top + y) * distance.width + left + x) * 4
        for (let channel = 0; channel < 3; channel++) {
          distance.data[offset + channel] = Math.round(255 * Math.max(0, Math.min(1, 0.5 - distances[channel] / (2 * DISTANCE_RANGE))))
        }
        // Raw ImageData preserves the three distance channels independently of alpha.
        distance.data[offset + 3] = shade[4 * (Math.max(0, Math.min(IMAGE_SIZE - 1, y)) * IMAGE_SIZE
          + Math.max(0, Math.min(IMAGE_SIZE - 1, x)))]
        if (x >= 0 && x < IMAGE_SIZE && y >= 0 && y < IMAGE_SIZE) {
          alpha[y * IMAGE_SIZE + x] = Math.min(...distances) <= 0 ? 255 : 0
        }
      }
    }
    const base = form === 'multi_trunk' ? 0.9 : 0.95
    cells.set(form, {
      left: left / distance.width, right: (left + IMAGE_SIZE) / distance.width,
      top: top / distance.height, bottom: (top + IMAGE_SIZE) / distance.height,
      crownFraction: artwork.crownFraction,
      heightFraction: base - artwork.crownTop,
      base,
      alpha, size: IMAGE_SIZE,
    })
  })
  // How fast a channel changes per unit of texture coordinate, an edge's distance field having a
  // slope of one: the shader's antialiasing width from the coordinates' own derivatives.
  const distanceScale = [distance.width / IMAGE_SIZE / (2 * DISTANCE_RANGE), distance.height / IMAGE_SIZE / (2 * DISTANCE_RANGE)]
  return { distance, cells, distanceScale }
}
