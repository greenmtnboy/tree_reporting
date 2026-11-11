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
      <span v-for="step in classes" :key="step.label" class="tree-dot-map__class">
        <span class="tree-dot-map__class-mark">
          <span :style="{ width: `${step.size}px`, height: `${step.size}px`, background: step.color }" />
        </span>
        <span>{{ step.label }}</span>
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
 * across all of them; TREE_DOT_MAP_QUERY). Cells are gathered into squares of
 * whole cells, a few pixels wide, and each square is one round dot whose size
 * and shade both step with its count: a sparse street is a trail of small pale
 * dots, a dense block a mass of large dark ones that touch.
 *
 * The squares are whole cells on the cells' own lattice, never screen pixels:
 * snapping 110 m cells to a pixel grid puts two cells in some squares and one
 * in their neighbours, which reads as density that is not there. The steps are
 * a few classes cut at quantiles of the squares, so each shade covers a real
 * share of the map and the legend can name its counts.
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
const classes = ref<Array<{ label: string; color: string; size: number }>>([])
const legendTitle = ref('Trees per block')
const scale = ref<{ px: number; label: string } | null>(null)
const peak = ref<{ x: number; y: number; size: number; countText: string } | null>(null)
const hover = ref<{ x: number; y: number; countText: string; place: string; lat: number; lng: number } | null>(null)

// Light to dark on paper, dim to bright on the dark theme: more trees is
// always the mark that stands further from the page.
const RAMPS: Record<string, Array<[number, number, number]>> = {
  light: [[190, 211, 168], [136, 174, 118], [84, 136, 82], [40, 94, 56], [14, 48, 32]],
  dark: [[58, 86, 62], [98, 138, 92], [148, 186, 126], [198, 226, 162], [242, 249, 212]],
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

/** A round number near the value: a class edge a reader can hold. */
const EDGE_LEADS = [1, 1.5, 2, 3, 4, 5, 7.5, 10]
function roundEdge(value: number): number {
  if (value <= 1) return 1
  const power = 10 ** Math.floor(Math.log10(value))
  const lead = value / power
  const nearest = EDGE_LEADS.reduce((best, step) => (Math.abs(step - lead) < Math.abs(best - lead) ? step : best))
  return Math.max(2, Math.round(nearest * power))
}

/**
 * Lower edges of the classes above the first, from quantiles of the occupied
 * squares. Uneven on purpose: most squares hold few trees, and the top class
 * is kept to the densest few percent so it marks places, not regions.
 */
const CLASS_QUANTILES = [0.4, 0.7, 0.88, 0.97]
function classEdges(sorted: number[]): number[] {
  const edges: number[] = []
  for (const q of CLASS_QUANTILES) {
    if (!sorted.length) break
    const edge = roundEdge(sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))])
    if (edge > 1 && edge > (edges[edges.length - 1] ?? 1) && edge <= sorted[sorted.length - 1]) edges.push(edge)
  }
  return edges
}

function distanceLabel(metres: number): string {
  if (metres >= 950) return `${(metres / 1000).toFixed(metres >= 9500 ? 0 : 1).replace(/\.0$/, '')} km`
  return `${Math.round(metres / 10) * 10} m`
}

const NICE_DEGREES = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 45]
const NICE_METRES = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000, 2000000, 5000000]

function degreeLabel(value: number, positive: string, negative: string, step: number): string {
  const digits = step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3
  return `${Math.abs(value).toFixed(digits)}°${value >= 0 ? positive : negative}`
}

// What the last frame drew, kept for hover and click.
type Frame = {
  keyAt: (x: number, y: number) => number
  bins: Map<number, number>
  toLat: (y: number) => number
  toLon: (x: number) => number
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

  // Squares of whole cells: the finest that is still 4 px tall on screen, so
  // a dot has room to differ in size from its neighbour. That is one ~110 m
  // cell on a large card and two or three on a small one. A cell is narrower
  // on screen than it is tall away from the equator, so a square takes more of
  // them across than down.
  const MIN_PX = 4
  const cellPxY = cellDegrees * pxPerDegree
  const cellPxX = cellPxY * cosLat
  const cellsDown = Math.max(1, Math.ceil(MIN_PX / cellPxY))
  const cellsAcross = Math.max(1, Math.round((cellsDown * cellPxY) / cellPxX))
  const squareW = cellsAcross * cellPxX
  const squareH = cellsDown * cellPxY
  const square = Math.min(squareW, squareH)
  const KEY_SPAN = 1_000_000
  const column = (lon: number) => Math.floor(Math.round(lon / cellDegrees) / cellsAcross)
  const row = (lat: number) => Math.floor(Math.round(lat / cellDegrees) / cellsDown)
  const keyOf = (lat: number, lon: number) => (row(lat) + KEY_SPAN / 2) * KEY_SPAN + column(lon) + KEY_SPAN / 2
  // The middle of a square: its first cell's centre plus half the cells after it.
  const centreX = (key: number) => toX(((key % KEY_SPAN) - KEY_SPAN / 2 + (cellsAcross - 1) / 2 / cellsAcross) * cellsAcross * cellDegrees)
  const centreY = (key: number) => toY((Math.floor(key / KEY_SPAN) - KEY_SPAN / 2 + (cellsDown - 1) / 2 / cellsDown) * cellsDown * cellDegrees)

  const gather = (source: Cells) => {
    const bins = new Map<number, number>()
    for (let i = 0; i < source.n; i += 1) {
      const key = keyOf(source.lat[i], source.lon[i])
      bins.set(key, (bins.get(key) ?? 0) + source.count[i])
    }
    return bins
  }
  // One path per colour: a fill per dot is the slow way to draw ten thousand.
  // `reach` is the dot's radius as a share of its square, applied to each axis:
  // whole cells rarely make an exact square, and a round dot in an oblong one
  // leaves stripes of paper between columns.
  const paint = (keys: number[], color: string, reach: number) => {
    const rx = Math.max(0.9, squareW * reach)
    const ry = Math.max(0.9, squareH * reach)
    const radius = Math.max(rx, ry)
    ctx.fillStyle = color
    ctx.beginPath()
    for (const key of keys) {
      const x = centreX(key)
      const y = centreY(key)
      if (x < -radius || y < -radius || x > W + radius || y > H + radius) continue
      ctx.moveTo(x + rx, y)
      ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2)
    }
    ctx.fill()
  }

  if (ghostCells && ghostCells.n > 0) {
    paint([...gather(ghostCells).keys()], GHOST[theme] ?? GHOST.light, 0.42)
  }

  const bins = gather(cells)
  const counts = [...bins.values()].sort((a, b) => a - b)
  const edges = classEdges(counts)
  const steps = edges.length + 1
  // Smallest dots leave paper between them; the largest overlap their
  // neighbours, so the densest ground closes up into a solid mass.
  const reachOf = (step: number) => (steps === 1 ? 0.42 : 0.26 + (0.32 * step) / (steps - 1))
  const colorOf = (step: number) => rampColor(theme, steps === 1 ? 0.6 : step / (steps - 1))
  const byStep: number[][] = Array.from({ length: steps }, () => [])
  for (const [key, count] of bins) {
    let step = 0
    while (step < edges.length && count >= edges[step]) step += 1
    byStep[step].push(key)
  }
  // Palest first, so a dense dot is never hidden under a sparse neighbour.
  byStep.forEach((keys, step) => paint(keys, colorOf(step), reachOf(step)))

  drawn.value = cells.n > 0 || Boolean(ghostCells?.n)
  classes.value = counts.length
    ? byStep.map((_, step) => {
        const low = step === 0 ? 1 : edges[step - 1]
        const high = step < edges.length ? edges[step] - 1 : null
        return {
          label: high == null ? `${formatCount(low)}+` : high === low ? formatCount(low) : `${formatCount(low)}–${formatCount(high)}`,
          color: colorOf(step),
          // Drawn larger than on the map, in the same proportions, to be readable.
          size: Math.round(22 * reachOf(step)),
        }
      })
    : []
  legendTitle.value = `Trees per ${distanceLabel(cellsDown * cellDegrees * 111_320)} square`

  if (props.markPeak && counts.length > 3) {
    let peakKey = -1
    let peakCount = 0
    for (const [key, count] of bins) {
      if (count > peakCount) { peakCount = count; peakKey = key }
    }
    if (peakKey >= 0) {
      peak.value = {
        x: centreX(peakKey),
        y: centreY(peakKey),
        size: Math.max(18, square * 3),
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

  frame = { keyAt: (x, y) => keyOf(toLat(y), toLon(x)), bins, toLat, toLon }
}

// --- Hover and click -----------------------------------------------------------

function onMove(event: MouseEvent) {
  if (!frame || !wrapperRef.value) return
  const rect = wrapperRef.value.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const count = frame.bins.get(frame.keyAt(x, y))
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

.tree-dot-map__class,
.tree-dot-map__ghost-key {
  display: flex;
  align-items: center;
  gap: 6px;
  font-variant-numeric: tabular-nums;
}

.tree-dot-map__class-mark {
  width: 12px;
  height: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.tree-dot-map__class-mark > span {
  border-radius: 50%;
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
