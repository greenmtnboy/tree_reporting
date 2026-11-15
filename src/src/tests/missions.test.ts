import { describe, expect, it } from 'vitest'
import { buildMissions, evaluateCityBadges, type RankedTree } from '../lib/missions'

const tree = (id: string, city = 'USBOS'): RankedTree => ({
  treeId: id, city, species: 'Quercus rubra', treeForm: 'broadleaf',
  lat: 42, lng: -71, rarityTier: 'rare', trunkRank: 1, canopyRank: null,
})
const visit = (t: RankedTree) => ({ ...t, ranking: t })

describe('city missions', () => {
  it('counts distinct trees, keeps cities separate, and counts tied champions', () => {
    const trees = Array.from({ length: 5 }, (_, i) => tree(String(i)))
    const visits = [...trees.map(visit), visit(trees[0]!), visit(tree('elsewhere', 'USLAX'))]
    const earned = evaluateCityBadges(visits).filter(b => b.earned)
    expect(earned.filter(b => b.city === 'USBOS').map(b => b.key)).toEqual(['trunk', 'rare'])
    expect(earned.filter(b => b.city === 'USBOS').reduce((s, b) => s + b.points, 0)).toBe(350)
    expect(buildMissions('USBOS', trees, [visit(trees[1]!)])[0]?.earned).toBe(false)
    expect(buildMissions('USBOS', trees, [visit(trees[1]!)]).find(m => m.key === 'trunk')?.earned).toBe(true)
  })

  it('requires enough targets, skips visited targets, and never infers legacy ranks', () => {
    const t = tree('a')
    expect(buildMissions('USBOS', [t], []).map(m => m.key)).toEqual(['trunk'])
    expect(evaluateCityBadges([{ ...t, ranking: null }])).toEqual([])
    const trees = Array.from({ length: 5 }, (_, i) => tree(String(i)))
    const mission = buildMissions('USBOS', trees, [visit(trees[0]!)]).find(m => m.key === 'rare')!
    expect(mission.progress).toBe(1)
    expect(mission.nextTree?.treeId).toBe('1')
  })

  it('sends people to flagged trees for Second Opinions, which needs no ranking', () => {
    const targets = ['f1', 'f2', 'f3', 'f4'].map(treeId => ({ treeId, city: 'USSFO' }))
    const flaggedVisit = (treeId: string) => ({ treeId, city: 'USSFO', treeForm: null, ranking: null, reviewFlag: 'dbh_outlier' })
    expect(buildMissions('USSFO', [], [], targets.slice(0, 2)).map(m => m.key)).toEqual([])
    const fresh = buildMissions('USSFO', [], [], targets).find(m => m.key === 'second-look')!
    expect(fresh.nextTree?.treeId).toBe('f1')
    const visits = [flaggedVisit('f1'), flaggedVisit('f1'), flaggedVisit('f2')]
    const mission = buildMissions('USSFO', [], visits, targets).find(m => m.key === 'second-look')!
    expect(mission.progress).toBe(2)
    expect(mission.nextTree?.treeId).toBe('f3')
    expect(evaluateCityBadges(visits).filter(b => b.earned)).toEqual([])
    const earned = evaluateCityBadges([...visits, flaggedVisit('f3')]).filter(b => b.earned)
    expect(earned.map(b => b.id)).toEqual(['city:USSFO:second-look'])
  })

  it('offers palms where available and prioritizes them in LA’s biome', () => {
    const trees = Array.from({ length: 3 }, (_, i) => ({ ...tree(String(i), 'USLAX'), treeForm: 'palm' }))
    expect(buildMissions('USLAX', trees, [])[0]?.key).toBe('palm')
    expect(buildMissions('USBOS', trees, [])).toEqual([])
  })
})
