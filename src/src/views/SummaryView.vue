<template>
  <div class="summary-page">
    <div class="summary-header">
      <div class="summary-title-row">
        <div>
          <p class="summary-eyebrow">Urban Forest Dashboard</p>
          <h1>{{ summaryTitle }}</h1>
        </div>
        <div v-if="cityContextPills.length" class="city-context-pills">
          <span v-for="pill in cityContextPills" :key="pill.label" class="city-context-pill">
            <span class="city-context-pill-label">{{ pill.label }}</span>
            <span class="city-context-pill-value">{{ pill.value }}</span>
          </span>
        </div>
      </div>
      <p class="summary-intro">
        Inventory concentration, ecological fit, resilience, and value for the active city.
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
        v-for="kpi in visibleKpiCharts"
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
          :allow-cross-filter="false"
        />
      </div>
    </div>

    <section
      v-for="section in visibleSummarySections"
      :key="section.id"
      class="summary-section"
    >
      <div class="summary-section-header">
        <h2>{{ section.title }}</h2>
        <p>{{ section.subtitle }}</p>
      </div>

      <div class="chart-row">
        <div
          v-for="card in section.cards"
          :key="card.id"
          :class="[
            'chart-card',
            card.width === 'wide' ? 'chart-card--wide' : null,
          ]"
        >
          <div class="chart-card-header">
            <h3>{{ chartById(card.id).title }}</h3>
            <span v-if="chartById(card.id).subtitle" class="chart-sub">{{ chartById(card.id).subtitle }}</span>
          </div>
          <EmbeddedDashboardChart
            :item-id="card.id"
            v-bind="sharedChartProps"
            :filters="filtersForChart(card.id)"
            :title="chartById(card.id).title"
            :query="chartById(card.id).query"
            :chart-config="chartById(card.id).chartConfig"
            :selection-filters="selectionFiltersForChart(card.id)"
            :allow-cross-filter="chartById(card.id).allowCrossFilter ?? true"
            @dimension-click="handleChartClick"
            @background-click="clearChartSelection(card.id)"
          />
        </div>
      </div>
    </section>
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
import { getCityBiome, getCityUsdaZone } from '../composables/dashboardContextSource'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import {
  SUMMARY_CHARTS_BY_ID,
  SUMMARY_DASHBOARD_IMPORTS,
  SUMMARY_KPI_CHARTS,
  SUMMARY_SECTIONS,
  getSummaryBaseFilters,
  readSummaryRouteCity,
} from '../composables/summaryDashboardConfig'
import { useSummaryFilters } from '../composables/useSummaryFilters'
import cityConfig from '../cityConfig.json'

const route = useRoute()
const router = useRouter()
const { selectedCity, setSelectedCity } = useMapData()
const { initialize, connectionId, queryExecutionService, setDashboardContext } = useSummaryDashboardExecution()

const sharedChartProps = {
  connectionId,
  queryExecutionService,
  imports: SUMMARY_DASHBOARD_IMPORTS as DashboardImport[],
}

const cityFilter = ref<CityCode | null>(null)
const {
  crossFilters,
  activeSummaryFilters,
} = useSummaryFilters()

const cityName = computed(() => {
  if (!cityFilter.value) {
    return 'All Cities'
  }
  const cfg = (cityConfig as Record<string, { name: string }>)[cityFilter.value]
  return cfg?.name ?? cityFilter.value
})

const summaryTitle = computed(() =>
  cityFilter.value ? `${cityName.value} City Summary` : 'City Summary',
)

const baseFilters = computed(() => getSummaryBaseFilters(cityFilter.value))

const cityContextPills = computed(() => {
  if (!cityFilter.value) {
    return []
  }
  return [
    { label: 'USDA zone', value: String(getCityUsdaZone(cityFilter.value)) },
    { label: 'Biome', value: getCityBiome(cityFilter.value) },
  ]
})

const visibleKpiCharts = computed(() =>
  SUMMARY_KPI_CHARTS.filter((chart) => cityFilter.value || !chart.requiresCitySelection),
)

const visibleSummarySections = computed(() =>
  SUMMARY_SECTIONS
    .map((section) => ({
      ...section,
      cards: section.cards.filter((card) => {
        const chart = chartById(card.id)
        return cityFilter.value || !chart.requiresCitySelection
      }),
    }))
    .filter((section) => section.cards.length > 0),
)

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

function chartById(chartId: string) {
  return SUMMARY_CHARTS_BY_ID[chartId]
}

function clearFilter(key: string) {
  if (key === 'city') {
    cityFilter.value = null
    return
  }
  crossFilters.clearSource(key)
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
    setDashboardContext(city)
  },
  { immediate: true },
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
      void router.replace({ query: nextQuery })
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

.city-context-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 999px;
  background: rgba(47, 125, 79, 0.1);
}

.city-context-pill-label {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgba(154, 166, 154, 0.82);
}

.city-context-pill-value {
  font-size: 0.82rem;
  color: var(--color-foam);
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

.city-context-pills {
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
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

.summary-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.summary-section-header {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.summary-section-header h2 {
  margin: 0;
  font-size: 1.08rem;
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: 0.03em;
}

.summary-section-header p {
  margin: 0;
  color: rgba(154, 166, 154, 0.82);
  font-size: 0.82rem;
  line-height: 1.45;
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
  flex: 1 1 260px;
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

.chart-card--wide {
  flex: 2 1 420px;
}

.chart-card-header {
  position: relative;
  z-index: 1;
  margin-bottom: 16px;
  flex: 0 0 auto;
}

.chart-card-header h3 {
  margin: 0;
  font-size: 1rem;
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: 0.03em;
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
  .chart-card--wide {
    flex-basis: 100%;
  }
}
</style>
