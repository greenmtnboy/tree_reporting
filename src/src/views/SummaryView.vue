<template>
  <div class="summary-page">
    <div class="summary-header">
      <div class="summary-title-row">
        <div>
          <p class="summary-eyebrow">Urban forest snapshot</p>
          <h1>Tree Analytics</h1>
        </div>
        <div class="city-badge">{{ cityName }}</div>
      </div>
      <p class="summary-intro">
        Explore canopy composition, species mix, and resilience indicators for the active city.
      </p>
      <div v-if="activeFilters.length" class="active-filters">
        <span v-for="filter in activeFilters" :key="filter.key" class="filter-chip">
          <span class="filter-chip-label">{{ filter.label }}</span>
          <button class="filter-chip-clear" :title="`Remove ${filter.label}`" @click="clearFilter(filter.key)">
            x
          </button>
        </span>
        <button class="clear-all-btn" @click="clearAllFilters">Clear all</button>
      </div>
    </div>

    <div class="kpi-row">
      <div
        v-for="kpi in kpiCharts"
        :key="kpi.id"
        class="chart-card chart-card--kpi chart-card--metric"
      >
        <div class="metric-label">{{ kpi.label }}</div>
        <EmbeddedDashboardChart
          :item-id="kpi.id"
          v-bind="sharedChartProps"
          :filters="filtersForChart(kpi.id)"
          :title="kpi.title"
          :query="kpi.query"
          :chart-config="{ chartType: 'headline', xField: kpi.xField, showTitle: false }"
        />
      </div>
    </div>

    <div class="chart-row">
      <div class="chart-card chart-card--narrow">
        <div class="chart-card-header">
          <h2>Tree Types</h2>
        </div>
        <EmbeddedDashboardChart
          item-id="tree-category"
          v-bind="sharedChartProps"
          :filters="filtersForChart('tree-category')"
          title="Tree Types"
          query="SELECT tree_category, count(tree_id) as tree_count WHERE tree_category IS NOT NULL ORDER BY tree_count DESC;"
          :chart-config="{ chartType: 'donut', xField: 'tree_count', yField: 'tree_category', colorField: 'tree_category', showTitle: false }"
          :selection-filters="selectionFiltersForChart('tree-category')"
          @dimension-click="handleChartClick"
          @background-click="clearChartSelection('tree-category')"
        />
      </div>

      <div class="chart-card chart-card--wide">
        <div class="chart-card-header">
          <h2>
            Top Species
            <span v-if="selectedCategory" class="header-filter-hint">
              filtered to <em>{{ formatFilterValue(selectedCategory) }}</em>
            </span>
          </h2>
          <span class="chart-sub">top 15 by tree count</span>
        </div>
        <EmbeddedDashboardChart
          item-id="top-species"
          v-bind="sharedChartProps"
          :filters="filtersForChart('top-species')"
          title="Top Species"
          query="SELECT species, count(tree_id) as tree_count WHERE species IS NOT NULL ORDER BY tree_count DESC LIMIT 15;"
          :chart-config="{ chartType: 'bar', xField: 'species', yField: 'tree_count', showTitle: false }"
          :selection-filters="selectionFiltersForChart('top-species')"
          @dimension-click="handleChartClick"
          @background-click="clearChartSelection('top-species')"
        />
      </div>
    </div>

    <div class="chart-row">
      <div class="chart-card">
        <div class="chart-card-header">
          <h2>Native Status</h2>
        </div>
        <EmbeddedDashboardChart
          item-id="native-status"
          v-bind="sharedChartProps"
          :filters="filtersForChart('native-status')"
          title="Native Status"
          query="SELECT native_status, count(tree_id) as tree_count WHERE native_status IS NOT NULL ORDER BY tree_count DESC;"
          :chart-config="{ chartType: 'barh', xField: 'tree_count', yField: 'native_status', showTitle: false }"
          :selection-filters="selectionFiltersForChart('native-status')"
          @dimension-click="handleChartClick"
          @background-click="clearChartSelection('native-status')"
        />
      </div>

      <div class="chart-card">
        <div class="chart-card-header">
          <h2>Drought Tolerance</h2>
          <span class="chart-sub">enriched species only</span>
        </div>
        <EmbeddedDashboardChart
          item-id="drought-tolerance"
          v-bind="sharedChartProps"
          :filters="filtersForChart('drought-tolerance')"
          title="Drought Tolerance"
          query="SELECT drought_tolerance, count(tree_id) as tree_count WHERE drought_tolerance IS NOT NULL ORDER BY tree_count DESC;"
          :chart-config="{ chartType: 'barh', xField: 'tree_count', yField: 'drought_tolerance', showTitle: false }"
        />
      </div>

      <div class="chart-card">
        <div class="chart-card-header">
          <h2>Trees Planted by Year</h2>
          <span class="chart-sub">where planting dates are available</span>
        </div>
        <EmbeddedDashboardChart
          item-id="plant-year"
          v-bind="sharedChartProps"
          :filters="filtersForChart('plant-year')"
          title="Trees Planted by Year"
          query="SELECT plant_date.year as plant_year, count(tree_id) as tree_count WHERE plant_date IS NOT NULL ORDER BY plant_year ASC;"
          :chart-config="{ chartType: 'bar', xField: 'plant_year', yField: 'tree_count', showTitle: false }"
          :allow-cross-filter="false"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  type DashboardImport,
  type DimensionClick,
  type CrossFilterSelection,
  useCrossFilterController,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import EmbeddedDashboardChart from '../components/EmbeddedDashboardChart.vue'
import { useMapData, CITY_CONFIG, type CityCode } from '../composables/useMapData'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import cityConfig from '../cityConfig.json'

const route = useRoute()
const router = useRouter()
const { selectedCity, setSelectedCity } = useMapData()
const { initialize, connectionId, queryExecutionService } = useSummaryDashboardExecution()

const sharedChartProps = {
  connectionId,
  queryExecutionService,
  imports: [{ id: 'tree_enrichment', name: 'tree_enrichment', alias: '' }] as DashboardImport[],
}

const kpiCharts = [
  { id: 'total-trees',     label: 'Inventory',     title: 'Total Trees',             query: 'SELECT count(tree_id) as total_trees;',                                                                          xField: 'total_trees' },
  { id: 'unique-species',  label: 'Biodiversity',  title: 'Unique Species',          query: 'SELECT count_distinct(species) as unique_species WHERE species IS NOT NULL;',                                    xField: 'unique_species' },
  { id: 'average-dbh',     label: 'Average size',  title: 'Average Trunk Diameter',  query: 'SELECT avg(diameter_at_breast_height) as avg_dbh WHERE diameter_at_breast_height IS NOT NULL;',                 xField: 'avg_dbh' },
  { id: 'evergreen-trees', label: 'Evergreen share', title: 'Evergreen Trees',       query: 'SELECT count(tree_id) as evergreen_trees WHERE is_evergreen = true;',                                            xField: 'evergreen_trees' },
] as const

const crossFilterableCharts: { id: string; label: string; format?: (v: string) => string }[] = [
  { id: 'tree-category', label: 'Type',    format: formatFilterValue },
  { id: 'top-species',   label: 'Species' },
  { id: 'native-status', label: 'Native',  format: formatFilterValue },
]

const cityFilter = ref<CityCode | null>(null)
const crossFilters = useCrossFilterController({
  validFields: ['tree_category', 'species', 'native_status'],
})

const cityName = computed(() => {
  if (!cityFilter.value) {
    return 'All Cities'
  }
  const cfg = (cityConfig as Record<string, { name: string }>)[cityFilter.value]
  return cfg?.name ?? cityFilter.value
})

const baseFilters = computed(() => {
  const filters: string[] = []
  if (cityFilter.value) {
    filters.push(`city = '${cityFilter.value}'`)
  }
  return filters
})

function currentSelectionValue(source: string, field: string) {
  crossFilters.version.value
  const match = crossFilters
    .getSelections()
    .find(
      (selection: CrossFilterSelection) =>
        selection.source === source && typeof selection.filters[field] === 'string',
    )
  return match?.filters[field] ?? null
}

const selectedCategory = computed(() => currentSelectionValue('tree-category', 'tree_category'))

const activeFilters = computed(() => {
  const filters: Array<{ key: string; label: string }> = []
  if (cityFilter.value) {
    filters.push({ key: 'city', label: `City: ${cityName.value}` })
  }
  crossFilters.version.value
  const grouped = new Map<string, string[]>()
  for (const selection of crossFilters.getSelections()) {
    const [field, value] = Object.entries(selection.filters)[0] ?? []
    if (!field || typeof value !== 'string') continue
    const values = grouped.get(selection.source) ?? []
    values.push(value)
    grouped.set(selection.source, values)
  }
  for (const { id, label, format } of crossFilterableCharts) {
    const values = grouped.get(id)
    if (values) {
      filters.push({ key: id, label: `${label}: ${values.map(format ?? ((v) => v)).join(', ')}` })
    }
  }
  return filters
})

function formatFilterValue(value: string) {
  return value.replace(/_/g, ' ')
}

function clearFilter(key: string) {
  if (key === 'city') cityFilter.value = null
  else crossFilters.clearSource(key)
}

function clearAllFilters() {
  cityFilter.value = null
  crossFilters.clearAll()
}

function filtersForChart(chartId: string) {
  crossFilters.version.value
  return crossFilters.getSqlFiltersFor(chartId, baseFilters.value)
}

function selectionFiltersForChart(chartId: string) {
  crossFilters.version.value
  return crossFilters
    .getSelections()
    .filter((selection: CrossFilterSelection) => selection.source === chartId)
    .map((selection: CrossFilterSelection) => ({
      source: selection.source,
      value: selection.chart ?? selection.filters,
    }))
}

function handleChartClick(info: DimensionClick) {
  crossFilters.applyDimensionClick(info)
}

function clearChartSelection(chartId: string) {
  crossFilters.clearSource(chartId)
}

function readRouteCity(value: unknown): CityCode | null {
  const city = Array.isArray(value) ? value[0] : value
  if (typeof city !== 'string') {
    return null
  }
  return city in CITY_CONFIG ? (city as CityCode) : null
}

const initialRouteCity = readRouteCity(route.query.city)
if (initialRouteCity) {
  cityFilter.value = initialRouteCity
  if (initialRouteCity !== selectedCity.value) {
    setSelectedCity(initialRouteCity)
  }
} else {
  cityFilter.value = selectedCity.value
}

onMounted(() => {
  void initialize()
})

watch(
  () => route.query.city,
  (routeCity) => {
    const nextCity = readRouteCity(routeCity)
    cityFilter.value = nextCity
    if (nextCity && nextCity !== selectedCity.value) {
      setSelectedCity(nextCity)
    }
  },
)

watch(
  cityFilter,
  (city) => {
    crossFilters.clearAll()
    const routeCity = readRouteCity(route.query.city)
    if (route.name === 'summary' && routeCity !== city) {
      const nextQuery = { ...route.query }
      if (city) {
        nextQuery.city = city
      } else {
        delete nextQuery.city
      }
      void router.replace({
        query: nextQuery,
      })
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.summary-page {
  padding: 28px 30px 40px;
  overflow-y: auto;
  height: 100%;
  box-sizing: border-box;
  background:
    radial-gradient(circle at top left, rgba(56, 189, 248, 0.1), transparent 24%),
    radial-gradient(circle at top right, rgba(168, 85, 247, 0.1), transparent 18%),
    linear-gradient(180deg, #11182d 0%, #151d35 42%, #11182d 100%);
  color: #e0e0e0;
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.summary-header {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.summary-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.summary-eyebrow {
  margin: 0 0 6px;
  font-size: 0.74rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #7dd3fc;
}

.summary-intro {
  margin: 0;
  max-width: 720px;
  color: #94a3b8;
  font-size: 0.95rem;
  line-height: 1.45;
}

.summary-title-row h1 {
  margin: 0;
  font-size: 2rem;
  font-weight: 800;
  color: #f8fafc;
  letter-spacing: -0.03em;
}

.city-badge {
  align-self: flex-start;
  background: linear-gradient(180deg, rgba(56, 189, 248, 0.2), rgba(14, 116, 144, 0.18));
  border: 1px solid rgba(125, 211, 252, 0.4);
  color: #67e8f9;
  padding: 8px 14px;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
}

.active-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(56, 189, 248, 0.12);
  border: 1px solid rgba(103, 232, 249, 0.24);
  border-radius: 16px;
  padding: 5px 10px 5px 12px;
  font-size: 0.78rem;
  color: #67e8f9;
}

.filter-chip-clear {
  background: none;
  border: none;
  color: #4fc3f7;
  cursor: pointer;
  font-size: 0.9rem;
  line-height: 1;
  padding: 0;
  opacity: 0.7;
}

.filter-chip-clear:hover {
  opacity: 1;
}

.clear-all-btn {
  background: none;
  border: none;
  color: #94a3b8;
  cursor: pointer;
  font-size: 0.78rem;
  text-decoration: underline;
  padding: 0;
}

.clear-all-btn:hover {
  color: #e2e8f0;
}

.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.chart-row {
  display: flex;
  gap: 16px;
  align-items: stretch;
  flex-wrap: wrap;
}

.chart-card {
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(180deg, rgba(23, 34, 63, 0.96), rgba(18, 28, 52, 0.98)),
    #16213e;
  border: 1px solid rgba(43, 92, 161, 0.45);
  border-radius: 16px;
  padding: 20px 22px;
  flex: 1;
  min-width: 260px;
  height: 380px;
  min-height: 0;
  overflow: hidden;
  box-shadow:
    0 16px 40px rgba(2, 6, 23, 0.24),
    inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.chart-card--kpi {
  height: 220px;
  padding: 16px 18px;
}

.chart-card--metric {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metric-label {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #7dd3fc;
}

.chart-card--narrow {
  flex: 0 0 420px;
}

.chart-card--wide {
  flex: 2 1 560px;
}

.chart-card-header {
  margin-bottom: 16px;
  flex: 0 0 auto;
}

.chart-card-header h2 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
  color: #dbe7ff;
  letter-spacing: 0.01em;
}

.header-filter-hint {
  font-weight: 400;
  color: #94a3b8;
  font-size: 0.8rem;
  margin-left: 6px;
}

.header-filter-hint em {
  color: #4fc3f7;
  font-style: normal;
  text-transform: capitalize;
}

.chart-sub {
  display: block;
  font-size: 0.78rem;
  color: #7f8ea8;
  margin-top: 6px;
  line-height: 1.35;
}

@media (max-width: 900px) {
  .summary-page {
    padding: 20px 18px 30px;
  }

  .kpi-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .summary-title-row h1 {
    font-size: 1.7rem;
  }
}

@media (max-width: 640px) {
  .kpi-row {
    grid-template-columns: 1fr;
  }

  .chart-card,
  .chart-card--narrow,
  .chart-card--wide {
    min-width: 100%;
    flex-basis: 100%;
  }
}
</style>
