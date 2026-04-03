import type { ChartConfig, DashboardImport } from '@trilogy-data/trilogy-studio-components/dashboard'
import { CITY_CONFIG, type CityCode } from './useMapData'
import { treeFormColorSql } from '../treeFormColors'

export type SpeciesDashboardChart = {
  id: string
  title: string
  subtitle?: string
  query: string
  chartConfig: ChartConfig
  renderMode?: 'chart' | 'markdown'
  allowCrossFilter?: boolean
  requiresCitySelection?: boolean
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
  cards: Array<{ id: string; width?: 'default' | 'wide' | 'full'; height?: 'default' | 'tall' }>
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
    id: 'sp-biome-fit',
    label: 'Ecological Fit',
    title: 'Biome Fit %',
    query: "import std.display; SELECT (count(tree_id ? native_locality_bucket = 'Native' or native_locality_bucket = 'Same biome, non-native') / count(tree_id))::float::percent as biome_fit_pct;",
    xField: 'biome_fit_pct',
    requiresCitySelection: true,
  },
]

export const SPECIES_CHARTS: SpeciesDashboardChart[] = [
  // Spotlight — rich markdown fact sheet for the selected species
  {
    id: 'sp-spotlight',
    title: 'Species Fact Sheet',
    subtitle: 'Dominant species detail for the current genus or species filter',
    query: `SELECT
species,
common_names[1] as common_name,
description,
tree_form,
mature_height_min_ft,
mature_height_max_ft,
canopy_spread_min_ft,
canopy_spread_max_ft,
growth_rate,
bloom_months,
wildlife_value,
drought_tolerance,
water_needs,
count(tree_id) by species as tree_count,
rank(species) over (order by tree_count desc, species asc) as tree_rank
HAVING tree_rank = 1;`,
    chartConfig: { chartType: 'headline', xField: 'tree_count', showTitle: false },
    renderMode: 'markdown',
  },

  // City distribution — which cities have this species, colored by nativeness
  {
    id: 'sp-city-distribution',
    title: 'City Distribution',
    subtitle: 'Trees per city, colored by nativeness context',
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
    },
  },
  {
    id: 'sp-city-presence-map',
    title: 'Global City Footprint',
    subtitle: 'Cities containing this taxon, sized by tree count',
    query: `SELECT
city,
avg(longitude) as city_longitude,
avg(latitude) as city_latitude,
count(tree_id) as tree_count
WHERE city IS NOT NULL
and latitude IS NOT NULL
and longitude IS NOT NULL
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
  },

  // Age distribution — decade of planting
  {
    id: 'sp-age-distribution',
    title: 'Planting by Year',
    subtitle: 'When trees in this taxon were planted',
    query: 
    
    `import std.date;
  SELECT
year(plant_date)::int::year as planted_year,
count(tree_id) as tree_count
WHERE plant_date IS NOT NULL AND year(plant_date) > 1700
and year(plant_date) <= year(current_date())
ORDER BY planted_year ASC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'planted_year',
      yField: 'tree_count',
      showTitle: false,
      hideLegend: true,
    },
  },

  // Size distribution — DBH buckets
  {
    id: 'sp-size-distribution',
    title: 'Trunk Size Distribution',
    subtitle: 'Diameter at breast height (inches)',
    query: `SELECT
case
when diameter_at_breast_height < 6 then 1
when diameter_at_breast_height < 12 then 2
when diameter_at_breast_height < 24 then 3
when diameter_at_breast_height < 36 then 4
else 5
end as dbh_order,
case
when diameter_at_breast_height < 6 then 'Under 6"'
when diameter_at_breast_height < 12 then '6–12"'
when diameter_at_breast_height < 24 then '12–24"'
when diameter_at_breast_height < 36 then '24–36"'
else 'Over 36"'
end as dbh_bucket,
count(tree_id) as tree_count
WHERE diameter_at_breast_height IS NOT NULL
ORDER BY dbh_order ASC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'dbh_bucket',
      yField: 'tree_count',
      showTitle: false,
      hideLegend: true,
    },
  },

  // Ecological Fit
  {
    id: 'sp-hardiness-fit',
    title: 'Hardiness Zone Fit',
    subtitle: 'Compared with the active city zone',
    query: `import std.color;
SELECT
hardiness_fit_bucket,
count(tree_id) as tree_count,
case hardiness_fit_bucket
when 'Well within zone' then '#2F7D4F'
when 'Edge of tolerance' then '#D4A14A'
when 'Outside zone' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE hardiness_fit_bucket IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'hardiness_fit_bucket',
      colorField: 'hardiness_fit_bucket',
      showTitle: false,
      hideLegend: true,
    },
    requiresCitySelection: true,
  },
  {
    id: 'sp-water-resilience',
    title: 'Water vs Drought',
    subtitle: 'Operational resilience profile',
    query: `import std.color;
SELECT
water_resilience_bucket,
count(tree_id) as tree_count,
case water_resilience_bucket
when 'Low water / high drought tolerance' then '#2F7D4F'
when 'Moderate / mixed' then '#D4A14A'
when 'High water / low drought tolerance' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE water_resilience_bucket IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'water_resilience_bucket',
      colorField: 'water_resilience_bucket',
      showTitle: false,
      hideLegend: true,
    },
  },

  // Physical Profile
  {
    id: 'sp-tree-form',
    title: 'Tree Forms',
    subtitle: 'Structural variety in the filtered set',
    query: `import std.color;
SELECT
tree_form,
${treeFormColorSql('tree_form', 'cat_color')},
count(tree_id) as tree_count
WHERE tree_form IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'donut',
      xField: 'tree_count',
      yField: 'tree_form',
      colorField: 'tree_form',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'sp-growth-rate',
    title: 'Growth Rate',
    subtitle: 'Maintenance pressure signal',
    query: `import std.color;
SELECT
growth_rate,
count(tree_id) as tree_count,
case growth_rate
when 'slow' then '#1F5A4E'
when 'moderate' then '#D4A14A'
when 'fast' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE growth_rate IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'growth_rate',
      colorField: 'growth_rate',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'sp-lifespan',
    title: 'Lifespan Profile',
    subtitle: 'Replacement risk over time',
    query: `import std.color;
SELECT
lifespan_bucket,
count(tree_id) as tree_count,
case lifespan_bucket
when 'Long-lived (150+y)' then '#2F7D4F'
when 'Medium-lived (50-149y)' then '#D4A14A'
when 'Short-lived (<50y)' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE lifespan_bucket IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'lifespan_bucket',
      colorField: 'lifespan_bucket',
      showTitle: false,
      hideLegend: true,
    },
  },

  // Impact
  {
    id: 'sp-wildlife-value',
    title: 'Wildlife Value',
    subtitle: 'Ecological value from enriched species',
    query: `import std.color;
SELECT
wildlife_value,
count(tree_id) as tree_count,
case wildlife_value
when 'high' then '#2F7D4F'
when 'moderate' then '#D4A14A'
when 'low' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE wildlife_value IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'wildlife_value',
      colorField: 'wildlife_value',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'sp-fire-risk',
    title: 'Fire Risk',
    subtitle: 'Forward-looking resilience profile',
    query: `import std.color;
SELECT
fire_risk,
count(tree_id) as tree_count,
case fire_risk
when 'low' then '#2F7D4F'
when 'moderate' then '#D4A14A'
when 'high' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE fire_risk IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'fire_risk',
      colorField: 'fire_risk',
      showTitle: false,
      hideLegend: true,
    },
  },
]

export const SPECIES_SECTIONS: SpeciesDashboardSection[] = [
  {
    id: 'sp-spotlight-section',
    title: 'Species Profile',
    subtitle: 'Taxonomy context, dominant species detail, and city presence for the current filter.',
    cards: [
      { id: 'sp-spotlight', width: 'wide', height: 'tall' },
      { id: 'sp-city-presence-map', width: 'full', height: 'tall' },
      { id: 'sp-city-distribution' },
    ],
  },
  {
    id: 'sp-distribution',
    title: 'Population & Distribution',
    subtitle: 'When these trees were planted and how large they have grown.',
    cards: [
      { id: 'sp-age-distribution' },
      { id: 'sp-size-distribution' },
    ],
  },
  {
    id: 'sp-ecological',
    title: 'Ecological Fit',
    subtitle: 'Nativeness, hardiness, and water resilience against the local context.',
    cards: [
      { id: 'sp-hardiness-fit' },
      { id: 'sp-water-resilience' },
    ],
  },
  {
    id: 'sp-physical',
    title: 'Physical Profile',
    subtitle: 'Structural form, growth pace, and longevity.',
    cards: [
      { id: 'sp-tree-form' },
      { id: 'sp-growth-rate' },
      { id: 'sp-lifespan' },
    ],
  },
  {
    id: 'sp-impact',
    title: 'Impact',
    subtitle: 'Ecological value and risk profile.',
    cards: [
      { id: 'sp-wildlife-value' },
      { id: 'sp-fire-risk' },
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
