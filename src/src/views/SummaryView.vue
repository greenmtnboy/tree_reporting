<template>
  <div class="summary-page">
    <div class="summary-header">
      <div class="summary-title-row">
        <div>
          <p class="summary-eyebrow">Urban Forest Dashboard</p>
          <h1>{{ summaryTitle }}</h1>
          <span class="sketch-rule sketch-rule--title" aria-hidden="true"></span>
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
          :dashboard-group="embeddedDashboardGroup"
          :filters="filtersForChart(kpi.id)"
          :title="kpi.title"
          :query="kpi.query"
          :chart-config="{ chartType: 'headline', xField: kpi.xField, showTitle: false }"
          :allow-cross-filter="false"
        />
      </div>
    </div>

    <div class="tree-map-card">
      <TreeDotMap
        item-id="tree-dot-map"
        v-bind="sharedChartProps"
        :dashboard-group="embeddedDashboardGroup"
        :filters="filtersForChart('tree-dot-map')"
        :priority="100"
      />
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
            chartById(card.id).renderMode === 'carousel' ? 'chart-card--carousel' : null,
            emptyChartIds.has(card.id) ? 'chart-card--empty' : null,
          ]"
        >
          <div class="chart-card-header">
            <h3>{{ emptyChartIds.has(card.id) ? 'No planting data for this city' : chartById(card.id).title }}</h3>
            <span v-if="!emptyChartIds.has(card.id) && chartById(card.id).subtitle" class="chart-sub">{{ chartById(card.id).subtitle }}</span>
          </div>
          <SpeciesCarousel
            v-if="chartById(card.id).renderMode === 'carousel'"
            :item-id="card.id"
            v-bind="sharedChartProps"
            :dashboard-group="embeddedDashboardGroup"
            :filters="filtersForChart(card.id)"
            :query="chartById(card.id).query"
          />
          <SummaryMarkdownCard
            v-else-if="chartById(card.id).renderMode === 'markdown'"
            :item-id="card.id"
            v-bind="sharedChartProps"
            :dashboard-group="embeddedDashboardGroup"
            :filters="filtersForChart(card.id)"
            :query="chartById(card.id).query"
          />
          <EmbeddedDashboardChart
            v-else
            v-show="!emptyChartIds.has(card.id)"
            :item-id="card.id"
            v-bind="sharedChartProps"
            :dashboard-group="embeddedDashboardGroup"
            :filters="filtersForChart(card.id)"
            :title="chartById(card.id).title"
            :query="chartById(card.id).query"
            :chart-config="chartById(card.id).chartConfig"
            :selection-filters="selectionFiltersForChart(card.id)"
            :allow-cross-filter="chartById(card.id).allowCrossFilter ?? true"
            @dimension-click="handleChartClick"
            @background-click="clearChartSelection(card.id)"
            @results-empty="emptyChartIds.add(card.id)"
            @results-present="emptyChartIds.delete(card.id)"
          />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  useEmbeddedDashboardGroup,
  type DashboardImport,
  type DimensionClick,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import EmbeddedDashboardChart from '../components/EmbeddedDashboardChart.vue'
import SummaryMarkdownCard from '../components/SummaryMarkdownCard.vue'
import SpeciesCarousel from '../components/SpeciesCarousel.vue'
import TreeDotMap from '../components/TreeDotMap.vue'
import { useMapData, type CityCode } from '../composables/useMapData'
import {
  buildDashboardContextParameters,
  getCityBiome,
  getCityUsdaZone,
} from '../composables/dashboardContextSource'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import {
  SUMMARY_CHARTS_BY_ID,
  SUMMARY_DASHBOARD_IMPORTS,
  SUMMARY_KPI_CHARTS,
  SUMMARY_SECTIONS,
  getSummaryBaseFilters,
  readSummaryRouteCity,
} from '../composables/summaryDashboardConfig'
import { filterSummaryDimensionClick, useSummaryFilters } from '../composables/useSummaryFilters'
import { useMapLifecycle } from '../composables/useMapLifecycle'
import cityConfig from '../cityConfig.json'

const route = useRoute()
const router = useRouter()
const { selectedCity, displayCity } = useMapData()
const { activateCity } = useMapLifecycle()
const { initialize, connectionId, queryExecutionService, setDashboardContext } = useSummaryDashboardExecution()

const embeddedDashboardGroup = useEmbeddedDashboardGroup({
  dashboardId: `summary-${connectionId}`,
  connectionId,
  queryExecutionService,
  imports: SUMMARY_DASHBOARD_IMPORTS as DashboardImport[],
  // One resolver round trip for the whole page. The executor compiles a batch
  // of at most this many items per request, and each request re-hydrates the
  // full model bundle (~300 KB, 2-3 s); the default of ten split the page's
  // eighteen charts into two serial requests, the second of which could not
  // start until every query of the first had returned.
  maxConcurrentQueries: 64,
})

const cityFilter = ref<CityCode | null>(null)
const emptyChartIds = reactive(new Set<string>())
const dashboardContextParameters = computed(() => buildDashboardContextParameters(cityFilter.value))

const sharedChartProps = computed(() => ({
  connectionId,
  queryExecutionService,
  imports: SUMMARY_DASHBOARD_IMPORTS as DashboardImport[],
  parameters: dashboardContextParameters.value,
}))

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
  return crossFilters.getSqlFilterLikesFor(chartId, baseFilters.value)
}

function selectionFiltersForChart(chartId: string) {
  void crossFilters.version.value
  return crossFilters.getChartSelectionsFor(chartId).map((value) => ({
    source: chartId,
    value,
  }))
}

function handleChartClick(info: DimensionClick) {
  const filtered = filterSummaryDimensionClick(info)
  if (!filtered) {
    return
  }
  crossFilters.applyDimensionClick(filtered)
}

function clearChartSelection(chartId: string) {
  crossFilters.clearSource(chartId)
}

const initialRouteCity = readSummaryRouteCity(route.query.city)
if (initialRouteCity) {
  cityFilter.value = initialRouteCity
  if (initialRouteCity !== selectedCity.value) {
    activateCity(initialRouteCity)
  }
} else {
  // No city in the URL: follow the city the map is flying to, not the one it is
  // flying away from, then commit it so the dashboard queries the right context.
  cityFilter.value = displayCity.value
  if (displayCity.value !== selectedCity.value) {
    activateCity(displayCity.value)
  }
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
      activateCity(nextCity)
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
    emptyChartIds.clear()
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
/* A page of the field journal: paper cards with a hairline, no drop shadows,
   serif titles underlined by a pencil stroke, the biome sketch showing faintly
   through. Charts take their colours from the same tokens (see
   EmbeddedDashboardChart), so the plot areas are transparent on the paper. */
.summary-page {
  --journal-paper: rgba(var(--surface-rgb), 0.78);
  --journal-rule: color-mix(in srgb, var(--color-border) 80%, transparent);
  --journal-corners: 18px 14px 20px 15px / 15px 20px 14px 18px;
  --trilogy-chart-font: var(--font-body);
  padding: 28px 30px 48px;
  overflow-y: auto;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 26px;
  color: var(--color-ink);
}

/* A short hand-drawn stroke, masked so it takes the ink colour of its owner. */
.sketch-rule,
.summary-section-header h2::after,
.chart-card-header h3::after {
  content: '';
  display: block;
  width: 72px;
  height: 7px;
  margin-top: 6px;
  background: var(--sketch-leaf);
  opacity: 0.7;
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 8' preserveAspectRatio='none'%3E%3Cpath d='M1 5.4C18 2.4 32 6.6 48 4.2S78 1.6 96 4.6s17 1.4 23 .4' fill='none' stroke='%23000' stroke-width='1.7' stroke-linecap='round'/%3E%3C/svg%3E") no-repeat left center / 100% 100%;
  mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 8' preserveAspectRatio='none'%3E%3Cpath d='M1 5.4C18 2.4 32 6.6 48 4.2S78 1.6 96 4.6s17 1.4 23 .4' fill='none' stroke='%23000' stroke-width='1.7' stroke-linecap='round'/%3E%3C/svg%3E") no-repeat left center / 100% 100%;
}

.summary-header {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.summary-title-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.summary-eyebrow {
  margin: 0 0 6px;
  font-size: 0.72rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.summary-title-row h1 {
  margin: 0;
  font-size: 2.1rem;
  font-family: var(--font-display);
  font-weight: 400;
  letter-spacing: -.025em;
  line-height: 1.1;
}

.sketch-rule--title {
  width: 132px;
  height: 9px;
  margin-top: 10px;
}

.summary-intro {
  margin: 0;
  max-width: 720px;
  color: rgba(var(--ink-rgb), 0.78);
  line-height: 1.55;
}

.city-context-pills {
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.city-context-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  padding: 7px 12px;
  border: 1px solid var(--journal-rule);
  border-radius: 999px;
  background: var(--journal-paper);
}

.city-context-pill-label {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.city-context-pill-value {
  font-size: 0.82rem;
  color: var(--color-ink);
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
  padding: 5px 8px 5px 12px;
  border: 1px solid rgba(var(--accent-rgb), 0.28);
  background: var(--accent-soft);
  border-radius: 999px;
  font-size: 0.78rem;
  color: var(--color-leaf);
}

.filter-chip-clear {
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 0.8rem;
  line-height: 1;
}

.filter-chip-clear:hover {
  background: rgba(var(--accent-rgb), 0.18);
}

.clear-all-btn {
  background: none;
  border: none;
  color: var(--color-muted);
  cursor: pointer;
  font-size: 0.78rem;
  text-decoration: underline;
  text-underline-offset: 3px;
  padding: 0;
}

.clear-all-btn:hover {
  color: var(--color-ink);
}

.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 14px;
}

/* The paper card. */
.chart-card,
.tree-map-card {
  position: relative;
  background: var(--journal-paper);
  border: 1px solid var(--journal-rule);
  border-radius: var(--journal-corners);
  box-shadow: none;
}

.tree-map-card {
  flex-shrink: 0;
  width: 100%;
  height: 380px;
  overflow: hidden;
}

.summary-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.summary-section-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.summary-section-header h2 {
  margin: 0;
  font-size: 1.3rem;
  font-family: var(--font-display);
  font-weight: 400;
  letter-spacing: -.01em;
}

.summary-section-header p {
  margin: 0;
  color: var(--color-muted);
  font-size: 0.84rem;
  line-height: 1.45;
}

.chart-row {
  display: flex;
  gap: 14px;
  align-items: stretch;
  flex-wrap: wrap;
}

.chart-card {
  display: flex;
  flex-direction: column;
  padding: 18px 20px 16px;
  flex: 1 1 260px;
  min-width: 0;
  height: 380px;
  min-height: 0;
  overflow: hidden;
  isolation: isolate;
}

.chart-card--kpi {
  --trilogy-chart-font: var(--font-display);
  height: 140px;
  padding: 14px 16px 10px;
  border-radius: 14px 18px 15px 17px / 17px 14px 18px 15px;
}

.chart-card--metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-label {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.chart-card--wide {
  flex: 2 1 420px;
}

.chart-card--empty {
  height: auto;
  min-height: 0;
  padding: 14px 20px;
  background: transparent;
  border-style: dashed;
}

.chart-card--empty .chart-card-header {
  margin-bottom: 0;
}

.chart-card--empty .chart-card-header h3 {
  font-size: 0.9rem;
  font-style: italic;
  font-weight: 400;
  color: var(--color-muted);
}

.chart-card--empty .chart-card-header h3::after {
  display: none;
}

.chart-card-header {
  position: relative;
  z-index: 1;
  margin-bottom: 12px;
  flex: 0 0 auto;
}

.chart-card-header h3 {
  margin: 0;
  font-size: 1.08rem;
  font-family: var(--font-display);
  font-weight: 400;
  letter-spacing: -.005em;
  line-height: 1.25;
}

.chart-card-header h3::after {
  width: 56px;
  height: 6px;
  margin-top: 5px;
  opacity: 0.55;
}

.chart-sub {
  display: block;
  font-size: 0.78rem;
  color: var(--color-muted);
  margin-top: 6px;
  line-height: 1.45;
}

@media (max-width: 900px) {
  .summary-page {
    padding: 20px 18px 36px;
  }

  .summary-title-row h1 {
    font-size: 1.7rem;
  }

  .city-context-pills {
    justify-content: flex-start;
  }
}

@media (max-width: 560px) {
  .chart-card,
  .chart-card--wide {
    flex-basis: 100%;
  }

  /* The carousel stacks its photo over its text on a phone; let the card
     grow with it instead of clipping the species name. */
  .chart-card--carousel {
    height: auto;
    min-height: 380px;
  }
}
</style>
