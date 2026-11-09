import { describe, expect, it } from 'vitest'
import {
  CEILING_RECOVERY_BATCHES, SPRITE_LIMIT_MAX, SPRITE_LIMIT_MIN, SOFTWARE_SPRITE_LIMIT_MAX, UPLOAD_BUDGET_MS,
  crownVisibleDistance, initialSpriteBudget, isSoftwareRenderer, nextSpriteBudget, type BatchSample,
} from './spriteBudget'
import { MAX_TREE_SPRITES } from './treeCrowns'

const ok: BatchSample = { limit: MAX_TREE_SPRITES, capped: true, reach: 1600, target: 1600, uploadMs: 2, buildMs: 30 }

describe('sprite budget', () => {
  it('aims at the distance a 6 m crown is 4 px wide', () => {
    // MapLibre's focal length at 800 px tall and its default 36.87 degree field of view.
    expect(crownVisibleDistance(1200)).toBe(1800)
  })

  it('grows while a capped batch falls short of the target, and shrinks when it reaches far past it', () => {
    const start = initialSpriteBudget(false)
    const grown = nextSpriteBudget(start, { ...ok, reach: 1000 })
    expect(grown.limit).toBeGreaterThan(start.limit)
    expect(nextSpriteBudget(start, { ...ok, reach: 2400 }).limit).toBe(start.limit)
    expect(nextSpriteBudget(start, { ...ok, reach: 4000 }).limit).toBeLessThan(start.limit)
    // An uncapped batch says nothing about reach.
    expect(nextSpriteBudget(start, { ...ok, capped: false, reach: Infinity }).limit).toBe(start.limit)
  })

  it('backs off and holds a ceiling when a batch goes over budget, then lifts it after a good run', () => {
    let budget = nextSpriteBudget(initialSpriteBudget(false), { ...ok, uploadMs: UPLOAD_BUDGET_MS + 1 })
    expect(budget.limit).toBeLessThan(MAX_TREE_SPRITES)
    const ceiling = budget.ceiling
    // Short of target, it may not grow past the ceiling.
    budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit, reach: 500 })
    expect(budget.limit).toBeLessThanOrEqual(ceiling)
    for (let i = 0; i < CEILING_RECOVERY_BATCHES; i++) budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit, reach: 500 })
    expect(budget.ceiling).toBeGreaterThan(ceiling)
  })

  it('stays within its bounds, smaller on a software renderer', () => {
    let budget = initialSpriteBudget(false)
    for (let i = 0; i < 40; i++) budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit, reach: 10 })
    expect(budget.limit).toBe(SPRITE_LIMIT_MAX)
    for (let i = 0; i < 40; i++) budget = nextSpriteBudget(budget, { ...ok, limit: budget.limit, reach: 1e6 })
    expect(budget.limit).toBe(SPRITE_LIMIT_MIN)
    let software = initialSpriteBudget(true)
    for (let i = 0; i < 40; i++) software = nextSpriteBudget(software, { ...ok, limit: software.limit, reach: 10 })
    expect(software.limit).toBe(SOFTWARE_SPRITE_LIMIT_MAX)
    expect(isSoftwareRenderer('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)')).toBe(true)
    expect(isSoftwareRenderer('ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0, D3D11)')).toBe(false)
  })
})
