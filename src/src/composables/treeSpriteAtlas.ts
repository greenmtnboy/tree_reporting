import { paintUprightShade, treeArtwork, treeShapeDistance, type TreeShape } from '../lib/treeArtwork'
import { CROWN_FORMS } from '../lib/crownShapes'
import type { TreeForm } from '../types'
import type { TreeAppearance } from '../lib/leaflessArtwork'
import { bakeWhenIdle } from './treeCrownShapeAtlas'

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

type SpriteAtlas = ReturnType<ReturnType<typeof baker>['finish']>
const atlases = new Map<TreeAppearance, SpriteAtlas>()
const pending = new Map<TreeAppearance, Promise<SpriteAtlas>>()

/**
 * One atlas stores trunk, foliage and bloom distances in RGB and the foliage value in alpha. The
 * leafless one has the same cells in the same places (its trees keep their leafy envelope), so a
 * vertex's coordinates read either.
 */
export function createTreeSpriteAtlas(appearance: TreeAppearance = 'leafy') {
  let atlas = atlases.get(appearance)
  if (!atlas) {
    const bake = baker(appearance)
    const work = bake.rows()
    while (!work.next().done);
    atlas = bake.finish()
    atlases.set(appearance, atlas)
  }
  return atlas
}

/** The atlas built at idle, a few rows a slice: the leafless one, which only a winter map needs. */
export function loadTreeSpriteAtlas(appearance: TreeAppearance): Promise<SpriteAtlas> {
  let request = pending.get(appearance)
  if (!request) {
    request = (async () => {
      const done = atlases.get(appearance)
      if (done) return done
      const bake = baker(appearance)
      await bakeWhenIdle(bake.rows())
      if (!atlases.has(appearance)) atlases.set(appearance, bake.finish())
      return atlases.get(appearance)!
    })()
    pending.set(appearance, request)
  }
  return request
}

function baker(appearance: TreeAppearance) {
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
  // Every form's cell, a row of texels a step.
  function* rows() {
    for (let index = 0; index < SPRITE_FORMS.length; index++) yield* cell(index)
  }
  function* cell(index: number) {
    const form = SPRITE_FORMS[index]
    const artwork = treeArtwork(form, appearance)
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
      yield
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
  }
  // How fast a channel changes per unit of texture coordinate, an edge's distance field having a
  // slope of one: the shader's antialiasing width from the coordinates' own derivatives.
  const distanceScale = [distance.width / IMAGE_SIZE / (2 * DISTANCE_RANGE), distance.height / IMAGE_SIZE / (2 * DISTANCE_RANGE)]
  return { rows, finish: () => ({ distance, cells, distanceScale }) }
}
