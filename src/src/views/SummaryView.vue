<template>
  <div class="dash-page summary-page">
    <header class="dash-header">
      <div class="dash-title-row">
        <div>
          <h1>{{ summaryTitle }}</h1>
          <span class="dash-rule dash-rule--title" aria-hidden="true"></span>
        </div>
        <div v-if="cityContextPills.length" class="dash-pills">
          <span v-for="pill in cityContextPills" :key="pill.label" class="dash-pill">
            <span class="dash-pill-label">{{ pill.label }}</span>
            <span class="dash-pill-value">{{ pill.value }}</span>
          </span>
        </div>
      </div>
      <p v-if="lede" class="dash-lede">
        <strong>{{ lede.total }}</strong> trees mapped<template v-if="lede.identified">,
        <strong>{{ lede.identified }}</strong> of them identified to species ({{ lede.identifiedShare }})</template>.
        <template v-if="lede.mostlyUnidentified">
          The rest were mapped without a species, most of them from aerial imagery. Species figures on this page count
          identified trees only.
        </template>
      </p>
      <div v-if="activeFilters.length" class="dash-filters">
        <span v-for="filter in activeFilters" :key="filter.key" class="dash-chip">
          <span>{{ filter.label }}</span>
          <button class="dash-chip-clear" :title="`Remove ${filter.label}`" @click="clearFilter(filter.key)">x</button>
        </span>
        <button class="dash-link-btn" @click="clearAllFilters">Clear all</button>
      </div>
    </header>

    <!-- The map, with the page's headline numbers beside it. -->
    <div class="dash-grid summary-hero">
      <div class="dash-card dash-card--flush dash-span-hero summary-map-card">
        <div class="dash-card-header summary-map-header">
          <h3>Where the trees are</h3>
        </div>
        <TreeDotMap
          item-id="tree-dot-map"
          v-bind="sharedProps"
          :filters="filtersForChart('tree-dot-map')"
          :priority="100"
          :pickable="Boolean(cityFilter)"
          @pick="openMapAt"
        />
      </div>

      <div class="dash-span-side summary-side">
        <div class="dash-stats">
          <div v-for="tile in statTiles" :key="tile.id" class="dash-stat" :title="tile.title">
            <span class="dash-stat-label">{{ tile.label }}</span>
            <span class="dash-stat-value" :class="{ 'dash-stat-value--pending': tile.pending }">{{ tile.value }}</span>
            <span class="dash-stat-note">{{ tile.note }}</span>
          </div>
        </div>

        <div class="dash-card">
          <div class="dash-card-header">
            <h3>{{ chartById('tree-sources').title }}</h3>
          </div>
          <SourceSplit
            item-id="tree-sources"
            v-bind="sharedProps"
            :filters="filtersForChart('tree-sources')"
            :query="chartById('tree-sources').query"
          />
        </div>

        <div class="dash-card">
          <div class="dash-card-header">
            <h3>Concentration</h3>
            <span class="dash-card-sub">The largest species and genus, as shares of identified trees</span>
          </div>
          <DiversityRule :species="topSpecies" :genus="topGenus" :family="topFamily" />
        </div>
      </div>
    </div>

    <section class="dash-section">
      <div class="dash-section-header">
        <h2>Species</h2>
        <p>What the identified trees are, and how concentrated the mix is.</p>
      </div>
      <div class="dash-grid">
        <div class="dash-card dash-span-l summary-species-card">
          <div class="dash-card-header">
            <h3>{{ chartById('top-tree-spotlight').title }}</h3>
            <span class="dash-card-sub">{{ speciesListSubtitle }}</span>
          </div>
          <SpeciesRankList
            :rows="speciesList.rows.value"
            :loading="speciesList.loading.value"
            :total-trees="identifiedTrees"
            :canopy-by-species="canopyBySpecies"
            :total-canopy="identifiedCanopy"
            :selected="selectedSpecies"
            @select="selectedSpecies = $event"
          />
        </div>

        <div class="dash-card dash-span-l summary-species-card summary-profile-card">
          <SpeciesProfile v-if="selectedRow" :row="selectedRow" :count-line="selectedCountLine" compact>
            <template #actions>
              <router-link class="dash-link-btn" :to="speciesExplorerRoute">Open in the species explorer</router-link>
              <button class="dash-link-btn" @click="filterToSelectedSpecies">
                {{ speciesFilterActive ? 'Stop filtering to this species' : 'Filter this page to it' }}
              </button>
            </template>
          </SpeciesProfile>
          <div v-else class="dash-state">
            {{ speciesList.loading.value ? 'Loading species…' : 'No identified species in this view.' }}
          </div>
        </div>

      </div>
    </section>

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
            chartById(card.id).kind === 'chart' ? 'dash-card--chart' : null,
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
            :denominator="chartById(card.id).buckets!.hideWhenThin ? identifiedTrees : undefined"
            denominator-label="identified trees"
            :min-buckets="chartById(card.id).buckets!.hideWhenThin ? 2 : 1"
            :clickable="Boolean(chartById(card.id).buckets!.filterField)"
            :selected="selectedBuckets(card.id)"
            @select="toggleBucket(card.id, $event)"
            @coverage="coverage[card.id] = $event"
          />
          <template v-else>
            <div v-if="emptyChartIds.has(card.id)" class="dash-state">No planting dates recorded for this view.</div>
            <EmbeddedDashboardChart
              v-show="!emptyChartIds.has(card.id)"
              :item-id="card.id"
              v-bind="sharedChartProps"
              :dashboard-group="embeddedDashboardGroup"
              :filters="filtersForChart(card.id)"
              :title="chartById(card.id).title"
              :query="chartById(card.id).query"
              :chart-config="chartById(card.id).chartConfig"
              :allow-cross-filter="chartById(card.id).allowCrossFilter ?? true"
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useEmbeddedDashboardGroup, type DashboardImport } from '@trilogy-data/trilogy-studio-components/dashboard'
import '../assets/dashboard.css'
import BucketBars from '../components/BucketBars.vue'
import DiversityRule from '../components/DiversityRule.vue'
import EmbeddedDashboardChart from '../components/EmbeddedDashboardChart.vue'
import SourceSplit from '../components/SourceSplit.vue'
import SpeciesProfile from '../components/SpeciesProfile.vue'
import SpeciesRankList from '../components/SpeciesRankList.vue'
import TreeDotMap from '../components/TreeDotMap.vue'
import { useMapData, type CityCode } from '../composables/useMapData'
import {
  buildDashboardContextParameters,
  getCityBiome,
  getCityUsdaZone,
} from '../composables/dashboardContextSource'
import { rowNumber, rowText, useDashboardRows } from '../composables/useDashboardRows'
import { useFlyTo } from '../composables/useFlyTo'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import {
  LOW_COVERAGE_THRESHOLD,
  SUMMARY_CHARTS_BY_ID,
  SUMMARY_DASHBOARD_IMPORTS,
  SUMMARY_KPIS_BY_ID,
  SUMMARY_SECTIONS,
  getSummaryBaseFilters,
  readSummaryRouteCity,
  type SummaryDashboardSection,
} from '../composables/summaryDashboardConfig'
import { extractGenusFromSpecies } from '../composables/speciesDashboardConfig'
import { useSummaryFilters, type SummaryFilterField } from '../composables/useSummaryFilters'
import { useMapLifecycle } from '../composables/useMapLifecycle'
import { formatArea, formatCompact, formatCount, formatShare, isGenusOnly } from '../lib/dashboardFormat'
import cityConfig from '../cityConfig.json'

const route = useRoute()
const router = useRouter()
const { selectedCity, displayCity } = useMapData()
const { activateCity } = useMapLifecycle()
const { flyTo } = useFlyTo()
const { initialize, connectionId, queryExecutionService, setDashboardContext } = useSummaryDashboardExecution()

const embeddedDashboardGroup = useEmbeddedDashboardGroup({
  dashboardId: `summary-${connectionId}`,
  connectionId,
  queryExecutionService,
  imports: SUMMARY_DASHBOARD_IMPORTS as DashboardImport[],
  // One resolver round trip for the whole page. The executor compiles a batch
  // of at most this many items per request, and each request re-hydrates the
  // full model bundle (~300 KB, 2-3 s); the default of ten split the page's
  // charts into serial requests, each of which could not start until every
  // query of the one before had returned.
  maxConcurrentQueries: 64,
})

const cityFilter = ref<CityCode | null>(null)
const emptyChartIds = reactive(new Set<string>())
const coverage = reactive<Record<string, number | null>>({})
const dashboardContextParameters = computed(() => buildDashboardContextParameters(cityFilter.value))

/** What a card the page draws itself takes; see useDashboardRows. */
const sharedProps = computed(() => ({
  connectionId,
  dashboardGroup: embeddedDashboardGroup,
  imports: SUMMARY_DASHBOARD_IMPORTS as DashboardImport[],
  parameters: dashboardContextParameters.value,
}))

/** What an embedded Trilogy chart takes. */
const sharedChartProps = computed(() => ({
  connectionId,
  queryExecutionService,
  imports: SUMMARY_DASHBOARD_IMPORTS as DashboardImport[],
  parameters: dashboardContextParameters.value,
}))

const { crossFilters, activeSummaryFilters, selectionsByField, applyValuesForField, clearFields } = useSummaryFilters()

const cityName = computed(() => {
  if (!cityFilter.value) {
    return 'All Cities'
  }
  const cfg = (cityConfig as Record<string, { name: string }>)[cityFilter.value]
  return cfg?.name ?? cityFilter.value
})

const summaryTitle = computed(() => (cityFilter.value ? `${cityName.value} City Summary` : 'All Cities Summary'))

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

function chartById(chartId: string) {
  return SUMMARY_CHARTS_BY_ID[chartId]
}

function filtersForChart(chartId: string) {
  void crossFilters.version.value
  return crossFilters.getSqlFilterLikesFor(chartId, baseFilters.value)
}

// --- Headline numbers ----------------------------------------------------------

function kpiRows(id: string, enabled: () => boolean = () => true) {
  return useDashboardRows(
    () => ({
      ...sharedProps.value,
      itemId: id,
      query: SUMMARY_KPIS_BY_ID[id].query,
      filters: filtersForChart(id),
    }),
    { title: SUMMARY_KPIS_BY_ID[id].title, enabled },
  )
}

const totalTreesRows = kpiRows('total-trees')
const identifiedRows = kpiRows('identified-trees')
const uniqueSpeciesRows = kpiRows('unique-species')
const biomeFitRows = kpiRows('local-native-share', () => Boolean(cityFilter.value))
const canopyRows = kpiRows('canopy-total')
const topGenusRows = kpiRows('top-genus')
const topFamilyRows = kpiRows('top-family')

const first = (rows: { value: Array<Record<string, unknown>> }, field: string) => rowNumber(rows.value[0]?.[field])

const totalTrees = computed(() => first(totalTreesRows.rows, 'total_trees'))
const identifiedTrees = computed(() => first(identifiedRows.rows, 'identified_trees'))
const uniqueSpecies = computed(() => first(uniqueSpeciesRows.rows, 'unique_species'))
const biomeFit = computed(() => first(biomeFitRows.rows, 'biome_fit_pct'))
const canopyArea = computed(() => first(canopyRows.rows, 'canopy_area'))
const sizedTrees = computed(() => first(canopyRows.rows, 'sized_trees'))

const identifiedShare = computed(() =>
  totalTrees.value && identifiedTrees.value != null ? identifiedTrees.value / totalTrees.value : null,
)

const lede = computed(() => {
  if (totalTrees.value == null) return null
  const share = identifiedShare.value
  return {
    total: formatCount(totalTrees.value),
    identified: share != null && share < 0.995 ? formatCount(identifiedTrees.value) : null,
    identifiedShare: formatShare(share),
    mostlyUnidentified: share != null && share < 0.9,
  }
})

// --- Species ---------------------------------------------------------------------

const speciesList = useDashboardRows(
  () => ({
    ...sharedProps.value,
    itemId: 'top-tree-spotlight',
    query: chartById('top-tree-spotlight').query,
    filters: filtersForChart('top-tree-spotlight'),
  }),
  { title: 'Most common species' },
)

const canopyList = useDashboardRows(
  () => ({
    ...sharedProps.value,
    itemId: 'canopy-by-species',
    query: chartById('canopy-by-species').query,
    filters: filtersForChart('canopy-by-species'),
  }),
  { title: 'Crown area by species' },
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

// The crown area of the identified trees is what a species' share is of. The
// list is the top 500 species by area, which is all but a sliver of it.
const identifiedCanopy = computed(() => {
  let total = 0
  for (const area of canopyBySpecies.value.values()) total += area
  return total || null
})

const selectedSpecies = ref<string | null>(null)

watch(
  () => speciesList.rows.value,
  (rows) => {
    const names = rows.map((row) => rowText(row.species))
    if (!selectedSpecies.value || !names.includes(selectedSpecies.value)) {
      selectedSpecies.value = names[0] ?? null
    }
  },
  { immediate: true },
)

const selectedRow = computed(
  () => speciesList.rows.value.find((row) => rowText(row.species) === selectedSpecies.value) ?? null,
)

const selectedCountLine = computed(() => {
  const row = selectedRow.value
  if (!row) return null
  const count = rowNumber(row.tree_count) ?? 0
  const share = identifiedTrees.value ? count / identifiedTrees.value : null
  const where = cityFilter.value ? `in ${cityName.value}` : 'across all cities'
  return `${formatCount(count)} trees ${where}${share != null ? ` · ${formatShare(share)} of identified trees` : ''}`
})

const speciesListSubtitle = computed(() => {
  const shown = speciesList.rows.value.length
  const unique = uniqueSpecies.value
  const scope = unique != null && unique > shown ? `The top ${shown} of ${formatCount(unique)} species` : 'Every species'
  return `${scope}, by share of identified trees. Pick one to read about it.`
})

const speciesExplorerRoute = computed(() => ({
  path: '/species',
  query: {
    ...(cityFilter.value ? { city: cityFilter.value } : {}),
    ...(selectedSpecies.value
      ? { genus: extractGenusFromSpecies(selectedSpecies.value) ?? undefined, species: selectedSpecies.value }
      : {}),
  },
}))

const speciesFilterActive = computed(() => {
  void crossFilters.version.value
  return Boolean(selectedSpecies.value && (selectionsByField().species ?? []).includes(selectedSpecies.value))
})

function filterToSelectedSpecies() {
  if (!selectedSpecies.value) return
  if (speciesFilterActive.value) {
    clearFields(['species'])
  } else {
    applyValuesForField('species', [selectedSpecies.value])
  }
}

// --- Concentration -----------------------------------------------------------------

const shareOfIdentified = (count: number | null) =>
  count != null && identifiedTrees.value ? count / identifiedTrees.value : null

// The largest species proper: a record identified only to genus ("Fraxinus")
// is several species, and is the genus bar's business.
const topSpecies = computed(() => {
  const row = speciesList.rows.value.find((entry) => !isGenusOnly(rowText(entry.species) ?? ''))
  return { name: rowText(row?.species), share: shareOfIdentified(rowNumber(row?.tree_count)) }
})
const topGenus = computed(() => {
  const row = topGenusRows.rows.value[0]
  return { name: rowText(row?.genus_name), share: shareOfIdentified(rowNumber(row?.tree_count)) }
})
// Null until the enrichment table records families; the card then gains its third bar.
const topFamily = computed(() => {
  const row = topFamilyRows.rows.value[0]
  const name = rowText(row?.family)
  return name ? { name, share: shareOfIdentified(rowNumber(row?.tree_count)) } : null
})

const topFiveShare = computed(() => {
  const rows = speciesList.rows.value.slice(0, 5)
  if (!rows.length) return null
  return shareOfIdentified(rows.reduce((sum, row) => sum + (rowNumber(row.tree_count) ?? 0), 0))
})

// --- Tiles -----------------------------------------------------------------------

const statTiles = computed(() => {
  const tiles = [
    {
      id: 'total-trees',
      label: 'Trees mapped',
      value: formatCompact(totalTrees.value),
      note: totalTrees.value != null ? `${formatCount(totalTrees.value)} in all` : '',
      title: 'Every tree in the view, from every source',
      pending: totalTrees.value == null,
    },
    {
      id: 'identified-trees',
      label: 'Identified to species',
      value: formatShare(identifiedShare.value),
      note: identifiedTrees.value != null ? `${formatCount(identifiedTrees.value)} trees` : '',
      title: 'Trees whose record names a species or genus, whatever their source',
      pending: identifiedShare.value == null,
    },
    {
      id: 'unique-species',
      label: 'Species',
      value: formatCount(uniqueSpecies.value),
      note: 'distinct taxa recorded',
      title: 'Distinct species, hybrids, cultivars and genus-only records',
      pending: uniqueSpecies.value == null,
    },
    {
      id: 'top-5-share',
      label: 'Top five species',
      value: formatShare(topFiveShare.value),
      note: 'of identified trees',
      title: 'The share of identified trees held by the five most common species',
      pending: topFiveShare.value == null,
    },
    {
      id: 'canopy-total',
      label: 'Crown area',
      value: formatArea(canopyArea.value),
      note: sizedTrees.value != null ? `summed over ${formatCompact(sizedTrees.value)} sized trees` : '',
      title: 'Each tree’s crown as a circle, summed. Crowns overlap, so this is more than the ground they shade.',
      pending: canopyArea.value == null,
    },
  ]
  if (cityFilter.value) {
    tiles.splice(4, 0, {
      id: 'local-native-share',
      label: 'Biome fit',
      value: formatShare(biomeFit.value),
      note: 'native here or to this biome',
      title: 'Identified trees whose species is native to the city’s ecoregion or to its biome elsewhere',
      pending: biomeFit.value == null,
    })
  }
  return tiles
})

// --- Cards -----------------------------------------------------------------------

const visibleSections = computed(() =>
  SUMMARY_SECTIONS.map((section) => ({
    ...section,
    cards: section.cards.filter((card) => cityFilter.value || !chartById(card.id).requiresCitySelection),
  })).filter((section) => section.cards.length > 0),
)

function selectedBuckets(chartId: string): string[] {
  void crossFilters.version.value
  const field = chartById(chartId).buckets?.filterField
  return field ? (selectionsByField()[field] ?? []) : []
}

function toggleBucket(chartId: string, bucket: string) {
  const field = chartById(chartId).buckets?.filterField as SummaryFilterField | undefined
  if (!field) return
  const selected = selectedBuckets(chartId)
  applyValuesForField(field, selected.includes(bucket) ? [] : [bucket])
}

/**
 * A trait card is hidden while too few of the identified trees have the
 * trait recorded to say anything about the city. It stays mounted, so it
 * keeps counting and returns when the enrichment table fills in; a card the
 * page is filtered by is never hidden.
 */
function isHidden(chartId: string): boolean {
  const buckets = chartById(chartId).buckets
  if (!buckets?.hideWhenThin) return false
  if (selectedBuckets(chartId).length) return false
  const known = coverage[chartId]
  return known != null && known < LOW_COVERAGE_THRESHOLD
}

function hiddenTitles(section: SummaryDashboardSection): string[] {
  return section.cards.filter((card) => isHidden(card.id)).map((card) => chartById(card.id).title.toLowerCase())
}

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

/** A click on the dot map opens the full map at that spot. */
async function openMapAt(location: { lat: number; lng: number }) {
  await router.push({ path: '/', query: { ...(cityFilter.value ? { city: cityFilter.value } : {}), mode: 'explore' } })
  flyTo({ lat: location.lat, lng: location.lng, zoom: 16.5 })
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
    if (route.name !== 'summary') return
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
    for (const key of Object.keys(coverage)) delete coverage[key]
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
.summary-hero {
  align-items: stretch;
}

.summary-map-card {
  min-height: 440px;
}

.summary-map-header {
  position: absolute;
  top: 14px;
  left: 18px;
  margin: 0;
  pointer-events: none;
}

.summary-side {
  display: flex;
  flex-direction: column;
  gap: var(--dash-gap);
  min-width: 0;
}

.summary-side .dash-stats {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.summary-side .dash-card {
  flex: 1 1 auto;
}

/* The list scrolls inside a fixed box; the profile beside it matches. */
.summary-species-card {
  height: 440px;
}

.summary-profile-card {
  overflow-y: auto;
  scrollbar-width: thin;
}

@container dash (max-width: 720px) {
  .summary-map-card {
    min-height: 0;
    height: 340px;
  }

  .summary-species-card {
    height: auto;
    max-height: 520px;
  }
}
</style>
