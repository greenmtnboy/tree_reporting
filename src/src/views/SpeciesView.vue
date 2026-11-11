<template>
  <div class="dash-page species-page">
    <header class="dash-header">
      <div class="dash-title-row">
        <div>
          <h1>{{ pageTitle }}</h1>
          <span class="dash-rule dash-rule--title" aria-hidden="true"></span>
        </div>
        <div class="species-pickers">
          <label class="species-picker">
            <span class="dash-pill-label">Genus</span>
            <SpeciesSearchFilter
              v-model="genusFilter"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :connection-ready="ready"
              :imports="SPECIES_DASHBOARD_IMPORTS as DashboardImport[]"
              :base-filters="genusSelectorFilters"
              :top-query="TOP_GENUS_QUERY"
              :full-query="GENUS_OPTIONS_QUERY"
              :include-all-option="true"
              all-option-label="All genera"
              placeholder="Search genera"
              loading-message="Loading genera..."
              empty-message="No genera match this view."
              @options-loaded="handleGenusOptionsLoaded"
            />
          </label>
          <label class="species-picker">
            <span class="dash-pill-label">Species</span>
            <SpeciesSearchFilter
              v-model="speciesFilter"
              :connection-id="connectionId"
              :query-execution-service="queryExecutionService"
              :connection-ready="ready"
              :imports="SPECIES_DASHBOARD_IMPORTS as DashboardImport[]"
              :base-filters="speciesSelectorBaseFilters"
              :top-query="TOP_SPECIES_QUERY"
              :full-query="SPECIES_OPTIONS_QUERY"
              :auto-select-top="false"
              :include-all-option="true"
              all-option-label="All species"
              :disabled="!genusFilter"
              placeholder="All species in the genus"
              disabled-placeholder="Select a genus first"
              loading-message="Loading species..."
              empty-message="No species match this genus."
              @options-loaded="handleSpeciesOptionsLoaded"
            />
          </label>
        </div>
      </div>

      <div v-if="activeFilters.length" class="dash-filters">
        <span v-for="filter in activeFilters" :key="filter.key" class="dash-chip">
          <span>{{ filter.label }}</span>
          <button class="dash-chip-clear" :title="`Remove ${filter.label}`" @click="clearFilter(filter.key)">x</button>
        </span>
        <button class="dash-link-btn" @click="clearAllFilters">Clear all</button>
      </div>
    </header>

    <div v-if="gateMessage" class="dash-card dash-state">{{ gateMessage }}</div>

    <template v-else>
      <!-- One species: its field-guide entry. A genus: its species, ranked. -->
      <div v-if="speciesFilter" class="dash-grid">
        <div class="dash-card dash-span-hero">
          <SpeciesProfile v-if="profileRow" :row="profileRow" :count-line="profileCountLine">
            <template #actions>
              <button class="dash-link-btn" @click="speciesFilter = null">Back to all {{ genusFilter }} species</button>
            </template>
          </SpeciesProfile>
          <div v-else class="dash-state">
            {{ speciesList.loading.value ? 'Loading species…' : 'No trees of this species in this view.' }}
          </div>
        </div>
        <div class="dash-span-side species-side">
          <div class="dash-stats">
            <div v-for="tile in statTiles" :key="tile.id" class="dash-stat" :title="tile.title">
              <span class="dash-stat-label">{{ tile.label }}</span>
              <span class="dash-stat-value" :class="{ 'dash-stat-value--pending': tile.pending }">{{ tile.value }}</span>
              <span class="dash-stat-note">{{ tile.note }}</span>
            </div>
          </div>
        </div>
      </div>

      <template v-else>
        <div class="dash-stats">
          <div v-for="tile in statTiles" :key="tile.id" class="dash-stat" :title="tile.title">
            <span class="dash-stat-label">{{ tile.label }}</span>
            <span class="dash-stat-value" :class="{ 'dash-stat-value--pending': tile.pending }">{{ tile.value }}</span>
            <span class="dash-stat-note">{{ tile.note }}</span>
          </div>
        </div>

        <div class="dash-grid">
          <div class="dash-card dash-span-l species-list-card">
            <div class="dash-card-header">
              <h3>{{ genusFilter }} species</h3>
              <span class="dash-card-sub">By share of the genus. Pick one to read about it.</span>
            </div>
            <SpeciesRankList
              :rows="speciesList.rows.value"
              :loading="speciesList.loading.value"
              :total-trees="totalTrees"
              :canopy-by-species="canopyBySpecies"
              :total-canopy="canopyOfListed"
              :selected="highlighted"
              @select="highlighted = $event"
            />
          </div>
          <div class="dash-card dash-span-l species-list-card species-profile-card">
            <SpeciesProfile v-if="profileRow" :row="profileRow" :count-line="profileCountLine" compact>
              <template #actions>
                <button class="dash-link-btn" @click="speciesFilter = highlighted">Focus the page on this species</button>
              </template>
            </SpeciesProfile>
            <div v-else class="dash-state">
              {{ speciesList.loading.value ? 'Loading species…' : 'No identified species in this genus here.' }}
            </div>
          </div>
        </div>
      </template>

      <!-- Where it grows, with the two size breakdowns beside the map. -->
      <div class="dash-grid">
        <div class="dash-card dash-card--flush dash-span-hero species-map-card">
          <div class="dash-card-header species-map-header">
            <h3>{{ mapTitle }}</h3>
          </div>
          <TreeDotMap
            item-id="sp-city-dot-map"
            v-bind="sharedProps"
            :filters="filtersForChart('sp-city-dot-map')"
            :ghost-filters="cityFilter ? cityBaseFilters : null"
            :priority="100"
            :mark-peak="Boolean(cityFilter)"
            :pickable="Boolean(cityFilter)"
            @pick="openMapAt"
          />
        </div>
        <div class="dash-span-side species-side">
          <div v-for="id in SIZE_CARD_IDS" :key="id" class="dash-card">
            <div class="dash-card-header">
              <h3>{{ chartById(id).title }}</h3>
              <span v-if="chartById(id).subtitle" class="dash-card-sub">{{ chartById(id).subtitle }}</span>
            </div>
            <BucketBars
              :item-id="id"
              v-bind="sharedProps"
              :filters="filtersForChart(id)"
              :query="chartById(id).query"
              :label-field="chartById(id).buckets!.labelField"
              :order="chartById(id).buckets!.order"
              :colors="chartById(id).buckets!.colors"
              :unknown="chartById(id).buckets!.unknown"
              :denominator="totalTrees"
              denominator-label="these trees"
              empty-message="Not recorded for these trees."
            />
          </div>
        </div>
      </div>

      <section v-for="section in visibleSections" :key="section.id" class="dash-section">
        <div class="dash-section-header">
          <h2>{{ section.title }}</h2>
          <p>{{ section.subtitle }}</p>
        </div>

        <div class="dash-grid">
          <div
            v-for="card in section.cards"
            v-show="!isHidden(card.id)"
            :key="card.id"
            :class="[
              'dash-card',
              `dash-span-${card.span}`,
              chartById(card.id).kind === 'chart' ? (card.id === 'sp-city-presence-map' ? 'dash-card--chart-tall' : 'dash-card--chart') : null,
            ]"
          >
            <div class="dash-card-header">
              <h3>{{ chartById(card.id).title }}</h3>
              <span v-if="chartById(card.id).subtitle" class="dash-card-sub">{{ chartById(card.id).subtitle }}</span>
            </div>

            <BucketBars
              v-if="chartById(card.id).kind === 'buckets'"
              :item-id="card.id"
              v-bind="sharedProps"
              :filters="filtersForChart(card.id)"
              :query="chartById(card.id).query"
              :label-field="chartById(card.id).buckets!.labelField"
              :order="chartById(card.id).buckets!.order"
              :labels="chartById(card.id).buckets!.labels"
              :colors="chartById(card.id).buckets!.colors"
              :unknown="chartById(card.id).buckets!.unknown"
              :denominator="chartById(card.id).buckets!.hideWhenThin ? totalTrees : undefined"
              denominator-label="these trees"
              :min-buckets="chartById(card.id).buckets!.hideWhenThin ? 2 : 1"
              :clickable="Boolean(chartById(card.id).buckets!.filterField)"
              :selected="selectedBuckets(card.id)"
              @select="toggleBucket(card.id, $event)"
              @coverage="coverage[card.id] = $event"
            />
            <template v-else>
              <div v-if="emptyChartIds.has(card.id)" class="dash-state">Nothing recorded for these trees.</div>
              <EmbeddedDashboardChart
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

        <p v-if="hiddenTitles(section).length" class="dash-footnote">
          Not shown, because too few species have the data yet: {{ hiddenTitles(section).join(', ') }}.
        </p>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  useEmbeddedDashboardGroup,
  type DashboardImport,
  type DimensionClick,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import '../assets/dashboard.css'
import BucketBars from '../components/BucketBars.vue'
import EmbeddedDashboardChart from '../components/EmbeddedDashboardChart.vue'
import SpeciesProfile from '../components/SpeciesProfile.vue'
import SpeciesRankList from '../components/SpeciesRankList.vue'
import SpeciesSearchFilter from '../components/SpeciesSearchFilter.vue'
import TreeDotMap from '../components/TreeDotMap.vue'
import { buildDashboardContextParameters } from '../composables/dashboardContextSource'
import { rowNumber, rowText, useDashboardRows } from '../composables/useDashboardRows'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData, type CityCode } from '../composables/useMapData'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import { LOW_COVERAGE_THRESHOLD } from '../composables/summaryDashboardConfig'
import {
  GENUS_OPTIONS_QUERY,
  SPECIES_OPTIONS_QUERY,
  SPECIES_RANKING_QUERY,
  TOP_GENUS_QUERY,
  TOP_SPECIES_QUERY,
  SPECIES_CHARTS_BY_ID,
  SPECIES_DASHBOARD_IMPORTS,
  SPECIES_KPI_CHARTS,
  SPECIES_SECTIONS,
  extractGenusFromSpecies,
  getGenusSqlFilter,
  getSpeciesViewBaseFilters,
  parseScientificName,
  readSpeciesRouteCity,
  type SpeciesDashboardSection,
} from '../composables/speciesDashboardConfig'
import { filterSpeciesDimensionClick, useSpeciesFilters } from '../composables/useSpeciesFilters'
import { useMapLifecycle } from '../composables/useMapLifecycle'
import { formatArea, formatCompact, formatCount, formatShare, speciesDisplayName } from '../lib/dashboardFormat'
import cityConfig from '../cityConfig.json'

type FilterOption = {
  label: string
  value: string
  count: number | null
}

// Drawn beside the map rather than in a section below it.
const SIZE_CARD_IDS = ['sp-size-distribution', 'sp-crown-size'] as const

const route = useRoute()
const router = useRouter()
let syncingRoute = false
const { selectedCity } = useMapData()
const { activateCity } = useMapLifecycle()
const { flyTo } = useFlyTo()
const { initialize, connectionId, queryExecutionService, setDashboardContext, ready } = useSummaryDashboardExecution()

const embeddedDashboardGroup = useEmbeddedDashboardGroup({
  dashboardId: `species-${connectionId}`,
  connectionId,
  queryExecutionService,
  imports: SPECIES_DASHBOARD_IMPORTS as DashboardImport[],
  // One resolver round trip for the whole page; see SummaryView.
  maxConcurrentQueries: 64,
})

const cityFilter = ref<CityCode | null>(null)
const genusFilter = ref<string | null>(null)
const speciesFilter = ref<string | null>(null)
const genusOptions = ref<FilterOption[]>([])
const speciesOptions = ref<FilterOption[]>([])
const emptyChartIds = reactive(new Set<string>())
const coverage = reactive<Record<string, number | null>>({})
const dashboardContextParameters = computed(() => buildDashboardContextParameters(cityFilter.value))

/** What a card the page draws itself takes; see useDashboardRows. */
const sharedProps = computed(() => ({
  connectionId,
  dashboardGroup: embeddedDashboardGroup,
  imports: SPECIES_DASHBOARD_IMPORTS as DashboardImport[],
  parameters: dashboardContextParameters.value,
}))

/** What an embedded Trilogy chart takes. */
const sharedChartProps = computed(() => ({
  connectionId,
  queryExecutionService,
  imports: SPECIES_DASHBOARD_IMPORTS as DashboardImport[],
  parameters: dashboardContextParameters.value,
}))

const { crossFilters, activeSpeciesFilters, speciesFilterMeta } = useSpeciesFilters()

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
  if (!cityFilter.value) return 'all cities'
  const cfg = (cityConfig as Record<string, { name: string }>)[cityFilter.value]
  return cfg?.name ?? cityFilter.value
})

const gateMessage = computed(() => {
  if (!ready.value) {
    return 'Loading the species explorer…'
  }
  if (genusFilter.value) {
    return null
  }
  return cityFilter.value
    ? 'Finding the most common genus in this city…'
    : 'Finding the most common genus across all cities…'
})

function handleGenusOptionsLoaded(options: FilterOption[]) {
  genusOptions.value = options
}

function handleSpeciesOptionsLoaded(options: FilterOption[]) {
  speciesOptions.value = options
}

function chartById(chartId: string) {
  return SPECIES_CHARTS_BY_ID[chartId]
}

function filtersForChart(chartId: string) {
  void crossFilters.version.value
  return crossFilters.getSqlFilterLikesFor(chartId, baseFilters.value)
}

// --- The species in view ---------------------------------------------------------

const speciesList = useDashboardRows(
  () => ({
    ...sharedProps.value,
    itemId: 'sp-spotlight',
    query: chartById('sp-spotlight').query,
    filters: filtersForChart('sp-spotlight'),
  }),
  { title: 'Species in view', enabled: () => Boolean(genusFilter.value) },
)

const canopyList = useDashboardRows(
  () => ({
    ...sharedProps.value,
    itemId: 'sp-canopy-by-species',
    query: chartById('sp-canopy-by-species').query,
    filters: filtersForChart('sp-canopy-by-species'),
  }),
  { title: 'Crown area by species', enabled: () => Boolean(genusFilter.value) },
)

// Every identified species in the city, under the city filter alone: what a
// selected species is ranked against.
const ranking = useDashboardRows(
  () => ({
    ...sharedProps.value,
    itemId: 'sp-city-ranking',
    query: SPECIES_RANKING_QUERY,
    filters: cityBaseFilters.value,
  }),
  { title: 'Species ranking', enabled: () => Boolean(speciesFilter.value) },
)

const canopyBySpecies = computed(() => {
  const map = new Map<string, number>()
  for (const row of canopyList.rows.value) {
    const species = rowText(row.species)
    const area = rowNumber(row.canopy_area)
    if (species && area) map.set(species, area)
  }
  return map
})

const canopyOfListed = computed(() => {
  let total = 0
  for (const area of canopyBySpecies.value.values()) total += area
  return total || null
})

// The species the genus view's profile shows; the page is not filtered by it.
const highlighted = ref<string | null>(null)

watch(
  () => speciesList.rows.value,
  (rows) => {
    const names = rows.map((row) => rowText(row.species))
    if (!highlighted.value || !names.includes(highlighted.value)) {
      highlighted.value = names[0] ?? null
    }
  },
  { immediate: true },
)

const profileSpecies = computed(() => speciesFilter.value ?? highlighted.value)
const profileRow = computed(
  () => speciesList.rows.value.find((row) => rowText(row.species) === profileSpecies.value) ?? null,
)

// --- Numbers -----------------------------------------------------------------------

function kpiRows(id: string) {
  const kpi = SPECIES_KPI_CHARTS.find((entry) => entry.id === id)!
  return useDashboardRows(
    () => ({ ...sharedProps.value, itemId: id, query: kpi.query, filters: filtersForChart(id) }),
    { title: kpi.title, enabled: () => Boolean(genusFilter.value) },
  )
}

const totalTreesRows = kpiRows('sp-total-trees')
const cityCountRows = kpiRows('sp-city-count')
const canopyRows = kpiRows('sp-canopy-total')

const totalTrees = computed(() => rowNumber(totalTreesRows.rows.value[0]?.total_trees))
const cityCount = computed(() => rowNumber(cityCountRows.rows.value[0]?.city_count))
const canopyArea = computed(() => rowNumber(canopyRows.rows.value[0]?.canopy_area))
const sizedTrees = computed(() => rowNumber(canopyRows.rows.value[0]?.sized_trees))

const rankInfo = computed(() => {
  const rows = ranking.rows.value
  if (!speciesFilter.value || !rows.length) return null
  let total = 0
  let rank = 0
  let count = 0
  rows.forEach((row, index) => {
    const n = rowNumber(row.tree_count) ?? 0
    total += n
    if (rowText(row.species) === speciesFilter.value) {
      rank = index + 1
      count = n
    }
  })
  if (!rank) return null
  return { rank, of: rows.length, share: total ? count / total : null }
})

const genusTaxa = computed(() => {
  let hybrids = 0
  let cultivars = 0
  for (const option of speciesOptions.value) {
    const type = parseScientificName(option.value).taxonType
    if (type === 'hybrid') hybrids += 1
    else if (type === 'varietal') cultivars += 1
  }
  return { total: speciesOptions.value.length, hybrids, cultivars }
})

const profileCountLine = computed(() => {
  const row = profileRow.value
  if (!row) return null
  const count = rowNumber(row.tree_count) ?? 0
  const where = cityFilter.value ? `in ${cityName.value}` : 'across all cities'
  const share = !speciesFilter.value && totalTrees.value ? ` · ${formatShare(count / totalTrees.value)} of the genus` : ''
  return `${formatCount(count)} trees ${where}${share}`
})

const statTiles = computed(() => {
  const scope = cityFilter.value ? cityName.value : 'all cities'
  const tiles = [
    {
      id: 'sp-total-trees',
      label: 'Trees',
      value: formatCompact(totalTrees.value),
      note: totalTrees.value != null ? `${formatCount(totalTrees.value)} in ${scope}` : '',
      title: 'Trees of this taxon in the view',
      pending: totalTrees.value == null,
    },
  ]
  if (speciesFilter.value) {
    const info = rankInfo.value
    tiles.push(
      {
        id: 'sp-rank',
        label: 'Rank',
        value: info ? `#${formatCount(info.rank)}` : '–',
        note: info ? `of ${formatCount(info.of)} species in ${scope}` : '',
        title: 'Where this species stands among the identified species here, by tree count',
        pending: !info,
      },
      {
        id: 'sp-share',
        label: 'Share',
        value: formatShare(info?.share),
        note: 'of identified trees',
        title: 'This species as a share of the trees identified to species here',
        pending: !info,
      },
    )
  } else {
    const taxa = genusTaxa.value
    const kinds = [
      taxa.hybrids ? `${taxa.hybrids} hybrid${taxa.hybrids === 1 ? '' : 's'}` : null,
      taxa.cultivars ? `${taxa.cultivars} cultivar${taxa.cultivars === 1 ? '' : 's'} or varieties` : null,
    ].filter(Boolean)
    tiles.push({
      id: 'sp-taxa',
      label: 'Taxa',
      value: taxa.total ? formatCount(taxa.total) : '–',
      note: kinds.length ? `including ${kinds.join(', ')}` : 'recorded in this genus',
      title: 'Distinct species, hybrids, cultivars and genus-only records in this genus here',
      pending: !taxa.total,
    })
  }
  if (!cityFilter.value) {
    tiles.push({
      id: 'sp-city-count',
      label: 'Cities',
      value: formatCount(cityCount.value),
      note: 'hold at least one',
      title: 'Cities whose inventory holds this taxon',
      pending: cityCount.value == null,
    })
  }
  tiles.push({
    id: 'sp-canopy-total',
    label: 'Crown area',
    value: formatArea(canopyArea.value),
    note: sizedTrees.value != null ? `summed over ${formatCompact(sizedTrees.value)} sized trees` : '',
    title: 'Each tree’s crown as a circle, summed. Crowns overlap, so this is more than the ground they shade.',
    pending: canopyArea.value == null,
  })
  return tiles
})

const pageTitle = computed(() => {
  if (speciesFilter.value) {
    return speciesDisplayName(speciesFilter.value, profileRow.value?.common_name)
  }
  return genusFilter.value ? `${genusFilter.value} in ${cityFilter.value ? cityName.value : 'all cities'}` : 'Species Explorer'
})

const mapTitle = computed(() => {
  const subject = speciesFilter.value ? 'it grows' : `${genusFilter.value ?? 'they'} grow${genusFilter.value ? 's' : ''}`
  return `Where ${subject}`
})

// --- Cards -------------------------------------------------------------------------

const visibleSections = computed(() =>
  SPECIES_SECTIONS.map((section) => ({
    ...section,
    cards: section.cards.filter((card) => {
      const chart = chartById(card.id)
      if (chart.requiresCitySelection && !cityFilter.value) return false
      if (chart.requiresAllCities && cityFilter.value) return false
      if (chart.genusOnly && speciesFilter.value) return false
      return true
    }),
  })).filter((section) => section.cards.length > 0),
)

const fieldByChartId = new Map(speciesFilterMeta.map((meta) => [meta.id, meta.field] as const))

function selectedBuckets(chartId: string): string[] {
  void crossFilters.version.value
  const field = fieldByChartId.get(chartId)
  if (!field) return []
  const values: string[] = []
  for (const selection of crossFilters.getSelections()) {
    const entry = selection.filters[field]
    if (selection.source === chartId && entry?.op === 'eq' && typeof entry.value === 'string') values.push(entry.value)
  }
  return values
}

function toggleBucket(chartId: string, bucket: string) {
  const field = fieldByChartId.get(chartId)
  if (!field) return
  const wasSelected = selectedBuckets(chartId).includes(bucket)
  crossFilters.clearSource(chartId)
  if (!wasSelected) {
    crossFilters.applyDimensionClick({ source: chartId, filters: { [field]: { op: 'eq' as const, value: bucket } } }, 'add')
  }
}

/** See SummaryView: a trait card with too little data is hidden, not removed. */
function isHidden(chartId: string): boolean {
  const buckets = chartById(chartId).buckets
  if (!buckets?.hideWhenThin) return false
  if (selectedBuckets(chartId).length) return false
  const known = coverage[chartId]
  return known != null && known < LOW_COVERAGE_THRESHOLD
}

function hiddenTitles(section: SpeciesDashboardSection): string[] {
  return section.cards.filter((card) => isHidden(card.id)).map((card) => chartById(card.id).title.toLowerCase())
}

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

/** A click on the dot map opens the full map at that spot. */
async function openMapAt(location: { lat: number; lng: number }) {
  await router.push({ path: '/', query: { ...(cityFilter.value ? { city: cityFilter.value } : {}), mode: 'explore' } })
  flyTo({ lat: location.lat, lng: location.lng, zoom: 16.5 })
}

function readRouteText(value: unknown): string | null {
  const normalized = Array.isArray(value) ? value[0] : value
  return typeof normalized === 'string' && normalized ? normalized : null
}

const initialRouteCity = readSpeciesRouteCity(route.query.city)
if (initialRouteCity) {
  cityFilter.value = initialRouteCity
  if (initialRouteCity !== selectedCity.value) {
    activateCity(initialRouteCity)
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
    if (syncingRoute || route.name !== 'species') return
    const nextCity = readSpeciesRouteCity(routeCity)
    cityFilter.value = nextCity
    if (nextCity && nextCity !== selectedCity.value) {
      activateCity(nextCity)
    }
  },
)

watch(
  () => route.query.genus,
  (routeGenus) => {
    if (syncingRoute || route.name !== 'species') return
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
    if (syncingRoute || route.name !== 'species') return
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
  (city, previousCity) => {
    emptyChartIds.clear()
    for (const key of Object.keys(coverage)) delete coverage[key]
    crossFilters.clearAll()
    // A different city has different species: start again from its top genus.
    // Not on the first run, which would drop a genus the URL asked for.
    if (previousCity !== undefined) {
      genusFilter.value = null
      speciesFilter.value = null
    }
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
    for (const key of Object.keys(coverage)) delete coverage[key]
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
.species-pickers {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 12px;
}

.species-picker {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 250px;
  max-width: 100%;
}

.species-side {
  display: flex;
  flex-direction: column;
  gap: var(--dash-gap);
  min-width: 0;
}

.species-side .dash-stats {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.species-side .dash-card {
  flex: 1 1 auto;
}

.species-list-card {
  height: 440px;
}

.species-profile-card {
  overflow-y: auto;
  scrollbar-width: thin;
}

.species-map-card {
  min-height: 420px;
}

.species-map-header {
  position: absolute;
  top: 14px;
  left: 18px;
  margin: 0;
  pointer-events: none;
}

@container dash (max-width: 720px) {
  .species-map-card {
    min-height: 0;
    height: 340px;
  }

  .species-list-card {
    height: auto;
    max-height: 520px;
  }
}
</style>
