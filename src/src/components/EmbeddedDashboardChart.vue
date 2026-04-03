<template>
  <TrilogyEmbedProvider theme="dark">
    <div ref="containerRef" class="embedded-dashboard-chart">
      <DashboardChart
        :dashboard-id="resolvedDashboardId"
        :item-id="itemId"
        :get-item-data="getItemData"
        :set-item-data="setItemData"
        :edit-mode="false"
        :symbols="[]"
        :get-dashboard-query-executor="getDashboardQueryExecutor"
        @dimension-click="handleDimensionClick"
        @background-click="handleBackgroundClick"
      />
    </div>
  </TrilogyEmbedProvider>
</template>

<script setup lang="ts">
import {
  DashboardChart,
  TrilogyEmbedProvider,
  useEmbeddedDashboardGroup,
  type EmbeddedDashboardGroup,
  type DashboardImport,
  type DashboardExecutionService,
  type ChartConfig,
  type DimensionClick,
  type GridItemDataResponse,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    query: string
    itemId: string
    connectionId: string
    queryExecutionService: DashboardExecutionService
    chartConfig?: ChartConfig
    imports?: DashboardImport[]
    filters?: string[]
    selectionField?: string
    selectedValue?: string | null
    selectionFilters?: Array<{ source: string; value: Record<string, string> }>
    dashboardId?: string
    dashboardGroup?: EmbeddedDashboardGroup
    priority?: number
    allowCrossFilter?: boolean
  }>(),
  {
    imports: () => [{ id: 'tree_enrichment', name: 'tree_enrichment', alias: '' }],
    filters: () => [],
    selectedValue: null,
    selectionFilters: () => [],
    dashboardId: undefined,
    priority: 0,
    allowCrossFilter: true,
  },
)

const emit = defineEmits<{
  dimensionClick: [info: DimensionClick]
  backgroundClick: []
  resultsEmpty: []
  resultsPresent: []
}>()

const containerRef = ref<HTMLElement | null>(null)
const standaloneDashboardId = computed(() => props.dashboardId ?? `summary-${props.connectionId}`)
const resolvedDashboardId = computed(() => props.dashboardGroup?.dashboard.id ?? standaloneDashboardId.value)

let localDashboardGroup: EmbeddedDashboardGroup | null = null

function getActiveDashboardGroup() {
  if (props.dashboardGroup) {
    return props.dashboardGroup
  }

  if (!localDashboardGroup) {
    localDashboardGroup = useEmbeddedDashboardGroup({
      dashboardId: standaloneDashboardId.value,
      connectionId: props.connectionId,
      queryExecutionService: props.queryExecutionService,
      imports: props.imports,
    })
  }

  return localDashboardGroup
}

let resizeObserver: ResizeObserver | null = null
const lastQuerySignature = ref<string | null>(null)

function buildFilters() {
  return props.filters.map((value) => ({ source: 'summary-view', value }))
}

function buildChartFilters() {
  if (props.selectionFilters.length) {
    return props.selectionFilters
  }
  if (!props.selectionField || !props.selectedValue) {
    return []
  }
  return [
    {
      source: props.itemId,
      value: {
        [props.selectionField]: props.selectedValue,
      },
    },
  ]
}

function syncExternalState() {
  const dashboardGroup = getActiveDashboardGroup()
  dashboardGroup.setConnection(props.connectionId)
  dashboardGroup.setImports(props.imports)
  dashboardGroup.registerItem({
    itemId: props.itemId,
    title: props.title,
    query: props.query,
    priority: props.priority,
    chartConfig: props.chartConfig,
    allowCrossFilter: props.allowCrossFilter,
    filters: buildFilters(),
    chartFilters: buildChartFilters(),
  })
}

function getQuerySignature() {
  return JSON.stringify({
    query: props.query,
    filters: buildFilters().map((filter) => filter.value),
    imports: props.imports.map((imp) => ({ name: imp.name, alias: imp.alias })),
    connectionId: props.connectionId,
  })
}

function getItemData(itemId: string, dashboardId: string): GridItemDataResponse {
  const data = getActiveDashboardGroup().getItemData(itemId, dashboardId) as unknown as Record<string, unknown> | null
  if (!data || typeof data !== 'object') {
    return data as unknown as GridItemDataResponse
  }

  const error = data.error
  if (error && typeof error === 'object') {
    return {
      ...data,
      error: JSON.stringify(error, null, 2),
    } as unknown as GridItemDataResponse
  }

  return data as unknown as GridItemDataResponse
}

function setItemData(itemId: string, dashboardId: string, data: Record<string, unknown>) {
  getActiveDashboardGroup().setItemData(itemId, dashboardId, data)
}

function getDashboardQueryExecutor(dashboardId: string) {
  return getActiveDashboardGroup().getDashboardQueryExecutor(dashboardId)
}

// Set before emitting dimension-click / background-click so the filter watcher
// that fires in the same flush cycle skips re-running this chart's own query.
let suppressNextFilterUpdate = false

function handleDimensionClick(info: DimensionClick) {
  suppressNextFilterUpdate = true
  emit('dimensionClick', info)
}

function handleBackgroundClick() {
  suppressNextFilterUpdate = true
  emit('backgroundClick')
}

async function runQuery(force = false) {
  syncExternalState()
  if (!props.query.trim()) {
    getActiveDashboardGroup().setItemData(props.itemId, resolvedDashboardId.value, {
      results: null,
      error: null,
      loading: false,
    })
    lastQuerySignature.value = null
    return
  }

  const nextSignature = getQuerySignature()
  if (!force && lastQuerySignature.value === nextSignature) {
    return
  }

  lastQuerySignature.value = nextSignature
  await nextTick()
  getActiveDashboardGroup().scheduleRun(props.itemId)
}

function updateDimensions() {
  if (!containerRef.value) {
    return
  }
  const rect = containerRef.value.getBoundingClientRect()
  getActiveDashboardGroup().setItemData(props.itemId, resolvedDashboardId.value, {
    width: Math.max(Math.floor(rect.width), 240),
    height: Math.max(Math.floor(rect.height), 220),
  })
}

watch(
  () => [props.title, JSON.stringify(props.chartConfig ?? null), props.allowCrossFilter],
  () => {
    syncExternalState()
  },
)

watch(
  () => [props.query, JSON.stringify(props.filters), JSON.stringify(props.imports), props.connectionId],
  () => {
    if (suppressNextFilterUpdate) {
      suppressNextFilterUpdate = false
      return
    }
    void runQuery()
  },
)

watch(
  () => JSON.stringify([props.selectionFilters, props.selectionField, props.selectedValue]),
  () => {
    getActiveDashboardGroup().setItemData(props.itemId, resolvedDashboardId.value, {
      chartFilters: buildChartFilters(),
    })
  },
)

watchEffect(() => {
  const data = getItemData(props.itemId, resolvedDashboardId.value) as Record<string, unknown> | null
  if (!data || data.loading) return
  const results = data.results as { data?: unknown[] } | null
  if (!results) return
  const rows = results.data
  if (!Array.isArray(rows)) return
  if (rows.length === 0) {
    emit('resultsEmpty')
  } else {
    emit('resultsPresent')
  }
})

onMounted(() => {
  syncExternalState()
  updateDimensions()
  resizeObserver = new ResizeObserver(() => updateDimensions())
  if (containerRef.value) {
    resizeObserver.observe(containerRef.value)
  }
  void runQuery()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  localDashboardGroup?.dispose()
})
</script>

<style scoped>
.embedded-dashboard-chart {
  display: flex;
  flex: 1 1 auto;
  height: 100%;
  min-height: 220px;
  min-width: 0;
  min-height: 0;
  position: relative;
  overflow: hidden;
}

.embedded-dashboard-chart :deep(.chart-placeholder) {
  min-height: 0;
  height: 100%;
  align-items: stretch;
}

/* Hide the redundant "Warning" badge — "Query Error" title is sufficient */
.embedded-dashboard-chart :deep(.message-badge) {
  display: none;
}

/* Keep error panel inside the card */
.embedded-dashboard-chart :deep(.error-message) {
  overflow: hidden;
  height: 100%;
  max-height: 100%;
  box-sizing: border-box;
}

.embedded-dashboard-chart :deep(.message-shell),
.embedded-dashboard-chart :deep(.message-body) {
  min-height: 0;
  overflow: auto;
}

/* Wrap long query/filter text instead of overflowing */
.embedded-dashboard-chart :deep(.message-query) {
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: break-word;
}
</style>
