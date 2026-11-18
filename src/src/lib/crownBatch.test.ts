import { describe, expect, it } from 'vitest'
import { buildCrownBatch, CROWN_STRIDE } from './crownBatch'
import { CROWN_FORMS, unpackCrownAlpha } from './crownShapes'
import { DEFAULT_CROWN_WIDTH, metersPerMercatorUnit, type TreeCrownColumns } from './treeCrowns'

function columns(rows: { id: string; lat: number; width?: number }[]): TreeCrownColumns {
  return {
    id: rows.map(r => r.id),
    lng: Float64Array.from(rows, () => 0),
    lat: Float64Array.from(rows, r => r.lat),
    width: Float64Array.from(rows, r => r.width ?? NaN),
    measured: Uint8Array.from(rows, r => (r.width === undefined ? 0 : 1)),
    color: rows.map(() => '#ff0000'),
    category: rows.map(() => 'broadleaf'),
    dbh: Float64Array.from(rows, () => NaN),
    height: Float64Array.from(rows, () => NaN),
  }
}
const cell = { left: 0, right: 0.5, top: 0, bottom: 0.5, crownFraction: 0.8, heightFraction: 0.9, base: 0.95 }
const camera = { lng: 0, lat: 0, altitude: 500 }

describe('buildCrownBatch', () => {
  it('orders desktop sprites back to front along the bearing', () => {
    const c = columns([{ id: 'near', lat: -0.001 }, { id: 'far', lat: 0.002 }, { id: 'mid', lat: 0.001 }])
    const north = buildCrownBatch(c, camera, { simplified: false, bearing: 0, cells: { default: cell } })
    expect(north.id).toEqual(['far', 'mid', 'near'])
    expect(buildCrownBatch(c, camera, { simplified: false, bearing: 180, cells: { default: cell } }).id).toEqual(['near', 'mid', 'far'])
    expect(north.vertices.length).toBe(3 * 6 * CROWN_STRIDE)
    // Positions are relative to the query camera; a tree with no width gets the default crown.
    const radius = north.vertices[4] * metersPerMercatorUnit(0.002) * 2
    expect(radius).toBeCloseTo(DEFAULT_CROWN_WIDTH)
    expect(north.lat[0]).toBe(0.002)
  })

  it('records how far a capped batch reaches, and only then', () => {
    const c = columns([{ id: 'a', lat: 0.001 }, { id: 'b', lat: 0.002 }])
    expect(buildCrownBatch(c, camera, { simplified: false, bearing: 0 }).reach).toBe(Infinity)
    const capped = buildCrownBatch(c, { ...camera, spriteLimit: 2 }, { simplified: false, bearing: 0 })
    expect(capped.reach).toBeCloseTo(Math.hypot(0.002 * 111_320, 500), -2)
    expect(capped.rows).toBe(2)
    // The optional distance cull is ground metres; the reach is that, seen from the camera's height.
    expect(buildCrownBatch(c, { ...camera, spriteDistance: 1200 }, { simplified: false, bearing: 0 }).reach).toBeCloseTo(Math.hypot(1200, 500))
    // A batch the cap cut short ends at its farthest sprite, inside the cull.
    expect(buildCrownBatch(c, { ...camera, spriteLimit: 2, spriteDistance: 1200 }, { simplified: false, bearing: 0 }).reach).toBeCloseTo(capped.reach)
  })

  it('draws only measured crowns on a phone', () => {
    const c = columns([{ id: 'unmeasured', lat: 0.001 }, { id: 'measured', lat: 0.001, width: 8 }])
    const batch = buildCrownBatch(c, camera, { simplified: true, bearing: 0 })
    expect(batch.id).toEqual(['measured'])
    expect(batch.rows).toBe(2)
  })

  it('draws a deciduous tree bare in the winter where it stands, and only there', () => {
    // Trees far from the camera in latitude, so the desktop draws them all (no distance cut).
    const rows = [
      { id: 'boston', lat: 42, deciduous: 1 }, { id: 'boston-pine', lat: 42, deciduous: 0 },
      { id: 'melbourne', lat: -37, deciduous: 1 }, { id: 'singapore', lat: 1.3, deciduous: 1 },
    ]
    const c = { ...columns(rows), deciduous: Uint8Array.from(rows, r => r.deciduous) }
    const bareIn = (date: Date) => {
      const b = buildCrownBatch(c, camera, { simplified: false, bearing: 0, date: date.getTime() })
      return b.id.filter((_, i) => unpackCrownAlpha(b.vertices[i * 6 * CROWN_STRIDE + 8]).form >= CROWN_FORMS.length).sort()
    }
    expect(bareIn(new Date(2027, 0, 15))).toEqual(['boston'])
    expect(bareIn(new Date(2027, 6, 15))).toEqual(['melbourne'])
    expect(bareIn(new Date(2027, 4, 15))).toEqual([])
    // No date: every tree in leaf, as on a renderer that was never told the season.
    expect(buildCrownBatch(c, camera, { simplified: false, bearing: 0 }).bare).toBe(0)
  })
})
