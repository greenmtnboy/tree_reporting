<template>
  <div class="summary-page">
    <section class="summary-header">
      <div class="summary-title-row">
        <div>
          <p class="summary-eyebrow">Summary</p>
          <h1>Tree Analytics</h1>
        </div>
        <div class="city-badge">{{ cityLabel }}</div>
      </div>
      <p class="summary-intro">
        Inventory mix, species distribution, and resilience indicators for the active city.
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
    </section>

    <section class="kpi-row" :class="{ 'is-loading': loading }">
      <article
        v-for="kpi in kpiCharts"
        :key="kpi.id"
        class="chart-card chart-card--kpi chart-card--metric"
      >
        <div class="metric-label">{{ kpi.label }}</div>
        <div class="chart-frame">
          <EmbeddedDashboardChart
            :key="`${selectedCity}-${kpi.id}`"
            :dashboard-id="dashboardId"
            :title="kpi.title"
            :item-id="kpi.id"
            :connection-id="connectionId"
            :query-execution-service="queryExecutionService"
            :query="kpi.query"
            :chart-config="{ chartType: 'headline', xField: kpi.xField, showTitle: false }"
            :filters="filtersForChart(kpi.id)"
          />
        </div>
      </article>
    </section>

    <section v-if="loadError" class="dashboard-error">
      <h2>Dashboard unavailable</h2>
      <p>{{ loadError }}</p>
    </section>

    <section v-else-if="loading || !dbReady" class="dashboard-loading">
      <div class="dashboard-loading-spinner"></div>
      <span>Loading summary...</span>
    </section>

    <template v-else>
      <div class="chart-row">
        <article class="chart-card chart-card--wide">
          <div class="chart-card-header">
            <h2>Tree Types</h2>
            <span class="chart-sub">Share of inventory</span>
          </div>
          <div class="chart-frame">
            <EmbeddedDashboardChart
              :key="`${selectedCity}-tree-types`"
              :dashboard-id="dashboardId"
              :title="'Tree Types'"
              :item-id="'tree-types'"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :query="treeTypesQuery"
              :chart-config="treeTypesConfig"
              :filters="filtersForChart('tree-types')"
              :selection-filters="selectionFiltersForChart('tree-types')"
              @dimension-click="handleChartClick"
              @background-click="clearChartSelection('tree-types')"
            />
          </div>
        </article>

        <article class="chart-card chart-card--narrow">
          <div class="chart-card-header">
            <h2>
              Top Species
              <span v-if="selectedCategory" class="header-filter-hint">
                in <em>{{ formatFilterValue(selectedCategory) }}</em>
              </span>
            </h2>
            <span class="chart-sub">Top 15 by tree count</span>
          </div>
          <div class="chart-frame">
            <EmbeddedDashboardChart
              :key="`${selectedCity}-top-species`"
              :dashboard-id="dashboardId"
              :title="'Top Species'"
              :item-id="'top-species'"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :query="topSpeciesQuery"
              :chart-config="topSpeciesConfig"
              :filters="filtersForChart('top-species')"
              :selection-filters="selectionFiltersForChart('top-species')"
              @dimension-click="handleChartClick"
              @background-click="clearChartSelection('top-species')"
            />
          </div>
        </article>
      </div>

      <div class="chart-row">
        <article class="chart-card">
          <div class="chart-card-header">
            <h2>Native Status</h2>
            <span class="chart-sub">Counts by inventory</span>
          </div>
          <div class="chart-frame">
            <EmbeddedDashboardChart
              :key="`${selectedCity}-native-status`"
              :dashboard-id="dashboardId"
              :title="'Native Status'"
              :item-id="'native-status'"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :query="nativeStatusQuery"
              :chart-config="nativeStatusConfig"
              :filters="filtersForChart('native-status')"
              :selection-filters="selectionFiltersForChart('native-status')"
              @dimension-click="handleChartClick"
              @background-click="clearChartSelection('native-status')"
            />
          </div>
        </article>

        <article class="chart-card">
          <div class="chart-card-header">
            <h2>Drought Tolerance</h2>
            <span class="chart-sub">Enriched species only</span>
          </div>
          <div class="chart-frame">
            <EmbeddedDashboardChart
              :key="`${selectedCity}-drought-tolerance`"
              :dashboard-id="dashboardId"
              :title="'Drought Tolerance'"
              :item-id="'drought-tolerance'"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :query="droughtToleranceQuery"
              :chart-config="droughtToleranceConfig"
              :filters="filtersForChart('drought-tolerance')"
              :allow-cross-filter="false"
            />
          </div>
        </article>

        <article class="chart-card">
          <div class="chart-card-header">
            <h2>Trees Planted by Year</h2>
            <span class="chart-sub">Years with planting data</span>
          </div>
          <div class="chart-frame">
            <EmbeddedDashboardChart
              :key="`${selectedCity}-planted-by-year`"
              :dashboard-id="dashboardId"
              :title="'Trees Planted by Year'"
              :item-id="'planted-by-year'"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :query="plantedByYearQuery"
              :chart-config="plantedByYearConfig"
              :filters="filtersForChart('planted-by-year')"
              :allow-cross-filter="false"
            />
          </div>
        </article>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  type CrossFilterSelection,
  type DimensionClick,
  useCrossFilterController,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import EmbeddedDashboardChart from '../components/EmbeddedDashboardChart.vue'
import { useMapData, CITY_CONFIG, type CityCode } from '../composables/useMapData'
import { useWorkerDashboardExecution } from '../composables/useWorkerDashboardExecution'

const treeTypesConfig = {
  chartType: 'donut',
  xField: 'tree_count',
  yField: 'tree_category',
  colorField: 'tree_category',
  showTitle: false,
} as const

const topSpeciesConfig = {
  chartType: 'bar',
  xField: 'species',
  yField: 'tree_count',
  showTitle: false,
} as const

const nativeStatusConfig = {
  chartType: 'barh',
  xField: 'tree_count',
  yField: 'native_status',
  showTitle: false,
} as const

const droughtToleranceConfig = {
  chartType: 'barh',
  xField: 'tree_count',
  yField: 'drought_tolerance',
  showTitle: false,
} as const

const plantedByYearConfig = {
  chartType: 'bar',
  xField: 'plant_year',
  yField: 'tree_count',
  showTitle: false,
} as const

const treeTypesQuery =
  'SELECT tree_category, count(tree_id) as tree_count WHERE tree_category IS NOT NULL ORDER BY tree_count DESC;'
const topSpeciesQuery =
  'SELECT species, count(tree_id) as tree_count WHERE species IS NOT NULL ORDER BY tree_count DESC LIMIT 15;'
const nativeStatusQuery =
  'SELECT native_status, count(tree_id) as tree_count WHERE native_status IS NOT NULL ORDER BY tree_count DESC;'
const droughtToleranceQuery =
  'SELECT drought_tolerance, count(tree_id) as tree_count WHERE drought_tolerance IS NOT NULL ORDER BY tree_count DESC;'
const plantedByYearQuery =
  'SELECT plant_date.year as plant_year, count(tree_id) as tree_count WHERE plant_date IS NOT NULL ORDER BY plant_year ASC;'

const kpiCharts = [
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
    query: 'SELECT count_distinct(species) as unique_species WHERE species IS NOT NULL;',
    xField: 'unique_species',
  },
  {
    id: 'average-dbh',
    label: 'Average size',
    title: 'Average Trunk Diameter',
    query: 'SELECT avg(diameter_at_breast_height) as avg_dbh WHERE diameter_at_breast_height IS NOT NULL;',
    xField: 'avg_dbh',
  },
  {
    id: 'evergreen-trees',
    label: 'Evergreen share',
    title: 'Evergreen Trees',
    query: 'SELECT count(tree_id) as evergreen_trees WHERE is_evergreen = true;',
    xField: 'evergreen_trees',
  },
] as const

const route = useRoute()
const { selectedCity, setSelectedCity } = useMapData()
const { connectionId, queryExecutionService, setCityContext } = useWorkerDashboardExecution()
const dbReady = computed(() => !loading.value && !loadError.value)

const crossFilterableCharts: { id: string; key: string; label: string; format?: (v: string) => string }[] = [
  { id: 'tree-types', key: 'tree_category', label: 'Type', format: formatFilterValue },
  { id: 'top-species', key: 'species', label: 'Species' },
  { id: 'native-status', key: 'native_status', label: 'Native', format: formatFilterValue },
]

const crossFilters = useCrossFilterController({
  validFields: ['tree_category', 'species', 'native_status'],
})

const loading = ref(true)
const loadError = ref<string | null>(null)

const cityLabel = computed(() => CITY_CONFIG[selectedCity.value]?.name ?? selectedCity.value)
const selectedCategory = computed(() => currentSelectionValue('tree-types', 'tree_category'))
const filterSignature = computed(() => {
  crossFilters.version.value
  return JSON.stringify(crossFilters.getSelections())
})
const dashboardId = computed(() => `summary-${selectedCity.value}`)

const activeFilters = computed(() => {
  crossFilters.version.value
  const grouped = new Map<string, string[]>()

  for (const selection of crossFilters.getSelections()) {
    const [field, value] = Object.entries(selection.filters)[0] ?? []
    if (!field || typeof value !== 'string') continue
    const values = grouped.get(selection.source) ?? []
    values.push(value)
    grouped.set(selection.source, values)
  }

  return crossFilterableCharts.flatMap(({ id, label, format }) => {
    const values = grouped.get(id)
    return values ? [{ key: id, label: `${label}: ${values.map(format ?? ((v) => v)).join(', ')}` }] : []
  })
})

function formatFilterValue(value: string) {
  return value.replace(/_/g, ' ')
}

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

function buildWhereClause(extraFilters: string[]) {
  return extraFilters.length ? `WHERE ${extraFilters.join(' AND ')}` : ''
}

function clearFilter(key: string) {
  crossFilters.clearSource(key)
}

function clearAllFilters() {
  crossFilters.clearAll()
}

function filtersForChart(chartId: string) {
  crossFilters.version.value
  return crossFilters.getSqlFiltersFor(chartId)
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

async function syncCityContext() {
  loading.value = true
  loadError.value = null

  try {
    await setCityContext(selectedCity.value)
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error)
  } finally {
    loading.value = false
  }
}

function readRouteCity(value: unknown): CityCode | null {
  const city = Array.isArray(value) ? value[0] : value
  if (typeof city !== 'string') return null
  return city in CITY_CONFIG ? (city as CityCode) : null
}

const initialRouteCity = readRouteCity(route.query.city)
if (initialRouteCity && initialRouteCity !== selectedCity.value) {
  setSelectedCity(initialRouteCity)
}

watch(
  () => route.query.city,
  (routeCity) => {
    const nextCity = readRouteCity(routeCity)
    if (nextCity && nextCity !== selectedCity.value) {
      setSelectedCity(nextCity)
    }
    clearAllFilters()
  },
)

watch(
  () => selectedCity.value,
  () => {
    void syncCityContext()
  },
  { immediate: true },
)
</script>

<style scoped>
.summary-page {
  height: 100%;
  overflow-y: auto;
  padding: 30px 30px 40px;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 22px;
  color: var(--color-ink);
}

.summary-page::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.026) 1px, transparent 1px),
    linear-gradient(0deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
  background-size: 44px 44px;
  opacity: 0.25;
  pointer-events: none;
}

.summary-header,
.kpi-row,
.chart-row,
.dashboard-error,
.dashboard-loading {
  position: relative;
  z-index: 1;
}

.summary-header {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.summary-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.summary-eyebrow {
  margin: 0 0 8px;
  font-size: 0.72rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.summary-title-row h1 {
  margin: 0;
  font-size: 2.25rem;
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: 0.01em;
}

.summary-intro {
  margin: 0;
  max-width: 760px;
  color: rgba(237, 242, 235, 0.78);
  line-height: 1.55;
}

.summary-meta {
  font-size: 0.75rem;
  letter-spacing: 0.04em;
  color: rgba(154, 166, 154, 0.74);
}

.city-badge {
  align-self: flex-start;
  padding: 10px 14px;
  border: 1px solid rgba(167, 227, 178, 0.16);
  background:
    linear-gradient(180deg, rgba(58, 64, 72, 0.72), rgba(28, 31, 36, 0.92));
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-leaf);
}

.active-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px 6px 12px;
  border: 1px solid rgba(107, 175, 146, 0.18);
  background: rgba(47, 125, 79, 0.12);
  color: var(--color-leaf);
  font-size: 0.78rem;
}

.filter-chip-label {
  text-transform: capitalize;
}

.filter-chip-clear,
.clear-all-btn {
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  font-size: 0.8rem;
}

.clear-all-btn {
  color: rgba(237, 242, 235, 0.65);
  text-decoration: underline;
}

.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.metric-label {
  position: relative;
  z-index: 1;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: rgba(154, 166, 154, 0.85);
}

.chart-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  flex-wrap: wrap;
}

.chart-card {
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 260px;
  height: 380px;
  min-height: 0;
  padding: 20px 22px 22px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.84), rgba(28, 31, 36, 0.96));
  box-shadow: var(--shadow-soft);
  isolation: isolate;
}

.chart-card--kpi {
  height: 220px;
  padding: 16px 18px;
}

.chart-card--metric {
  gap: 8px;
}

.chart-card::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 88px;
  height: 1px;
  background: linear-gradient(90deg, rgba(77, 163, 255, 0.8), rgba(167, 227, 178, 0));
}

.chart-card--wide {
  flex: 2 1 560px;
}

.chart-card--narrow {
  flex: 0 0 420px;
}

.chart-card-header {
  position: relative;
  z-index: 1;
  margin-bottom: 16px;
  flex: 0 0 auto;
}

.chart-card-header h2 {
  margin: 0;
  font-size: 1rem;
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: 0.03em;
}

.chart-sub {
  display: block;
  margin-top: 8px;
  font-size: 0.78rem;
  color: rgba(154, 166, 154, 0.82);
  line-height: 1.45;
}

.header-filter-hint {
  margin-left: 8px;
  font-family: var(--font-body);
  font-size: 0.78rem;
  font-weight: 500;
  letter-spacing: 0;
  color: rgba(237, 242, 235, 0.62);
}

.header-filter-hint em {
  font-style: normal;
  color: var(--color-leaf);
  text-transform: capitalize;
}

.chart-frame {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
}

.dashboard-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 240px;
  color: rgba(237, 242, 235, 0.72);
}

.dashboard-loading-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(167, 227, 178, 0.2);
  border-top-color: var(--color-moss);
  border-radius: 50%;
  animation: summary-spin 0.8s linear infinite;
}

.dashboard-error {
  padding: 18px 20px;
  border: 1px solid rgba(217, 122, 58, 0.2);
  background: rgba(122, 92, 62, 0.14);
}

.dashboard-error h2 {
  margin: 0 0 8px;
  font-size: 1rem;
  font-family: var(--font-display);
  letter-spacing: 0.03em;
}

.dashboard-error p {
  margin: 0;
  line-height: 1.5;
  color: rgba(237, 242, 235, 0.78);
}

.is-loading .chart-card--kpi {
  opacity: 0.72;
}

@keyframes summary-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 960px) {
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
  .chart-card--wide,
  .chart-card--narrow {
    min-width: 100%;
    flex-basis: 100%;
  }

  .summary-title-row {
    flex-direction: column;
  }

  .city-badge {
    align-self: stretch;
    text-align: center;
  }
}
</style>
