import { describe, expect, it } from 'vitest'
import { CROWN_FORMS, CROWN_TURNS, crownEdgeDistance, crownFormIndex, crownShape, crownTurn, packCrownAlpha, unpackCrownAlpha } from './crownShapes'

describe('crown shapes', () => {
  it('keep every outline inside the crown disc, reaching it, and closed round atan\'s seam', () => {
    for (const form of CROWN_FORMS) {
      let reach = 0
      for (let i = 0; i < 3600; i++) {
        const a = (i / 3600) * Math.PI * 2 - Math.PI
        // Walk out along the ray to the outline.
        let r = 0
        while (r < 1.2 && crownShape(form, r * Math.cos(a), r * Math.sin(a)).edge < 0) r += 0.002
        expect(r, `${form} at ${a.toFixed(3)}`).toBeLessThanOrEqual(1.002)
        reach = Math.max(reach, r)
      }
      expect(reach, form).toBeGreaterThan(0.985)
      // Either side of the seam the outline is the same.
      const below = crownShape(form, -0.9, -1e-6).edge, above = crownShape(form, -0.9, 1e-6).edge
      expect(Math.abs(below - above), form).toBeLessThan(1e-3)
    }
  })

  it('give each form a different crown', () => {
    // The share of the disc each covers, sampled on a grid: no two forms alike.
    const areas = CROWN_FORMS.filter(form => form !== 'default').map(form => {
      let inside = 0
      for (let y = -1; y <= 1; y += 0.02) for (let x = -1; x <= 1; x += 0.02) if (crownShape(form, x, y).edge < 0) inside++
      return inside
    })
    expect(new Set(areas).size).toBe(areas.length)
    // The palm's fronds leave most of its disc open.
    expect(areas[CROWN_FORMS.indexOf('palm')]).toBeLessThan(areas[CROWN_FORMS.indexOf('broadleaf')] * 0.6)
  })

  it('read the outline as a distance, one width round a steep lobe', () => {
    // Just inside a palm frond's flank the radial offset overstates the distance; the edge distance does not.
    const a = 0.3, r = 0.5
    const radial = crownShape('palm', r * Math.cos(a), r * Math.sin(a)).edge
    expect(Math.abs(crownEdgeDistance('palm', r * Math.cos(a), r * Math.sin(a)))).toBeLessThan(Math.abs(radial))
    // Where the outline is circles (multi-trunk's) the two agree.
    expect(crownEdgeDistance('multi_trunk', 0, 0)).toBeCloseTo(crownShape('multi_trunk', 0, 0).edge, 2)
  })
})

describe('the crown packed in the colour alpha', () => {
  it('round-trips flags, form and turn exactly through a float32', () => {
    const f32 = new Float32Array(1)
    for (let flags = 0; flags <= 3; flags++) {
      for (let form = 0; form < CROWN_FORMS.length; form++) {
        for (let turn = 0; turn < CROWN_TURNS; turn++) {
          f32[0] = packCrownAlpha(flags, form, turn)
          expect(unpackCrownAlpha(f32[0])).toEqual({ flags, form, turn })
          // The shaders' measured (> 0.5 after the unpack) and notable (> 1.5) tests read the flags alone.
          expect(Math.floor(f32[0])).toBe(flags)
        }
      }
    }
  })

  it('maps an unknown form to the default, and a tree to the same turn every batch', () => {
    expect(CROWN_FORMS[crownFormIndex('not-a-form')]).toBe('default')
    expect(CROWN_FORMS[crownFormIndex('palm')]).toBe('palm')
    const turn = crownTurn(-122.4194, 37.7749)
    expect(crownTurn(-122.4194, 37.7749)).toBe(turn)
    expect(turn).toBeGreaterThanOrEqual(0)
    expect(turn).toBeLessThan(CROWN_TURNS)
    const turns = new Set(Array.from({ length: 200 }, (_, i) => crownTurn(-122.4 + i * 1e-4, 37.77 + i * 7e-5)))
    expect(turns.size).toBe(CROWN_TURNS)
  })
})
