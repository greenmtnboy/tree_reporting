<template>
  <div class="species-carousel">
    <div v-if="loading" class="carousel-state">Loading species...</div>
    <div v-else-if="error" class="carousel-state carousel-state--error">{{ error }}</div>
    <div v-else-if="!species.length" class="carousel-state">No matching species in the current filter set.</div>

    <template v-else>
      <div class="carousel-nav">
        <button
          class="carousel-arrow"
          :disabled="currentIndex <= 0"
          @click="prev"
        >&lsaquo;</button>
        <span class="carousel-counter">{{ currentIndex + 1 }} of {{ species.length }}{{ species.length >= 100 ? ' (top 100)' : '' }}</span>
        <button
          class="carousel-arrow"
          :disabled="currentIndex >= species.length - 1"
          @click="next"
        >&rsaquo;</button>
      </div>

      <div class="carousel-body">
        <div class="carousel-photo-pane">
          <img
            v-if="current.photo_url"
            :src="String(current.photo_url)"
            :alt="String(current.species || 'species photo')"
            class="carousel-photo"
            loading="lazy"
          />
          <div v-else class="carousel-photo-placeholder">No photo available</div>
          <div v-if="current.photo_attribution" class="carousel-photo-attr">
            {{ current.photo_attribution }}
          </div>
        </div>

        <div class="carousel-detail-pane">
          <div class="carousel-intro">
            <span class="carousel-common-name">{{ displayTitle }}</span>
            <span v-if="current.common_name" class="carousel-sci-name">{{ current.species }}</span>
          </div>
          <div class="carousel-count">
            {{ Number(current.tree_count).toLocaleString() }} trees in the current view
          </div>
          <p v-if="current.description" class="carousel-description">{{ current.description }}</p>
          <ul class="carousel-facts">
            <li v-for="fact in currentFacts" :key="fact[0]">
              <strong>{{ fact[0] }}:</strong> {{ fact[1] }}
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, toValue, watch } from 'vue'
import type { DashboardImport, DashboardExecutionService, SqlFilterLike } from '@trilogy-data/trilogy-studio-components/dashboard'

type SpeciesRow = Record<string, unknown>

const props = withDefaults(
  defineProps<{
    connectionId: string
    queryExecutionService: DashboardExecutionService
    imports?: DashboardImport[]
    query: string
    filters?: SqlFilterLike[] | string[]
    parameters?: Record<string, unknown>
    itemId: string
  }>(),
  {
    imports: () => [],
    filters: () => [],
    parameters: () => ({}),
  },
)

const loading = ref(false)
const error = ref<string | null>(null)
const species = ref<SpeciesRow[]>([])
const currentIndex = ref(0)
const latestLoadId = ref(0)
const activeCancellation = ref<{ cancel: () => void } | null>(null)

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const current = computed(() => species.value[currentIndex.value] ?? ({} as SpeciesRow))
const displayTitle = computed(() => {
  const commonName = formatCommonName(current.value.common_name)
  if (!commonName) return current.value.species
  const cultivar = formatCommonName(extractCultivar(current.value.species))
  return cultivar ? `${commonName} (${cultivar})` : commonName
})

function prev() {
  if (currentIndex.value > 0) currentIndex.value--
}

function next() {
  if (currentIndex.value < species.value.length - 1) currentIndex.value++
}

function formatCommonName(value: unknown) {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  if (!normalized) return null
  return normalized
    .toLowerCase()
    .replace(/(^|[\s\-\/(])([a-z])/g, (_match, prefix: string, letter: string) => `${prefix}${letter.toUpperCase()}`)
}

function extractCultivar(value: unknown) {
  if (typeof value !== 'string') return null
  const match = value.match(/'([^']+)'/)
  return match?.[1]?.trim() || null
}

function formatTitleCase(value: unknown) {
  if (typeof value !== 'string' || !value.trim()) return null
  return value.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())
}

function formatRange(min: unknown, max: unknown, unit: string) {
  const a = typeof min === 'number' ? min : null
  const b = typeof max === 'number' ? max : null
  if (a == null && b == null) return null
  if (a != null && b != null) return a === b ? `${a} ${unit}` : `${a}-${b} ${unit}`
  return `${a ?? b} ${unit}`
}

function formatBloomMonths(value: unknown) {
  if (!Array.isArray(value) || !value.length) return null
  const months = [...new Set(
    value.filter((m): m is number => typeof m === 'number' && m >= 1 && m <= 12).sort((a, b) => a - b),
  )]
  if (!months.length) return null
  if (months.length === 12) return 'Year-round'
  const contiguous = months.every((m, i) => i === 0 || m === months[i - 1] + 1)
  if (contiguous) {
    const s = MONTH_LABELS[months[0] - 1]
    const e = MONTH_LABELS[months[months.length - 1] - 1]
    return s === e ? s : `${s}-${e}`
  }
  return months.map((m) => MONTH_LABELS[m - 1]).join(', ')
}

function parseScientificName(value: unknown) {
  if (typeof value !== 'string' || !value.trim()) return { genus: null, specificEpithet: null, taxonType: null as string | null }
  const tokens = value.trim().split(/\s+/).filter(Boolean)
  const genus = tokens[0] ?? null
  const hasHybrid = tokens.some((t) => t === 'x' || t === '\u00d7')
  const hasVarietal = tokens.some((t) => /^(var\.?|subsp\.?|ssp\.?|f\.?|cv\.?)$/i.test(t)) || value.includes("'")
  const epithetIdx = tokens[1] === 'x' || tokens[1] === '\u00d7' ? 2 : 1
  const specificEpithet = tokens[epithetIdx] ?? null
  const taxonType = hasHybrid ? 'Hybrid' : hasVarietal ? 'Varietal / cultivar' : specificEpithet ? 'Species' : null
  return { genus, specificEpithet, taxonType }
}

const currentFacts = computed(() => {
  const r = current.value
  const tax = parseScientificName(r.species)
  return [
    ['Genus', tax.genus],
    ['Specific epithet', tax.specificEpithet],
    ['Taxon type', tax.taxonType],
    ['Form', formatTitleCase(r.tree_form)],
    ['Mature height', formatRange(r.mature_height_min_ft, r.mature_height_max_ft, 'ft')],
    ['Canopy spread', formatRange(r.canopy_spread_min_ft, r.canopy_spread_max_ft, 'ft')],
    ['Growth rate', formatTitleCase(r.growth_rate)],
    ['Bloom period', formatBloomMonths(r.bloom_months)],
    ['Wildlife value', formatTitleCase(r.wildlife_value)],
    ['Drought tolerance', formatTitleCase(r.drought_tolerance)],
    ['Water needs', formatTitleCase(r.water_needs)],
  ].filter(([, v]) => Boolean(v)) as [string, string][]
})

function buildParameters() {
  const p = toValue(props.parameters as Record<string, unknown> | undefined)
  return p && typeof p === 'object' ? { ...p } : {}
}

async function load() {
  const loadId = ++latestLoadId.value
  activeCancellation.value?.cancel()
  activeCancellation.value = null

  if (!props.query.trim()) {
    species.value = []
    error.value = null
    loading.value = false
    return
  }

  loading.value = true
  error.value = null
  try {
    const resolvedFilters = (props.filters || []).map((f) => (typeof f === 'string' ? { value: f } : f))
    const filterStrings = resolvedFilters.map((f) => f.value)
    const filterParams = Object.assign({}, ...resolvedFilters.map((f) => (f as SqlFilterLike).parameters || {}))
    const execution = await props.queryExecutionService.executeQueriesBatch(
      props.connectionId,
      [{
        label: props.itemId,
        query: props.query,
        extra_filters: filterStrings,
        parameters: { ...buildParameters(), ...filterParams },
      }],
      'trilogy',
      props.imports.map((imp) => ({ name: imp.name, alias: imp.alias })),
    )
    activeCancellation.value = execution.cancellation
    const batch = await execution.resultPromise
    if (loadId !== latestLoadId.value) return
    const result = batch.results[0]
    if (!result?.success || !result.results) {
      throw new Error(result?.error || 'Failed to load species carousel.')
    }
    const payload = result.results.toJSON() as { data?: SpeciesRow[] }
    const rows = Array.isArray(payload.data) ? payload.data : []
    species.value = rows
    // Reset to first species when data reloads
    currentIndex.value = 0
  } catch (err) {
    if (loadId !== latestLoadId.value) return
    species.value = []
    error.value = (err as Error).message
  } finally {
    if (loadId === latestLoadId.value) {
      loading.value = false
      activeCancellation.value = null
    }
  }
}

watch(
  () => `${props.connectionId}::${props.query}::${JSON.stringify(props.filters)}::${JSON.stringify(props.imports)}::${JSON.stringify(buildParameters())}`,
  () => { void load() },
  { immediate: true },
)

onBeforeUnmount(() => {
  activeCancellation.value?.cancel()
  activeCancellation.value = null
})
</script>

<style scoped>
.species-carousel {
  flex: 1 1 auto;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.carousel-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: rgba(154, 166, 154, 0.82);
  font-size: 0.84rem;
}

.carousel-state--error {
  color: #d48f72;
}

.carousel-nav {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  flex: 0 0 auto;
}

.carousel-arrow {
  width: 32px;
  height: 32px;
  border: 1px solid rgba(167, 227, 178, 0.18);
  border-radius: 8px;
  background: rgba(15, 21, 17, 0.36);
  color: var(--color-foam, #edf2eb);
  font-size: 1.2rem;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.carousel-arrow:hover:not(:disabled) {
  background: rgba(47, 125, 79, 0.18);
  border-color: rgba(167, 227, 178, 0.32);
}

.carousel-arrow:disabled {
  opacity: 0.3;
  cursor: default;
}

.carousel-counter {
  font-size: 0.76rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: rgba(154, 166, 154, 0.82);
}

.carousel-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  gap: 20px;
  overflow: auto;
}

.carousel-photo-pane {
  flex: 0 0 auto;
  width: 220px;
  height: 240px;
  display: flex;
  flex-direction: column;
}

.carousel-photo {
  width: 220px;
  height: 220px;
  flex: 0 0 220px;
  object-fit: cover;
  border-radius: 12px;
  border: 1px solid rgba(167, 227, 178, 0.12);
}

.carousel-photo-placeholder {
  width: 220px;
  height: 220px;
  flex: 0 0 220px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  border: 1px solid rgba(167, 227, 178, 0.08);
  background: rgba(12, 17, 21, 0.3);
  color: rgba(154, 166, 154, 0.5);
  font-size: 0.8rem;
}

.carousel-photo-attr {
  margin-top: 4px;
  font-size: 0.66rem;
  color: rgba(154, 166, 154, 0.6);
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.carousel-detail-pane {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.carousel-intro {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.carousel-common-name {
  font-size: 1.2rem;
  font-weight: 700;
  font-family: var(--font-display);
  color: var(--color-ink, #edf2eb);
}

.carousel-sci-name {
  font-size: 0.88rem;
  font-style: italic;
  color: rgba(237, 242, 235, 0.7);
}

.carousel-count {
  font-size: 0.82rem;
  color: rgba(167, 227, 178, 0.82);
  font-weight: 600;
}

.carousel-description {
  margin: 0;
  font-size: 0.86rem;
  line-height: 1.6;
  color: rgba(237, 242, 235, 0.78);
}

.carousel-facts {
  margin: 4px 0 0;
  padding-left: 1.1rem;
  list-style: disc;
}

.carousel-facts li {
  margin-bottom: 3px;
  font-size: 0.84rem;
  color: rgba(237, 242, 235, 0.82);
  line-height: 1.5;
}

.carousel-facts li::marker {
  color: rgba(167, 227, 178, 0.68);
}

.carousel-facts strong {
  color: var(--color-ink, #edf2eb);
  font-weight: 700;
}

@media (max-width: 640px) {
  .carousel-body {
    flex-direction: column;
  }

  .carousel-photo-pane {
    width: 160px;
    height: 180px;
    align-self: center;
  }

  .carousel-photo,
  .carousel-photo-placeholder {
    width: 160px;
    height: 160px;
    flex: 0 0 160px;
  }
}
</style>
