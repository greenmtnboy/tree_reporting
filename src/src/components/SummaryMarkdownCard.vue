<template>
  <div class="summary-markdown-card">
    <div v-if="loading" class="summary-markdown-card__state">Loading spotlight...</div>
    <div v-else-if="error" class="summary-markdown-card__state summary-markdown-card__state--error">
      {{ error }}
    </div>
    <div v-else-if="!markdown" class="summary-markdown-card__state">No matching tree data in the current filter set.</div>
    <div v-show="!loading && !error && markdown" class="summary-markdown-card__content">
      <MarkdownRenderer :markdown="markdown" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, toValue, watch, watchEffect } from 'vue'
import { MarkdownRenderer } from '@trilogy-data/trilogy-studio-components/dashboard'
import type { DashboardImport, DashboardExecutionService, SqlFilterLike, EmbeddedDashboardGroup } from '@trilogy-data/trilogy-studio-components/dashboard'

const props = withDefaults(
  defineProps<{
    connectionId: string
    queryExecutionService: DashboardExecutionService
    imports?: DashboardImport[]
    query: string
    filters?: SqlFilterLike[] | string[]
    parameters?: Record<string, unknown>
    itemId: string
    dashboardGroup?: EmbeddedDashboardGroup
  }>(),
  {
    imports: () => [],
    filters: () => [],
    parameters: () => ({}),
    dashboardGroup: undefined,
  },
)

const loading = ref(false)
const error = ref<string | null>(null)
const row = ref<Record<string, unknown> | null>(null)
const latestLoadId = ref(0)
const activeCancellation = ref<{ cancel: () => void } | null>(null)
const spotlightDebugEnabled = typeof window !== 'undefined'
  && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function buildParameters() {
  const parameters = toValue(props.parameters as Record<string, unknown> | undefined)
  return parameters && typeof parameters === 'object' ? { ...parameters } : {}
}

function formatTitleCase(value: unknown) {
  if (typeof value !== 'string' || !value.trim()) return null
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatRange(min: unknown, max: unknown, unit: string) {
  const minValue = typeof min === 'number' ? min : null
  const maxValue = typeof max === 'number' ? max : null
  if (minValue == null && maxValue == null) return null
  if (minValue != null && maxValue != null) {
    return minValue === maxValue ? `${minValue} ${unit}` : `${minValue}-${maxValue} ${unit}`
  }
  return `${minValue ?? maxValue} ${unit}`
}

function formatBloomMonths(value: unknown) {
  if (!Array.isArray(value) || value.length === 0) return null
  const months = Array.from(
    new Set(
      value
        .filter((month): month is number => typeof month === 'number' && month >= 1 && month <= 12)
        .sort((a, b) => a - b),
    ),
  )
  if (!months.length) return null
  if (months.length === 12) return 'Year-round'
  const contiguous = months.every((month, index) => index === 0 || month === months[index - 1] + 1)
  if (contiguous) {
    const start = MONTH_LABELS[months[0] - 1]
    const end = MONTH_LABELS[months[months.length - 1] - 1]
    return start === end ? start : `${start}-${end}`
  }
  return months.map((month) => MONTH_LABELS[month - 1]).join(', ')
}

function parseScientificName(value: unknown) {
  if (typeof value !== 'string' || !value.trim()) {
    return { genus: null, specificEpithet: null, taxonType: null as string | null }
  }
  const tokens = value.trim().split(/\s+/).filter(Boolean)
  const genus = tokens[0] ?? null
  const hasHybridMarker = tokens.some((token) => token === 'x' || token === '×')
  const hasVarietalMarker = tokens.some((token) => /^(var\.?|subsp\.?|ssp\.?|f\.?|cv\.?)$/i.test(token))
    || value.includes("'")
  const specificEpithetIndex = tokens[1] === 'x' || tokens[1] === '×' ? 2 : 1
  const specificEpithet = tokens[specificEpithetIndex] ?? null
  const taxonType = hasHybridMarker
    ? 'Hybrid'
    : hasVarietalMarker
      ? 'Varietal / cultivar'
      : specificEpithet
        ? 'Species'
        : null
  return { genus, specificEpithet, taxonType }
}

function debugSpotlight(message: string, details?: Record<string, unknown>) {
  if (!spotlightDebugEnabled) {
    return
  }
  console.debug(`[SummaryMarkdownCard:${props.itemId}] ${message}`, details ?? {})
}

function buildGroupFilters(): SqlFilterLike[] {
  return (props.filters || []).map((f) =>
    typeof f === 'string' ? { source: props.itemId, value: f } : f,
  )
}

// --- Dashboard group mode ---
const dashboardId = computed(() => props.dashboardGroup?.dashboard.id ?? '')
const lastGroupSignature = ref<string | null>(null)

function syncWithGroup() {
  if (!props.dashboardGroup) return
  props.dashboardGroup.setConnection(props.connectionId)
  props.dashboardGroup.setImports(props.imports)
  props.dashboardGroup.registerItem({
    itemId: props.itemId,
    title: 'Spotlight Card',
    query: props.query,
    priority: 0,
    allowCrossFilter: false,
    filters: buildGroupFilters(),
    chartFilters: [],
    parameters: buildParameters(),
  })
}

async function runViaGroup() {
  syncWithGroup()
  if (!props.query.trim()) {
    row.value = null
    error.value = null
    loading.value = false
    return
  }
  const sig = JSON.stringify({
    query: props.query,
    filters: buildGroupFilters().map((f) => ({ value: f.value, parameters: f.parameters })),
    parameters: buildParameters(),
  })
  if (lastGroupSignature.value === sig) return
  lastGroupSignature.value = sig
  await nextTick()
  props.dashboardGroup!.scheduleRun(props.itemId)
}

watchEffect(() => {
  if (!props.dashboardGroup) return
  const data = props.dashboardGroup.getItemData(props.itemId, dashboardId.value) as unknown as Record<string, unknown> | null
  if (!data) return
  if (data.loading) {
    loading.value = true
    return
  }
  loading.value = false
  if (data.error) {
    error.value = typeof data.error === 'string' ? data.error : 'Failed to load spotlight card.'
    row.value = null
    return
  }
  error.value = null
  const results = data.results as { data?: Array<Record<string, unknown>> } | null
  row.value = Array.isArray(results?.data) ? (results!.data[0] ?? null) : null
})

const markdown = computed(() => {
  if (!row.value) return ''

  const species = typeof row.value.species === 'string' ? row.value.species : 'Unknown species'
  const commonName = typeof row.value.common_name === 'string' && row.value.common_name.trim()
    ? row.value.common_name
    : null
  const count = typeof row.value.tree_count === 'number' ? row.value.tree_count.toLocaleString() : null
  const description = typeof row.value.description === 'string' && row.value.description.trim()
    ? row.value.description.trim()
    : null
  const taxonomy = parseScientificName(species)

  const facts = [
    ['Genus', taxonomy.genus],
    ['Specific epithet', taxonomy.specificEpithet],
    ['Taxon type', taxonomy.taxonType],
    ['Form', formatTitleCase(row.value.tree_form)],
    ['Mature height', formatRange(row.value.mature_height_min_ft, row.value.mature_height_max_ft, 'ft')],
    ['Canopy spread', formatRange(row.value.canopy_spread_min_ft, row.value.canopy_spread_max_ft, 'ft')],
    ['Growth rate', formatTitleCase(row.value.growth_rate)],
    ['Bloom period', formatBloomMonths(row.value.bloom_months)],
    ['Wildlife value', formatTitleCase(row.value.wildlife_value)],
    ['Drought tolerance', formatTitleCase(row.value.drought_tolerance)],
    ['Water needs', formatTitleCase(row.value.water_needs)],
  ].filter(([, value]) => Boolean(value))

  const introName = commonName ? `**${commonName}**` : `**${species}**`
  const introSpecies = commonName ? ` (*${species}*)` : ''
  const introCount = count ? `, with **${count}** trees in the current view.` : '.'

  return [
    `${introName}${introSpecies} is the most common tree in the current filter set${introCount}`,
    description ?? '',
    ...facts.map(([label, value]) => `- **${label}:** ${value}`),
  ]
    .filter(Boolean)
    .join('\n\n')
})

async function load() {
  const loadId = latestLoadId.value + 1
  latestLoadId.value = loadId
  activeCancellation.value?.cancel()
  activeCancellation.value = null

  debugSpotlight('load:start', {
    loadId,
    connectionId: props.connectionId,
    filters: props.filters,
    query: props.query,
  })

  if (!props.query.trim()) {
    row.value = null
    error.value = null
    loading.value = false
    debugSpotlight('load:skip-empty-query', { loadId })
    return
  }

  loading.value = true
  error.value = null
  try {
    const resolvedFilters = (props.filters || []).map((f) =>
      typeof f === 'string' ? { value: f } : f,
    )
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
    if (loadId !== latestLoadId.value) {
      debugSpotlight('load:stale-result-ignored', { loadId })
      return
    }
    const result = batch.results[0]
    if (!result?.success || !result.results) {
      debugSpotlight('load:query-failed', {
        loadId,
        batchSuccess: batch.success,
        result,
      })
      throw new Error(result?.error || 'Failed to load spotlight card.')
    }
    const payload = result.results.toJSON() as { data?: Array<Record<string, unknown>> }
    debugSpotlight('load:query-succeeded', {
      loadId,
      generatedSql: result.generatedSql,
      rowCount: Array.isArray(payload.data) ? payload.data.length : null,
      firstRow: Array.isArray(payload.data) ? (payload.data[0] ?? null) : null,
      payload,
    })
    row.value = Array.isArray(payload.data) ? (payload.data[0] ?? null) : null
  } catch (err) {
    if (loadId !== latestLoadId.value) {
      debugSpotlight('load:stale-error-ignored', {
        loadId,
        error: err instanceof Error ? err.message : String(err),
      })
      return
    }
    row.value = null
    error.value = (err as Error).message
    debugSpotlight('load:error', {
      loadId,
      error: error.value,
    })
  } finally {
    if (loadId === latestLoadId.value) {
      loading.value = false
      activeCancellation.value = null
      debugSpotlight('load:finish', {
        loadId,
        hasRow: Boolean(row.value),
        error: error.value,
      })
    }
  }
}

watch(
  () => `${props.connectionId}::${props.query}::${JSON.stringify(props.filters)}::${JSON.stringify(props.imports)}::${JSON.stringify(buildParameters())}`,
  () => {
    if (props.dashboardGroup) {
      void runViaGroup()
    } else {
      void load()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  activeCancellation.value?.cancel()
  activeCancellation.value = null
})
</script>

<style scoped>
.summary-markdown-card {
  flex: 1 1 auto;
  min-height: 0;
  height: 100%;
  overflow: auto;
}

.summary-markdown-card__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: rgba(154, 166, 154, 0.82);
  font-size: 0.84rem;
}

.summary-markdown-card__state--error {
  color: #d48f72;
}

.summary-markdown-card__content {
  height: 100%;
  color: rgba(237, 242, 235, 0.9);
}

.summary-markdown-card__content :deep(.markdown-root),
.summary-markdown-card__content :deep(.markdown-content),
.summary-markdown-card__content :deep(.markdown-body) {
  color: rgba(237, 242, 235, 0.9);
  font-size: 0.92rem;
  line-height: 1.6;
}

.summary-markdown-card__content :deep(*) {
  color: inherit;
}

.summary-markdown-card__content :deep(p),
.summary-markdown-card__content :deep(li) {
  color: rgba(237, 242, 235, 0.82);
}

.summary-markdown-card__content :deep(strong) {
  color: var(--color-ink);
  font-weight: 700;
}

.summary-markdown-card__content :deep(em) {
  color: rgba(237, 242, 235, 0.88);
}

.summary-markdown-card__content :deep(p:first-child) {
  margin-top: 0;
  color: rgba(237, 242, 235, 0.94);
  font-size: 1rem;
  line-height: 1.55;
}

.summary-markdown-card__content :deep(ul) {
  margin: 0.8rem 0 0;
  padding-left: 1.1rem;
}

.summary-markdown-card__content :deep(li) {
  margin-bottom: 0.35rem;
}

.summary-markdown-card__content :deep(li::marker) {
  color: rgba(167, 227, 178, 0.68);
}

.summary-markdown-card__content :deep(a) {
  color: var(--color-leaf);
}
</style>
