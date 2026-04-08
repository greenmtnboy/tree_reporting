<template>
  <div class="species-top-chart">
    <div v-if="loading" class="top-chart-state">Loading top species...</div>
    <div v-else-if="error" class="top-chart-state top-chart-state--error">{{ error }}</div>
    <div v-else-if="!bars.length" class="top-chart-state">No species data available.</div>
    <div v-else class="top-chart-bars">
      <div v-for="bar in bars" :key="bar.species" class="top-chart-row">
        <div class="top-chart-label" :title="bar.species">{{ bar.displayName }}</div>
        <div class="top-chart-bar-track">
          <div class="top-chart-bar-fill" :style="{ width: bar.widthPct + '%' }" />
        </div>
        <div class="top-chart-pct">{{ bar.pctLabel }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, toValue, watch } from 'vue'
import type { DashboardImport, DashboardExecutionService, SqlFilterLike } from '@trilogy-data/trilogy-studio-components/dashboard'

type SpeciesRow = { species: string; common_name?: string; tree_count: number }
type Bar = { species: string; displayName: string; widthPct: number; pctLabel: string }

const props = withDefaults(
  defineProps<{
    connectionId: string
    queryExecutionService: DashboardExecutionService
    imports?: DashboardImport[]
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
const topSpecies = ref<SpeciesRow[]>([])
const totalTrees = ref(0)
const latestLoadId = ref(0)
const activeCancellation = ref<{ cancel: () => void } | null>(null)

const TOP_QUERY = `SELECT
species,
common_names[1] as common_name,
count(tree_id) as tree_count
WHERE species IS NOT NULL
ORDER BY tree_count DESC
LIMIT 15;`

const TOTAL_QUERY = `SELECT count(tree_id) as total_trees;`

function formatCommonName(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  return value
    .toLowerCase()
    .replace(/(^|[\s\-/(])([a-z])/g, (_m, prefix: string, letter: string) => `${prefix}${letter.toUpperCase()}`)
}

const bars = computed<Bar[]>(() => {
  if (!topSpecies.value.length) return []
  const maxCount = topSpecies.value[0].tree_count
  const total = totalTrees.value || 1
  return topSpecies.value.map((row) => {
    const pct = (row.tree_count / total) * 100
    return {
      species: row.species,
      displayName: formatCommonName(row.common_name) || row.species,
      widthPct: maxCount > 0 ? (row.tree_count / maxCount) * 100 : 0,
      pctLabel: pct >= 1 ? `${pct.toFixed(1)}%` : `${pct.toFixed(2)}%`,
    }
  })
})

function buildParameters() {
  const p = toValue(props.parameters as Record<string, unknown> | undefined)
  return p && typeof p === 'object' ? { ...p } : {}
}

function resolveFilters(): { filterStrings: string[]; filterParams: Record<string, unknown> } {
  const resolved = (props.filters || []).map((f) => (typeof f === 'string' ? { value: f } : f))
  const filterStrings = resolved.map((f) => f.value)
  const filterParams = Object.assign({}, ...resolved.map((f) => (f as SqlFilterLike).parameters || {}))
  return { filterStrings, filterParams }
}

async function load() {
  const loadId = ++latestLoadId.value
  activeCancellation.value?.cancel()
  activeCancellation.value = null

  loading.value = true
  error.value = null
  try {
    const { filterStrings, filterParams } = resolveFilters()
    const params = { ...buildParameters(), ...filterParams } as Record<string, string | number | boolean>
    const importRefs = props.imports.map((imp) => ({ name: imp.name, alias: imp.alias }))

    const execution = await props.queryExecutionService.executeQueriesBatch(
      props.connectionId,
      [
        { label: `${props.itemId}-top`, query: TOP_QUERY, extra_filters: filterStrings, parameters: params },
        { label: `${props.itemId}-total`, query: TOTAL_QUERY, extra_filters: filterStrings, parameters: params },
      ],
      'trilogy',
      importRefs,
    )
    activeCancellation.value = execution.cancellation
    const batch = await execution.resultPromise
    if (loadId !== latestLoadId.value) return

    const topResult = batch.results[0]
    const totalResult = batch.results[1]

    if (!topResult?.success || !topResult.results) {
      throw new Error(topResult?.error || 'Failed to load top species.')
    }
    if (!totalResult?.success || !totalResult.results) {
      throw new Error(totalResult?.error || 'Failed to load total count.')
    }

    const topPayload = topResult.results.toJSON() as { data?: SpeciesRow[] }
    const totalPayload = totalResult.results.toJSON() as { data?: Array<Record<string, unknown>> }

    topSpecies.value = Array.isArray(topPayload.data) ? topPayload.data : []
    const totalRow = Array.isArray(totalPayload.data) ? totalPayload.data[0] : null
    totalTrees.value = Number(totalRow?.total_trees ?? 0)
  } catch (err) {
    if (loadId !== latestLoadId.value) return
    topSpecies.value = []
    totalTrees.value = 0
    error.value = (err as Error).message
  } finally {
    if (loadId === latestLoadId.value) {
      loading.value = false
      activeCancellation.value = null
    }
  }
}

watch(
  () => `${props.connectionId}::${JSON.stringify(props.filters)}::${JSON.stringify(props.imports)}::${JSON.stringify(toValue(props.parameters))}`,
  () => void load(),
  { immediate: true },
)

onBeforeUnmount(() => {
  activeCancellation.value?.cancel()
  activeCancellation.value = null
})
</script>

<style scoped>
.species-top-chart {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: auto;
}

.top-chart-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: rgba(154, 166, 154, 0.82);
  font-size: 0.84rem;
}

.top-chart-state--error {
  color: #d48f72;
}

.top-chart-bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.top-chart-row {
  display: grid;
  grid-template-columns: 160px 1fr 52px;
  align-items: center;
  gap: 10px;
}

.top-chart-label {
  font-size: 0.78rem;
  color: rgba(237, 242, 235, 0.88);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.top-chart-bar-track {
  height: 16px;
  background: rgba(167, 227, 178, 0.06);
  border-radius: 4px;
  overflow: hidden;
}

.top-chart-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--species-accent, #2F7D4F), rgba(107, 175, 146, 0.72));
  border-radius: 4px;
  transition: width 0.4s ease;
  min-width: 2px;
}

.top-chart-pct {
  font-size: 0.74rem;
  font-weight: 600;
  color: rgba(167, 227, 178, 0.82);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 640px) {
  .top-chart-row {
    grid-template-columns: 120px 1fr 48px;
    gap: 6px;
  }

  .top-chart-label {
    font-size: 0.72rem;
  }
}
</style>
