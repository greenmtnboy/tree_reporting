import { treeArtwork, treeShapeDistance, type TreeShape } from '../lib/treeArtwork'
import type { TreeForm } from '../types'

const IMAGE_SIZE = 192
const GUTTER = 4
const CELL = IMAGE_SIZE + GUTTER * 2
const COLUMNS = 3
const DISTANCE_RANGE = 8 / IMAGE_SIZE
export const SPRITE_FORMS: TreeForm[] = ['broadleaf', 'conifer', 'palm', 'columnar', 'ornamental', 'spreading', 'weeping', 'multi_trunk', 'default']

export interface SpriteCell {
  left: number
  right: number
  top: number
  bottom: number
  crownFraction: number
  base: number
  alpha: Uint8Array
  size: number
}

let cachedAtlas: ReturnType<typeof buildAtlas> | undefined

/** One shared RGB distance atlas: trunk, foliage, then blooms. No color variants. */
export function createTreeSpriteAtlas() {
  return cachedAtlas ??= buildAtlas()
}

function buildAtlas() {
  const distance = document.createElement('canvas')
  distance.width = CELL * COLUMNS
  distance.height = CELL * Math.ceil(SPRITE_FORMS.length / COLUMNS)
  const ctx = distance.getContext('2d')!
  const pixels = ctx.createImageData(distance.width, distance.height)
  const cells = new Map<string, SpriteCell>()
  // Opaque alpha is essential: distance channels are data, not premultiplied color.
  for (let p = 3; p < pixels.data.length; p += 4) pixels.data[p] = 255
  const union = (shapes: TreeShape[], x: number, y: number) => {
    let d = Infinity
    for (const shape of shapes) d = Math.min(d, treeShapeDistance(shape, x, y))
    return d
  }
  SPRITE_FORMS.forEach((form, index) => {
    const artwork = treeArtwork(form)
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
          pixels.data[offset + channel] = Math.round(255 * Math.max(0, Math.min(1, 0.5 - distances[channel] / (2 * DISTANCE_RANGE))))
        }
        if (x >= 0 && x < IMAGE_SIZE && y >= 0 && y < IMAGE_SIZE) {
          alpha[y * IMAGE_SIZE + x] = Math.min(...distances) <= 0 ? 255 : 0
        }
      }
    }
    cells.set(form, {
      left: left / distance.width, right: (left + IMAGE_SIZE) / distance.width,
      top: top / distance.height, bottom: (top + IMAGE_SIZE) / distance.height,
      crownFraction: artwork.crownFraction,
      base: form === 'multi_trunk' ? 0.9 : 0.95,
      alpha, size: IMAGE_SIZE,
    })
  })
  ctx.putImageData(pixels, 0, 0)
  return { distance, cells }
}
