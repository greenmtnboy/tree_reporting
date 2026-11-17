import { describe, expect, it } from 'vitest'
import { FETCH_DETAIL, FETCH_PHRASES, NEARBY_LOAD_STEPS, nearbyLoadStepIndex } from './loadProgress'

const key = (state: Parameters<typeof nearbyLoadStepIndex>[0]) => NEARBY_LOAD_STEPS[nearbyLoadStepIndex(state)].key

describe('nearbyLoadStepIndex', () => {
  it('starts on fetching the engine before the worker reports anything', () => {
    expect(key({ initStage: null, cityContextPending: 0, cityContextLoaded: false })).toBe('engine')
  })

  it('follows the worker through its init stages', () => {
    for (const stage of ['engine', 'guide', 'world'] as const) {
      expect(key({ initStage: stage, cityContextPending: 1, cityContextLoaded: false })).toBe(stage)
    }
  })

  it('loads the bookmark once init is done, then draws the trees', () => {
    expect(key({ initStage: 'ready', cityContextPending: 1, cityContextLoaded: false })).toBe('city')
    expect(key({ initStage: 'ready', cityContextPending: 0, cityContextLoaded: false })).toBe('city')
    expect(key({ initStage: 'ready', cityContextPending: 0, cityContextLoaded: true })).toBe('trees')
  })

  it('goes back to the bookmark when a later map asks for a context again', () => {
    expect(key({ initStage: 'ready', cityContextPending: 1, cityContextLoaded: true })).toBe('city')
  })

  it('always shows the location as found, and only moves forward through the list', () => {
    expect(NEARBY_LOAD_STEPS[0].key).toBe('located')
    const order = [null, 'engine', 'guide', 'world', 'ready'] as const
    const indices = order.map((initStage) => nearbyLoadStepIndex({ initStage, cityContextPending: 1, cityContextLoaded: false }))
    expect(indices).toEqual([...indices].sort((a, b) => a - b))
    expect(Math.min(...indices)).toBeGreaterThan(0)
    expect(indices.every((i) => i >= 0)).toBe(true)
  })
})

describe('step and phrase copy', () => {
  it('every step has a playful title and a plain meaning, and the keys are unique', () => {
    for (const step of NEARBY_LOAD_STEPS) {
      expect(step.title.length).toBeGreaterThan(0)
      expect(step.detail.length).toBeGreaterThan(0)
      expect(step.title).not.toBe(step.detail)
    }
    expect(new Set(NEARBY_LOAD_STEPS.map((s) => s.key)).size).toBe(NEARBY_LOAD_STEPS.length)
    expect(NEARBY_LOAD_STEPS.map((s) => s.detail)).toContain('Fetching dependencies')
    expect(NEARBY_LOAD_STEPS.map((s) => s.detail)).toContain('Loading local data')
  })

  it('the corner leaf has playful phrases and a plain line', () => {
    expect(FETCH_PHRASES.length).toBeGreaterThan(1)
    expect(FETCH_DETAIL).toMatch(/fetching/i)
  })
})
