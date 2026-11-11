import type { ChartConfig, DashboardImport } from '@trilogy-data/trilogy-studio-components/dashboard'
import { CITY_CONFIG, type CityCode } from './useMapData'
import { REAL_SPECIES_PREDICATE } from '../data/species'

/**
 * How a card is drawn.
 *
 * - `chart`: a Trilogy chart (a time series, a map); it needs a box with axes.
 * - `buckets`: a breakdown into a handful of categories, drawn by the page as
 *   labelled bars (BucketBars.vue). Most cards are this, which is what lets
 *   four of them sit across a wide screen.
 * - `custom`: the page draws it from the rows itself (the species list, the
 *   source split, the stat tiles).
 */
export type DashboardCardKind = 'chart' | 'buckets' | 'custom'

export type DashboardBuckets = {
  /** Result column holding the bucket. */
  labelField: string
  /** Buckets in their natural order. */
  order?: readonly string[]
  /** Display text per bucket. */
  labels?: Record<string, string>
  colors?: Record<string, string>
  /** Buckets that mean "not known", reported as coverage instead of drawn. */
  unknown?: readonly string[]
  /** The cross-filter field a click on a bar sets; omit for a read-only card. */
  filterField?: string
  /**
   * Hide the card while its known buckets cover less of the identified trees
   * than LOW_COVERAGE_THRESHOLD. For the species-enrichment traits, where a
   * field filled in for 29 species out of 4,500 says nothing about a city.
   */
  hideWhenThin?: boolean
}

export type SummaryDashboardChart = {
  id: string
  title: string
  subtitle?: string
  query: string
  kind: DashboardCardKind
  /** The chart to draw for `chart`; for the others, what the chat describes the card as. */
  chartConfig: ChartConfig
  buckets?: DashboardBuckets
  allowCrossFilter?: boolean
  requiresCitySelection?: boolean
}

export type SummaryDashboardKpi = {
  id: string
  label: string
  title: string
  query: string
  xField: string
  requiresCitySelection?: boolean
}

/** Column spans of the page grid; see assets/dashboard.css. */
export type DashboardSpan = 's' | 'm' | 'l' | 'xl'

export type SummaryDashboardSectionCard = {
  id: string
  span: DashboardSpan
}

export type SummaryDashboardSection = {
  id: string
  title: string
  subtitle: string
  cards: SummaryDashboardSectionCard[]
}

/** A trait card is hidden while it is known for less than this share of the identified trees. */
export const LOW_COVERAGE_THRESHOLD = 0.1

export const SUMMARY_DASHBOARD_IMPORTS: DashboardImport[] = [
  { id: 'tree_enrichment', name: 'tree_enrichment', alias: '' },
  { id: 'dashboard_context', name: 'dashboard_context', alias: '' },
]

// Rendered by TreeDotMap on both the summary and species pages. Declared here
// so the dashboard-query compile test covers it alongside the chart queries.
//
// Was broken under a nativeness cross-filter: filtering on a concept reached
// through the enrichment -> ecoregion unnest+merge lost the join axis, and the
// planner emitted a keyless join, so the map showed every tree instead of the
// native ones. That was an upstream planner bug, handed off with a repro rather
// than worked around here, and the deployed resolver has since fixed it —
// dashboard-queries.test.ts is what would notice it coming back.
// The dot map counts trees per map cell rather than reading every tree: about
// 110 m for one city (a pixel or so of the card) and about 1.1 km across all
// cities. Both are summary grains (data/raw/tree_rollups.preql), so the map is
// a re-sum of the cell summary under any species-level filter instead of a
// scan of the tree file.
export const TREE_DOT_MAP_QUERY =
  'SELECT map_cell_latitude, map_cell_longitude, count(tree_id) as tree_count WHERE map_cell_latitude IS NOT NULL AND map_cell_longitude IS NOT NULL;'

export const TREE_DOT_MAP_ALL_CITIES_QUERY =
  'SELECT map_coarse_cell_latitude, map_coarse_cell_longitude, count(tree_id) as tree_count WHERE map_coarse_cell_latitude IS NOT NULL AND map_coarse_cell_longitude IS NOT NULL;'

/** The dot map's query for a page: the fine cells for one city, coarse for all. */
export function treeDotMapQuery(city: string | null | undefined): string {
  return city && city !== 'ALL' ? TREE_DOT_MAP_QUERY : TREE_DOT_MAP_ALL_CITIES_QUERY
}

// --- Palettes ----------------------------------------------------------------
// One meaning per colour across the page: green is the favourable end of a
// scale, ochre the middle, clay the unfavourable end.

const GOOD = 'var(--dash-good)'
const MID = 'var(--dash-mid)'
const POOR = 'var(--dash-poor)'
const SCALE = ['#9cc69b', '#77ad7d', '#548f63', '#3a7250', '#22523a']

function scaleColors(order: readonly string[]): Record<string, string> {
  return Object.fromEntries(order.map((key, index) => [key, SCALE[Math.min(index, SCALE.length - 1)]]))
}

export const NATIVENESS_ORDER = [
  'Native',
  'Same biome, non-native',
  'Native Region, Different Biome',
  'Non-Native, Different Biome',
] as const
export const NATIVENESS_LABELS: Record<string, string> = {
  Native: 'Native here',
  'Same biome, non-native': 'Same biome elsewhere',
  'Native Region, Different Biome': 'Same realm, other biome',
  'Non-Native, Different Biome': 'From another realm',
}
const NATIVENESS_COLORS: Record<string, string> = {
  Native: GOOD,
  'Same biome, non-native': '#6baf92',
  'Native Region, Different Biome': '#b59a54',
  'Non-Native, Different Biome': POOR,
}

export const DBH_ORDER = ['Under 6"', '6–12"', '12–24"', '24–36"', 'Over 36"'] as const
export const CROWN_ORDER = ['Under 3 m', '3–5 m', '5–8 m', '8–12 m', 'Over 12 m'] as const
const LEVELS = ['low', 'moderate', 'high'] as const
const LEVEL_LABELS = { low: 'Low', moderate: 'Moderate', high: 'High', slow: 'Slow', fast: 'Fast' }

export const TREE_FORM_LABELS: Record<string, string> = {
  broadleaf: 'Broadleaf',
  conifer: 'Conifer',
  palm: 'Palm',
  columnar: 'Columnar',
  ornamental: 'Ornamental',
  spreading: 'Spreading',
  weeping: 'Weeping',
  multi_trunk: 'Multi-trunk',
}

export const SOURCE_KIND_ORDER = ['municipal', 'osm', 'community', 'satellite', 'predicted'] as const
export const SOURCE_KIND_LABELS: Record<string, string> = {
  municipal: 'City inventory',
  osm: 'OpenStreetMap',
  community: 'Community reports',
  satellite: 'Aerial imagery, reviewed',
  predicted: 'Aerial imagery, predicted',
}

function bucketQuery(field: string): string {
  return `SELECT
${field},
count(tree_id) as tree_count
WHERE ${field} IS NOT NULL
ORDER BY tree_count DESC;`
}

function barConfig(field: string): ChartConfig {
  return { chartType: 'barh', xField: 'tree_count', yField: field, showTitle: false, hideLegend: true }
}

/** The profile fields a species row carries for SpeciesProfile.vue. */
export const SPECIES_PROFILE_FIELDS = `species,
common_names[1] as common_name,
family,
description,
tree_form,
is_evergreen,
mature_height_min_ft,
mature_height_max_ft,
canopy_spread_min_ft,
canopy_spread_max_ft,
growth_rate,
lifespan_min_years,
lifespan_max_years,
bloom_months,
usda_zone_min,
usda_zone_max,
wildlife_value,
fire_risk,
drought_tolerance,
water_needs,
photo_url,
photo_attribution,
trunk_photo_url,
trunk_photo_attribution`

/** Identified species in the view, most common first, each with its profile. */
export const SPECIES_LIST_QUERY = `SELECT
${SPECIES_PROFILE_FIELDS},
count(tree_id) by species as tree_count
WHERE ${REAL_SPECIES_PREDICATE}
ORDER BY tree_count DESC, species ASC
LIMIT 100;`

/** Crown area by species, for the canopy bar under each species' tree bar. */
export const CANOPY_BY_SPECIES_QUERY = `SELECT
species,
sum(canopy_area_m2) as canopy_area
WHERE ${REAL_SPECIES_PREDICATE}
ORDER BY canopy_area DESC
LIMIT 500;`

// Planting years counted as "recent" for the last-decade card: the year is
// fixed when the bundle is built, which is close enough for a decade.
export const RECENT_PLANTING_FROM = new Date().getFullYear() - 10

/**
 * The page's headline numbers. Each is one small query; the page draws them
 * as tiles and as the sentence under the title.
 */
export const SUMMARY_KPI_CHARTS: SummaryDashboardKpi[] = [
  {
    id: 'total-trees',
    label: 'Trees mapped',
    title: 'Total Trees',
    query: 'SELECT count(tree_id) as total_trees;',
    xField: 'total_trees',
  },
  {
    // A tree is identified when its species is a taxon rather than a
    // placeholder, whatever its source: an imagery detection the model named
    // counts, a city record that says "Unknown" does not.
    id: 'identified-trees',
    label: 'Identified to species',
    title: 'Identified Trees',
    query: `SELECT count(tree_id) as identified_trees WHERE ${REAL_SPECIES_PREDICATE};`,
    xField: 'identified_trees',
  },
  {
    id: 'unique-species',
    label: 'Species',
    title: 'Unique Species',
    query: `SELECT count(species ? count(tree_id) by species > 0) as unique_species WHERE ${REAL_SPECIES_PREDICATE};`,
    xField: 'unique_species',
  },
  {
    id: 'local-native-share',
    label: 'Biome fit',
    title: 'Biome Fit %',
    // Of the identified trees: an unidentified tree has no native range, and
    // counting it as non-native made a city mapped from imagery read 16%.
    query: `import std.display; SELECT (count_distinct(tree_id ? native_locality_bucket = 'Native' or native_locality_bucket = 'Same biome, non-native') / count_distinct(tree_id))::float::percent as biome_fit_pct WHERE ${REAL_SPECIES_PREDICATE};`,
    xField: 'biome_fit_pct',
    requiresCitySelection: true,
  },
  {
    id: 'canopy-total',
    label: 'Crown area',
    title: 'Crown Area',
    query: 'SELECT sum(canopy_area_m2) as canopy_area, count(tree_id ? crown_class IS NOT NULL) as sized_trees;',
    xField: 'canopy_area',
  },
  {
    id: 'top-genus',
    label: 'Largest genus',
    title: 'Largest Genus',
    query: `SELECT
  split(species, ' ')[1] as genus_name,
  count(tree_id) as tree_count
WHERE ${REAL_SPECIES_PREDICATE}
ORDER BY tree_count DESC
LIMIT 1;`,
    xField: 'tree_count',
  },
  {
    id: 'top-family',
    label: 'Largest family',
    title: 'Largest Family',
    query: `SELECT
  family,
  count(tree_id) as tree_count
WHERE family IS NOT NULL and ${REAL_SPECIES_PREDICATE}
ORDER BY tree_count DESC
LIMIT 1;`,
    xField: 'tree_count',
  },
]

export const SUMMARY_CHARTS: SummaryDashboardChart[] = [
  {
    id: 'tree-sources',
    title: 'Where the records come from',
    subtitle: 'Trees by kind of source, and how many of each are identified to species',
    kind: 'custom',
    query: `SELECT
source_kind,
count(tree_id) as tree_count,
count(tree_id ? ${REAL_SPECIES_PREDICATE}) as identified_count
ORDER BY tree_count DESC;`,
    chartConfig: barConfig('source_kind'),
    allowCrossFilter: false,
  },
  {
    id: 'top-tree-spotlight',
    title: 'Most common species',
    subtitle: 'Identified trees only. Pick a species to read about it.',
    kind: 'custom',
    query: SPECIES_LIST_QUERY,
    chartConfig: { chartType: 'headline', xField: 'tree_count', showTitle: false },
    allowCrossFilter: false,
  },
  {
    id: 'canopy-by-species',
    title: 'Crown area by species',
    kind: 'custom',
    query: CANOPY_BY_SPECIES_QUERY,
    chartConfig: barConfig('species'),
    allowCrossFilter: false,
  },
  {
    id: 'plant-year',
    title: 'Planting by year',
    subtitle: 'Trees with a recorded planting date',
    kind: 'chart',
    query: `import std.color;
SELECT
plant_year,
count(tree_id) as tree_count,
case
when 2026 - plant_year < 10 then '0-9 years ago'
when 2026 - plant_year < 20 then '10-19 years ago'
when 2026 - plant_year < 30 then '20-29 years ago'
when 2026 - plant_year < 40 then '30-39 years ago'
when 2026 - plant_year < 50 then '40-49 years ago'
else '50+ years ago'
end as planting_window,
case
when 2026 - plant_year < 10 then '#A7E3B2'
when 2026 - plant_year < 20 then '#8DCEA3'
when 2026 - plant_year < 30 then '#6BAF92'
when 2026 - plant_year < 40 then '#4E9872'
when 2026 - plant_year < 50 then '#2F7D4F'
else '#1F5A34'
end::string::hex as color
WHERE plant_year > 1800 and plant_year < 2030
ORDER BY plant_year ASC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'plant_year',
      yField: 'tree_count',
      colorField: 'planting_window',
      showTitle: false,
      hideLegend: true,
      hideXAxisTitle: true,
      yAxisTitle: 'Trees planted',
    } as ChartConfig,
    allowCrossFilter: false,
  },
  {
    id: 'recent-plantings',
    title: 'Planted in the last ten years',
    subtitle: `Most planted species since ${RECENT_PLANTING_FROM}`,
    kind: 'buckets',
    query: `SELECT
coalesce(common_names[1], species) as display_name,
count(tree_id) as tree_count
WHERE ${REAL_SPECIES_PREDICATE} and plant_year >= ${RECENT_PLANTING_FROM}
ORDER BY tree_count DESC
LIMIT 8;`,
    chartConfig: barConfig('display_name'),
    buckets: { labelField: 'display_name', unknown: [] },
    allowCrossFilter: false,
  },
  {
    id: 'trunk-size',
    title: 'Trunk size',
    subtitle: 'Diameter at breast height',
    kind: 'buckets',
    // The class and its label are model concepts (tree_summary_grains),
    // selected under their own names, so the (city, species, DBH class)
    // summary answers this card.
    query: `SELECT
dbh_class,
dbh_class_label,
count(tree_id) as tree_count
WHERE dbh_class IS NOT NULL
ORDER BY dbh_class ASC;`,
    chartConfig: barConfig('dbh_class_label'),
    buckets: { labelField: 'dbh_class_label', order: DBH_ORDER, colors: scaleColors(DBH_ORDER), unknown: [] },
    allowCrossFilter: false,
  },
  {
    id: 'crown-size',
    title: 'Crown width',
    subtitle: 'Measured where a source recorded it, estimated from the trunk otherwise',
    kind: 'buckets',
    query: `SELECT
crown_class,
crown_class_label,
count(tree_id) as tree_count
WHERE crown_class IS NOT NULL
ORDER BY crown_class ASC;`,
    chartConfig: barConfig('crown_class_label'),
    buckets: { labelField: 'crown_class_label', order: CROWN_ORDER, colors: scaleColors(CROWN_ORDER), unknown: [] },
    allowCrossFilter: false,
  },
  {
    id: 'native-locality',
    title: 'Nativeness',
    subtitle: 'Each species against the city’s own ecoregion, biome and realm',
    kind: 'buckets',
    query: bucketQuery('native_locality_bucket'),
    chartConfig: barConfig('native_locality_bucket'),
    buckets: {
      labelField: 'native_locality_bucket',
      order: NATIVENESS_ORDER,
      labels: NATIVENESS_LABELS,
      colors: NATIVENESS_COLORS,
      filterField: 'native_locality_bucket',
      // A species with no recorded range is Unknown, reported as coverage.
      hideWhenThin: true,
    },
    requiresCitySelection: true,
  },
  {
    id: 'tree-form',
    title: 'Tree form',
    subtitle: 'Growth habit of the planted mix',
    kind: 'buckets',
    query: bucketQuery('tree_form'),
    chartConfig: barConfig('tree_form'),
    // `default` is the renderer's fallback shape, not a form a tree has.
    buckets: { labelField: 'tree_form', labels: TREE_FORM_LABELS, unknown: ['default'], filterField: 'tree_form', hideWhenThin: true },
  },
  {
    id: 'water-resilience',
    title: 'Water and drought',
    subtitle: 'Water needs against drought tolerance',
    kind: 'buckets',
    query: bucketQuery('water_resilience_bucket'),
    chartConfig: barConfig('water_resilience_bucket'),
    buckets: {
      labelField: 'water_resilience_bucket',
      order: ['Low water / high drought tolerance', 'Moderate / mixed', 'High water / low drought tolerance'],
      labels: {
        'Low water / high drought tolerance': 'Thrifty, drought-hardy',
        'Moderate / mixed': 'Moderate or mixed',
        'High water / low drought tolerance': 'Thirsty, drought-prone',
      },
      colors: {
        'Low water / high drought tolerance': GOOD,
        'Moderate / mixed': MID,
        'High water / low drought tolerance': POOR,
      },
      filterField: 'water_resilience_bucket',
      hideWhenThin: true,
    },
  },
  {
    id: 'hardiness-fit',
    title: 'Hardiness zone fit',
    subtitle: 'Each species’ zone range against the city’s zone',
    kind: 'buckets',
    query: bucketQuery('hardiness_fit_bucket'),
    chartConfig: barConfig('hardiness_fit_bucket'),
    buckets: {
      labelField: 'hardiness_fit_bucket',
      order: ['Well within zone', 'Edge of tolerance', 'Outside zone'],
      colors: { 'Well within zone': GOOD, 'Edge of tolerance': MID, 'Outside zone': POOR },
      filterField: 'hardiness_fit_bucket',
      hideWhenThin: true,
    },
    requiresCitySelection: true,
  },
  {
    id: 'wildlife-value',
    title: 'Wildlife value',
    subtitle: 'Food and habitat the species offers',
    kind: 'buckets',
    query: bucketQuery('wildlife_value'),
    chartConfig: barConfig('wildlife_value'),
    buckets: {
      labelField: 'wildlife_value',
      order: ['high', 'moderate', 'low'],
      labels: LEVEL_LABELS,
      colors: { high: GOOD, moderate: MID, low: POOR },
      filterField: 'wildlife_value',
      hideWhenThin: true,
    },
  },
  {
    id: 'growth-rate',
    title: 'Growth rate',
    subtitle: 'How fast the planted mix puts on size',
    kind: 'buckets',
    query: bucketQuery('growth_rate'),
    chartConfig: barConfig('growth_rate'),
    buckets: {
      labelField: 'growth_rate',
      order: ['slow', 'moderate', 'fast'],
      labels: LEVEL_LABELS,
      colors: { slow: '#22523a', moderate: '#548f63', fast: '#9cc69b' },
      filterField: 'growth_rate',
      hideWhenThin: true,
    },
  },
  {
    id: 'lifespan-profile',
    title: 'Lifespan',
    subtitle: 'How long the species typically lives',
    kind: 'buckets',
    query: bucketQuery('lifespan_bucket'),
    chartConfig: barConfig('lifespan_bucket'),
    buckets: {
      labelField: 'lifespan_bucket',
      order: ['Short-lived (<50y)', 'Medium-lived (50-149y)', 'Long-lived (150+y)'],
      labels: {
        'Short-lived (<50y)': 'Under 50 years',
        'Medium-lived (50-149y)': '50–149 years',
        'Long-lived (150+y)': '150 years and over',
      },
      colors: {
        'Short-lived (<50y)': '#9cc69b',
        'Medium-lived (50-149y)': '#548f63',
        'Long-lived (150+y)': '#22523a',
      },
      filterField: 'lifespan_bucket',
      hideWhenThin: true,
    },
  },
  {
    id: 'sun-exposure',
    title: 'Sun exposure',
    subtitle: 'The shadiest site each species tolerates',
    kind: 'buckets',
    query: bucketQuery('sun_exposure_label'),
    chartConfig: barConfig('sun_exposure_label'),
    buckets: {
      labelField: 'sun_exposure_label',
      order: ['Full sun', 'Partial shade', 'Shade'],
      colors: { 'Full sun': MID, 'Partial shade': '#6baf92', Shade: '#22523a' },
      filterField: 'sun_exposure_label',
      hideWhenThin: true,
    },
  },
  {
    id: 'fire-risk',
    title: 'Fire risk',
    subtitle: 'How readily the species burns',
    kind: 'buckets',
    query: bucketQuery('fire_risk'),
    chartConfig: barConfig('fire_risk'),
    buckets: {
      labelField: 'fire_risk',
      order: LEVELS,
      labels: LEVEL_LABELS,
      colors: { low: GOOD, moderate: MID, high: POOR },
      filterField: 'fire_risk',
      hideWhenThin: true,
    },
  },
]

/**
 * The card sections under the page's hero (the map, the tiles and the source
 * split) and its species list, which the view lays out itself.
 */
export const SUMMARY_SECTIONS: SummaryDashboardSection[] = [
  {
    id: 'structure',
    title: 'Age and size',
    subtitle: 'When the trees went in, and how large they have grown.',
    cards: [
      { id: 'plant-year', span: 'xl' },
      { id: 'recent-plantings', span: 'm' },
      { id: 'trunk-size', span: 'm' },
      { id: 'crown-size', span: 'm' },
    ],
  },
  {
    id: 'traits',
    title: 'What the species are like',
    subtitle: 'Identified trees, grouped by what is known about their species. Click a bar to filter the page.',
    cards: [
      { id: 'native-locality', span: 's' },
      { id: 'tree-form', span: 's' },
      { id: 'water-resilience', span: 's' },
      { id: 'hardiness-fit', span: 's' },
      { id: 'wildlife-value', span: 's' },
      { id: 'growth-rate', span: 's' },
      { id: 'lifespan-profile', span: 's' },
      { id: 'sun-exposure', span: 's' },
      { id: 'fire-risk', span: 's' },
    ],
  },
]

export const SUMMARY_CHARTS_BY_ID = Object.fromEntries(
  SUMMARY_CHARTS.map((chart) => [chart.id, chart]),
) as Record<string, SummaryDashboardChart>

export const SUMMARY_KPIS_BY_ID = Object.fromEntries(
  SUMMARY_KPI_CHARTS.map((chart) => [chart.id, chart]),
) as Record<string, SummaryDashboardKpi>

export function readSummaryRouteCity(value: unknown): CityCode | null {
  const city = Array.isArray(value) ? value[0] : value
  if (typeof city !== 'string') {
    return null
  }
  return city in CITY_CONFIG ? (city as CityCode) : null
}

export function getSummaryBaseFilters(city: CityCode | null): string[] {
  if (!city) {
    return []
  }
  return [`city = '${city}'`]
}
