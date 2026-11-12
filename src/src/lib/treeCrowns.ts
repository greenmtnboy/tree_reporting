/** Shared limits for the worker query and the GPU layer. Distances are metres. */
export const CROWN_ZOOM_START = 16.5
export const CROWN_ZOOM_FULL = 18
/**
 * The desktop's dot-to-sprite handoff. Sprites fade in from SPRITE_CROWN_ZOOM_START to
 * SPRITE_BASE_OPACITY at SPRITE_OPAQUE_ZOOM while the dots under them fade out over the same
 * span (treeDots' circleOpacity and useMapLayers' fallback read these), then climb to fully
 * opaque by SPRITE_FULL_OPACITY_ZOOM. Above SPRITE_OPAQUE_ZOOM dots show only past the sprites' reach.
 */
export const SPRITE_OPAQUE_ZOOM = 14
/** Zooms over which the dots hand over to the sprites. */
export const SPRITE_HANDOFF_ZOOMS = 0.6
export const SPRITE_CROWN_ZOOM_START = SPRITE_OPAQUE_ZOOM - SPRITE_HANDOFF_ZOOMS
export const SPRITE_BASE_OPACITY = 0.72
export const SPRITE_FULL_OPACITY_ZOOM = 21
/** Desktop sprites are drawn at metre scale but never narrower than this many CSS pixels. */
export const MIN_SPRITE_PX = 4
/** Metres; the crown a desktop sprite is drawn with when the tree has no measured or predicted width. */
export const DEFAULT_CROWN_WIDTH = 6
/**
 * Desktop sprites seen from above. An upright sprite shortens with the angle the camera looks
 * down at it (by the cosine of that elevation), and between these two elevations it turns into
 * a disc of its crown: the tree as seen from overhead, foreshortened to sin(elevation) as tall as
 * it is wide, so round only from straight above. Per tree, so in a pitched view the trees
 * under the camera are seen from above and the distant ones from the side.
 */
export const SPRITE_TOPDOWN_START_DEG = 55
export const SPRITE_TOPDOWN_FULL_DEG = 75

/** How a sprite looks from `sinElevation` (camera height over distance): its height scale and how far it has become a crown disc. */
export function spriteViewShape(sinElevation: number): { squash: number; top: number } {
  const s = Math.min(1, Math.max(0, sinElevation))
  const a = Math.sin(SPRITE_TOPDOWN_START_DEG * Math.PI / 180), b = Math.sin(SPRITE_TOPDOWN_FULL_DEG * Math.PI / 180)
  const t = Math.min(1, Math.max(0, (s - a) / (b - a)))
  return { squash: Math.sqrt(1 - s * s), top: t * t * (3 - 2 * t) }
}

/** A capped sprite batch fades out over the last tenth of its reach, where the dots take over. */
export const REACH_FADE = 0.9
export const CROWN_DISTANCE_FULL = 700
export const CROWN_DISTANCE_MAX = 1800
export const MAX_CROWNS = 4096
/** The desktop's default sprite budget; the layer may run with another (TreeRenderView.spriteLimit). */
export const MAX_TREE_SPRITES = 32768
/** localStorage key that overrides the desktop sprite budget, for sweeps and debugging. */
export const SPRITE_LIMIT_KEY = 'sf_trees_sprite_limit'
/** localStorage key for the optional desktop sprite distance (TreeRenderView.spriteDistance); unset draws as far as the budget reaches. */
export const SPRITE_DISTANCE_KEY = 'sf_trees_sprite_distance'

/**
 * Metres from the query camera at which a desktop batch's sprites hand over to the dots: the
 * farthest sprite when the budget cut the batch short, otherwise the optional ground cull
 * (`spriteDistance`, metres along the ground, so as 3D distance from a camera at `altitude`),
 * otherwise Infinity.
 */
export function spriteReach(capped: boolean, farthest: number, altitude: number, spriteDistance?: number): number {
  if (capped) return farthest
  return spriteDistance ? Math.hypot(spriteDistance, altitude) : Infinity
}
export const MAX_CROWN_WIDTH = 60
/** Above the tallest living trees (~116 m); the LIDAR staging already drops crowns over 60 m. */
export const MAX_TREE_HEIGHT = 120
export const MERCATOR_WORLD_METERS = 40075016.68557849

export interface CrownCamera { lng: number; lat: number; altitude: number }
export interface TreeRenderView extends CrownCamera {
  /** Desktop markers cover the viewport; only nearby ones acquire metre scale. */
  bounds?: { west: number; south: number; east: number; north: number }
  /** The padded view footprint inside `bounds`: a convex polygon in EPSG:3857 metres (lib/viewFootprint). */
  footprint?: [number, number][]
  /** Desktop sprite budget for this query; MAX_TREE_SPRITES when absent. */
  spriteLimit?: number
  /** Optional desktop cull: metres along the ground from the camera past which the dots carry the trees. */
  spriteDistance?: number
  /**
   * The ground on screen, for a phone's crowns: the read loads the cells under
   * it and takes the rest from whatever is held. Without it the read loaded
   * every cell within CROWN_DISTANCE_MAX, a whole block for a street's view.
   */
  view?: { west: number; south: number; east: number; north: number }
}
/**
 * A sprite query's rows as columns: numbers in typed arrays, NaN for a
 * missing value. One object per tree cost more to build from the Arrow
 * result than the query took.
 */
export interface TreeCrownColumns {
  id: string[]
  lng: Float64Array
  lat: Float64Array
  width: Float64Array
  measured: Uint8Array
  color: string[]
  category: string[]
  dbh: Float64Array
  height: Float64Array
}

export function crownColumnBuffers(c: TreeCrownColumns): ArrayBuffer[] {
  return [c.lng, c.lat, c.width, c.measured, c.dbh, c.height].map(a => a.buffer as ArrayBuffer)
}

export function crownZoomOpacity(zoom: number): number {
  return Math.max(0, Math.min(1, (zoom - CROWN_ZOOM_START) / (CROWN_ZOOM_FULL - CROWN_ZOOM_START)))
}

export function spriteOpacity(zoom: number): number {
  if (zoom < SPRITE_OPAQUE_ZOOM) {
    return SPRITE_BASE_OPACITY * Math.max(0, (zoom - SPRITE_CROWN_ZOOM_START) / (SPRITE_OPAQUE_ZOOM - SPRITE_CROWN_ZOOM_START))
  }
  const t = (zoom - SPRITE_OPAQUE_ZOOM) / (SPRITE_FULL_OPACITY_ZOOM - SPRITE_OPAQUE_ZOOM)
  return Math.min(1, SPRITE_BASE_OPACITY + t * (1 - SPRITE_BASE_OPACITY))
}

/** Reject corrupt sizes instead of rendering a plausible-looking clipped measurement. */
export function validCrownWidth(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= MAX_CROWN_WIDTH
}

export function validTreeHeight(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= MAX_TREE_HEIGHT
}

/**
 * How much taller than the artwork's own proportions a tree is drawn once it
 * reaches metre scale: the cell height its height needs over the cell width
 * its crown needs. 1 (the artwork as drawn) without both. Clamped so a stub
 * or a pole stays recognisably the same form.
 */
export const MIN_SPRITE_ASPECT = 0.5
export const MAX_SPRITE_ASPECT = 2.5
export function spriteAspect(width: unknown, height: unknown, crownFraction: number, heightFraction: number): number {
  if (!validCrownWidth(width) || !validTreeHeight(height) || !(crownFraction > 0) || !(heightFraction > 0)) return 1
  const aspect = (height / heightFraction) / (width / crownFraction)
  return Math.max(MIN_SPRITE_ASPECT, Math.min(MAX_SPRITE_ASPECT, aspect))
}

export function mercatorPoint(lng: number, lat: number): [number, number] {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI / 180
  return [(lng + 180) / 360, (1 - Math.log(Math.tan(Math.PI / 4 + clamped / 2)) / Math.PI) / 2]
}

export function metersPerMercatorUnit(lat: number): number {
  return MERCATOR_WORLD_METERS * Math.cos(lat * Math.PI / 180)
}
