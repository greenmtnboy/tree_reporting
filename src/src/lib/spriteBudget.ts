import { MAX_TREE_SPRITES } from './treeCrowns'

/**
 * The desktop sprite budget adjusts itself after every batch, by rendering cost
 * alone: it grows while batches hit their cap and stay within cost, and backs
 * off when one goes over, at a moment the user will not see it. Cost is the main thread's buffer upload (the one
 * hitch left once the worker builds batches) and the worker's build time
 * (which delays every batch). How far the sprites reach is whatever that buys,
 * unless the optional distance cull (TreeRenderView.spriteDistance) stops
 * them sooner; a sprite at its MIN_SPRITE_PX floor still reads differently
 * from a dot, so no reach counts as "enough". Boston at 1280x800 on an RX 6800
 * XT: 0.8-1 ms of build per thousand sprites.
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
/**
 * A batch over cost stops the growth at once but steps the limit down only later (`stepDown`):
 * stepping down at once takes away the band of sprites it just painted. The layer applies it at
 * the next fetch after the view has turned this many degrees, or has travelled stepDownTravel.
 */
export const STEP_DOWN_TURN_DEG = 45
/** Each adjustment multiplies or divides the limit by this. */
export const SPRITE_LIMIT_STEP = 1.25
/** In-budget batches before a ceiling set by an over-budget one lifts a step. */
export const CEILING_RECOVERY_BATCHES = 20

export function isSoftwareRenderer(renderer: string): boolean {
  return /swiftshader|llvmpipe|softpipe|basic render|software/i.test(renderer)
}

export interface SpriteBudget {
  limit: number
  /** The most the limit may grow to: lowered when a batch goes over budget, lifted again after a run of good ones. */
  ceiling: number
  max: number
  okStreak: number
  /** The limit a batch over cost asked to step down to, waiting for a moment it will not show (settleStepDown). */
  stepDown?: number
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
  uploadMs: number
  buildMs: number
}

const snap = (n: number) => Math.round(n / 1024) * 1024

/**
 * The budget after a batch. Steps one SPRITE_LIMIT_STEP at a time: up when a capped batch was
 * within cost (more trees were there to draw). A batch over cost sets the ceiling at its own
 * limit, keeping the sprites it drew, and schedules one step down (`stepDown`). An uncapped
 * batch drew every tree it was asked for and leaves the limit alone.
 */
export function nextSpriteBudget(budget: SpriteBudget, s: BatchSample): SpriteBudget {
  const clamp = (n: number, ceiling: number) => Math.max(SPRITE_LIMIT_MIN, Math.min(ceiling, snap(n)))
  if (s.uploadMs > UPLOAD_BUDGET_MS || s.buildMs > BUILD_BUDGET_MS) {
    const limit = clamp(s.limit, budget.max)
    return { ...budget, limit, ceiling: limit, okStreak: 0, stepDown: budget.stepDown ?? clamp(s.limit / SPRITE_LIMIT_STEP, budget.max) }
  }
  let ceiling = budget.ceiling, okStreak = budget.okStreak + 1
  if (okStreak >= CEILING_RECOVERY_BATCHES && ceiling < budget.max && budget.stepDown === undefined) {
    ceiling = clamp(ceiling * SPRITE_LIMIT_STEP, budget.max)
    okStreak = 0
  }
  const limit = s.capped ? clamp(s.limit * SPRITE_LIMIT_STEP, ceiling) : budget.limit
  return { ...budget, limit, ceiling, okStreak }
}

/** Apply a scheduled step down: the limit and the ceiling drop to it. */
export function settleStepDown(budget: SpriteBudget): SpriteBudget {
  if (budget.stepDown === undefined) return budget
  const { stepDown, ...rest } = budget
  return { ...rest, limit: stepDown, ceiling: stepDown }
}

/**
 * Ground metres the camera must travel before a step down will not show: at an even density a
 * limit SPRITE_LIMIT_STEP smaller reaches 1/sqrt(SPRITE_LIMIT_STEP) as far, so after this much
 * travel forward the shorter reach still covers everything the old one painted ahead.
 */
export function stepDownTravel(groundReach: number): number {
  return groundReach * (1 - 1 / Math.sqrt(SPRITE_LIMIT_STEP))
}

/** Whether a scheduled step down can apply: the view has turned STEP_DOWN_TURN_DEG, or travelled stepDownTravel, since the batch that asked for it. */
export function stepDownUnseen(turnDeg: number, travel: number, groundReach: number): boolean {
  const turn = Math.abs(((turnDeg % 360) + 540) % 360 - 180)
  return turn >= STEP_DOWN_TURN_DEG || travel >= stepDownTravel(groundReach)
}
