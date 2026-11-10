import { describe, expect, it } from 'vitest'
import {
  CEILING_RECOVERY_BATCHES, SPRITE_LIMIT_MAX, SPRITE_LIMIT_STEP, STEP_DOWN_TURN_DEG, SPRITE_LIMIT_MIN, SOFTWARE_SPRITE_LIMIT_MAX, UPLOAD_BUDGET_MS, BUILD_BUDGET_MS,
  initialSpriteBudget, isSoftwareRenderer, nextSpriteBudget, settleStepDown, stepDownTravel, stepDownUnseen, type BatchSample,
} from './spriteBudget'
import { MAX_TREE_SPRITES } from './treeCrowns'

const ok: BatchSample = { limit: MAX_TREE_SPRITES, capped: true, uploadMs: 2, buildMs: 30 }

describe('sprite budget', () => {
  it('grows while capped batches stay within cost, whatever they reach', () => {
    const start = initialSpriteBudget(false)
    expect(nextSpriteBudget(start, ok).limit).toBeGreaterThan(start.limit)
    // An uncapped batch drew every tree it was asked for.
    expect(nextSpriteBudget(start, { ...ok, capped: false }).limit).toBe(start.limit)
  })

  it('over cost, keeps the sprites it drew and stops growing, stepping down only when settled', () => {
    for (const over of [{ uploadMs: UPLOAD_BUDGET_MS + 1 }, { buildMs: BUILD_BUDGET_MS + 1 }]) {
      let budget = nextSpriteBudget(initialSpriteBudget(false), { ...ok, ...over })
      expect(budget.limit).toBe(MAX_TREE_SPRITES)
      expect(budget.ceiling).toBe(MAX_TREE_SPRITES)
      const stepDown = budget.stepDown!
      expect(stepDown).toBeLessThan(MAX_TREE_SPRITES)
      // Further overages before it settles schedule no second step; good batches neither grow nor lift the ceiling.
      budget = nextSpriteBudget(budget, { ...ok, ...over })
      for (let i = 0; i < CEILING_RECOVERY_BATCHES + 1; i++) budget = nextSpriteBudget(budget, ok)
      expect(budget.stepDown).toBe(stepDown)
      expect(budget.limit).toBe(MAX_TREE_SPRITES)
      expect(budget.ceiling).toBe(MAX_TREE_SPRITES)
      budget = settleStepDown(budget)
      expect(budget).toMatchObject({ limit: stepDown, ceiling: stepDown })
      expect(budget.stepDown).toBeUndefined()
    }
  })

  it('a step down settles after a turn or enough travel forward, not a small move', () => {
    expect(stepDownUnseen(10, 50, 3000)).toBe(false)
    expect(stepDownUnseen(STEP_DOWN_TURN_DEG, 0, 3000)).toBe(true)
    expect(stepDownUnseen(-170, 0, 3000)).toBe(true)
    expect(stepDownUnseen(350, 0, 3000)).toBe(false)
    // A limit 1.25x smaller reaches ~89% as far; travelling the other ~11% keeps the edge ahead where it was.
    expect(stepDownTravel(3000) + 3000 / Math.sqrt(SPRITE_LIMIT_STEP)).toBeCloseTo(3000)
    expect(stepDownUnseen(0, stepDownTravel(3000), 3000)).toBe(true)
    // With no reach to keep (an uncapped batch, no cull) it settles at the next fetch.
    expect(stepDownUnseen(0, 0, 0)).toBe(true)
  })

  it('backs off and holds a ceiling when a batch goes over budget, then lifts it after a good run', () => {
    for (const over of [{ uploadMs: UPLOAD_BUDGET_MS + 1 }, { buildMs: BUILD_BUDGET_MS + 1 }]) {
      let budget = settleStepDown(nextSpriteBudget(initialSpriteBudget(false), { ...ok, ...over }))
      expect(budget.limit).toBeLessThan(MAX_TREE_SPRITES)
      const ceiling = budget.ceiling
      budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit })
      expect(budget.limit).toBeLessThanOrEqual(ceiling)
      for (let i = 0; i < CEILING_RECOVERY_BATCHES; i++) budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit })
      expect(budget.ceiling).toBeGreaterThan(ceiling)
    }
  })

  it('stays within its bounds, smaller on a software renderer', () => {
    let budget = initialSpriteBudget(false)
    for (let i = 0; i < 40; i++) budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit })
    expect(budget.limit).toBe(SPRITE_LIMIT_MAX)
    for (let i = 0; i < 40; i++) budget = settleStepDown(nextSpriteBudget(budget, { ...ok, limit: budget.limit, uploadMs: 100 }))
    expect(budget.limit).toBe(SPRITE_LIMIT_MIN)
    let software = initialSpriteBudget(true)
    for (let i = 0; i < 40; i++) software = nextSpriteBudget(software, { ...ok, limit: software.limit })
    expect(software.limit).toBe(SOFTWARE_SPRITE_LIMIT_MAX)
    expect(isSoftwareRenderer('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)')).toBe(true)
    expect(isSoftwareRenderer('ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0, D3D11)')).toBe(false)
  })
})
