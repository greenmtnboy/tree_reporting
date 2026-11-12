import type { ChartConfig, DashboardImport } from '@trilogy-data/trilogy-studio-components/dashboard'
import { CITY_CONFIG, type CityCode } from './useMapData'
import { REAL_SPECIES_PREDICATE } from '../data/species'
import {
  CANOPY_BY_SPECIES_QUERY,
  SPECIES_PROFILE_FIELDS,
  SUMMARY_CHARTS_BY_ID,
  type DashboardBuckets,
  type DashboardCardKind,
  type DashboardSpan,
} from './summaryDashboardConfig'

export type SpeciesDashboardChart = {
  id: string
  title: string
  subtitle?: string
  query: string
  kind: DashboardCardKind
  chartConfig: ChartConfig
  buckets?: DashboardBuckets
  allowCrossFilter?: boolean
  requiresCitySelection?: boolean
  /** Only when no city is selected: a chart across cities is one bar under one. */
  requiresAllCities?: boolean
  /**
   * Only while a whole genus is in view. With one species selected every
   * tree shares the species' traits, so the trait cards are one bar each;
   * the species profile states them as facts instead.
   */
  genusOnly?: boolean
}

export type SpeciesDashboardKpi = {
  id: string
  label: string
  title: string
  query: string
  xField: string
  requiresCitySelection?: boolean
}

export type SpeciesDashboardSection = {
  id: string
  title: string
  subtitle: string
  cards: Array<{ id: string; span: DashboardSpan }>
}

export const SPECIES_GENUS_EXPR = "split(species, ' ')[1]"

export type ScientificNameParts = {
  genus: string | null
  specificEpithet: string | null
  taxonType: 'species' | 'hybrid' | 'varietal' | 'unknown'
}

// Reuse the same imports as the summary dashboard — same data sources
export const SPECIES_DASHBOARD_IMPORTS: DashboardImport[] = [
  { id: 'tree_enrichment', name: 'tree_enrichment', alias: '' },
  { id: 'dashboard_context', name: 'dashboard_context', alias: '' },
]

export const SPECIES_KPI_CHARTS: SpeciesDashboardKpi[] = [
  {
    id: 'sp-total-trees',
    label: 'Trees',
    title: 'Total Trees',
    query: 'SELECT count(tree_id) as total_trees;',
    xField: 'total_trees',
  },
  {
    id: 'sp-city-count',
    label: 'Cities',
    title: 'Cities Present',
    query: 'SELECT count(city ? count(tree_id) by city > 0) as city_count WHERE city IS NOT NULL;',
    xField: 'city_count',
  },
  {
    id: 'sp-canopy-total',
    label: 'Crown area',
    title: 'Crown Area',
    query: 'SELECT sum(canopy_area_m2) as canopy_area, count(tree_id ? crown_class IS NOT NULL) as sized_trees;',
    xField: 'canopy_area',
  },
]

// Taxonomy selector queries, rendered by SpeciesSearchFilter in SpeciesView.
// They live here rather than inline in the view so the dashboard-query
// compile test can enumerate every query the page issues.
export const GENUS_OPTIONS_QUERY = `SELECT
  ${SPECIES_GENUS_EXPR} as option_value,
  ${SPECIES_GENUS_EXPR} as option_label,
  count(tree_id) as tree_count
WHERE ${REAL_SPECIES_PREDICATE}
ORDER BY tree_count DESC;`

export const TOP_GENUS_QUERY = `${GENUS_OPTIONS_QUERY.slice(0, -1)}
LIMIT 1;`

export const SPECIES_OPTIONS_QUERY = `SELECT
  species as option_value,
  species as option_label,
  count(tree_id) as tree_count
WHERE ${REAL_SPECIES_PREDICATE}
ORDER BY tree_count DESC;`

export const TOP_SPECIES_QUERY = `${SPECIES_OPTIONS_QUERY.slice(0, -1)}
LIMIT 1;`

/**
 * Every identified species in the page's city (or in all of them), counted,
 * under the city filter alone: what a selected species is ranked against. The
 * page's other queries carry the genus or species filter and cannot see it.
 */
export const SPECIES_RANKING_QUERY = `SELECT
  species,
  count(tree_id) as tree_count
WHERE ${REAL_SPECIES_PREDICATE}
ORDER BY tree_count DESC;`

function summaryBuckets(id: string, spId: string): SpeciesDashboardChart {
  const chart = SUMMARY_CHARTS_BY_ID[id]
  return {
    id: spId,
    title: chart.title,
    subtitle: chart.subtitle,
    query: chart.query,
    kind: chart.kind,
    chartConfig: chart.chartConfig,
    buckets: chart.buckets,
    requiresCitySelection: chart.requiresCitySelection,
    genusOnly: true,
  }
}

export const SPECIES_CHARTS: SpeciesDashboardChart[] = [
  // The species in view, most common first, each with its profile: the list
  // while a genus is in view, the one profile once a species is selected.
  {
    id: 'sp-spotlight',
    title: 'Species in this genus',
    subtitle: 'Pick a species to read about it, then focus the page on it.',
    kind: 'custom',
    query: `SELECT
${SPECIES_PROFILE_FIELDS},
count(tree_id) by species as tree_count
WHERE ${REAL_SPECIES_PREDICATE}
ORDER BY tree_count DESC, species ASC
LIMIT 100;`,
    chartConfig: { chartType: 'headline', xField: 'tree_count', showTitle: false },
    allowCrossFilter: false,
  },
  {
    id: 'sp-canopy-by-species',
    title: 'Crown area by species',
    kind: 'custom',
    query: CANOPY_BY_SPECIES_QUERY,
    chartConfig: { chartType: 'barh', xField: 'canopy_area', yField: 'species', showTitle: false, hideLegend: true },
    allowCrossFilter: false,
  },

  // City distribution — which cities have this taxon, colored by nativeness
  {
    id: 'sp-city-distribution',
    title: 'Trees by city',
    subtitle: 'Coloured by how native the taxon is to each city',
    kind: 'chart',
    query: `import std.color;
SELECT
city,
native_locality_bucket,
count(tree_id) as tree_count,
case native_locality_bucket
when 'Native' then '#2F7D4F'
when 'Same biome, non-native' then '#6BAF92'
when 'Native Region, Different Biome' then '#B59A54'
when 'Non-Native, Different Biome' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE city IS NOT NULL AND native_locality_bucket IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'city',
      colorField: 'native_locality_bucket',
      showTitle: false,
      hideLegend: false,
      hideYAxisTitle: true,
      xAxisTitle: 'Trees',
    } as ChartConfig,
    requiresAllCities: true,
  },
  {
    id: 'sp-city-presence-map',
    title: 'Where it is planted',
    subtitle: 'Cities holding this taxon, sized by tree count',
    kind: 'chart',
    // Each city sits at the mean position of all its trees (`tree_rollup_city`),
    // so the map is a count by city the (city, species) summary serves.
    query: `SELECT
city,
city_longitude,
city_latitude,
count(tree_id) as tree_count
WHERE city IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'geo-map',
      xField: 'city_longitude',
      yField: 'city_latitude',
      sizeField: 'tree_count',
      annotationField: 'city',
      showTitle: false,
      hideLegend: false,
    },
    requiresAllCities: true,
  },

  {
    id: 'sp-age-distribution',
    title: 'Planting by year',
    subtitle: 'Trees with a recorded planting date',
    kind: 'chart',
    query: `import std.date;
SELECT
plant_year::int::year as planted_year,
count(tree_id) as tree_count
WHERE plant_year > 1700
and plant_year <= year(current_date())
ORDER BY planted_year ASC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'planted_year',
      yField: 'tree_count',
      showTitle: false,
      hideLegend: true,
      hideXAxisTitle: true,
      yAxisTitle: 'Trees planted',
    } as ChartConfig,
  },
  {
    ...summaryBuckets('trunk-size', 'sp-size-distribution'),
    genusOnly: false,
  },
  {
    ...summaryBuckets('crown-size', 'sp-crown-size'),
    genusOnly: false,
  },

  summaryBuckets('tree-form', 'sp-tree-form'),
  summaryBuckets('water-resilience', 'sp-water-resilience'),
  summaryBuckets('hardiness-fit', 'sp-hardiness-fit'),
  summaryBuckets('wildlife-value', 'sp-wildlife-value'),
  summaryBuckets('growth-rate', 'sp-growth-rate'),
  summaryBuckets('lifespan-profile', 'sp-lifespan'),
  summaryBuckets('fire-risk', 'sp-fire-risk'),
]

/**
 * The card sections under the page's species block and its map, which the
 * view lays out itself (with the trunk and crown size cards beside the map).
 */
export const SPECIES_SECTIONS: SpeciesDashboardSection[] = [
  {
    id: 'sp-history',
    title: 'Planting and spread',
    subtitle: 'When these trees went in, and which cities hold them.',
    cards: [
      { id: 'sp-age-distribution', span: 'xl' },
      { id: 'sp-city-presence-map', span: 'l' },
      { id: 'sp-city-distribution', span: 'l' },
    ],
  },
  {
    id: 'sp-traits',
    title: 'What the genus is like',
    subtitle: 'Its trees grouped by what is known about their species. Click a bar to filter the page.',
    cards: [
      { id: 'sp-tree-form', span: 's' },
      { id: 'sp-water-resilience', span: 's' },
      { id: 'sp-hardiness-fit', span: 's' },
      { id: 'sp-wildlife-value', span: 's' },
      { id: 'sp-growth-rate', span: 's' },
      { id: 'sp-lifespan', span: 's' },
      { id: 'sp-fire-risk', span: 's' },
    ],
  },
]

export const SPECIES_CHARTS_BY_ID = Object.fromEntries(
  SPECIES_CHARTS.map((chart) => [chart.id, chart]),
) as Record<string, SpeciesDashboardChart>

export function readSpeciesRouteCity(value: unknown): CityCode | null {
  const city = Array.isArray(value) ? value[0] : value
  if (typeof city !== 'string') return null
  return city in CITY_CONFIG ? (city as CityCode) : null
}

export function escapeSpeciesFilterValue(value: string): string {
  return value
    .replace(/'/g, "''")
}

function buildSafeLikeClauses(field: string, rawValue: string): string[] {
  const normalized = rawValue.trim().replace(/\s+/g, ' ')
  const tokens = normalized
    .split(/[^A-Za-z0-9.-]+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 2)

  if (tokens.length === 0) {
    return []
  }

  const prefix = tokens.slice(0, Math.min(tokens.length, 2)).join(' ')
  const trailingTokens = tokens.slice(2)
  const clauses = [`${field} like '${escapeSpeciesFilterValue(prefix)}%'`]

  for (const token of trailingTokens) {
    clauses.push(`${field} like '%${escapeSpeciesFilterValue(token)}%'`)
  }

  return clauses
}

export function getGenusSqlFilter(genus: string): string {
  return `${SPECIES_GENUS_EXPR} = '${escapeSpeciesFilterValue(genus)}'`
}

export function getSpeciesSqlFilter(species: string): string {
  if (!species.includes("'")) {
    return `species = '${escapeSpeciesFilterValue(species)}'`
  }

  const clauses = buildSafeLikeClauses('species', species)
  if (clauses.length === 0) {
    return `species = '${escapeSpeciesFilterValue(species)}'`
  }

  return clauses.length === 1 ? clauses[0] : `(${clauses.join(' and ')})`
}

export function extractGenusFromSpecies(species: string | null | undefined): string | null {
  if (!species || typeof species !== 'string') return null
  const [genus] = species.trim().split(/\s+/)
  return genus || null
}

export function parseScientificName(species: string | null | undefined): ScientificNameParts {
  if (!species || typeof species !== 'string') {
    return { genus: null, specificEpithet: null, taxonType: 'unknown' }
  }
  const tokens = species.trim().split(/\s+/).filter(Boolean)
  const genus = tokens[0] ?? null
  if (!genus) {
    return { genus: null, specificEpithet: null, taxonType: 'unknown' }
  }
  const hasHybridMarker = tokens.some((token) => token === 'x' || token === '×')
  const hasVarietalMarker = tokens.some((token) => /^(var\.?|subsp\.?|ssp\.?|f\.?|cv\.?)$/i.test(token))
    || species.includes("'")
  const specificEpithetIndex = tokens[1] === 'x' || tokens[1] === '×' ? 2 : 1
  const specificEpithet = tokens[specificEpithetIndex] ?? null
  const taxonType = hasHybridMarker
    ? 'hybrid'
    : hasVarietalMarker
      ? 'varietal'
      : specificEpithet
        ? 'species'
        : 'unknown'
  return {
    genus,
    specificEpithet,
    taxonType,
  }
}

export function getSpeciesBaseFilters(city: CityCode | null, species: string | null): string[] {
  const filters: string[] = []
  if (city) {
    filters.push(`city = '${city}'`)
  }
  if (species) {
    filters.push(getSpeciesSqlFilter(species))
  }
  return filters
}

export function getSpeciesViewBaseFilters(
  city: CityCode | null,
  genus: string | null,
  species: string | null,
): string[] {
  if (species) {
    return getSpeciesBaseFilters(city, species)
  }

  const filters: string[] = []
  if (city) {
    filters.push(`city = '${city}'`)
  }
  if (genus) {
    filters.push(getGenusSqlFilter(genus))
  }
  return filters
}
