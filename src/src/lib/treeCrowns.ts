/** Shared limits for the worker query and the GPU layer. Distances are metres. */
export const CROWN_ZOOM_START = 16.5
export const CROWN_ZOOM_FULL = 18
export const SPRITE_CROWN_ZOOM_START = 14.4
export const SPRITE_CROWN_ZOOM_FULL = 19.5
export const CROWN_DISTANCE_FULL = 700
export const CROWN_DISTANCE_MAX = 1800
export const MAX_CROWNS = 4096
export const MAX_TREE_SPRITES = 32768
export const MAX_CROWN_WIDTH = 60
export const MERCATOR_WORLD_METERS = 40075016.68557849

export interface CrownCamera { lng: number; lat: number; altitude: number }
export interface TreeRenderView extends CrownCamera {
  /** Desktop markers cover the viewport; only nearby ones acquire metre scale. */
  bounds?: { west: number; south: number; east: number; north: number }
}
export interface TreeCrown {
  id: string
  lng: number
  lat: number
  width: number | null
  measured: boolean
  color: string
  category?: string
  dbh?: number
}

export function crownZoomOpacity(zoom: number): number {
  return Math.max(0, Math.min(1, (zoom - CROWN_ZOOM_START) / (CROWN_ZOOM_FULL - CROWN_ZOOM_START)))
}

export function spriteCrownBlend(zoom: number): number {
  const t = Math.max(0, Math.min(1, (zoom - SPRITE_CROWN_ZOOM_START) / (SPRITE_CROWN_ZOOM_FULL - SPRITE_CROWN_ZOOM_START)))
  return t * t * (3 - 2 * t)
}

export function spriteOpacity(zoom: number): number {
  if (zoom < 15) return 0.72 * Math.max(0, (zoom - SPRITE_CROWN_ZOOM_START) / (15 - SPRITE_CROWN_ZOOM_START))
  return Math.min(1, 0.72 + (zoom - 15) / 6 * 0.28)
}

/** The same continuous marker baseline for rendering and hit testing. */
export function spriteBaseScale(zoom: number): [number, number] {
  const stops = [[14.4, 0.04, 0.1], [15, 0.055, 0.15], [18, 0.2, 0.72], [21, 0.28, 1]]
  for (let i = 1; i < stops.length; i++) {
    if (zoom <= stops[i][0]) {
      const a = stops[i - 1], b = stops[i]
      const t = Math.max(0, Math.min(1, (zoom - a[0]) / (b[0] - a[0])))
      return [a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])]
    }
  }
  return [0.28, 1]
}

/** Reject corrupt sizes instead of rendering a plausible-looking clipped measurement. */
export function validCrownWidth(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= MAX_CROWN_WIDTH
}

export function mercatorPoint(lng: number, lat: number): [number, number] {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI / 180
  return [(lng + 180) / 360, (1 - Math.log(Math.tan(Math.PI / 4 + clamped / 2)) / Math.PI) / 2]
}

export function metersPerMercatorUnit(lat: number): number {
  return MERCATOR_WORLD_METERS * Math.cos(lat * Math.PI / 180)
}
