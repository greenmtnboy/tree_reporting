<template>
  <div ref="wrapperRef" class="tree-dot-map">
    <canvas ref="canvasRef" class="tree-dot-map__canvas" />
    <transition name="fade">
      <div v-if="loading" class="tree-dot-map__loading">Loading map&hellip;</div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, watchEffect, onMounted, onBeforeUnmount, nextTick, toRaw, toValue, computed } from 'vue'
import type { DashboardImport, DashboardExecutionService, SqlFilterLike, EmbeddedDashboardGroup } from '@trilogy-data/trilogy-studio-components/dashboard'
import { TREE_DOT_MAP_QUERY } from '../composables/summaryDashboardConfig'

type Point = { latitude: number; longitude: number }

const props = withDefaults(
  defineProps<{
    connectionId: string
    queryExecutionService: DashboardExecutionService
    imports?: DashboardImport[]
    filters?: SqlFilterLike[] | string[]
    parameters?: Record<string, unknown>
    itemId: string
    dashboardGroup?: EmbeddedDashboardGroup
    /**
     * Where this query sits in the dashboard group's run order (lower runs
     * first). The map pulls one row per tree, 1.5M for Bogotá, and the
     * group executes its queries one after another, so the pages that
     * embed it put it last: it is decorative, and the headline numbers
     * should not wait behind it.
     */
    priority?: number
  }>(),
  {
    imports: () => [],
    filters: () => [],
    parameters: () => ({}),
    dashboardGroup: undefined,
    priority: 0,
  },
)

const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapperRef = ref<HTMLDivElement | null>(null)
const loading = ref(false)
const latestLoadId = ref(0)
const activeCancellation = ref<{ cancel: () => void } | null>(null)
// Plain, not a ref: a reactive array of a million row objects would proxy
// every row on each read, and nothing needs to watch the points themselves.
let lastPoints: Point[] = []

const QUERY = TREE_DOT_MAP_QUERY

const dashboardId = computed(() => props.dashboardGroup?.dashboard.id ?? '')

function buildParameters() {
  const parameters = toValue(props.parameters as Record<string, unknown> | undefined)
  return parameters && typeof parameters === 'object' ? { ...parameters } : {}
}

function buildGroupFilters(): SqlFilterLike[] {
  return (props.filters || []).map((f) =>
    typeof f === 'string' ? { source: props.itemId, value: f } : f,
  )
}

// --- Dashboard group mode ---
const lastGroupSignature = ref<string | null>(null)

function syncWithGroup() {
  if (!props.dashboardGroup) return
  props.dashboardGroup.setConnection(props.connectionId)
  props.dashboardGroup.setImports(props.imports)
  props.dashboardGroup.registerItem({
    itemId: props.itemId,
    title: 'Tree Distribution',
    query: QUERY,
    priority: props.priority,
    allowCrossFilter: false,
    filters: buildGroupFilters(),
    chartFilters: [],
    parameters: buildParameters(),
  })
}

async function runViaGroup() {
  syncWithGroup()
  const sig = JSON.stringify({
    query: QUERY,
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
  // The group's store is reactive; read the rows raw so a million-row result
  // is not wrapped in a proxy per row on the way to the canvas.
  const results = toRaw(data.results) as { data?: Point[] } | null
  const points = results?.data ?? []
  lastPoints = points
  nextTick().then(() => renderDots(points))
})

// One tree is one 0.6 CSS px square at 45% opacity; overlapping trees compound,
// which is what shades a dense street darker. Compounded alpha by tree count.
const DOT_RGB = [107, 195, 140] as const
const DOT_ALPHA = 0.45
const ALPHA_BY_COUNT = new Uint8Array(256)
for (let n = 1; n < 256; n++) {
  ALPHA_BY_COUNT[n] = Math.round(255 * (1 - Math.pow(1 - DOT_ALPHA, n)))
}

function renderDots(points: Point[]) {
  const canvas = canvasRef.value
  if (!canvas) return

  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return

  const cw = Math.round(rect.width * dpr)
  const ch = Math.round(rect.height * dpr)
  canvas.width = cw
  canvas.height = ch

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const W = rect.width
  const H = rect.height

  ctx.clearRect(0, 0, cw, ch)

  if (!points.length) return

  let minLon = Infinity, maxLon = -Infinity
  let minLat = Infinity, maxLat = -Infinity
  for (const p of points) {
    if (p.longitude < minLon) minLon = p.longitude
    if (p.longitude > maxLon) maxLon = p.longitude
    if (p.latitude < minLat) minLat = p.latitude
    if (p.latitude > maxLat) maxLat = p.latitude
  }

  const lonRange = maxLon - minLon
  const latRange = maxLat - minLat
  if (lonRange === 0 || latRange === 0) return

  // Correct for longitude shrinking at higher latitudes (Mercator-style)
  const midLat = (minLat + maxLat) / 2
  const cosLat = Math.cos((midLat * Math.PI) / 180)
  const adjustedLonRange = lonRange * cosLat

  const pad = 12
  const availW = W - pad * 2
  const availH = H - pad * 2
  const scaleX = availW / adjustedLonRange
  const scaleY = availH / latRange
  const scale = Math.min(scaleX, scaleY)
  const originX = pad + (availW - adjustedLonRange * scale) / 2
  const originY = pad + (availH - latRange * scale) / 2

  // Count trees per device pixel, then paint the bitmap once. A million
  // fillRect calls cost seconds on the main thread; a million array
  // increments and one putImageData cost tens of milliseconds.
  const counts = new Uint8Array(cw * ch)
  const lonScale = cosLat * scale * dpr
  const latScale = scale * dpr
  const x0 = originX * dpr
  const y0 = (H - originY) * dpr
  for (const p of points) {
    const x = (x0 + (p.longitude - minLon) * lonScale) | 0
    const y = (y0 - (p.latitude - minLat) * latScale) | 0
    if (x < 0 || y < 0 || x >= cw || y >= ch) continue
    const i = y * cw + x
    if (counts[i] < 255) counts[i]++
  }

  const image = ctx.createImageData(cw, ch)
  const pixels = new Uint32Array(image.data.buffer)
  const [r, g, b] = DOT_RGB
  const rgb = (b << 16) | (g << 8) | r
  for (let i = 0; i < counts.length; i++) {
    const n = counts[i]
    if (n) pixels[i] = (ALPHA_BY_COUNT[n] << 24) | rgb
  }
  ctx.putImageData(image, 0, 0)
}

async function loadDirect() {
  const loadId = latestLoadId.value + 1
  latestLoadId.value = loadId
  activeCancellation.value?.cancel()
  activeCancellation.value = null

  loading.value = true

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
        query: QUERY,
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
    if (!result?.success || !result.results) return

    const payload = result.results.toJSON() as { data?: Point[] }
    const points = payload.data ?? []
    lastPoints = points

    await nextTick()
    renderDots(points)
  } catch {
    // silently ignore — map is decorative
  } finally {
    if (loadId === latestLoadId.value) {
      loading.value = false
      activeCancellation.value = null
    }
  }
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  if (wrapperRef.value) {
    resizeObserver = new ResizeObserver(() => {
      renderDots(lastPoints)
    })
    resizeObserver.observe(wrapperRef.value)
  }
})

onBeforeUnmount(() => {
  activeCancellation.value?.cancel()
  activeCancellation.value = null
  resizeObserver?.disconnect()
  resizeObserver = null
})

watch(
  () => `${props.connectionId}::${JSON.stringify(props.filters)}::${JSON.stringify(buildParameters())}`,
  () => {
    if (props.dashboardGroup) {
      void runViaGroup()
    } else {
      void loadDirect()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.tree-dot-map {
  position: relative;
  width: 100%;
  height: 100%;
}

.tree-dot-map__canvas {
  display: block;
  width: 100%;
  height: 100%;
}

.tree-dot-map__loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  color: rgba(var(--muted-rgb), 0.5);
  pointer-events: none;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.4s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
