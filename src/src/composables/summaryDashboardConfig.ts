import type { ChartConfig, DashboardImport } from '@trilogy-data/trilogy-studio-components/dashboard'
import { CITY_CONFIG, type CityCode } from './useMapData'

export type SummaryDashboardChart = {
  id: string
  title: string
  query: string
  chartConfig: ChartConfig
  allowCrossFilter?: boolean
}

export type SummaryDashboardKpi = {
  id: string
  label: string
  title: string
  query: string
  xField: string
}

export const SUMMARY_DASHBOARD_IMPORTS: DashboardImport[] = [
  { id: 'tree_enrichment', name: 'tree_enrichment', alias: '' },
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
    label: 'Biodiversity',
    title: 'Unique Species',
    query: 'SELECT count(species ? count(tree_id) by species >0) as unique_species WHERE species IS NOT NULL;',
    xField: 'unique_species',
  },
  {
    id: 'average-dbh',
    label: 'Average size',
    title: '',
    query: 'SELECT round(avg(diameter_at_breast_height),2) as avg_dbh_inches WHERE diameter_at_breast_height IS NOT NULL;',
    xField: 'avg_dbh',
  },
  {
    id: 'evergreen-trees',
    label: 'Evergreens',
    title: 'Evergreen Trees',
    query: 'SELECT count(tree_id) as evergreen_trees WHERE is_evergreen = true;',
    xField: 'evergreen_trees',
  },
]

export const SUMMARY_CHARTS: SummaryDashboardChart[] = [
  {
    id: 'tree-category',
    title: 'Tree Types',
    query: `import std.color;
SELECT
tree_category,
case lower(tree_category)
when 'palm' then '#D4A14A'
when 'broadleaf' then '#A7E3B2'
when 'columnar' then '#6BAF92'
when 'coniferous' then '#1F5A4E'
when 'ornamental' then '#A96F49'
when 'spreading' then '#2F7D4F'
else '#7E9D86'
end::string::hex as cat_color,
count(tree_id) as tree_count
WHERE tree_category IS NOT NULL ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'donut',
      xField: 'tree_count',
      yField: 'tree_category',
      colorField: 'cat_color',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'top-species',
    title: 'Top Species',
    query: `import std.color;
SELECT
species,
count(tree_id) as tree_count,
'#2F7D4F'::string::hex as color
WHERE species IS NOT NULL ORDER BY tree_count DESC LIMIT 15;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'species',
      yField: 'tree_count',
      colorField: 'species',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'native-status',
    title: 'Native Status',
    query: `import std.color;
SELECT
native_status,
count(tree_id) as tree_count,
'#2F7D4F'::string::hex as color
WHERE native_status IS NOT NULL ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'native_status',
      colorField: 'native_status',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'drought-tolerance',
    title: 'Drought Tolerance',
    query: `import std.color;
SELECT
drought_tolerance,
count(tree_id) as tree_count,
'#2F7D4F'::string::hex as color
WHERE drought_tolerance IS NOT NULL ORDER BY tree_count DESC;`,
    chartConfig: {
      chartType: 'barh',
      xField: 'tree_count',
      yField: 'drought_tolerance',
      colorField: 'drought_tolerance',
      showTitle: false,
      hideLegend: true,
    },
  },
  {
    id: 'plant-year',
    title: 'Trees Planted by Year',
    query: `import std.color;
SELECT
plant_date.year as plant_year,
count(tree_id) as tree_count,
'#2F7D4F'::string::hex as color
WHERE plant_date IS NOT NULL ORDER BY plant_year ASC;`,
    chartConfig: {
      chartType: 'bar',
      xField: 'plant_year',
      yField: 'tree_count',
      colorField: 'color',
      showTitle: false,
      hideLegend: true,
    },
    allowCrossFilter: false,
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
