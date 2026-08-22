/**
 * A tiny, hand-built inventory the dashboard queries can be *executed* against.
 *
 * Compiling a query only proves the planner produced SQL. It does not prove the
 * SQL answers the question — a join that loses its key still compiles, and then
 * evaluates the filter against unrelated rows. So the compile test seeds these
 * rows into DuckDB, runs the generated SQL, and checks the numbers.
 *
 * The rows are chosen so that every cross-filter dimension splits them: each
 * bucket the dashboard can filter on has at least one tree in it and at least
 * one tree out of it, which is what makes "the filter did nothing" detectable.
 * Expectations are derived from these rows by the reference implementation
 * below rather than hardcoded, so changing a fixture cannot leave a stale
 * expected number behind.
 */

export type FixtureEcoregion = {
  ecoregion_id: number
  ecoregion_name: string
  biome: string
  realm: string
}

export type FixtureSpecies = {
  species: string
  genus: string
  common_names: string[]
  tree_form: string
  native_ecoregions: number[] | null
  usda_zone_min: number | null
  usda_zone_max: number | null
  water_needs: string | null
  drought_tolerance: string | null
  sun_exposure: string[]
  lifespan_min_years: number | null
  lifespan_max_years: number | null
  growth_rate: string | null
  wildlife_value: string | null
  fire_risk: string | null
  description: string
  photo_url: string | null
  photo_attribution: string | null
  mature_height_min_ft: number | null
  mature_height_max_ft: number | null
  canopy_spread_min_ft: number | null
  canopy_spread_max_ft: number | null
  bloom_months: number[]
  is_evergreen: boolean | null
}

export type FixtureTree = {
  tree_id: string
  city: string
  species: string
  data_source: string
  plant_date: string | null
  diameter_at_breast_height: number | null
  latitude: number
  longitude: number
}

// San Francisco's own ecoregion is 423 (see dashboardContextSource.ts); 425
// shares its biome, 339 shares only its realm, 664 shares neither.
export const FIXTURE_ECOREGIONS: FixtureEcoregion[] = [
  {
    ecoregion_id: 423,
    ecoregion_name: 'California coastal sage and chaparral',
    biome: 'Mediterranean Forests, Woodlands & Scrub',
    realm: 'nearctic',
  },
  {
    ecoregion_id: 425,
    ecoregion_name: 'California interior chaparral and woodlands',
    biome: 'Mediterranean Forests, Woodlands & Scrub',
    realm: 'nearctic',
  },
  {
    ecoregion_id: 339,
    ecoregion_name: 'Northeastern coastal forests',
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'nearctic',
  },
  {
    ecoregion_id: 664,
    ecoregion_name: 'Western European broadleaf forests',
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'palearctic',
  },
]

export const FIXTURE_SPECIES: FixtureSpecies[] = [
  {
    species: 'Quercus agrifolia',
    genus: 'Quercus',
    common_names: ['Coast live oak', 'California live oak'],
    tree_form: 'broadleaf',
    native_ecoregions: [423],
    usda_zone_min: 8,
    usda_zone_max: 10,
    water_needs: 'low',
    drought_tolerance: 'high',
    sun_exposure: ['full_sun'],
    lifespan_min_years: 150,
    lifespan_max_years: 250,
    growth_rate: 'slow',
    wildlife_value: 'high',
    fire_risk: 'low',
    description: 'An evergreen oak native to the California coast.',
    photo_url: 'https://example.invalid/quercus.jpg',
    photo_attribution: 'Fixture',
    mature_height_min_ft: 30,
    mature_height_max_ft: 80,
    canopy_spread_min_ft: 25,
    canopy_spread_max_ft: 70,
    bloom_months: [3, 4],
    is_evergreen: true,
  },
  {
    species: 'Platanus x hispanica',
    genus: 'Platanus',
    common_names: ['London plane'],
    tree_form: 'broadleaf',
    // Same biome as SF, different ecoregion.
    native_ecoregions: [425],
    usda_zone_min: 5,
    usda_zone_max: 9,
    water_needs: 'high',
    drought_tolerance: 'low',
    sun_exposure: ['full_sun', 'partial_shade'],
    lifespan_min_years: 50,
    lifespan_max_years: 100,
    growth_rate: 'fast',
    wildlife_value: 'moderate',
    fire_risk: 'moderate',
    description: 'A hybrid plane widely planted as a street tree.',
    photo_url: null,
    photo_attribution: null,
    mature_height_min_ft: 50,
    mature_height_max_ft: 100,
    canopy_spread_min_ft: 40,
    canopy_spread_max_ft: 80,
    bloom_months: [4],
    is_evergreen: false,
  },
  {
    species: 'Acer rubrum',
    genus: 'Acer',
    common_names: ['Red maple'],
    tree_form: 'spreading',
    // Same realm as SF, different biome.
    native_ecoregions: [339],
    usda_zone_min: 3,
    usda_zone_max: 9,
    water_needs: 'moderate',
    drought_tolerance: 'moderate',
    sun_exposure: ['partial_shade'],
    lifespan_min_years: 60,
    lifespan_max_years: 120,
    growth_rate: 'moderate',
    wildlife_value: 'moderate',
    fire_risk: 'moderate',
    description: 'A fast-growing maple of eastern North America.',
    photo_url: null,
    photo_attribution: null,
    mature_height_min_ft: 40,
    mature_height_max_ft: 70,
    canopy_spread_min_ft: 30,
    canopy_spread_max_ft: 50,
    bloom_months: [3],
    is_evergreen: false,
  },
  {
    species: 'Tilia cordata',
    genus: 'Tilia',
    common_names: ['Small-leaved lime'],
    tree_form: 'columnar',
    // Neither SF's biome nor its realm.
    native_ecoregions: [664],
    usda_zone_min: 3,
    usda_zone_max: 7,
    water_needs: 'moderate',
    drought_tolerance: 'low',
    sun_exposure: ['shade'],
    lifespan_min_years: 20,
    lifespan_max_years: 40,
    growth_rate: 'moderate',
    wildlife_value: 'low',
    fire_risk: 'high',
    description: 'A European lime planted for its scented flowers.',
    photo_url: null,
    photo_attribution: null,
    mature_height_min_ft: 40,
    mature_height_max_ft: 70,
    canopy_spread_min_ft: 25,
    canopy_spread_max_ft: 40,
    bloom_months: [6, 7],
    is_evergreen: false,
  },
  {
    // A sentinel, not a taxon: the row exists so `species` survives the join to
    // enrichment, and its values are authored (mirroring
    // src/src/data/species.ts) rather than guessed by a model.
    species: 'Unknown',
    genus: '',
    common_names: ['Species not recorded'],
    tree_form: 'default',
    native_ecoregions: null,
    usda_zone_min: null,
    usda_zone_max: null,
    water_needs: null,
    drought_tolerance: null,
    sun_exposure: [],
    lifespan_min_years: null,
    lifespan_max_years: null,
    growth_rate: null,
    wildlife_value: null,
    fire_risk: null,
    description: 'This tree is in the inventory, but its source did not record a species.',
    photo_url: null,
    photo_attribution: null,
    mature_height_min_ft: null,
    mature_height_max_ft: null,
    canopy_spread_min_ft: null,
    canopy_spread_max_ft: null,
    bloom_months: [],
    is_evergreen: null,
  },
]

// `Unknown` is a species sentinel and deliberately has no enrichment row: it
// exercises the unenriched path, where the tree joins to nulls and each derived
// bucket falls to whatever its null branch yields.
export const UNENRICHED_SPECIES = 'Unknown'

export const FIXTURE_TREES: FixtureTree[] = [
  { tree_id: 'fx-1', city: 'USSFO', species: 'Quercus agrifolia', data_source: 'SF_OPENDATA', plant_date: '1998-03-04', diameter_at_breast_height: 30, latitude: 37.771, longitude: -122.411 },
  { tree_id: 'fx-2', city: 'USSFO', species: 'Quercus agrifolia', data_source: 'SF_OPENDATA', plant_date: '2015-11-20', diameter_at_breast_height: 8, latitude: 37.772, longitude: -122.412 },
  { tree_id: 'fx-3', city: 'USSFO', species: 'Platanus x hispanica', data_source: 'SF_OPENDATA', plant_date: '2004-06-01', diameter_at_breast_height: 20, latitude: 37.773, longitude: -122.413 },
  { tree_id: 'fx-4', city: 'USSFO', species: 'Acer rubrum', data_source: 'SF_OPENDATA', plant_date: null, diameter_at_breast_height: 14, latitude: 37.774, longitude: -122.414 },
  { tree_id: 'fx-5', city: 'USSFO', species: 'Tilia cordata', data_source: 'SF_OPENDATA', plant_date: '1975-04-15', diameter_at_breast_height: 40, latitude: 37.775, longitude: -122.415 },
  { tree_id: 'fx-6', city: 'USSFO', species: 'Tilia cordata', data_source: 'COMMUNITY_USSFO', plant_date: '2021-09-09', diameter_at_breast_height: 4, latitude: 37.776, longitude: -122.416 },
  { tree_id: 'fx-7', city: 'USSFO', species: UNENRICHED_SPECIES, data_source: 'SF_OPENDATA', plant_date: null, diameter_at_breast_height: null, latitude: 37.777, longitude: -122.417 },
  // A second city, so the all-cities view has something to aggregate across and
  // a `city = 'USSFO'` filter has something to exclude.
  { tree_id: 'fx-8', city: 'USNYC', species: 'Acer rubrum', data_source: 'NYC_OPENDATA', plant_date: '2010-05-05', diameter_at_breast_height: 18, latitude: 40.712, longitude: -74.006 },
  { tree_id: 'fx-9', city: 'USNYC', species: 'Tilia cordata', data_source: 'NYC_OPENDATA', plant_date: '1990-01-30', diameter_at_breast_height: 26, latitude: 40.713, longitude: -74.007 },
]

const SPECIES_BY_NAME = new Map(FIXTURE_SPECIES.map((entry) => [entry.species, entry]))
const ECOREGION_BY_ID = new Map(FIXTURE_ECOREGIONS.map((entry) => [entry.ecoregion_id, entry]))

/**
 * The city context the dashboard compiles into `dashboard_context`. Mirrors
 * `buildDashboardContextParameters`, restated here so the reference buckets do
 * not depend on the code under test.
 */
export type FixtureCityContext = {
  ecoregionId: number
  usdaZone: number
  biome: string
  realm: string
}

/**
 * The reference implementation of the derived buckets in
 * `buildDashboardContextSource`. A tree joins enrichment on species, and
 * enrichment reaches ecoregion through `unnest(native_ecoregions)` — so a tree
 * whose species is native to several ecoregions produces several rows, and the
 * bucket is the best match among them, in the CASE's order.
 *
 * An unenriched tree is not an absent row: it joins to nulls and the CASE runs
 * anyway, so each bucket returns whatever its null path yields. That is why
 * `Unknown` lands in "Non-Native, Different Biome" rather than dropping out —
 * verified against the generated SQL, not assumed.
 */
export function nativeLocalityBucket(tree: FixtureTree, context: FixtureCityContext): string | null {
  const species = SPECIES_BY_NAME.get(tree.species)
  if (!species) return 'Non-Native, Different Biome'
  const ecoregions = (species.native_ecoregions ?? [])
    .map((id) => ECOREGION_BY_ID.get(id))
    .filter(Boolean) as FixtureEcoregion[]
  if (ecoregions.some((entry) => entry.ecoregion_id === context.ecoregionId)) return 'Native'
  if (ecoregions.some((entry) => entry.biome === context.biome)) return 'Same biome, non-native'
  if (ecoregions.some((entry) => entry.realm === context.realm)) return 'Native Region, Different Biome'
  return 'Non-Native, Different Biome'
}

export function hardinessFitBucket(tree: FixtureTree, context: FixtureCityContext): string | null {
  const species = SPECIES_BY_NAME.get(tree.species)
  if (!species) return 'Unknown'
  const { usda_zone_min: min, usda_zone_max: max } = species
  if (min == null || max == null) return 'Unknown'
  if (context.usdaZone < min || context.usdaZone > max) return 'Outside zone'
  if (context.usdaZone === min || context.usdaZone === max) return 'Edge of tolerance'
  return 'Well within zone'
}

export function waterResilienceBucket(tree: FixtureTree): string | null {
  const species = SPECIES_BY_NAME.get(tree.species)
  if (!species) return 'Unknown'
  const { water_needs: water, drought_tolerance: drought } = species
  if (water === 'high' && drought === 'low') return 'High water / low drought tolerance'
  if (water === 'low' && drought === 'high') return 'Low water / high drought tolerance'
  if (water == null || drought == null) return 'Unknown'
  return 'Moderate / mixed'
}

export function lifespanBucket(tree: FixtureTree): string | null {
  const species = SPECIES_BY_NAME.get(tree.species)
  if (!species) return 'Unknown'
  const { lifespan_min_years: min, lifespan_max_years: max } = species
  if (max == null && min == null) return 'Unknown'
  const years = max ?? min ?? 0
  if (years < 50) return 'Short-lived (<50y)'
  if (years < 150) return 'Medium-lived (50-149y)'
  return 'Long-lived (150+y)'
}

export function sunExposureLabel(tree: FixtureTree): string | null {
  const species = SPECIES_BY_NAME.get(tree.species)
  if (!species) return null
  if (species.sun_exposure.includes('shade')) return 'Shade'
  if (species.sun_exposure.includes('partial_shade')) return 'Partial shade'
  if (species.sun_exposure.includes('full_sun')) return 'Full sun'
  return null
}

function speciesField(tree: FixtureTree, field: keyof FixtureSpecies): string | null {
  const species = SPECIES_BY_NAME.get(tree.species)
  if (!species) return null
  const value = species[field]
  return typeof value === 'string' ? value : null
}

/** The value a cross-filterable dimension takes for a fixture tree. */
export function fixtureFieldValue(
  tree: FixtureTree,
  field: string,
  context: FixtureCityContext,
): string | null {
  switch (field) {
    case 'species':
      return tree.species
    case 'tree_form':
      return speciesField(tree, 'tree_form')
    case 'growth_rate':
      return speciesField(tree, 'growth_rate')
    case 'wildlife_value':
      return speciesField(tree, 'wildlife_value')
    case 'fire_risk':
      return speciesField(tree, 'fire_risk')
    case 'native_locality_bucket':
      return nativeLocalityBucket(tree, context)
    case 'hardiness_fit_bucket':
      return hardinessFitBucket(tree, context)
    case 'water_resilience_bucket':
      return waterResilienceBucket(tree)
    case 'lifespan_bucket':
      return lifespanBucket(tree)
    case 'sun_exposure_label':
      return sunExposureLabel(tree)
    default:
      throw new Error(`No fixture reference for cross-filter field ${field}`)
  }
}

/**
 * A cross-filter value that each dimension splits the fixtures on: at least one
 * tree matches and at least one does not. Derived rather than written down, so
 * it cannot go stale.
 *
 * Returns null when the dimension cannot split them at all — which is a real
 * case, not a fixture gap: in the all-cities view there is no active ecoregion,
 * biome or realm to compare against, so every tree is "Non-Native, Different
 * Biome" and filtering on nativeness there proves nothing. The caller drops the
 * dimension rather than testing a filter that cannot fail.
 */
export function splittingValueFor(field: string, context: FixtureCityContext): string | null {
  const values = FIXTURE_TREES.map((tree) => fixtureFieldValue(tree, field, context))
  const counts = new Map<string, number>()
  for (const value of values) {
    if (value == null) continue
    counts.set(value, (counts.get(value) ?? 0) + 1)
  }
  const total = values.length
  for (const [value, count] of [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
    if (count < total) return value
  }
  return null
}
