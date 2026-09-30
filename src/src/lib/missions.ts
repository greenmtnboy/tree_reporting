import { CITY_CONFIG } from '../composables/useMapData'
import { getCityBiome } from '../composables/dashboardContextSource'

export type RarityTier = 'rare' | 'unusual' | 'common'
export interface RankingSnapshot {
  rarityTier: RarityTier | null
  trunkRank: number | null
  canopyRank: number | null
}
export interface RankedTree extends RankingSnapshot {
  treeId: string
  city: string
  species: string
  treeForm: string | null
  lat: number
  lng: number
}
export interface MissionCheckin {
  treeId: string
  city: string
  treeForm: string | null
  ranking?: RankingSnapshot | null
}
export const MISSION_DEFS = [
  { key: 'trunk', title: 'Trunk Champion', emoji: '🏆', target: 1, points: 150, instruction: 'Find a tree with the widest recorded trunk', matches: (t: MissionCheckin) => t.ranking?.trunkRank === 1 },
  { key: 'canopy', title: 'Under the Canopy', emoji: '☂️', target: 1, points: 150, instruction: 'Find a tree with the widest recorded canopy', matches: (t: MissionCheckin) => t.ranking?.canopyRank === 1 },
  { key: 'rare', title: 'Rare Encounters', emoji: '💎', target: 5, points: 200, instruction: 'Check in on 5 different rare trees', matches: (t: MissionCheckin) => t.ranking?.rarityTier === 'rare' },
  { key: 'unusual', title: 'Out of the Ordinary', emoji: '🔎', target: 5, points: 100, instruction: 'Check in on 5 different unusual trees', matches: (t: MissionCheckin) => t.ranking?.rarityTier === 'unusual' },
  { key: 'common', title: 'Neighborhood Regulars', emoji: '🏘️', target: 5, points: 50, instruction: 'Get to know 5 different common trees', matches: (t: MissionCheckin) => t.ranking?.rarityTier === 'common' },
  { key: 'palm', title: 'Palm Reader', emoji: '🌴', target: 3, points: 100, instruction: 'Check in on 3 different palms', matches: (t: MissionCheckin) => t.treeForm === 'palm' },
  { key: 'conifer', title: 'Evergreen Explorer', emoji: '🌲', target: 5, points: 100, instruction: 'Check in on 5 different conifers', matches: (t: MissionCheckin) => t.treeForm === 'conifer' },
] as const

export function evaluateCityBadges(checkins: MissionCheckin[]) {
  // A ranking snapshot marks the new mission era. Older visits still count
  // toward existing badges, but cannot retroactively invent ranking facts.
  const cities = [...new Set(checkins.filter(c => c.ranking).map(c => c.city))]
  return cities.flatMap(city => MISSION_DEFS.map(def => {
    const progress = Math.min(def.target, new Set(checkins
      .filter(c => c.city === city && c.ranking && def.matches(c))
      .map(c => c.treeId)).size)
    return { ...def, id: `city:${city}:${def.key}`, city,
      title: `${CITY_CONFIG[city]?.name ?? city}: ${def.title}`,
      description: `${def.instruction} in ${CITY_CONFIG[city]?.name ?? city}`,
      progress, earned: progress >= def.target }
  }))
}

export function buildMissions(city: string, trees: RankedTree[], checkins: MissionCheckin[]) {
  const biome = getCityBiome(city)
  return MISSION_DEFS.flatMap(def => {
    const candidates = trees.filter(t => t.city === city && def.matches({ ...t, ranking: t }))
    // The query supplies up to ten targets per mission, so only offer a mission
    // when enough mapped targets exist. Tied champions are all valid.
    if (candidates.length < def.target) return []
    const visited = new Set(checkins.filter(c => c.city === city && c.ranking && def.matches(c)).map(c => c.treeId))
    const progress = Math.min(def.target, visited.size)
    return [{ ...def, id: `city:${city}:${def.key}`, city, biome,
      description: `${def.instruction} in ${CITY_CONFIG[city]?.name ?? city}`,
      progress, earned: progress >= def.target,
      nextTree: candidates.find(t => !visited.has(t.treeId)),
    }]
  }).sort((a, b) => Number(a.earned) - Number(b.earned)
    || Number(b.key === 'palm' && /Mediterranean|Tropical|Desert/.test(biome))
      - Number(a.key === 'palm' && /Mediterranean|Tropical|Desert/.test(biome)))
}
