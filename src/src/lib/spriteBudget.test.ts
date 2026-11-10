import { describe, expect, it } from 'vitest'
import {
  CEILING_RECOVERY_BATCHES, SEVERE_OVERAGE, SPRITE_LIMIT_MAX, SPRITE_LIMIT_MIN, SOFTWARE_SPRITE_LIMIT_MAX, UPLOAD_BUDGET_MS, BUILD_BUDGET_MS,
  initialSpriteBudget, isSoftwareRenderer, nextSpriteBudget, type BatchSample,
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

  it('holds a batch a little over budget, keeping the sprites it drew, and stops growing there', () => {
    for (const over of [{ uploadMs: UPLOAD_BUDGET_MS + 1 }, { buildMs: BUILD_BUDGET_MS + 1 }]) {
      const budget = nextSpriteBudget(initialSpriteBudget(false), { ...ok, ...over })
      expect(budget.limit).toBe(MAX_TREE_SPRITES)
      expect(budget.ceiling).toBe(MAX_TREE_SPRITES)
      expect(nextSpriteBudget(budget, ok).limit).toBe(MAX_TREE_SPRITES)
    }
  })

  it('backs off and holds a ceiling when a batch goes well over budget, then lifts it after a good run', () => {
    for (const over of [{ uploadMs: UPLOAD_BUDGET_MS * SEVERE_OVERAGE + 1 }, { buildMs: BUILD_BUDGET_MS * SEVERE_OVERAGE + 1 }]) {
      let budget = nextSpriteBudget(initialSpriteBudget(false), { ...ok, ...over })
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
    for (let i = 0; i < 40; i++) budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit, uploadMs: 100 })
    expect(budget.limit).toBe(SPRITE_LIMIT_MIN)
    let software = initialSpriteBudget(true)
    for (let i = 0; i < 40; i++) software = nextSpriteBudget(software, { ...ok, limit: software.limit })
    expect(software.limit).toBe(SOFTWARE_SPRITE_LIMIT_MAX)
    expect(isSoftwareRenderer('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)')).toBe(true)
    expect(isSoftwareRenderer('ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0, D3D11)')).toBe(false)
  })
})
