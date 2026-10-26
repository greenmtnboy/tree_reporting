import { describe, expect, it } from 'vitest'
import {
  INTRO_END_ZOOM,
  INTRO_START_ZOOM,
  introLevelTileRange,
  introPoseAt,
  introProgressAtZoom,
  stepIntroFlyOut,
  type IntroFlyOutState,
  type IntroLevelGate,
  type TileRange,
} from '../composables/useMapIntroAnimation'

// The desktop intro's start pose on Boston (750 x 808 canvas, pitch 60), read
// from MapLibre in a production build, and the tiles MapLibre requested for
// each level during that fly-out. Tile fetching is paused while the camera
// moves, so a requested tile the warm-up left out draws empty: the trees
// disappeared from z14.7 until seconds after landing before this was warmed.
const BOSTON_START = {
  center: [-71.0589, 42.3601] as [number, number],
  bearing: 120,
  rotationDeg: 240,
  corners: [
    [-71.05491854043082, 42.35983724850021],
    [-71.05660132410796, 42.35768348316398],
    [-71.05951592834882, 42.3607474879463],
    [-71.05996682887621, 42.360170403864174],
  ] as [number, number][],
}
const BOSTON_REQUESTED: Record<number, TileRange> = {
  15: { minX: 9911, maxX: 9917, minY: 12117, maxY: 12121 },
  14: { minX: 4954, maxX: 4959, minY: 6055, maxY: 6061 },
  13: { minX: 2477, maxX: 2480, minY: 3027, maxY: 3030 },
}

const tileCount = (r: TileRange) => (r.maxX - r.minX + 1) * (r.maxY - r.minY + 1)

describe('introLevelTileRange', () => {
  it.each([15, 14, 13])('covers every z%i tile MapLibre requested on Boston', (level) => {
    const range = introLevelTileRange(level, BOSTON_START.corners, BOSTON_START.center, BOSTON_START.bearing, BOSTON_START.rotationDeg)
    const requested = BOSTON_REQUESTED[level]
    expect(range).not.toBeNull()
    expect(range!.minX).toBeLessThanOrEqual(requested.minX)
    expect(range!.maxX).toBeGreaterThanOrEqual(requested.maxX)
    expect(range!.minY).toBeLessThanOrEqual(requested.minY)
    expect(range!.maxY).toBeGreaterThanOrEqual(requested.maxY)
    // Each warm is one aggregate over the range; it must stay near the view.
    expect(tileCount(range!)).toBeLessThanOrEqual(tileCount(requested) * 2)
  })

  it('turns the footprint with the bearing', () => {
    // A wide, short view straight down at north-up: without rotation it lands
    // wide; turning a quarter on the way out it lands tall.
    const center: [number, number] = [0, 0]
    const wide: [number, number][] = [[-0.002, 0.0005], [0.002, 0.0005], [-0.002, -0.0005], [0.002, -0.0005]]
    const north = introLevelTileRange(13, wide, center, 0, 0, 16, 0)!
    const east = introLevelTileRange(13, wide, center, 0, 90, 16, 0)!
    expect(north.maxX - north.minX).toBeGreaterThan(north.maxY - north.minY)
    expect(east.maxY - east.minY).toBeGreaterThan(east.maxX - east.minX)
  })
})

describe('stepIntroFlyOut', () => {
  const FRAME_MS = 16
  const gatesBelowStart = (): IntroLevelGate[] => [15, 14, 13].map(level => ({
    level,
    stopAt: introProgressAtZoom(level + 1.05),
    ready: false,
  }))

  /** Runs frames until landing or `untilMs`, calling `onFrame` with the camera's zoom. */
  function fly(gates: IntroLevelGate[], untilMs: number, onFrame?: (nowMs: number, zoom: number) => void) {
    const state: IntroFlyOutState = { local: 0, rate: 1, held: null }
    const events = []
    let now = 0
    while (state.local < 1 && now < untilMs) {
      now += FRAME_MS
      const step = stepIntroFlyOut(state, gates, now, FRAME_MS)
      events.push(...step.events)
      onFrame?.(now, introPoseAt(step.t, [0, 0], 0, 240).zoom)
    }
    return { landedAt: state.local >= 1 ? now : null, events }
  }

  it('is the plain ten-second sweep when every level is warm', () => {
    const gates = gatesBelowStart().map(g => ({ ...g, ready: true }))
    const { landedAt, events } = fly(gates, 60_000)
    expect(landedAt).toBeGreaterThanOrEqual(10_000)
    expect(landedAt).toBeLessThan(10_100)
    expect(events).toEqual([])
  })

  it('never shows a level before its tiles are warm', () => {
    const gates = gatesBelowStart()
    const warmAt: Record<number, number> = { 15: 3_000, 14: 9_000, 13: 14_000 }
    let lowest = INTRO_START_ZOOM
    const { landedAt, events } = fly(gates, 60_000, (now, zoom) => {
      for (const g of gates) if (!g.ready && now >= warmAt[g.level]) g.ready = true
      lowest = Math.min(lowest, zoom)
      // A level is drawn once the camera zoom drops below level + 1.
      for (const g of gates) {
        if (zoom < g.level + 1) expect(now, `z${g.level} shown at ${now} ms, warm at ${warmAt[g.level]} ms`).toBeGreaterThanOrEqual(warmAt[g.level])
      }
    })
    expect(lowest).toBeCloseTo(INTRO_END_ZOOM, 5)
    expect(landedAt).toBeGreaterThan(14_000)
    expect(events.map(e => e.kind === 'held' ? e.level : -1)).toEqual([14, 13])
  })

  it('enters a level that never warms once the hold cap runs out', () => {
    const gates = gatesBelowStart()
    gates[0].ready = true
    gates[2].ready = true
    const { landedAt, events } = fly(gates, 60_000)
    expect(events[0]).toMatchObject({ kind: 'released-cold', level: 14 })
    expect(landedAt).not.toBeNull()
    expect(landedAt!).toBeLessThan(30_000)
  })
})
