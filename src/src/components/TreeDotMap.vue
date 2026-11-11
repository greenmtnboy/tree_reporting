<template>
  <div
    ref="wrapperRef"
    class="tree-dot-map"
    :class="{ 'tree-dot-map--pickable': pickable && hover }"
    @mousemove="onMove"
    @mouseleave="hover = null"
    @click="onClick"
  >
    <canvas ref="canvasRef" class="tree-dot-map__canvas" />

    <div
      v-if="hover"
      class="tree-dot-map__readout"
      :style="{ left: `${hover.x}px`, top: `${hover.y}px` }"
    >
      <strong>{{ hover.countText }}</strong>
      <span>{{ hover.place }}</span>
      <span v-if="pickable" class="tree-dot-map__hint">click to open the map here</span>
    </div>

    <div v-if="peak" class="tree-dot-map__peak" :style="{ left: `${peak.x}px`, top: `${peak.y}px` }">
      <span class="tree-dot-map__peak-ring" :style="{ width: `${peak.size}px`, height: `${peak.size}px` }" />
      <span class="tree-dot-map__peak-label">densest: {{ peak.countText }}</span>
    </div>

    <div v-if="drawn" class="tree-dot-map__legend">
      <span class="tree-dot-map__legend-title">{{ legendTitle }}</span>
      <span class="tree-dot-map__ramp">
        <span>fewer</span>
        <span class="tree-dot-map__ramp-bar" :style="{ background: rampCss }" />
        <span>{{ peakText }}</span>
      </span>
      <span v-if="hasGhost" class="tree-dot-map__ghost-key">
        <span class="tree-dot-map__ghost-swatch" />every tree in the city
      </span>
    </div>

    <div v-if="scale" class="tree-dot-map__scale">
      <span class="tree-dot-map__scale-bar" :style="{ width: `${scale.px}px` }" />
      <span>{{ scale.label }}</span>
    </div>

    <transition name="fade">
      <div v-if="loading && !drawn" class="tree-dot-map__state">Plotting trees&hellip;</div>
      <div v-else-if="!loading && !drawn" class="tree-dot-map__state">No mapped trees in this view.</div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { DashboardImport, EmbeddedDashboardGroup, SqlFilterLike } from '@trilogy-data/trilogy-studio-components/dashboard'
import { treeDotMapQuery } from '../composables/summaryDashboardConfig'
import { rowNumber, useDashboardRows } from '../composables/useDashboardRows'
import { useTheme } from '../composables/useTheme'
import { formatCount } from '../lib/dashboardFormat'

/**
 * Where the trees of a view are, as a density plot on a bare graticule: no
 * basemap, so the street grid the trees themselves trace is the map.
 *
 * The query returns tree counts per map cell (about 110 m for one city, 1.1 km
 * across all of them; TREE_DOT_MAP_QUERY). Cells are gathered into screen bins
 * a few pixels wide and shaded by the square root of the count, so a single tree is a visible
 * mark and a dense block is a dark one; one device pixel at 45% alpha, which
 * this replaces, all but vanished on the light theme.
 *
 * With `ghostFilters` the same cells are also fetched under those (wider)
 * filters and drawn first in a flat grey, and the frame is fitted to them: a
 * species is then read against where the city has trees at all, and the frame
 * stays put as the species changes.
 */
const props = withDefaults(
  defineProps<{
    itemId: string
    connectionId: string
    dashboardGroup: EmbeddedDashboardGroup
    imports?: DashboardImport[]
    filters?: SqlFilterLike[] | string[]
    parameters?: Record<string, unknown>
    /** Run order in the group; the map is the largest result, so pages put it last. */
    priority?: number
    /** Filters for the grey "every tree" layer under the plotted one. */
    ghostFilters?: SqlFilterLike[] | string[] | null
    /** Ring and label the densest spot. */
    markPeak?: boolean
    /** Emit `pick` with the clicked location. */
    pickable?: boolean
  }>(),
  {
    imports: () => [],
    filters: () => [],
    parameters: () => ({}),
    priority: 0,
    ghostFilters: null,
    markPeak: false,
    pickable: false,
  },
)

const emit = defineEmits<{ pick: [location: { lat: number; lng: number }] }>()

const { resolvedTheme } = useTheme()

const activeCity = computed(() => {
  const city = (props.parameters as Record<string, unknown> | undefined)?.active_city
  return typeof city === 'string' && city !== 'ALL' ? city : null
})
const query = computed(() => treeDotMapQuery(activeCity.value))
const hasGhost = computed(() => props.ghostFilters != null)

const shared = () => ({
  query: query.value,
  connectionId: props.connectionId,
  dashboardGroup: props.dashboardGroup,
  imports: props.imports,
  parameters: props.parameters,
})

const main = useDashboardRows(
  () => ({ ...shared(), itemId: props.itemId, filters: props.filters, priority: props.priority }),
  { title: 'Tree distribution' },
)

const ghost = useDashboardRows(
  () => ({ ...shared(), itemId: `${props.itemId}-all`, filters: props.ghostFilters ?? [], priority: props.priority + 1 }),
  { title: 'Tree distribution (all trees)', enabled: hasGhost },
)

const loading = computed(() => main.loading.value)

// --- Cells -------------------------------------------------------------------

type Cells = { lat: Float64Array; lon: Float64Array; count: Float64Array; n: number }

function toCells(rows: Array<Record<string, unknown>>): Cells {
  const lat = new Float64Array(rows.length)
  const lon = new Float64Array(rows.length)
  const count = new Float64Array(rows.length)
  let n = 0
  for (const row of rows) {
    const y = rowNumber(row.map_cell_latitude ?? row.map_coarse_cell_latitude)
    const x = rowNumber(row.map_cell_longitude ?? row.map_coarse_cell_longitude)
    if (y == null || x == null) continue
    lat[n] = y
    lon[n] = x
    count[n] = rowNumber(row.tree_count) ?? 1
    n += 1
  }
  return { lat, lon, count, n }
}

/**
 * The span holding all but the outlying half percent of trees at each end. A
 * handful of mislocated trees, or one arboretum across the river, otherwise
 * shrinks the city to a corner of the card.
 */
function trimmedSpan(values: Float64Array, weights: Float64Array, n: number): [number, number] {
  const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => values[a] - values[b])
  let total = 0
  for (let i = 0; i < n; i += 1) total += weights[i]
  const cut = total * 0.005
  let low = values[order[0]]
  let high = values[order[n - 1]]
  let seen = 0
  for (const i of order) {
    seen += weights[i]
    if (seen >= cut) {
      low = values[i]
      break
    }
  }
  seen = 0
  for (let k = n - 1; k >= 0; k -= 1) {
    const i = order[k]
    seen += weights[i]
    if (seen >= cut) {
      high = values[i]
      break
    }
  }
  return [low, high]
}

// --- Drawing -----------------------------------------------------------------

const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapperRef = ref<HTMLDivElement | null>(null)
const drawn = ref(false)
const peakText = ref('more')
const scale = ref<{ px: number; label: string } | null>(null)
const peak = ref<{ x: number; y: number; size: number; countText: string } | null>(null)
const hover = ref<{ x: number; y: number; countText: string; place: string; lat: number; lng: number } | null>(null)

// Light to dark on paper, dim to bright on the dark theme: more trees is
// always the mark that stands further from the page.
const RAMPS: Record<string, Array<[number, number, number]>> = {
  light: [[176, 202, 158], [84, 122, 80], [24, 58, 38]],
  dark: [[62, 88, 62], [150, 186, 132], [236, 246, 204]],
}
const GHOST: Record<string, string> = { light: 'rgba(48, 59, 45, 0.11)', dark: 'rgba(227, 230, 216, 0.11)' }
const GRID: Record<string, string> = { light: 'rgba(48, 59, 45, 0.09)', dark: 'rgba(227, 230, 216, 0.09)' }
const GRID_TEXT: Record<string, string> = { light: 'rgba(48, 59, 45, 0.5)', dark: 'rgba(227, 230, 216, 0.5)' }

function rampColor(theme: string, t: number): string {
  const stops = RAMPS[theme] ?? RAMPS.light
  const scaled = Math.min(1, Math.max(0, t)) * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(scaled))
  const f = scaled - i
  const [a, b] = [stops[i], stops[i + 1]]
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)}, ${Math.round(a[1] + (b[1] - a[1]) * f)}, ${Math.round(a[2] + (b[2] - a[2]) * f)})`
}

const rampCss = computed(() => {
  const stops = RAMPS[resolvedTheme.value] ?? RAMPS.light
  return `linear-gradient(90deg, ${stops.map((s) => `rgb(${s.join(',')})`).join(', ')})`
})

const legendTitle = computed(() => (activeCity.value ? 'Trees per ~110 m block' : 'Trees per ~1 km block'))

const NICE_DEGREES = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 45]
const NICE_METRES = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000, 2000000, 5000000]

function degreeLabel(value: number, positive: string, negative: string, step: number): string {
  const digits = step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3
  return `${Math.abs(value).toFixed(digits)}°${value >= 0 ? positive : negative}`
}

// What the last frame drew, kept for hover and click.
type Frame = {
  bin: number
  columns: number
  bins: Map<number, number>
  toLat: (y: number) => number
  toLon: (x: number) => number
  cellMetres: number
}
let frame: Frame | null = null

function render() {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return

  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.round(rect.width * dpr)
  canvas.height = Math.round(rect.height * dpr)
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, rect.width, rect.height)

  const theme = resolvedTheme.value
  const cells = toCells(main.rows.value)
  const ghostCells = hasGhost.value ? toCells(ghost.rows.value) : null
  const framing = ghostCells && ghostCells.n > 0 ? ghostCells : cells

  frame = null
  peak.value = null
  scale.value = null
  if (framing.n === 0) {
    drawn.value = false
    return
  }

  let [minLat, maxLat] = trimmedSpan(framing.lat, framing.count, framing.n)
  let [minLon, maxLon] = trimmedSpan(framing.lon, framing.count, framing.n)
  const cellDegrees = activeCity.value ? 0.001 : 0.01
  // One cell is still a place: give it room instead of a zero-width frame.
  if (maxLat - minLat < cellDegrees * 8) { minLat -= cellDegrees * 4; maxLat += cellDegrees * 4 }
  if (maxLon - minLon < cellDegrees * 8) { minLon -= cellDegrees * 4; maxLon += cellDegrees * 4 }

  const W = rect.width
  const H = rect.height
  const margin = { top: 14, right: 16, bottom: 26, left: 16 }
  const availW = W - margin.left - margin.right
  const availH = H - margin.top - margin.bottom
  const cosLat = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180)
  const lonSpan = (maxLon - minLon) * cosLat
  const latSpan = maxLat - minLat
  // 4% of breathing room around the trimmed span, same scale on both axes.
  const pxPerDegree = Math.min(availW / lonSpan, availH / latSpan) / 1.08
  const originX = margin.left + (availW - lonSpan * pxPerDegree) / 2
  const originY = margin.top + (availH - latSpan * pxPerDegree) / 2
  const toX = (lon: number) => originX + (lon - minLon) * cosLat * pxPerDegree
  const toY = (lat: number) => originY + (maxLat - lat) * pxPerDegree
  const toLon = (x: number) => minLon + (x - originX) / (cosLat * pxPerDegree)
  const toLat = (y: number) => maxLat - (y - originY) / pxPerDegree

  // Graticule: a few faint lines, labelled at the bottom and left edges.
  const degreesAcross = W / (cosLat * pxPerDegree)
  const step = NICE_DEGREES.find((d) => degreesAcross / d <= 7) ?? 45
  ctx.strokeStyle = GRID[theme] ?? GRID.light
  ctx.fillStyle = GRID_TEXT[theme] ?? GRID_TEXT.light
  ctx.lineWidth = 1
  ctx.font = '10px system-ui, sans-serif'
  ctx.setLineDash([2, 4])
  const firstLon = Math.ceil(toLon(0) / step) * step
  ctx.textAlign = 'center'
  ctx.textBaseline = 'bottom'
  for (let lon = firstLon; toX(lon) < W; lon += step) {
    const x = Math.round(toX(lon)) + 0.5
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke()
    if (x > 40 && x < W - 40) ctx.fillText(degreeLabel(lon, 'E', 'W', step), x, H - 4)
  }
  const firstLat = Math.ceil(toLat(H) / step) * step
  ctx.textAlign = 'left'
  ctx.textBaseline = 'bottom'
  for (let lat = firstLat; toY(lat) > 0; lat += step) {
    const y = Math.round(toY(lat)) + 0.5
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke()
    if (y > 24 && y < H - 30) ctx.fillText(degreeLabel(lat, 'N', 'S', step), 6, y - 2)
  }
  ctx.setLineDash([])

  // Screen bins: at least as wide as a cell, never under 2.5 px, so every
  // occupied cell is a mark the eye can find.
  const cellPx = cellDegrees * pxPerDegree
  const bin = Math.max(2.5, Math.min(cellPx, 9))
  const columns = Math.ceil(W / bin) + 1
  const gather = (source: Cells) => {
    const bins = new Map<number, number>()
    for (let i = 0; i < source.n; i += 1) {
      const x = toX(source.lon[i])
      const y = toY(source.lat[i])
      if (x < 0 || y < 0 || x >= W || y >= H) continue
      const key = Math.floor(y / bin) * columns + Math.floor(x / bin)
      bins.set(key, (bins.get(key) ?? 0) + source.count[i])
    }
    return bins
  }
  const paint = (bins: Map<number, number>, colorOf: (count: number) => string, size: number) => {
    const inset = (bin - size) / 2
    for (const [key, count] of bins) {
      ctx.fillStyle = colorOf(count)
      const x = (key % columns) * bin + inset
      const y = Math.floor(key / columns) * bin + inset
      ctx.fillRect(x, y, size, size)
    }
  }

  if (ghostCells && ghostCells.n > 0) {
    const ghostColor = GHOST[theme] ?? GHOST.light
    paint(gather(ghostCells), () => ghostColor, bin)
  }

  const bins = gather(cells)
  // Shade against the 99th percentile bin, not the single busiest one: one
  // nursery block otherwise leaves the rest of the city at the pale end.
  const counts = [...bins.values()].sort((a, b) => a - b)
  const top = counts.length ? counts[Math.min(counts.length - 1, Math.floor(counts.length * 0.99))] : 1
  // Square root, not log: a log scale puts every block with a dozen trees in
  // the dark half and a city mapped wall to wall reads as one slab.
  const ceiling = Math.max(top, 2)
  paint(bins, (count) => rampColor(theme, Math.sqrt(count / ceiling)), Math.max(1.6, bin - 0.6))

  drawn.value = cells.n > 0 || Boolean(ghostCells?.n)
  peakText.value = top >= 2 ? `${formatCount(top)}+` : 'more'

  if (props.markPeak && counts.length > 3) {
    let peakKey = -1
    let peakCount = 0
    for (const [key, count] of bins) {
      if (count > peakCount) { peakCount = count; peakKey = key }
    }
    if (peakKey >= 0) {
      peak.value = {
        x: (peakKey % columns) * bin + bin / 2,
        y: Math.floor(peakKey / columns) * bin + bin / 2,
        size: Math.max(18, bin * 3),
        countText: `${formatCount(peakCount)} trees`,
      }
    }
  }

  // Scale bar: the largest round distance that fits in about 110 px.
  const metresPerPx = 111_320 / pxPerDegree
  const length = [...NICE_METRES].reverse().find((m) => m / metresPerPx <= 110) ?? NICE_METRES[0]
  scale.value = {
    px: Math.round(length / metresPerPx),
    label: length >= 1000 ? `${length / 1000} km` : `${length} m`,
  }

  frame = { bin, columns, bins, toLat, toLon, cellMetres: (bin / pxPerDegree) * 111_320 }
}

// --- Hover and click -----------------------------------------------------------

function onMove(event: MouseEvent) {
  if (!frame || !wrapperRef.value) return
  const rect = wrapperRef.value.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const key = Math.floor(y / frame.bin) * frame.columns + Math.floor(x / frame.bin)
  const count = frame.bins.get(key)
  if (!count) {
    hover.value = null
    return
  }
  const lat = frame.toLat(y)
  const lng = frame.toLon(x)
  hover.value = {
    x: Math.min(rect.width - 150, Math.max(8, x + 12)),
    y: Math.min(rect.height - 62, Math.max(8, y + 12)),
    countText: `${formatCount(count)} ${count === 1 ? 'tree' : 'trees'}`,
    place: `${Math.abs(lat).toFixed(3)}°${lat >= 0 ? 'N' : 'S'}, ${Math.abs(lng).toFixed(3)}°${lng >= 0 ? 'E' : 'W'}`,
    lat,
    lng,
  }
}

function onClick() {
  if (!props.pickable || !hover.value) return
  emit('pick', { lat: hover.value.lat, lng: hover.value.lng })
}

// --- Lifecycle -----------------------------------------------------------------

let renderQueued = false
function scheduleRender() {
  if (renderQueued) return
  renderQueued = true
  void nextTick().then(() => {
    requestAnimationFrame(() => {
      renderQueued = false
      render()
    })
  })
}

watch([main.rows, ghost.rows, resolvedTheme, hasGhost], scheduleRender)

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  if (wrapperRef.value) {
    resizeObserver = new ResizeObserver(scheduleRender)
    resizeObserver.observe(wrapperRef.value)
  }
  scheduleRender()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})
</script>

<style scoped>
.tree-dot-map {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  flex: 1 1 auto;
  overflow: hidden;
}

.tree-dot-map--pickable {
  cursor: pointer;
}

.tree-dot-map__canvas {
  display: block;
  width: 100%;
  height: 100%;
}

.tree-dot-map__state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-style: italic;
  color: var(--color-muted);
  pointer-events: none;
}

.tree-dot-map__legend,
.tree-dot-map__scale,
.tree-dot-map__readout {
  position: absolute;
  font-size: 0.68rem;
  color: var(--color-muted);
  pointer-events: none;
}

.tree-dot-map__legend {
  top: 10px;
  right: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 7px 10px;
  border: 1px solid var(--journal-rule, var(--color-border));
  border-radius: 9px;
  background: rgba(var(--surface-rgb), 0.86);
}

.tree-dot-map__legend-title {
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: 0.6rem;
  color: var(--color-moss);
}

.tree-dot-map__ramp,
.tree-dot-map__ghost-key {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tree-dot-map__ramp-bar {
  width: 74px;
  height: 7px;
  border-radius: 2px;
}

.tree-dot-map__ghost-swatch {
  width: 12px;
  height: 7px;
  border-radius: 2px;
  background: rgba(var(--ink-rgb), 0.16);
}

.tree-dot-map__scale {
  right: 14px;
  bottom: 22px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.tree-dot-map__scale-bar {
  height: 5px;
  border: 1px solid rgba(var(--ink-rgb), 0.55);
  border-top: none;
}

.tree-dot-map__readout {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 6px 9px;
  border: 1px solid var(--journal-rule, var(--color-border));
  border-radius: 8px;
  background: rgba(var(--surface-rgb), 0.95);
  white-space: nowrap;
}

.tree-dot-map__readout strong {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-ink);
}

.tree-dot-map__hint {
  font-style: italic;
}

.tree-dot-map__peak {
  position: absolute;
  width: 0;
  height: 0;
  pointer-events: none;
}

.tree-dot-map__peak-ring {
  position: absolute;
  transform: translate(-50%, -50%);
  border: 1.5px solid var(--color-autumn);
  border-radius: 48% 52% 50% 50% / 52% 48% 52% 48%;
}

.tree-dot-map__peak-label {
  position: absolute;
  left: 14px;
  top: -22px;
  padding: 1px 6px;
  border-radius: 6px;
  background: rgba(var(--surface-rgb), 0.9);
  font-size: 0.68rem;
  font-style: italic;
  color: var(--color-autumn);
  white-space: nowrap;
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
