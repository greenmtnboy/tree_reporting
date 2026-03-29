<template>
  <div class="summary-page">
    <div class="summary-header">
      <div class="summary-title-row">
        <div>
          <p class="summary-eyebrow">Summary</p>
          <h1>Tree Analytics</h1>
        </div>
        <div class="city-badge">{{ cityName }}</div>
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
      <div class="chart-card chart-card--wide">
        <div class="chart-card-header">
          <h2>Tree Forms</h2>
        </div>
        <EmbeddedDashboardChart
          item-id="tree-form"
          v-bind="sharedChartProps"
          :filters="filtersForChart('tree-form')"
          :title="treeFormChart.title"
          :query="treeFormChart.query"
          :chart-config="treeFormChart.chartConfig"
          :selection-filters="selectionFiltersForChart('tree-form')"
          @dimension-click="handleChartClick"
          @background-click="clearChartSelection('tree-form')"
        />
      </div>

      <div class="chart-card chart-card--narrow">
        <div class="chart-card-header">
          <h2>
            Top Species
            <span v-if="selectedTreeForm" class="header-filter-hint">
              in <em>{{ formatFilterValue(selectedTreeForm) }}</em>
            </span>
          </h2>
          <span class="chart-sub">Top 15 by tree count</span>
        </div>
        <EmbeddedDashboardChart
          item-id="top-species"
          v-bind="sharedChartProps"
          :filters="filtersForChart('top-species')"
          :title="topSpeciesChart.title"
          :query="topSpeciesChart.query"
          :chart-config="topSpeciesChart.chartConfig"
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
          :title="nativeStatusChart.title"
          :query="nativeStatusChart.query"
          :chart-config="nativeStatusChart.chartConfig"
          :selection-filters="selectionFiltersForChart('native-status')"
          @dimension-click="handleChartClick"
          @background-click="clearChartSelection('native-status')"
        />
      </div>

      <div class="chart-card">
        <div class="chart-card-header">
          <h2>Drought Tolerance</h2>
          <span class="chart-sub">Enriched species only</span>
        </div>
        <EmbeddedDashboardChart
          item-id="drought-tolerance"
          v-bind="sharedChartProps"
          :filters="filtersForChart('drought-tolerance')"
          :title="droughtToleranceChart.title"
          :query="droughtToleranceChart.query"
          :chart-config="droughtToleranceChart.chartConfig"
          :selection-filters="selectionFiltersForChart('drought-tolerance')"
          @dimension-click="handleChartClick"
          @background-click="clearChartSelection('drought-tolerance')"
        />
      </div>

      <div class="chart-card chart-card--wide">
        <div class="chart-card-header">
          <h2>Trees Planted by Year</h2>
          <span class="chart-sub">Years with planting data</span>
        </div>
        <EmbeddedDashboardChart
          item-id="plant-year"
          v-bind="sharedChartProps"
          :filters="filtersForChart('plant-year')"
          :title="plantYearChart.title"
          :query="plantYearChart.query"
          :chart-config="plantYearChart.chartConfig"
          :selection-filters="selectionFiltersForChart('plant-year')"
          @dimension-click="handleChartClick"
          @background-click="clearChartSelection('plant-year')"
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
} from '@trilogy-data/trilogy-studio-components/dashboard'
import EmbeddedDashboardChart from '../components/EmbeddedDashboardChart.vue'
import { useMapData, type CityCode } from '../composables/useMapData'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import {
  SUMMARY_CHARTS_BY_ID,
  SUMMARY_DASHBOARD_IMPORTS,
  SUMMARY_KPI_CHARTS,
  getSummaryBaseFilters,
  readSummaryRouteCity,
} from '../composables/summaryDashboardConfig'
import { useSummaryFilters } from '../composables/useSummaryFilters'
import cityConfig from '../cityConfig.json'

const route = useRoute()
const router = useRouter()
const { selectedCity, setSelectedCity } = useMapData()
const { initialize, connectionId, queryExecutionService } = useSummaryDashboardExecution()

const sharedChartProps = {
  connectionId,
  queryExecutionService,
  imports: SUMMARY_DASHBOARD_IMPORTS as DashboardImport[],
}
const kpiCharts = SUMMARY_KPI_CHARTS
const treeFormChart = SUMMARY_CHARTS_BY_ID['tree-form']
const topSpeciesChart = SUMMARY_CHARTS_BY_ID['top-species']
const nativeStatusChart = SUMMARY_CHARTS_BY_ID['native-status']
const droughtToleranceChart = SUMMARY_CHARTS_BY_ID['drought-tolerance']
const plantYearChart = SUMMARY_CHARTS_BY_ID['plant-year']

const cityFilter = ref<CityCode | null>(null)
const {
  crossFilters,
  activeSummaryFilters,
  selectionsByField,
  formatSummaryFilterValue,
} = useSummaryFilters()

const cityName = computed(() => {
  if (!cityFilter.value) {
    return 'All Cities'
  }
  const cfg = (cityConfig as Record<string, { name: string }>)[cityFilter.value]
  return cfg?.name ?? cityFilter.value
})

const baseFilters = computed(() => {
  return getSummaryBaseFilters(cityFilter.value)
})

const selectedTreeForm = computed(() => selectionsByField().tree_form?.[0] ?? null)

const activeFilters = computed(() => {
  const filters: Array<{ key: string; label: string }> = []
  if (cityFilter.value) {
    filters.push({ key: 'city', label: `City: ${cityName.value}` })
  }
  for (const filter of activeSummaryFilters.value) {
    filters.push({ key: filter.key, label: filter.display })
  }
  return filters
})

function formatFilterValue(value: string) {
  return formatSummaryFilterValue(value)
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
  void crossFilters.version.value
  return crossFilters.getSqlFiltersFor(chartId, baseFilters.value)
}

function selectionFiltersForChart(chartId: string) {
  void crossFilters.version.value
  return crossFilters.getChartSelectionsFor(chartId).map((value) => ({
    source: chartId,
    value,
  }))
}

function handleChartClick(info: DimensionClick) {
  crossFilters.applyDimensionClick(info)
}

function clearChartSelection(chartId: string) {
  crossFilters.clearSource(chartId)
}

const initialRouteCity = readSummaryRouteCity(route.query.city)
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
    const nextCity = readSummaryRouteCity(routeCity)
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
    const routeCity = readSummaryRouteCity(route.query.city)
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
  display: flex;
  flex-direction: column;
  gap: 22px;
  color: var(--color-ink);
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
  margin: 0 0 8px;
  font-size: 0.72rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.summary-intro {
  margin: 0;
  max-width: 760px;
  color: rgba(237, 242, 235, 0.78);
  line-height: 1.55;
}

.summary-title-row h1 {
  margin: 0;
  font-size: 2rem;
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: 0.01em;
}

.city-badge {
  align-self: flex-start;
  padding: 10px 14px;
  border: 1px solid rgba(167, 227, 178, 0.16);
  border-radius: 10px;
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
  border-radius: 999px;
  font-size: 0.78rem;
  color: var(--color-leaf);
}

.filter-chip-clear {
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  font-size: 0.8rem;
}

.clear-all-btn {
  background: none;
  border: none;
  color: rgba(237, 242, 235, 0.65);
  cursor: pointer;
  font-size: 0.78rem;
  text-decoration: underline;
  padding: 0;
}

.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px;
}

.chart-row {
  display: flex;
  gap: 16px;
  align-items: stretch;
  flex-wrap: wrap;
}

.chart-card {
  position: relative;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.62), rgba(28, 31, 36, 0.72));
  border: 1px solid rgba(167, 227, 178, 0.1);
  border-radius: 14px;
  padding: 20px 22px;
  flex: 1 1 220px;
  min-width: 0;
  height: 380px;
  min-height: 0;
  overflow: hidden;
  box-shadow: 0 16px 36px rgba(7, 10, 11, 0.2);
  isolation: isolate;
}

.chart-card--kpi {
  height: 150px;
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
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: rgba(154, 166, 154, 0.85);
}

.chart-card--narrow {
  flex: 1 1 300px;
}

.chart-card--wide {
  flex: 2 1 380px;
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

.header-filter-hint {
  font-family: var(--font-body);
  font-size: 0.78rem;
  font-weight: 500;
  margin-left: 8px;
  color: rgba(237, 242, 235, 0.62);
}

.header-filter-hint em {
  color: var(--color-leaf);
  font-style: normal;
  text-transform: capitalize;
}

.chart-sub {
  display: block;
  font-size: 0.78rem;
  color: rgba(154, 166, 154, 0.82);
  margin-top: 8px;
  line-height: 1.45;
}

.chart-card::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 88px;
  height: 1px;
  background: linear-gradient(90deg, rgba(47, 125, 79, 0.58), rgba(167, 227, 178, 0));
}

@media (max-width: 900px) {
  .summary-page {
    padding: 20px 18px 30px;
  }

  .summary-title-row h1 {
    font-size: 1.7rem;
  }
}

@media (max-width: 560px) {
  .chart-card,
  .chart-card--narrow,
  .chart-card--wide {
    flex-basis: 100%;
  }
}
</style>
