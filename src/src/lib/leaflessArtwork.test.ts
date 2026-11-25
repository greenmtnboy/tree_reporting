import { describe, expect, it } from 'vitest'
import { CROWN_FORMS, crownShape } from './crownShapes'
import { treeArtwork } from './treeArtwork'

describe('leafless artwork', () => {
  it('keeps summer sizing while replacing foliage and flowers with finite bare limbs', () => {
    for (const form of CROWN_FORMS) {
      const summer = treeArtwork(form)
      const bare = treeArtwork(form, 'leafless')
      expect(bare.crownFraction, form).toBe(summer.crownFraction)
      expect(bare.crownTop, form).toBe(summer.crownTop)
      expect(bare.foliage).toEqual([])
      expect(bare.blooms).toEqual([])
      expect(bare.trunk.length).toBeGreaterThan(8)
      for (const shape of bare.trunk) {
        if (shape.kind !== 'polygon') throw new Error('Expected tapered branch')
        for (const point of shape.points) for (const coordinate of point) {
          expect(Number.isFinite(coordinate), form).toBe(true)
          expect(coordinate, form).toBeGreaterThanOrEqual(0)
          expect(coordinate, form).toBeLessThanOrEqual(1)
        }
      }
    }
  })

  it('draws sparse distinct branching inside the measured crown disc', () => {
    const areas: number[] = []
    for (const form of CROWN_FORMS) {
      // 40k samples per form: the bad points are collected and asserted once,
      // because an `expect` per sample cost ~20x the sampling itself and ran
      // this test into vitest's 5 s timeout on slower CI runners.
      let occupied = 0, outer = 0
      const nonFinite: [number, number][] = []
      const outsideDisc: [number, number][] = []
      for (let j = -100; j <= 100; j++) for (let i = -100; i <= 100; i++) {
        const x = i / 100, y = j / 100
        const shape = crownShape(form, x, y, 'leafless')
        if (!Number.isFinite(shape.edge)) nonFinite.push([x, y])
        if (shape.edge < 0) {
          const radius = Math.hypot(x, y)
          if (radius > 1) outsideDisc.push([x, y])
          outer = Math.max(outer, radius)
          occupied++
        }
      }
      expect(nonFinite, form).toEqual([])
      expect(outsideDisc, form).toEqual([])
      expect(crownShape(form, 0, 0, 'leafless').edge, form).toBeLessThan(0)
      expect(outer, form).toBeGreaterThan(.96)
      expect(occupied, form).toBeGreaterThan(100)
      expect(occupied, form).toBeLessThan(5000)
      areas.push(occupied)
    }
    expect(new Set(areas).size).toBe(CROWN_FORMS.length)
  })
  it('roots three close, splayed multi-trunk leaders in one stool', () => {
    expect(crownShape('multi_trunk', 0, 0, 'leafless').edge).toBeLessThan(0)
    for (const [x, y] of [[.05, .24], [-.22, -.08], [.24, -.15]]) {
      expect(crownShape('multi_trunk', x, y, 'leafless').edge).toBeLessThan(0)
    }
  })

})
