import { DEFAULT_CROWN_WIDTH, MAX_TREE_SPRITES, MIN_SPRITE_PX } from './treeCrowns'

/**
 * The desktop sprite budget adjusts itself after every batch. Its target is
 * reach: sprites only need to go as far as a typical crown stays wider than
 * MIN_SPRITE_PX, since past that a sprite is a dot-sized blob and the dots
 * carry it. Its limits are cost: the main thread's buffer upload (the one
 * hitch left once the worker builds batches) and the worker's build time
 * (which delays every batch). Boston at 1280x800 on an RX 6800 XT:
 * 0.8-1 ms of build per thousand sprites, a 6 m crown at 4 px at 1.6 km.
 */
export const SPRITE_LIMIT_MIN = 8192
export const SPRITE_LIMIT_MAX = 131072
/** Where a software renderer (SwiftShader, llvmpipe, Basic Render) starts, and the most it may grow to. */
export const SOFTWARE_SPRITE_LIMIT = 8192
export const SOFTWARE_SPRITE_LIMIT_MAX = 16384
/** Milliseconds a batch's GPU upload may stretch its frame by: under a frame. */
export const UPLOAD_BUDGET_MS = 12
/** Worker milliseconds a batch's sort and vertex fill may take before it delays sprites noticeably. */
export const BUILD_BUDGET_MS = 150
/** A capped batch should reach this much past the target, and reaching this many times it is more than needed. */
export const REACH_TARGET_MARGIN = 1.25
export const REACH_EXCESS = 2
/** Each adjustment multiplies or divides the limit by this. */
export const SPRITE_LIMIT_STEP = 1.25
/** In-budget batches before a ceiling set by an over-budget one lifts a step. */
export const CEILING_RECOVERY_BATCHES = 20

/** Metres from the camera at which a DEFAULT_CROWN_WIDTH crown is MIN_SPRITE_PX wide; `focalPx` is MapLibre's cameraToCenterDistance. */
export function crownVisibleDistance(focalPx: number): number {
  return DEFAULT_CROWN_WIDTH * focalPx / MIN_SPRITE_PX
}

export function isSoftwareRenderer(renderer: string): boolean {
  return /swiftshader|llvmpipe|softpipe|basic render|software/i.test(renderer)
}

export interface SpriteBudget {
  limit: number
  /** The most the limit may grow to: lowered when a batch goes over budget, lifted again after a run of good ones. */
  ceiling: number
  max: number
  okStreak: number
}

export function initialSpriteBudget(software: boolean): SpriteBudget {
  return software
    ? { limit: SOFTWARE_SPRITE_LIMIT, ceiling: SOFTWARE_SPRITE_LIMIT_MAX, max: SOFTWARE_SPRITE_LIMIT_MAX, okStreak: 0 }
    : { limit: MAX_TREE_SPRITES, ceiling: SPRITE_LIMIT_MAX, max: SPRITE_LIMIT_MAX, okStreak: 0 }
}

export interface BatchSample {
  /** The limit the batch was queried with, and whether it hit it. */
  limit: number
  capped: boolean
  /** Metres to the farthest sprite of a capped batch. */
  reach: number
  /** crownVisibleDistance for the view the batch was queried for. */
  target: number
  uploadMs: number
  buildMs: number
}

const snap = (n: number) => Math.round(n / 1024) * 1024

/** The budget after a batch. Steps one SPRITE_LIMIT_STEP at a time; a batch over budget also caps how far it may grow back. */
export function nextSpriteBudget(budget: SpriteBudget, s: BatchSample): SpriteBudget {
  const clamp = (n: number, ceiling: number) => Math.max(SPRITE_LIMIT_MIN, Math.min(ceiling, snap(n)))
  if (s.uploadMs > UPLOAD_BUDGET_MS || s.buildMs > BUILD_BUDGET_MS) {
    const limit = clamp(s.limit / SPRITE_LIMIT_STEP, budget.max)
    return { ...budget, limit, ceiling: limit, okStreak: 0 }
  }
  let ceiling = budget.ceiling, okStreak = budget.okStreak + 1
  if (okStreak >= CEILING_RECOVERY_BATCHES && ceiling < budget.max) {
    ceiling = clamp(ceiling * SPRITE_LIMIT_STEP, budget.max)
    okStreak = 0
  }
  let limit = budget.limit
  if (s.capped && s.reach < s.target * REACH_TARGET_MARGIN) limit = clamp(s.limit * SPRITE_LIMIT_STEP, ceiling)
  else if (s.capped && s.reach > s.target * REACH_EXCESS) limit = clamp(s.limit / SPRITE_LIMIT_STEP, ceiling)
  return { ...budget, limit, ceiling, okStreak }
}
