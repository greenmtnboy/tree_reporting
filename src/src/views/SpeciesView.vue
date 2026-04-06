<template>
  <div class="species-page" :style="speciesAccentStyle">
    <div class="species-header">
      <div class="species-title-row">
        <div>
          <p class="species-eyebrow">Urban Forest Dashboard</p>
          <h1>Tree Species Explorer</h1>
        </div>
        <div v-if="speciesContextPills.length" class="city-context-pills">
          <span v-for="pill in speciesContextPills" :key="pill.label" class="city-context-pill">
            <span class="city-context-pill-label">{{ pill.label }}</span>
            <span class="city-context-pill-value">{{ pill.value }}</span>
          </span>
        </div>
      </div>
      <p class="species-intro">
        Select a species to explore its presence across the urban forest — population size, city distribution, planting history, ecological fit, and physical profile.
      </p>

      <div class="species-filter-bar">
        <div class="species-filter-group">
          <span class="species-filter-label">Genus</span>
          <span class="species-filter-hint">Defaults to the most common genus in the current scope.</span>
          <SpeciesSearchFilter
            v-model="genusFilter"
            :connection-id="connectionId"
            :query-execution-service="queryExecutionService"
            :connection-ready="ready"
            :imports="SPECIES_DASHBOARD_IMPORTS as DashboardImport[]"
            :base-filters="genusSelectorFilters"
            :top-query="TOP_GENUS_QUERY"
            :full-query="GENUS_QUERY"
            :include-all-option="true"
            all-option-label="All genera"
            placeholder="Search genera"
            loading-message="Loading genera..."
            empty-message="No genera match this view."
            @options-loaded="handleGenusOptionsLoaded"
          />
        </div>
        <div class="species-filter-group">
          <span class="species-filter-label">Species</span>
          <span class="species-filter-hint">Optional. Focus a single binomial, hybrid, or varietal within the genus.</span>
          <SpeciesSearchFilter
            v-model="speciesFilter"
            :connection-id="connectionId"
            :query-execution-service="queryExecutionService"
            :connection-ready="ready"
            :imports="SPECIES_DASHBOARD_IMPORTS as DashboardImport[]"
            :base-filters="speciesSelectorBaseFilters"
            :top-query="TOP_SPECIES_QUERY"
            :full-query="SPECIES_QUERY"
            :auto-select-top="false"
            :include-all-option="true"
            all-option-label="All species"
            :disabled="!genusFilter"
            placeholder="Search species in this genus"
            disabled-placeholder="Select a genus first"
            loading-message="Loading species..."
            empty-message="No species match this genus."
            @options-loaded="handleSpeciesOptionsLoaded"
          />
        </div>
      </div>

      <div v-if="activeFilters.length" class="active-filters">
        <span v-for="filter in activeFilters" :key="filter.key" class="filter-chip">
          <span class="filter-chip-label">{{ filter.label }}</span>
          <button class="filter-chip-clear" :title="`Remove ${filter.label}`" @click="clearFilter(filter.key)">
            x
          </button>
        </span>
        <button class="clear-all-btn" @click="clearAllFilters">Clear all</button>
      </div>

      <div v-if="speciesContextPills.length" class="species-pill-grid">
        <span v-for="pill in speciesContextPills" :key="pill.label" class="city-context-pill">
          <span class="city-context-pill-label">{{ pill.label }}</span>
          <span class="city-context-pill-value">{{ pill.value }}</span>
        </span>
      </div>
    </div>

    <div v-if="gateMessage" class="species-gate-state">
      {{ gateMessage }}
    </div>

    <template v-else>
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

    <section
      v-for="section in visibleSections"
      :key="section.id"
      :class="[
        'species-section',
        section.id !== 'sp-spotlight-section' ? 'species-section--single-column' : null,
      ]"
    >
      <div class="species-section-header">
        <h2>{{ section.title }}</h2>
        <p>{{ section.subtitle }}</p>
      </div>

      <div class="chart-row">
        <div
          v-for="card in section.cards"
          :key="card.id"
          :class="[
            'chart-card',
            card.width === 'full' ? 'chart-card--full' : null,
            card.width === 'wide' ? 'chart-card--wide' : null,
            card.height === 'tall' ? 'chart-card--tall' : null,
            emptyChartIds.has(card.id) ? 'chart-card--empty' : null,
          ]"
        >
          <template v-if="card.id === 'sp-city-presence-map' && cityFilter">
            <div class="chart-card-header">
              <h3>City Tree Distribution</h3>
              <span class="chart-sub">All trees in the filtered set within {{ cityName }}</span>
            </div>
            <TreeDotMap
              item-id="sp-city-dot-map"
              v-bind="sharedChartProps"
              :filters="filtersForChart(card.id)"
            />
          </template>
          <template v-else>
            <div class="chart-card-header">
              <h3>{{ emptyChartIds.has(card.id) ? 'No data for this filter set' : chartById(card.id).title }}</h3>
              <span
                v-if="!emptyChartIds.has(card.id) && chartById(card.id).subtitle"
                class="chart-sub"
              >{{ chartById(card.id).subtitle }}</span>
            </div>
            <SummaryMarkdownCard
              v-if="chartById(card.id).renderMode === 'markdown'"
              :item-id="card.id"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :imports="SPECIES_DASHBOARD_IMPORTS as DashboardImport[]"
              :filters="filtersForChart(card.id)"
              :parameters="dashboardContextParameters"
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
          </template>
        </div>
      </div>
    </section>
    </template>
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
import SpeciesSearchFilter from '../components/SpeciesSearchFilter.vue'
import TreeDotMap from '../components/TreeDotMap.vue'
import { buildDashboardContextParameters } from '../composables/dashboardContextSource'
import { useMapData, type CityCode } from '../composables/useMapData'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import {
  SPECIES_GENUS_EXPR,
  SPECIES_CHARTS_BY_ID,
  SPECIES_DASHBOARD_IMPORTS,
  SPECIES_KPI_CHARTS,
  SPECIES_SECTIONS,
  extractGenusFromSpecies,
  getGenusSqlFilter,
  getSpeciesSqlFilter,
  getSpeciesViewBaseFilters,
  parseScientificName,
  readSpeciesRouteCity,
} from '../composables/speciesDashboardConfig'
import { filterSpeciesDimensionClick, useSpeciesFilters } from '../composables/useSpeciesFilters'
import { CATEGORY_COLORS } from '../treeFormColors'
import type { TreeForm } from '../types'
import cityConfig from '../cityConfig.json'

type FilterOption = {
  label: string
  value: string
  count: number | null
}

const GENUS_QUERY = `SELECT
  ${SPECIES_GENUS_EXPR} as option_value,
  ${SPECIES_GENUS_EXPR} as option_label,
  count(tree_id) as tree_count
WHERE species IS NOT NULL
ORDER BY tree_count DESC;`

const TOP_GENUS_QUERY = `SELECT
  ${SPECIES_GENUS_EXPR} as option_value,
  ${SPECIES_GENUS_EXPR} as option_label,
  count(tree_id) as tree_count
WHERE species IS NOT NULL
ORDER BY tree_count DESC
LIMIT 1;`

const SPECIES_QUERY = `SELECT
  species as option_value,
  species as option_label,
  count(tree_id) as tree_count
WHERE species IS NOT NULL
ORDER BY tree_count DESC;`

const TOP_SPECIES_QUERY = `SELECT
  species as option_value,
  species as option_label,
  count(tree_id) as tree_count
WHERE species IS NOT NULL
ORDER BY tree_count DESC
LIMIT 1;`

const route = useRoute()
const router = useRouter()
let syncingRoute = false
const { selectedCity, setSelectedCity } = useMapData()
const { initialize, connectionId, queryExecutionService, setDashboardContext, ready } = useSummaryDashboardExecution()

const embeddedDashboardGroup = useEmbeddedDashboardGroup({
  dashboardId: `species-${connectionId}`,
  connectionId,
  queryExecutionService,
  imports: SPECIES_DASHBOARD_IMPORTS as DashboardImport[],
})

const cityFilter = ref<CityCode | null>(null)
const genusFilter = ref<string | null>(null)
const speciesFilter = ref<string | null>(null)
const genusOptions = ref<FilterOption[]>([])
const speciesOptions = ref<FilterOption[]>([])
const emptyChartIds = reactive(new Set<string>())
const dashboardContextParameters = computed(() => buildDashboardContextParameters(cityFilter.value))

const sharedChartProps = computed(() => ({
  connectionId,
  queryExecutionService,
  imports: SPECIES_DASHBOARD_IMPORTS as DashboardImport[],
  parameters: dashboardContextParameters.value,
}))

const { crossFilters, activeSpeciesFilters } = useSpeciesFilters()

const cityBaseFilters = computed(() => (cityFilter.value ? [`city = '${cityFilter.value}'`] : []))
const genusSelectorFilters = computed(() => {
  void crossFilters.version.value
  return crossFilters.getSqlFiltersFor('sp-selector-genus', cityBaseFilters.value)
})
const speciesSelectorBaseFilters = computed(() => {
  void crossFilters.version.value
  return genusFilter.value
    ? crossFilters.getSqlFiltersFor('sp-selector-species', [...cityBaseFilters.value, getGenusSqlFilter(genusFilter.value)])
    : genusSelectorFilters.value
})
const baseFilters = computed(() =>
  getSpeciesViewBaseFilters(cityFilter.value, genusFilter.value, speciesFilter.value),
)

const cityName = computed(() => {
  if (!cityFilter.value) return 'All Cities'
  const cfg = (cityConfig as Record<string, { name: string }>)[cityFilter.value]
  return cfg?.name ?? cityFilter.value
})

// Species enrichment context — fetched when selected species changes
type SpeciesContext = { commonName: string; treeForm: string; growthRate: string }
const speciesContext = ref<SpeciesContext | null>(null)
let contextLoadVersion = 0
const gateMessage = computed(() => {
  if (!ready.value) {
    return 'Loading species explorer...'
  }
  if (genusFilter.value) {
    return null
  }
  return cityFilter.value
    ? 'Finding the top genus for this city...'
    : 'Finding the top genus across the urban forest...'
})

async function loadSpeciesContext(species: string | null) {
  if (!species) {
    speciesContext.value = null
    return
  }
  const version = ++contextLoadVersion
  try {
    const execution = await queryExecutionService.executeQueriesBatch(
      connectionId,
      [{
        label: 'species-context',
        query: `SELECT common_names[1] as common_name, tree_form, growth_rate WHERE species IS NOT NULL LIMIT 1;`,
        extra_filters: [getSpeciesSqlFilter(species)],
      }],
      'trilogy',
      SPECIES_DASHBOARD_IMPORTS.map((i) => ({ name: i.name, alias: i.alias })),
    )
    const batch = await execution.resultPromise
    if (version !== contextLoadVersion) return
    const r = batch.results[0]
    if (r?.success && r.results) {
      const payload = r.results.toJSON() as { data?: Array<Record<string, unknown>> }
      const row = Array.isArray(payload.data) ? payload.data[0] : null
      if (!row) {
        speciesContext.value = null
        return
      }
      const get = (field: string) => String(row[field] ?? '')
      speciesContext.value = {
        commonName: get('common_name'),
        treeForm: get('tree_form'),
        growthRate: get('growth_rate'),
      }
    } else {
      speciesContext.value = null
    }
  } catch {
    if (version === contextLoadVersion) speciesContext.value = null
  }
}

function handleGenusOptionsLoaded(options: FilterOption[]) {
  genusOptions.value = options
}

function handleSpeciesOptionsLoaded(options: FilterOption[]) {
  speciesOptions.value = options
}

function classifyTaxonBucket(scientificName: string) {
  const parts = parseScientificName(scientificName)
  return parts.taxonType
}

const selectedGenusOption = computed(() => (
  genusOptions.value.find((option) => option.value === genusFilter.value) ?? null
))

const selectedSpeciesNameParts = computed(() => parseScientificName(speciesFilter.value))

const genusTaxonomySummary = computed(() => {
  const summary = {
    speciesCount: 0,
    hybridCount: 0,
    varietalCount: 0,
    unknownCount: 0,
  }
  for (const option of speciesOptions.value) {
    const bucket = classifyTaxonBucket(option.value)
    if (bucket === 'species') summary.speciesCount += 1
    else if (bucket === 'hybrid') summary.hybridCount += 1
    else if (bucket === 'varietal') summary.varietalCount += 1
    else summary.unknownCount += 1
  }
  return {
    ...summary,
    totalTaxa: speciesOptions.value.length,
  }
})

const speciesContextPills = computed(() => {
  const pills: Array<{ label: string; value: string }> = []
  if (genusFilter.value) pills.push({ label: 'Genus', value: genusFilter.value })
  if (selectedGenusOption.value?.count != null) {
    pills.push({ label: 'Trees in genus', value: selectedGenusOption.value.count.toLocaleString() })
  }
  if (genusTaxonomySummary.value.totalTaxa > 0) {
    pills.push({ label: 'Taxa', value: genusTaxonomySummary.value.totalTaxa.toLocaleString() })
  }
  if (genusTaxonomySummary.value.speciesCount > 0) {
    pills.push({ label: 'Species', value: genusTaxonomySummary.value.speciesCount.toLocaleString() })
  }
  if (genusTaxonomySummary.value.hybridCount > 0) {
    pills.push({ label: 'Hybrids', value: genusTaxonomySummary.value.hybridCount.toLocaleString() })
  }
  if (genusTaxonomySummary.value.varietalCount > 0) {
    pills.push({ label: 'Varietals', value: genusTaxonomySummary.value.varietalCount.toLocaleString() })
  }
  if (speciesFilter.value && selectedSpeciesNameParts.value.specificEpithet) {
    pills.push({ label: 'Specific epithet', value: selectedSpeciesNameParts.value.specificEpithet })
  }
  if (speciesFilter.value && selectedSpeciesNameParts.value.taxonType !== 'unknown') {
    pills.push({
      label: 'Taxon type',
      value: selectedSpeciesNameParts.value.taxonType.replace(/\b\w/g, (letter) => letter.toUpperCase()),
    })
  }
  const ctx = speciesContext.value
  if (ctx?.commonName) pills.push({ label: 'Common name', value: ctx.commonName })
  if (ctx?.treeForm) pills.push({ label: 'Form', value: ctx.treeForm.replace(/_/g, ' ') })
  if (ctx?.growthRate) pills.push({ label: 'Growth', value: ctx.growthRate })
  return pills
})

const speciesAccentStyle = computed(() => {
  const form = speciesContext.value?.treeForm as TreeForm | undefined
  const color = form && form in CATEGORY_COLORS ? CATEGORY_COLORS[form] : CATEGORY_COLORS.default
  return { '--species-accent': color }
})

const visibleKpiCharts = computed(() =>
  SPECIES_KPI_CHARTS.filter((chart) => cityFilter.value || !chart.requiresCitySelection),
)

const visibleSections = computed(() =>
  SPECIES_SECTIONS
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
  if (genusFilter.value) {
    filters.push({ key: 'genus', label: `Genus: ${genusFilter.value}` })
  }
  if (speciesFilter.value) {
    filters.push({ key: 'species', label: `Species: ${speciesFilter.value}` })
  }
  for (const filter of activeSpeciesFilters.value) {
    filters.push({ key: filter.key, label: filter.display })
  }
  return filters
})

function chartById(chartId: string) {
  return SPECIES_CHARTS_BY_ID[chartId]
}

function clearFilter(key: string) {
  if (key === 'city') {
    cityFilter.value = null
    return
  }
  if (key === 'genus') {
    genusFilter.value = null
    speciesFilter.value = null
    return
  }
  if (key === 'species') {
    speciesFilter.value = null
    return
  }
  crossFilters.clearSource(key)
}

function clearAllFilters() {
  cityFilter.value = null
  genusFilter.value = null
  speciesFilter.value = null
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
  const filtered = filterSpeciesDimensionClick(info)
  if (!filtered) return
  crossFilters.applyDimensionClick(filtered)
}

function clearChartSelection(chartId: string) {
  crossFilters.clearSource(chartId)
}

function readRouteText(value: unknown): string | null {
  const normalized = Array.isArray(value) ? value[0] : value
  return typeof normalized === 'string' && normalized ? normalized : null
}

const initialRouteCity = readSpeciesRouteCity(route.query.city)
if (initialRouteCity) {
  cityFilter.value = initialRouteCity
  if (initialRouteCity !== selectedCity.value) {
    setSelectedCity(initialRouteCity)
  }
} else {
  cityFilter.value = null
}

const initialRouteSpecies = readRouteText(route.query.species)
const initialRouteGenus = readRouteText(route.query.genus) ?? extractGenusFromSpecies(initialRouteSpecies)
if (initialRouteGenus) genusFilter.value = initialRouteGenus
if (initialRouteSpecies) speciesFilter.value = initialRouteSpecies

onMounted(() => {
  void initialize()
})

watch(
  () => route.query.city,
  (routeCity) => {
    if (syncingRoute) return
    const nextCity = readSpeciesRouteCity(routeCity)
    cityFilter.value = nextCity
    if (nextCity && nextCity !== selectedCity.value) {
      setSelectedCity(nextCity)
    }
  },
)

watch(
  () => route.query.genus,
  (routeGenus) => {
    if (syncingRoute) return
    const routeSpecies = readRouteText(route.query.species)
    const next = readRouteText(routeGenus) ?? extractGenusFromSpecies(routeSpecies)
    if (genusFilter.value !== next) {
      genusFilter.value = next
    }
  },
)

watch(
  () => route.query.species,
  (routeSpecies) => {
    if (syncingRoute) return
    const next = readRouteText(routeSpecies)
    if (speciesFilter.value !== next) {
      speciesFilter.value = next
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
    // Clear species filter when city changes — species counts will be different
    genusFilter.value = null
    speciesFilter.value = null
    const routeCity = readSpeciesRouteCity(route.query.city)
    if (route.name === 'species' && routeCity !== city) {
      const nextQuery = { ...route.query }
      if (city) {
        nextQuery.city = city
      } else {
        delete nextQuery.city
      }
      syncingRoute = true
      void router.replace({ query: nextQuery }).finally(() => {
        syncingRoute = false
      })
    }
  },
  { immediate: true },
)

watch(genusFilter, (genus, previousGenus) => {
  if (genus !== previousGenus) {
    emptyChartIds.clear()
    crossFilters.clearAll()
  }
  if (speciesFilter.value && extractGenusFromSpecies(speciesFilter.value) !== genus) {
    speciesFilter.value = null
  }
})

watch(speciesFilter, (species) => {
  const derivedGenus = extractGenusFromSpecies(species)
  if (species && derivedGenus && genusFilter.value !== derivedGenus) {
    genusFilter.value = derivedGenus
  }
  void loadSpeciesContext(species)
})

watch([genusFilter, speciesFilter], ([genus, species]) => {
  if (route.name !== 'species') return
  const routeGenus = readRouteText(route.query.genus)
  const routeSpecies = readRouteText(route.query.species)
  if (routeGenus === genus && routeSpecies === species) return
  const nextQuery = { ...route.query }
  if (genus) {
    nextQuery.genus = genus
  } else {
    delete nextQuery.genus
  }
  if (species) {
    nextQuery.species = species
  } else {
    delete nextQuery.species
  }
  syncingRoute = true
  void router.replace({ query: nextQuery }).finally(() => {
    syncingRoute = false
  })
})
</script>

<style scoped>
.species-page {
  padding: 28px 30px 40px;
  overflow-y: auto;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 22px;
  color: var(--color-ink);
}

.species-header {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.species-title-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.species-eyebrow {
  margin: 0 0 8px;
  font-size: 0.72rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--species-accent, var(--color-moss));
  transition: color 0.4s ease;
}

.species-title-row h1 {
  margin: 0;
  font-size: 2rem;
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: 0.01em;
}

.species-intro {
  margin: 12px 0 0;
  max-width: 820px;
  color: rgba(237, 242, 235, 0.78);
  line-height: 1.65;
}

.city-context-pills {
  display: none;
}

.species-pill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
  gap: 10px;
}

.city-context-pill {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-height: 62px;
  padding: 11px 13px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 14px;
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.5), rgba(22, 26, 31, 0.76)),
    color-mix(in srgb, var(--species-accent, #2F7D4F) 10%, transparent);
  transition: background 0.4s ease, border-color 0.2s ease;
}

.city-context-pill-label {
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgba(154, 166, 154, 0.82);
}

.city-context-pill-value {
  font-size: 0.84rem;
  font-weight: 600;
  color: var(--color-foam);
}

.species-filter-bar {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  padding: 18px;
  background: linear-gradient(180deg, rgba(31, 36, 41, 0.76), rgba(23, 27, 32, 0.9));
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 18px;
  box-shadow: 0 18px 42px rgba(5, 8, 10, 0.22);
}

.species-filter-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  padding: 14px;
  background: rgba(12, 17, 21, 0.26);
  border: 1px solid rgba(167, 227, 178, 0.08);
  border-radius: 14px;
}

.species-filter-label {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--species-accent, var(--color-moss));
  transition: color 0.4s ease;
}

.species-filter-hint {
  font-size: 0.77rem;
  line-height: 1.45;
  color: rgba(154, 166, 154, 0.82);
}

.active-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px 8px 14px;
  border: 1px solid rgba(107, 175, 146, 0.18);
  background: rgba(47, 125, 79, 0.1);
  border-radius: 999px;
  font-size: 0.78rem;
  color: var(--color-leaf);
}

.filter-chip-clear {
  width: 20px;
  height: 20px;
  border: 1px solid rgba(167, 227, 178, 0.18);
  border-radius: 999px;
  background: rgba(15, 21, 17, 0.28);
  color: inherit;
  cursor: pointer;
  font-size: 0.8rem;
  line-height: 1;
}

.clear-all-btn {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 999px;
  color: rgba(237, 242, 235, 0.72);
  cursor: pointer;
  font-size: 0.78rem;
  padding: 8px 12px;
}

.species-gate-state {
  padding: 18px 20px;
  border: 1px solid rgba(167, 227, 178, 0.1);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(42, 47, 54, 0.52), rgba(28, 31, 36, 0.64));
  color: rgba(237, 242, 235, 0.72);
  font-size: 0.92rem;
}

.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px;
}

.kpi-row .chart-card {
  grid-column: auto;
}

.species-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.species-section-header {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.species-section-header h2 {
  margin: 0;
  font-size: 1.08rem;
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: 0.03em;
}

.species-section-header p {
  margin: 0;
  color: rgba(154, 166, 154, 0.82);
  font-size: 0.82rem;
  line-height: 1.45;
}

.chart-row {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 16px;
}

.chart-card {
  position: relative;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, rgba(42, 47, 54, 0.62), rgba(28, 31, 36, 0.72));
  border: 1px solid rgba(167, 227, 178, 0.1);
  border-radius: 14px;
  padding: 20px 22px;
  grid-column: 1 / -1;
  min-width: 0;
  height: 320px;
  min-height: 0;
  overflow: hidden;
  box-shadow: 0 16px 36px rgba(7, 10, 11, 0.2);
  isolation: isolate;
}

.chart-card--kpi {
  height: 124px;
  padding: 14px 16px;
}

.chart-card--metric {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metric-label {
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: rgba(154, 166, 154, 0.85);
}

.chart-card--wide {
  grid-column: 1 / -1;
}

.chart-card--full {
  grid-column: 1 / -1;
}

.chart-card--tall {
  height: 460px;
}

.species-section--single-column .chart-card {
  grid-column: 1 / -1;
}

.chart-card--empty {
  height: auto;
  min-height: 0;
}

.chart-card--empty .chart-card-header {
  margin-bottom: 0;
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
  background: linear-gradient(90deg, var(--species-accent, rgba(47, 125, 79, 0.58)), rgba(167, 227, 178, 0));
  opacity: 0.6;
  transition: background 0.4s ease;
}

@media (max-width: 900px) {
  .species-page {
    padding: 20px 18px 30px;
  }

  .species-title-row h1 {
    font-size: 1.7rem;
  }

  .chart-card,
  .chart-card--wide,
  .chart-card--full {
    grid-column: 1 / -1;
  }
}

@media (max-width: 560px) {
  .kpi-row {
    grid-template-columns: 1fr;
  }

  .species-filter-bar {
    padding: 14px;
  }

  .species-filter-group {
    padding: 12px;
  }
}
</style>
