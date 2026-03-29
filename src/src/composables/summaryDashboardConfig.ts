import type { ChartConfig, DashboardImport } from '@trilogy-data/trilogy-studio-components/dashboard'
import { CITY_CONFIG, type CityCode } from './useMapData'

export type SummaryDashboardChart = {
  id: string
  title: string
  subtitle?: string
  query: string
  chartConfig: ChartConfig
  renderMode?: 'chart' | 'markdown'
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

export type SummaryDashboardSectionCard = {
  id: string
  width?: 'default' | 'wide'
}

export type SummaryDashboardSection = {
  id: string
  title: string
  subtitle: string
  cards: SummaryDashboardSectionCard[]
}

export const SUMMARY_DASHBOARD_IMPORTS: DashboardImport[] = [
  { id: 'tree_enrichment', name: 'tree_enrichment', alias: '' },
  { id: 'dashboard_context', name: 'dashboard_context', alias: '' },
]

export const SUMMARY_KPI_CHARTS: SummaryDashboardKpi[] = [
  {
    id: 'total-trees',
    label: 'Inventory',
    title: 'Total Trees',
    query: 'SELECT count(tree_id) as total_trees;',
    xField: 'total_trees',
  },
  {
    id: 'unique-species',
    label: 'Diversity',
    title: 'Unique Species',
    query: 'SELECT count(species ? count(tree_id) by species > 0) as unique_species WHERE species IS NOT NULL;',
    xField: 'unique_species',
  },
  {
    id: 'top-5-share',
    label: 'Concentration',
    title: 'Top 5 Share %',
    query: `SELECT --species, 
    --dominance_rank,
    cumulative_tree_share_pct as top_5_species_share_pct having dominance_rank = 5;`,
    xField: 'top_5_species_share_pct',
  },
  {
    id: 'local-native-share',
    label: 'Ecological Fit',
    title: 'Biome Fit %',
    query: "import std.display; SELECT  (count(tree_id ? native_locality_bucket = 'Native' or native_locality_bucket = 'Same biome, non-native') / count(tree_id))::float::percent as biome_fit_pct;",
    xField: 'biome_fit_pct',
    requiresCitySelection: true,
  },
]

export const SUMMARY_CHARTS: SummaryDashboardChart[] = [
  {
    id: 'top-tree-spotlight',
    title: 'Top Tree In Current View',
    subtitle: 'Most common species in the active filter set',
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
count(tree_id) as tree_count,
rank(species) over (order by count(tree_id) by species desc, species asc) as tree_rank
WHERE species IS NOT NULL
HAVING tree_rank = 1;`,
    chartConfig: {
      chartType: 'headline',
      xField: 'tree_count',
      showTitle: false,
    },
    renderMode: 'markdown',
    allowCrossFilter: false,
  },
  {
    id: 'dominance-curve',
    title: 'Dominance Curve',
    subtitle: 'Cumulative share held by the top 50 ranked species',
    query: `import std.color;
SELECT
dominance_rank,
species,
count(tree_id) as tree_count,
cumulative_tree_share_pct,
'#6BAF92'::string::hex as curve_color
HAVING dominance_rank <= 50
ORDER BY dominance_rank ASC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'dominance_rank',
      yField: 'cumulative_tree_share_pct',
      colorField: 'curve_color',
      showTitle: false,
      hideLegend: true,
      annotationField: 'species',
    },
    allowCrossFilter: false,
  },
  {
    id: 'plant-year',
    title: 'Planting Waves',
    subtitle: 'Years with planting data',
    query: `import std.color;
SELECT
plant_date.year as plant_year,
count(tree_id) as tree_count,
case
when 2026 - plant_date.year < 10 then '0-9 years ago'
when 2026 - plant_date.year < 20 then '10-19 years ago'
when 2026 - plant_date.year < 30 then '20-29 years ago'
when 2026 - plant_date.year < 40 then '30-39 years ago'
when 2026 - plant_date.year < 50 then '40-49 years ago'
else '50+ years ago'
end as planting_window,
case
when 2026 - plant_date.year < 10 then '#A7E3B2'
when 2026 - plant_date.year < 20 then '#8DCEA3'
when 2026 - plant_date.year < 30 then '#6BAF92'
when 2026 - plant_date.year < 40 then '#4E9872'
when 2026 - plant_date.year < 50 then '#2F7D4F'
else '#1F5A34'
end::string::hex as color
WHERE plant_date IS NOT NULL and plant_year>1800
ORDER BY plant_year ASC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'plant_year',
      yField: 'tree_count',
      colorField: 'planting_window',
      showTitle: false,
      hideLegend: true,
    },
    allowCrossFilter: false,
  },
  {
    id: 'native-locality',
    title: 'Nativeness Context',
    subtitle: 'Local native vs biome and realm peers for the selected city',
    query: `import std.color;
SELECT
native_locality_bucket,
count(tree_id) as tree_count,
case native_locality_bucket
when 'Native' then '#2F7D4F'
when 'Same biome, non-native' then '#6BAF92'
when 'Native Region, Different Biome' then '#B59A54'
when 'Non-Native, Different Biome' then '#A96F49'
else '#7E9D86'
end::string::hex as bucket_color
WHERE native_locality_bucket IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'donut',
      xField: 'tree_count',
      yField: 'native_locality_bucket',
      colorField: 'native_locality_bucket',
      showTitle: false,
      hideLegend: true,
    },
    requiresCitySelection: true,
  },
  {
    id: 'hardiness-fit',
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
    id: 'water-resilience',
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
  {
    id: 'tree-form',
    title: 'Tree Forms',
    subtitle: 'Structural diversity across the inventory',
    query: `import std.color;
SELECT
tree_form,
case lower(tree_form)
when 'palm' then '#D4A14A'
when 'broadleaf' then '#A7E3B2'
when 'columnar' then '#6BAF92'
when 'conifer' then '#1F5A4E'
when 'ornamental' then '#A96F49'
when 'spreading' then '#2F7D4F'
when 'weeping' then '#5F9EA0'
when 'multi_trunk' then '#8B6B4A'
else '#7E9D86'
end::string::hex as cat_color,
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
    id: 'sun-exposure',
    title: 'Sun Exposure Fit',
    subtitle: 'Light tolerance in the planted mix',
    query: `import std.color;
SELECT
sun_exposure_label,
count(tree_id) as tree_count,
case sun_exposure_label
when 'Full sun' then '#D4A14A'
when 'Partial shade' then '#6BAF92'
when 'Shade' then '#1F5A4E'
else '#7E9D86'
end::string::hex as bucket_color
WHERE sun_exposure_label IS NOT NULL
ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'sun_exposure_label',
      yField: 'tree_count',
      colorField: 'sun_exposure_label',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'wildlife-value',
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
    id: 'lifespan-profile',
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
  {
    id: 'growth-rate',
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
    id: 'fire-risk',
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

export const SUMMARY_SECTIONS: SummaryDashboardSection[] = [
  {
    id: 'composition',
    title: 'Composition',
    subtitle: 'How concentrated the inventory is and when it was planted.',
    cards: [
      { id: 'dominance-curve', width: 'wide' },
      { id: 'plant-year', width: 'wide' },
    ],
  },
  {
    id: 'spotlight',
    title: 'Spotlight',
    subtitle: 'A quick read on the most common species in the current view.',
    cards: [
      { id: 'top-tree-spotlight', width: 'wide' },
    ],
  },
  {
    id: 'ecological-fit',
    title: 'Ecological Fit',
    subtitle: 'Nativeness, hardiness, and water resilience against the local context.',
    cards: [
      { id: 'native-locality' },
      { id: 'hardiness-fit' },
      { id: 'water-resilience' },
    ],
  },
  {
    id: 'diversity',
    title: 'Diversity',
    subtitle: 'Structural variety, light niches, and ecological value.',
    cards: [
      { id: 'tree-form' },
      { id: 'sun-exposure' },
      { id: 'wildlife-value' },
    ],
  },
  {
    id: 'risk-resilience',
    title: 'Risk / Resilience',
    subtitle: 'Longevity, maintenance burden, and fire profile.',
    cards: [
      { id: 'lifespan-profile' },
      { id: 'growth-rate' },
      { id: 'fire-risk' },
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
