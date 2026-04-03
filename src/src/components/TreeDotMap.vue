<template>
  <div ref="wrapperRef" class="tree-dot-map">
    <canvas ref="canvasRef" class="tree-dot-map__canvas" />
    <transition name="fade">
      <div v-if="loading" class="tree-dot-map__loading">Loading map&hellip;</div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import type { DashboardImport, DashboardExecutionService } from '@trilogy-data/trilogy-studio-components/dashboard'

const props = withDefaults(
  defineProps<{
    connectionId: string
    queryExecutionService: DashboardExecutionService
    imports?: DashboardImport[]
    filters?: string[]
    itemId: string
  }>(),
  {
    imports: () => [],
    filters: () => [],
  },
)

const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapperRef = ref<HTMLDivElement | null>(null)
const loading = ref(false)
const latestLoadId = ref(0)
const activeCancellation = ref<{ cancel: () => void } | null>(null)
const lastPoints = ref<Array<{ latitude: number; longitude: number }>>([])

const QUERY = 'SELECT latitude, longitude WHERE latitude IS NOT NULL AND longitude IS NOT NULL;'

function renderDots(points: Array<{ latitude: number; longitude: number }>) {
  const canvas = canvasRef.value
  if (!canvas) return

  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return

  canvas.width = rect.width * dpr
  canvas.height = rect.height * dpr

  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.scale(dpr, dpr)

  const W = rect.width
  const H = rect.height

  ctx.clearRect(0, 0, W, H)

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

  const pad = 12
  const availW = W - pad * 2
  const availH = H - pad * 2
  const scaleX = availW / lonRange
  const scaleY = availH / latRange
  const scale = Math.min(scaleX, scaleY)
  const originX = pad + (availW - lonRange * scale) / 2
  const originY = pad + (availH - latRange * scale) / 2

  // Vary opacity by density — single pass, fixed small dot
  ctx.fillStyle = 'rgba(107, 195, 140, 0.45)'

  for (const p of points) {
    const x = originX + (p.longitude - minLon) * scale
    const y = H - (originY + (p.latitude - minLat) * scale)
    ctx.fillRect(x - 0.3, y - 0.3, 0.6, 0.6)
  }
}

async function load() {
  const loadId = latestLoadId.value + 1
  latestLoadId.value = loadId
  activeCancellation.value?.cancel()
  activeCancellation.value = null

  if (!props.filters.length && props.connectionId) {
    // Always load when we have a connection
  }

  loading.value = true

  try {
    const execution = await props.queryExecutionService.executeQueriesBatch(
      props.connectionId,
      [{
        label: props.itemId,
        query: QUERY,
        extra_filters: props.filters,
      }],
      'trilogy',
      props.imports.map((imp) => ({ name: imp.name, alias: imp.alias })),
    )
    activeCancellation.value = execution.cancellation
    const batch = await execution.resultPromise

    if (loadId !== latestLoadId.value) return

    const result = batch.results[0]
    if (!result?.success || !result.results) return

    const payload = result.results.toJSON() as { data?: Array<{ latitude: number; longitude: number }> }
    const points = payload.data ?? []
    lastPoints.value = points

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
      renderDots(lastPoints.value)
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
  () => [JSON.stringify(props.filters), props.connectionId] as const,
  () => void load(),
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
  color: rgba(154, 166, 154, 0.5);
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
