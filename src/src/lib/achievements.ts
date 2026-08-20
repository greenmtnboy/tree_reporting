/**
 * Achievement definitions and the pure evaluator that scores them.
 *
 * Achievements are derived entirely from the user's own contribution history
 * (submissions + check-ins) — nothing is stored server-side. Tree facts a
 * check-in needs (species, form, size, rarity) are snapshotted onto the
 * check-in document at write time, because the parquet data for the tree's
 * city may not be loaded when achievements are evaluated. Older check-ins
 * without those fields simply don't count toward the tree-fact achievements.
 */

/** The subset of a submission that achievements read. */
export interface AchievementSubmission {
  city: string
  species: string | null
  submittedAt: Date | null
  /** Total photos attached (main + additional). */
  photoCount: number
}

/** The subset of a check-in that achievements read. */
export interface AchievementCheckin {
  city: string
  at: Date | null
  hasPhoto: boolean
  /** Snapshotted from the tree at check-in time; null on older check-ins. */
  species: string | null
  treeForm: string | null
  dbhInches: number | null
  plantYear: number | null
  /** How many trees of this species existed in the tree's city at check-in. */
  speciesCityCount: number | null
}

export interface ContributionStats {
  submissionCount: number
  checkinCount: number
  contributionCount: number
  photoCheckinCount: number
  identifiedContributionCount: number
  distinctSpeciesCount: number
  distinctCityCount: number
  distinctFormCount: number
  distinctDayCount: number
  palmCheckinCount: number
  coniferCheckinCount: number
  rareFindCount: number
  onlyOneInCityCount: number
  giantCheckinCount: number
  oldGrowthCheckinCount: number
  dawnCheckinCount: number
  nightCheckinCount: number
  multiPhotoSubmissionCount: number
}

export interface AchievementDef {
  id: string
  emoji: string
  title: string
  description: string
  target: number
  metric: keyof ContributionStats
}

export interface AchievementState extends AchievementDef {
  progress: number
  earned: boolean
}

const GIANT_DBH_INCHES = 40
const OLD_GROWTH_YEARS = 50
const RARE_SPECIES_CITY_MAX = 10
const DAWN_HOUR_END = 7
const NIGHT_HOUR_START = 21

export const ACHIEVEMENTS: AchievementDef[] = [
  // Firsts
  { id: 'first-submission', emoji: '🌱', title: 'First Roots', description: 'Submit your first tree', target: 1, metric: 'submissionCount' },
  { id: 'first-checkin', emoji: '📍', title: 'Say Hello', description: 'Check in on a tree for the first time', target: 1, metric: 'checkinCount' },
  { id: 'first-photo-checkin', emoji: '📸', title: 'Portrait Mode', description: 'Attach a photo to a check-in', target: 1, metric: 'photoCheckinCount' },
  { id: 'first-identified', emoji: '🔬', title: 'Nice to Meet You', description: 'Contribute a tree with its species identified', target: 1, metric: 'identifiedContributionCount' },
  { id: 'multi-photo', emoji: '🎞️', title: 'Full Coverage', description: 'Submit a tree with 3 or more photos', target: 1, metric: 'multiPhotoSubmissionCount' },

  // Submission milestones
  { id: 'submit-5', emoji: '🌿', title: 'Grove Starter', description: 'Submit 5 trees', target: 5, metric: 'submissionCount' },
  { id: 'submit-10', emoji: '🌳', title: 'Block Botanist', description: 'Submit 10 trees', target: 10, metric: 'submissionCount' },
  { id: 'submit-50', emoji: '🏞️', title: 'Canopy Builder', description: 'Submit 50 trees', target: 50, metric: 'submissionCount' },
  { id: 'submit-250', emoji: '🌆', title: 'Urban Forester', description: 'Submit 250 trees', target: 250, metric: 'submissionCount' },

  // Check-in milestones
  { id: 'checkin-5', emoji: '👣', title: 'Tree Trekker', description: 'Check in on 5 trees', target: 5, metric: 'checkinCount' },
  { id: 'checkin-10', emoji: '🐕', title: 'Bark Ranger', description: 'Check in on 10 trees', target: 10, metric: 'checkinCount' },
  { id: 'checkin-50', emoji: '🥾', title: 'Trail Blazer', description: 'Check in on 50 trees', target: 50, metric: 'checkinCount' },
  { id: 'checkin-250', emoji: '🌀', title: 'Force of Nature', description: 'Check in on 250 trees', target: 250, metric: 'checkinCount' },

  // Species diversity
  { id: 'species-5', emoji: '📖', title: 'Budding Botanist', description: 'Contribute 5 different species', target: 5, metric: 'distinctSpeciesCount' },
  { id: 'species-15', emoji: '🗺️', title: 'Field Guide', description: 'Contribute 15 different species', target: 15, metric: 'distinctSpeciesCount' },
  { id: 'species-40', emoji: '🎓', title: 'Dendrologist', description: 'Contribute 40 different species', target: 40, metric: 'distinctSpeciesCount' },

  // Tree forms
  { id: 'palm', emoji: '🌴', title: 'Palm Reader', description: 'Check in on a palm', target: 1, metric: 'palmCheckinCount' },
  { id: 'conifer-5', emoji: '🌲', title: 'Conifer Collector', description: 'Check in on 5 conifers', target: 5, metric: 'coniferCheckinCount' },
  { id: 'forms-4', emoji: '🔷', title: 'Shape Shifter', description: 'Check in on 4 different tree forms', target: 4, metric: 'distinctFormCount' },

  // Rarity
  { id: 'rare-find', emoji: '💎', title: 'Rare Find', description: `Check in on a species with ${RARE_SPECIES_CITY_MAX} or fewer trees in its city`, target: 1, metric: 'rareFindCount' },
  { id: 'one-of-one', emoji: '🦄', title: 'One of One', description: "Check in on the only tree of its species in the city", target: 1, metric: 'onlyOneInCityCount' },

  // Size and age
  { id: 'giant', emoji: '🐘', title: 'Gentle Giant', description: `Check in on a tree at least ${GIANT_DBH_INCHES}″ across`, target: 1, metric: 'giantCheckinCount' },
  { id: 'old-growth', emoji: '🕰️', title: 'Old Soul', description: `Check in on a tree planted ${OLD_GROWTH_YEARS}+ years ago`, target: 1, metric: 'oldGrowthCheckinCount' },

  // Geography
  { id: 'cities-2', emoji: '✈️', title: 'Branching Out', description: 'Contribute in 2 different cities', target: 2, metric: 'distinctCityCount' },
  { id: 'cities-5', emoji: '🌍', title: 'World Canopy', description: 'Contribute in 5 different cities', target: 5, metric: 'distinctCityCount' },

  // Habits
  { id: 'dawn', emoji: '🌅', title: 'Dawn Chorus', description: `Check in before ${DAWN_HOUR_END} am`, target: 1, metric: 'dawnCheckinCount' },
  { id: 'night', emoji: '🦉', title: 'Night Owl', description: 'Check in after dark (9 pm or later)', target: 1, metric: 'nightCheckinCount' },
  { id: 'days-7', emoji: '📆', title: 'Seven Rings', description: 'Contribute on 7 different days', target: 7, metric: 'distinctDayCount' },
]

function normalizeSpecies(value: string | null): string | null {
  const v = value?.trim().toLowerCase()
  if (!v || v === 'unknown') return null
  return v
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

export function buildStats(
  submissions: AchievementSubmission[],
  checkins: AchievementCheckin[],
): ContributionStats {
  const species = new Set<string>()
  const cities = new Set<string>()
  const forms = new Set<string>()
  const days = new Set<string>()

  let photoCheckins = 0
  let identified = 0
  let palms = 0
  let conifers = 0
  let rare = 0
  let onlyOne = 0
  let giants = 0
  let oldGrowth = 0
  let dawn = 0
  let night = 0
  let multiPhoto = 0

  const currentYear = new Date().getFullYear()

  for (const s of submissions) {
    const sp = normalizeSpecies(s.species)
    if (sp) {
      species.add(sp)
      identified += 1
    }
    if (s.city) cities.add(s.city.toUpperCase())
    if (s.submittedAt) days.add(dayKey(s.submittedAt))
    if (s.photoCount >= 3) multiPhoto += 1
  }

  for (const c of checkins) {
    const sp = normalizeSpecies(c.species)
    if (sp) {
      species.add(sp)
      identified += 1
    }
    if (c.city) cities.add(c.city.toUpperCase())
    if (c.at) {
      days.add(dayKey(c.at))
      const hour = c.at.getHours()
      if (hour < DAWN_HOUR_END) dawn += 1
      if (hour >= NIGHT_HOUR_START) night += 1
    }
    if (c.hasPhoto) photoCheckins += 1
    if (c.treeForm) {
      forms.add(c.treeForm)
      if (c.treeForm === 'palm') palms += 1
      if (c.treeForm === 'conifer') conifers += 1
    }
    if (c.speciesCityCount != null && c.speciesCityCount > 0) {
      if (c.speciesCityCount <= RARE_SPECIES_CITY_MAX) rare += 1
      if (c.speciesCityCount === 1) onlyOne += 1
    }
    if (c.dbhInches != null && c.dbhInches >= GIANT_DBH_INCHES) giants += 1
    if (c.plantYear != null && currentYear - c.plantYear >= OLD_GROWTH_YEARS) oldGrowth += 1
  }

  return {
    submissionCount: submissions.length,
    checkinCount: checkins.length,
    contributionCount: submissions.length + checkins.length,
    photoCheckinCount: photoCheckins,
    identifiedContributionCount: identified,
    distinctSpeciesCount: species.size,
    distinctCityCount: cities.size,
    distinctFormCount: forms.size,
    distinctDayCount: days.size,
    palmCheckinCount: palms,
    coniferCheckinCount: conifers,
    rareFindCount: rare,
    onlyOneInCityCount: onlyOne,
    giantCheckinCount: giants,
    oldGrowthCheckinCount: oldGrowth,
    dawnCheckinCount: dawn,
    nightCheckinCount: night,
    multiPhotoSubmissionCount: multiPhoto,
  }
}

/** Adapt an app submission record (structural — no Firestore import here). */
export function toAchievementSubmission(s: {
  city: string
  species: string | null
  submittedAt: Date | null
  additionalPhotoPaths: string[]
}): AchievementSubmission {
  return {
    city: s.city,
    species: s.species,
    submittedAt: s.submittedAt,
    photoCount: 1 + s.additionalPhotoPaths.length,
  }
}

/** Adapt an app check-in record (structural — no Firestore import here). */
export function toAchievementCheckin(c: {
  city: string
  at: Date | null
  photoPath: string | null
  species: string | null
  treeForm: string | null
  dbhInches: number | null
  plantYear: number | null
  speciesCityCount: number | null
}): AchievementCheckin {
  return {
    city: c.city,
    at: c.at,
    hasPhoto: c.photoPath != null,
    species: c.species,
    treeForm: c.treeForm,
    dbhInches: c.dbhInches,
    plantYear: c.plantYear,
    speciesCityCount: c.speciesCityCount,
  }
}

export function evaluateAchievements(
  submissions: AchievementSubmission[],
  checkins: AchievementCheckin[],
): AchievementState[] {
  const stats = buildStats(submissions, checkins)
  return ACHIEVEMENTS.map((def) => {
    const progress = Math.min(stats[def.metric], def.target)
    return { ...def, progress, earned: progress >= def.target }
  })
}

/**
 * Extract a plant year from the popup row's plant_date, which DuckDB may
 * surface as an ISO string, an epoch-milliseconds number, or a Date.
 */
export function plantYearFrom(value: string | number | Date | null | undefined): number | null {
  if (value == null) return null
  if (value instanceof Date) return value.getFullYear()
  if (typeof value === 'number') {
    const year = new Date(value).getUTCFullYear()
    return Number.isFinite(year) ? year : null
  }
  const match = /^(\d{4})/.exec(String(value))
  return match ? Number(match[1]) : null
}
