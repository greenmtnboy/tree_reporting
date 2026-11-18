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
      let occupied = 0, outer = 0
      for (let j = -100; j <= 100; j++) for (let i = -100; i <= 100; i++) {
        const x = i / 100, y = j / 100
        const shape = crownShape(form, x, y, 'leafless')
        expect(Number.isFinite(shape.edge), form).toBe(true)
        if (shape.edge < 0) {
          const radius = Math.hypot(x, y)
          expect(radius, form).toBeLessThanOrEqual(1)
          outer = Math.max(outer, radius)
          occupied++
        }
      }
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
