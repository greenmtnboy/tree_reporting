<template>
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
</template>

<script setup lang="ts">
import {
  DashboardChart,
  DashboardQueryExecutor,
  CELL_TYPES,
  type DashboardDefinition,
  type DashboardImport,
  type DashboardExecutionService,
  type GridItemDataResponse,
  type ChartConfig,
  type DimensionClick,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

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
}>()

const containerRef = ref<HTMLElement | null>(null)
const resolvedDashboardId = computed(() => props.dashboardId ?? `summary-${props.connectionId}`)

const itemState = reactive({
  type: CELL_TYPES.CHART,
  content: props.query,
  drilldown: null as string | null,
  name: props.title,
  allowCrossFilter: props.allowCrossFilter,
  width: 0,
  height: 320,
  chartConfig: props.chartConfig,
  drilldownChartConfig: null as ChartConfig | null,
  conceptFilters: [] as Array<{ source: string; value: Record<string, string> }>,
  chartFilters: [] as Array<{ source: string; value: Record<string, string> }>,
  filters: [] as Array<{ source: string; value: string }>,
  parameters: {} as Record<string, unknown>,
  results: null,
  loading: false,
  error: null as string | null,
  loadStartTime: null as number | null,
})

const dashboard = reactive<DashboardDefinition>({
  id: resolvedDashboardId.value,
  name: resolvedDashboardId.value,
  storage: 'local',
  connection: props.connectionId,
  layout: [
    {
      x: 0,
      y: props.priority,
      w: 12,
      h: 10,
      i: props.itemId,
      static: true,
    },
  ],
  gridItems: {
    [props.itemId]: itemState,
  },
  nextId: 1,
  createdAt: new Date(),
  updatedAt: new Date(),
  filter: null,
  imports: props.imports,
  version: 1,
  description: '',
  state: 'published',
})

const executor = new DashboardQueryExecutor(
  props.queryExecutionService,
  props.connectionId,
  resolvedDashboardId.value,
  () => dashboard,
  getItemData,
  setItemData,
)

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
  itemState.name = props.title
  itemState.content = props.query
  itemState.chartConfig = props.chartConfig
  itemState.allowCrossFilter = props.allowCrossFilter
  itemState.filters = buildFilters()
  itemState.chartFilters = buildChartFilters()
  dashboard.imports = props.imports
}

function getQuerySignature() {
  return JSON.stringify({
    query: props.query,
    filters: buildFilters().map((filter) => filter.value),
    imports: props.imports.map((imp) => ({ name: imp.name, alias: imp.alias })),
    connectionId: props.connectionId,
  })
}

function getStructuredQuery() {
  return itemState.drilldown || itemState.content
}

function getItemData(itemId: string, dashboardId: string): GridItemDataResponse {
  if (itemId !== props.itemId || dashboardId !== resolvedDashboardId.value) {
    return {
      type: CELL_TYPES.CHART,
      content: '',
      structured_content: { markdown: '', query: '' },
      rootContent: [],
      name: '',
      allowCrossFilter: true,
      hasDrilldown: false,
    }
  }

  return {
    type: CELL_TYPES.CHART,
    content: itemState.content,
    structured_content: { markdown: '', query: getStructuredQuery() },
    rootContent: [],
    name: itemState.name,
    allowCrossFilter: itemState.allowCrossFilter,
    width: itemState.width,
    height: itemState.height,
    chartConfig: itemState.drilldown ? itemState.drilldownChartConfig || undefined : itemState.chartConfig,
    connectionName: props.connectionId,
    imports: props.imports,
    conceptFilters: itemState.conceptFilters,
    chartFilters: itemState.chartFilters,
    filters: itemState.filters,
    parameters: itemState.parameters,
    results: itemState.results,
    loading: itemState.loading,
    error: itemState.error,
    loadStartTime: itemState.loadStartTime,
    hasDrilldown: Boolean(itemState.drilldown),
  }
}

function setItemData(itemId: string, dashboardId: string, data: Record<string, unknown>) {
  if (itemId !== props.itemId || dashboardId !== resolvedDashboardId.value) {
    return
  }

  if ('name' in data) itemState.name = (data.name as string) ?? itemState.name
  if ('content' in data) itemState.content = (data.content as string) ?? itemState.content
  if ('chartConfig' in data) itemState.chartConfig = (data.chartConfig as ChartConfig | undefined) ?? undefined
  if ('drilldown' in data) itemState.drilldown = (data.drilldown as string | null) ?? null
  if ('drilldownChartConfig' in data)
    itemState.drilldownChartConfig = (data.drilldownChartConfig as ChartConfig | null) ?? null
  if ('allowCrossFilter' in data) itemState.allowCrossFilter = Boolean(data.allowCrossFilter)
  if ('width' in data) itemState.width = Number(data.width ?? itemState.width)
  if ('height' in data) itemState.height = Number(data.height ?? itemState.height)
  if ('filters' in data) itemState.filters = (data.filters as typeof itemState.filters) ?? []
  if ('chartFilters' in data) itemState.chartFilters = (data.chartFilters as typeof itemState.chartFilters) ?? []
  if ('conceptFilters' in data)
    itemState.conceptFilters = (data.conceptFilters as typeof itemState.conceptFilters) ?? []
  if ('parameters' in data) itemState.parameters = (data.parameters as typeof itemState.parameters) ?? {}
  if ('results' in data) {
    itemState.results = data.results as typeof itemState.results
    itemState.loading = false
    itemState.error = null
    itemState.loadStartTime = null
  }
  if ('loading' in data) {
    itemState.loading = Boolean(data.loading)
    itemState.loadStartTime = itemState.loading ? Date.now() : null
  }
  if ('error' in data) {
    itemState.error = (data.error as string | null) ?? null
    if (itemState.error) {
      itemState.loading = false
      itemState.loadStartTime = null
    }
  }
}

function getDashboardQueryExecutor(dashboardId: string) {
  if (dashboardId !== resolvedDashboardId.value) {
    throw new Error(`Unexpected dashboard id ${dashboardId}`)
  }
  return executor
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
    itemState.results = null
    itemState.error = null
    itemState.loading = false
    lastQuerySignature.value = null
    return
  }

  const nextSignature = getQuerySignature()
  if (!force && lastQuerySignature.value === nextSignature) {
    return
  }

  lastQuerySignature.value = nextSignature
  await nextTick()
  executor.runSingle(props.itemId)
}

function updateDimensions() {
  if (!containerRef.value) {
    return
  }
  const rect = containerRef.value.getBoundingClientRect()
  itemState.width = Math.max(Math.floor(rect.width), 240)
  itemState.height = Math.max(Math.floor(rect.height), 220)
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
  () => JSON.stringify(props.selectionFilters),
  () => {
    itemState.chartFilters = buildChartFilters()
  },
)

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
