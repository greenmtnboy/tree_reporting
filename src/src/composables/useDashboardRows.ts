import { computed, nextTick, ref, shallowRef, toRaw, toValue, watch, watchEffect, type MaybeRefOrGetter } from 'vue'
import type {
  DashboardImport,
  EmbeddedDashboardGroup,
  SqlFilterLike,
} from '@trilogy-data/trilogy-studio-components/dashboard'

/**
 * What a card the page draws itself (a bar list, the dot map, the species
 * list) needs to take part in a dashboard group: the same props an embedded
 * chart takes, minus the chart.
 */
export type DashboardRowsProps = {
  itemId: string
  query: string
  connectionId: string
  dashboardGroup: EmbeddedDashboardGroup
  imports?: DashboardImport[]
  filters?: SqlFilterLike[] | string[]
  parameters?: Record<string, unknown>
  /** Where the query sits in the group's run order; lower runs first. */
  priority?: number
}

export type DashboardRow = Record<string, unknown>

/**
 * Register a query with the page's dashboard group and read its rows.
 *
 * The group batches every registered query into one resolver round trip and
 * runs the SQL on the page's DuckDB; this is the part of an embedded chart
 * that is not drawing. Rows come back raw (`toRaw`): the group's store is
 * reactive, and a 170k-row dot map must not be proxied row by row.
 */
export function useDashboardRows<Row extends DashboardRow = DashboardRow>(
  source: DashboardRowsProps | (() => DashboardRowsProps),
  options: { title?: string; enabled?: MaybeRefOrGetter<boolean> } = {},
) {
  const rows = shallowRef<Row[]>([])
  const loading = ref(true)
  const error = ref<string | null>(null)
  const loaded = ref(false)

  // A component passes its props; one that runs two queries passes a getter.
  const current = computed(() => (typeof source === 'function' ? source() : source))
  const dashboardId = computed(() => current.value.dashboardGroup.dashboard.id)
  const enabled = computed(() => toValue(options.enabled) ?? true)

  function parameters(): Record<string, unknown> {
    const value = toValue(current.value.parameters as Record<string, unknown> | undefined)
    return value && typeof value === 'object' ? { ...value } : {}
  }

  function filters(): SqlFilterLike[] {
    return (current.value.filters ?? []).map((filter) =>
      typeof filter === 'string' ? { source: current.value.itemId, value: filter } : filter,
    )
  }

  let lastSignature: string | null = null

  async function run() {
    if (!enabled.value || !current.value.query.trim()) {
      rows.value = []
      loading.value = false
      return
    }
    const group = current.value.dashboardGroup
    group.setConnection(current.value.connectionId)
    group.setImports(current.value.imports ?? [])
    group.registerItem({
      itemId: current.value.itemId,
      title: options.title ?? current.value.itemId,
      query: current.value.query,
      priority: current.value.priority ?? 0,
      allowCrossFilter: false,
      filters: filters(),
      chartFilters: [],
      parameters: parameters(),
    })
    const signature = JSON.stringify({
      query: current.value.query,
      filters: filters().map((filter) => ({ value: filter.value, parameters: filter.parameters })),
      parameters: parameters(),
    })
    if (signature === lastSignature) return
    lastSignature = signature
    loading.value = true
    await nextTick()
    group.scheduleRun(current.value.itemId)
  }

  watch(
    () =>
      `${enabled.value}::${current.value.connectionId}::${current.value.query}::${JSON.stringify(current.value.filters)}::${JSON.stringify(parameters())}`,
    () => void run(),
    { immediate: true },
  )

  watchEffect(() => {
    if (!enabled.value) return
    const data = current.value.dashboardGroup.getItemData(current.value.itemId, dashboardId.value) as unknown as
      | Record<string, unknown>
      | null
    if (!data) return
    if (data.loading) {
      loading.value = true
      return
    }
    if (data.error) {
      error.value = typeof data.error === 'string' ? data.error : 'This query failed.'
      rows.value = []
      loading.value = false
      return
    }
    const results = toRaw(data.results) as { data?: Row[] } | null
    if (!results) return
    error.value = null
    rows.value = Array.isArray(results.data) ? (toRaw(results.data) as Row[]) : []
    loading.value = false
    loaded.value = true
  })

  return { rows, loading, error, loaded }
}

/** A result cell as a number; DuckDB hands counts back as bigint. */
export function rowNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'bigint') return Number(value)
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  return null
}

export function rowText(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}
