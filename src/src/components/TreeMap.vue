<template>
  <div ref="mapContainer" class="tree-map"></div>
  <div v-if="isInitialLoading" class="map-loading">{{ loadingMessage }}</div>
  <div v-if="displayError" class="map-error">{{ displayError }}</div>
  <div v-if="!isInitialLoading && legendEntries.length" class="map-legend">
    <div v-for="entry in legendEntries" :key="entry.color" class="legend-entry">
      <span class="legend-swatch" :style="{ background: entry.color }"></span>
      <span class="legend-label">{{ entry.label }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, computed } from 'vue'
import maplibregl from 'maplibre-gl'
import { useTreeData } from '../composables/useTreeData'
import { registerTreeIcons, registerCategoryColoredIcons, CATEGORY_COLORS } from '../composables/useTreeCategories'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData } from '../composables/useMapData'
import { useDuckDB } from '../composables/useDuckDB'
import { useMapIntro } from '../composables/useMapIntro'
import type { TreeCategory } from '../types'

const props = defineProps<{
  simplified?: boolean
}>()

const mapContainer = ref<HTMLDivElement>()
const zoomLevel = ref(13)
const mapError = ref<string | null>(null)
const defaultQueryLoading = ref(true)
const introActive = ref(!props.simplified)
const { setIntroComplete } = useMapIntro()
if (props.simplified) setIntroComplete()
const loadingMessage = ref('Counting our conifers...')
let map: maplibregl.Map | null = null
let mapInitStartedAt = 0
let mapQueryChangedAt = 0
let firstTreesSourceLoadedLogged = false
let firstMapIdleAfterPublishLogged = false
let treeInteractionsBound = false
let lastIconDebugAt = 0
let prewarmStartedForRevision = -1
let introStarted = false
let introRafId: number | null = null
let introCancelled = false
let activeTreePopup: maplibregl.Popup | null = null
let popupRequestToken = 0
let treesSourceReloadNonce = 0
let zoomControlLabelEl: HTMLDivElement | null = null
let pendingSwoopFlyTimeout: number | null = null
const lastVisibleRangeSigByZoom = new Map<number, string>()
const introLockedRangeByZoom = new Map<number, { minX: number; maxX: number; minY: number; maxY: number }>()

const {
  query: duckQuery,
  ensureTileProtocolRegistered,
  setTileQuery,
  setPublishedTreeIdFilterSql,
  setColorOverrideSql,
  setViewportZoom,
  setViewportCenter,
  setVisibleTileRange,
  prefetchVisibleDetailTilesAtZoom,
  prewarmLodCaches,
  setAutoTileFetchEnabled,
  workerDistinctColors,
  workerColorLabelMap,
} = useDuckDB()
const { categoryIcons, loading, error, getSpeciesEnrichment } = useTreeData()
const { target: flyToTarget } = useFlyTo()
const { currentMapQuery, publishedTreeIdFilterSql, colorOverrideSql, colorLabelMap, mapQueryRevision, publishMapQuery } = useMapData()
const displayError = computed(() => error.value ?? mapError.value)
const isInitialLoading = computed(() => loading.value || defaultQueryLoading.value || introActive.value)

// Legend: derived from the worker's active color label map (updated when color map changes).
// When the agent provides a colorLabelMap via useMapData, prefer that; otherwise use the worker's.
const legendEntries = computed(() => {
  const labelMap = colorLabelMap.value ?? workerColorLabelMap.value
  if (!labelMap || Object.keys(labelMap).length === 0) return []
  return Object.entries(labelMap)
    .filter(([color]) => color !== '#66BB6A') // Hide "Other" from legend
    .map(([color, label]) => ({ color, label }))
})

// Active distinct colors from the worker, used to build heatmap layers dynamically.
const activeHeatmapColors = computed(() => {
  return workerDistinctColors.value.length > 0 ? workerDistinctColors.value : [...CATEGORY_COLORS]
})

const MAX_ZOOM = 19

const INTRO_CENTER: [number, number] = [-122.4194, 37.7749]
const INTRO_START_ZOOM = 18.5
const INTRO_END_ZOOM = 13.5
const INTRO_DURATION_MS = 10_000
const INTRO_ROTATION_DEG = 240
const INITIAL_TILE_PREFETCH_SCALE = 3.5
const TREES_SOURCE_MAXZOOM = 16
const SCROLL_WHEEL_ZOOM_RATE = 1 / 5800
const SCROLL_ZOOM_RATE = 1 / 400
const CENTER_ICON_GRID_RADIUS_PX = 96
const CENTER_ICON_GRID_SIZE = 3
const CENTER_ICON_GRID_MIN_POPULATED_CELLS = 5
const CENTER_ICON_GRID_MIN_TOTAL_ICONS = 8
const VIEWPORT_TREE_MIN_FEATURES = 1
const VIEWPORT_TREE_STABLE_FRAMES = 2
const TREE_CATEGORIES: TreeCategory[] = ['palm', 'broadleaf', 'spreading', 'coniferous', 'columnar', 'ornamental', 'default']
let lastBuiltHeatmapColors: string[] = []

// Centralized zoom pivot points for layer transitions/sizing.
const HEATMAP_ZOOM_INTENSITY_START = 10
const HEATMAP_ZOOM_INTENSITY_MID = 13
const HEATMAP_ZOOM_INTENSITY_END = 15
const HEATMAP_ZOOM_RADIUS_START = 10
const HEATMAP_ZOOM_RADIUS_MID = 13
const HEATMAP_ZOOM_RADIUS_END = 15
const HEATMAP_ZOOM_OPACITY_START = HEATMAP_ZOOM_RADIUS_START
const HEATMAP_ZOOM_OPACITY_MID = HEATMAP_ZOOM_RADIUS_MID
const HEATMAP_ZOOM_OPACITY_END = HEATMAP_ZOOM_RADIUS_END

const CIRCLE_ZOOM_MIN = 12.8
const CIRCLE_ZOOM_RADIUS_MID = 15
const CIRCLE_ZOOM_RADIUS_HIGH = 18
const CIRCLE_ZOOM_RADIUS_MAX = 20
const CIRCLE_ZOOM_MAX = 15.5
const CIRCLE_ZOOM_OPACITY_START = CIRCLE_ZOOM_MIN
const CIRCLE_ZOOM_OPACITY_MID = CIRCLE_ZOOM_RADIUS_MID
const CIRCLE_ZOOM_OPACITY_END = CIRCLE_ZOOM_MAX

const ICON_ZOOM_MIN = 14.4
const ICON_ZOOM_SIZE_MID = 15
const ICON_ZOOM_SIZE_HIGH = 18
const ICON_ZOOM_SIZE_MAX = 20
const ICON_ZOOM_OPACITY_START = ICON_ZOOM_MIN
const ICON_ZOOM_OPACITY_MID = ICON_ZOOM_SIZE_MID
const ICON_ZOOM_OPACITY_END = ICON_ZOOM_SIZE_MAX

type IntroPrefetchStatus = 'executed' | 'deduped' | 'skipped'
type IntroPrefetchCounters = { requested: number; executed: number; deduped: number; skipped: number }

const introPrefetchStatsByZoom = new Map<number, IntroPrefetchCounters>()

function resetIntroPrefetchStats() {
  introPrefetchStatsByZoom.clear()
}

function recordIntroPrefetchStatus(z: number, status: IntroPrefetchStatus) {
  const current = introPrefetchStatsByZoom.get(z) ?? {
    requested: 0,
    executed: 0,
    deduped: 0,
    skipped: 0,
  }
  current.requested += 1
  if (status === 'executed') current.executed += 1
  else if (status === 'deduped') current.deduped += 1
  else current.skipped += 1
  introPrefetchStatsByZoom.set(z, current)
}

function logIntroPrefetchSummary() {
  const rows = Array.from(introPrefetchStatsByZoom.entries())
    .sort((a, b) => b[0] - a[0])
    .map(([z, s]) => ({ z, ...s }))

  const totals = rows.reduce(
    (acc, row) => {
      acc.requested += row.requested
      acc.executed += row.executed
      acc.deduped += row.deduped
      acc.skipped += row.skipped
      return acc
    },
    { requested: 0, executed: 0, deduped: 0, skipped: 0 },
  )

  console.info('[Perf] map:intro-prefetch-summary', { totals, byZoom: rows })
}

function setMapInteractions(enabled: boolean) {
  if (!map) return
  const action = enabled ? 'enable' : 'disable'
  map.boxZoom[action]()
  map.doubleClickZoom[action]()
  map.dragPan[action]()
  map.dragRotate[action]()
  map.keyboard[action]()
  map.scrollZoom[action]()
  map.touchZoomRotate[action]()
  map.touchPitch[action]()
}

async function waitForTreesMilestone(timeoutMs = 2500): Promise<void> {
  if (!map) return
  if (map.isSourceLoaded('trees')) return

  await new Promise<void>((resolve) => {
    if (!map) return resolve()
    let done = false
    const finish = () => {
      if (done) return
      done = true
      map?.off('sourcedata', onSourceData)
      clearTimeout(timer)
      resolve()
    }
    const onSourceData = (e: any) => {
      if (e?.sourceId === 'trees' && e?.isSourceLoaded) finish()
    }
    const timer = setTimeout(finish, timeoutMs)
    map.on('sourcedata', onSourceData)
  })
}

async function waitForInitialDetailedTrees(timeoutMs = 5000): Promise<boolean> {
  if (!map) return false
  const startedAt = nowMs()

  return await new Promise<boolean>((resolve) => {
    if (!map) return resolve(false)

    const tick = () => {
      if (!map) return resolve(false)

      const iconCount = map.queryRenderedFeatures(undefined, { layers: ['trees-icon'] }).length
      if (iconCount > 0) return resolve(true)

      if (nowMs() - startedAt >= timeoutMs) return resolve(false)
      requestAnimationFrame(tick)
    }

    tick()
  })
}

async function waitForCenteredDetailedTrees(targetCenter: [number, number], timeoutMs = 5000): Promise<boolean> {
  if (!map) return false
  const startedAt = nowMs()

  return await new Promise<boolean>((resolve) => {
    if (!map) return resolve(false)

    const tick = () => {
      if (!map) return resolve(false)

      const centerPx = map.project(targetCenter)
      const gridSize = Math.max(1, CENTER_ICON_GRID_SIZE)
      const radiusPx = CENTER_ICON_GRID_RADIUS_PX
      const minX = centerPx.x - radiusPx
      const minY = centerPx.y - radiusPx
      const cellSize = (radiusPx * 2) / gridSize
      const centerIndex = Math.floor(gridSize / 2)

      let totalIcons = 0
      let populatedCells = 0
      let centerCellIcons = 0

      for (let row = 0; row < gridSize; row += 1) {
        for (let col = 0; col < gridSize; col += 1) {
          const cellMinX = minX + col * cellSize
          const cellMinY = minY + row * cellSize
          const cellMaxX = cellMinX + cellSize
          const cellMaxY = cellMinY + cellSize
          const cellIcons = map.queryRenderedFeatures(
            [
              [cellMinX, cellMinY],
              [cellMaxX, cellMaxY],
            ],
            { layers: ['trees-icon'] },
          ).length

          totalIcons += cellIcons
          if (cellIcons > 0) populatedCells += 1
          if (row === centerIndex && col === centerIndex) {
            centerCellIcons = cellIcons
          }
        }
      }

      const centerReady = centerCellIcons > 0
      const gridReady = populatedCells >= CENTER_ICON_GRID_MIN_POPULATED_CELLS
      const densityReady = totalIcons >= CENTER_ICON_GRID_MIN_TOTAL_ICONS
      if (centerReady && gridReady && densityReady) return resolve(true)
      if (nowMs() - startedAt >= timeoutMs) return resolve(false)
      requestAnimationFrame(tick)
    }

    tick()
  })
}

async function waitForViewportTreesRendered(timeoutMs = 6000): Promise<boolean> {
  if (!map) return false
  const startedAt = nowMs()

  return await new Promise<boolean>((resolve) => {
    if (!map) return resolve(false)
    let stableFrames = 0

    const tick = () => {
      if (!map) return resolve(false)

      const canvas = map.getCanvas()
      const width = Math.max(1, canvas.clientWidth)
      const height = Math.max(1, canvas.clientHeight)
      const features = map.queryRenderedFeatures(
        [
          [0, 0],
          [width, height],
        ],
        { layers: ['trees-icon', 'trees-circle'] },
      )
      const viewportTreeCount = features.length

      const sourceReady = map.isSourceLoaded('trees')
      const frameReady = sourceReady && viewportTreeCount >= VIEWPORT_TREE_MIN_FEATURES

      if (frameReady) {
        stableFrames += 1
        if (stableFrames >= VIEWPORT_TREE_STABLE_FRAMES) return resolve(true)
      } else {
        stableFrames = 0
      }

      if (nowMs() - startedAt >= timeoutMs) return resolve(false)
      requestAnimationFrame(tick)
    }

    tick()
  })
}

function computeVisibleTileRangeForZoom(z: number): { minX: number; maxX: number; minY: number; maxY: number } | null {
  if (!map) return null
  const bounds = map.getBounds()
  const clampLat = (lat: number) => Math.max(-85.05112878, Math.min(85.05112878, lat))
  const n = Math.pow(2, z)
  const lonToTileX = (lon: number) => Math.floor(((lon + 180) / 360) * n)
  const latToTileY = (lat: number) => {
    const latRad = (clampLat(lat) * Math.PI) / 180
    return Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n)
  }

  const minX = Math.max(0, Math.min(n - 1, lonToTileX(bounds.getWest())))
  const maxX = Math.max(0, Math.min(n - 1, lonToTileX(bounds.getEast())))
  const minY = Math.max(0, Math.min(n - 1, latToTileY(bounds.getNorth())))
  const maxY = Math.max(0, Math.min(n - 1, latToTileY(bounds.getSouth())))
  return { minX, maxX, minY, maxY }
}

function requestTreesSourceReload() {
  if (!map) return
  const source = map.getSource('trees') as any
  if (source && typeof source.reload === 'function') {
    source.reload()
  }
  map.triggerRepaint()
}

function forceTreesTileRefetchPass() {
  treesSourceReloadNonce += 1
  addTreeLayers()
  map?.triggerRepaint()
}

function runIntroZoomSegment(
  fromZoom: number,
  toZoom: number,
  fromT: number,
  toT: number,
  durationMs: number,
  startBearing: number,
  pitch: number,
  center: [number, number],
  rotationDeg: number,
  onProgress?: (globalT: number) => void,
): Promise<void> {
  return new Promise((resolve) => {
    if (!map) return resolve()
    const baseLng = center[0]
    const baseLat = center[1]
    const segmentStart = nowMs()

    const step = () => {
      if (!map || introCancelled) {
        introRafId = null
        return resolve()
      }

      const local = Math.max(0, Math.min(1, (nowMs() - segmentStart) / durationMs))
      const easedLocal = local * local * (3 - 2 * local)
      const globalT = fromT + (toT - fromT) * easedLocal
      onProgress?.(globalT)

      const zoom = fromZoom + (toZoom - fromZoom) * easedLocal
      const bearing = startBearing + rotationDeg * globalT
      const angle = globalT * Math.PI * 2
      // Start/end at exact center to avoid a visible hop.
      const radiusDeg = 0.0012 * Math.sin(globalT * Math.PI)
      const lng = baseLng + (Math.cos(angle) * radiusDeg) / Math.max(0.2, Math.cos((baseLat * Math.PI) / 180))
      const lat = baseLat + Math.sin(angle) * radiusDeg

      map.jumpTo({
        center: [lng, lat],
        zoom,
        bearing,
        pitch,
      })

      if (local < 1) {
        introRafId = requestAnimationFrame(step)
        return
      }
      introRafId = null
      resolve()
    }

    introRafId = requestAnimationFrame(step)
  })
}

async function runIntroZoomOut() {
  if (!map || introStarted) return
  introStarted = true
  introCancelled = false
  introActive.value = true
  resetIntroPrefetchStats()
  setAutoTileFetchEnabled(false)
  setMapInteractions(false)

  const startBearing = map.getBearing()
  const startPitch = map.getPitch()
  const introCenter: [number, number] = [INTRO_CENTER[0], INTRO_CENTER[1]]
  const introStartZoom = INTRO_START_ZOOM
  const introEndZoom = INTRO_END_ZOOM
  const introRotationDeg = INTRO_ROTATION_DEG
  const introDurationMs = INTRO_DURATION_MS
  let didRunIntroMotion = false

  try {
    // Re-anchor to the exact intro start pose first, then wait for centered
    // detailed icons before beginning motion.
    map.jumpTo({
      center: introCenter,
      zoom: introStartZoom,
      bearing: startBearing,
      pitch: startPitch,
    })

    // Publish initial visible ranges before prefetching.
    updateZoomLevel()

    // Hard gate intro movement until detailed tiles are actually rendered at
    // the starting viewpoint.
    const sourceStartZoom = Math.min(TREES_SOURCE_MAXZOOM, Math.round(introStartZoom))
    const prefetchZooms = Array.from(new Set([sourceStartZoom, Math.max(15, sourceStartZoom - 1)]))
    for (const z of prefetchZooms) {
      if (z < 15) continue
      const range = computeVisibleTileRangeForZoom(z)
      if (range) {
        introLockedRangeByZoom.set(z, range)
        setVisibleTileRange(z, range.minX, range.maxX, range.minY, range.maxY)
      }
      const status = await prefetchVisibleDetailTilesAtZoom(z, range ?? undefined)
      recordIntroPrefetchStatus(z, status)
    }

    // During intro auto-fetch is disabled; force a source reload pass so
    // MapLibre re-requests viewport tiles and consumes freshly prefetched
    // cached data instead of stale empty responses.
    requestTreesSourceReload()
    forceTreesTileRefetchPass()

    await waitForTreesMilestone(2500)
    const initialReady = await waitForInitialDetailedTrees(5000)
    const centeredReady = await waitForCenteredDetailedTrees(introCenter, 5000)
    const viewportReady = await waitForViewportTreesRendered(6000)

    const canStartMotion = initialReady || centeredReady || viewportReady

    if (!introCancelled && canStartMotion) {
      loadingMessage.value = 'Tracking seed dispersion...'
      let didSetFinalPhase = false
      await runIntroZoomSegment(
        introStartZoom,
        introEndZoom,
        0,
        1,
        introDurationMs,
        startBearing,
        startPitch,
        introCenter,
        introRotationDeg,
        (globalT) => {
          if (!didSetFinalPhase && globalT >= 0.72) {
            didSetFinalPhase = true
            loadingMessage.value = 'Reticulating splines...'
          }
        },
      )
      didRunIntroMotion = true
    } else if (!introCancelled) {
      console.warn('[Perf] map:intro-gate:blocked-motion', {
        canStartMotion,
        initialReady,
        centeredReady,
        viewportReady,
      })
    }
  } finally {
    setAutoTileFetchEnabled(true)
    if (map && didRunIntroMotion) {
      map.jumpTo({
        center: introCenter,
        zoom: introEndZoom,
        bearing: startBearing + introRotationDeg,
        pitch: startPitch,
      })
    }
    introActive.value = false
    setIntroComplete()
    introLockedRangeByZoom.clear()
    logIntroPrefetchSummary()
    setMapInteractions(true)
  }
}

const DEFAULT_MAP_QUERY = `
SELECT
  tree_id,
  species,
  latitude,
  longitude,
  diameter_at_breast_height
FROM trees
WHERE latitude IS NOT NULL AND longitude IS NOT NULL
`

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function isIconLayerDebugEnabled(): boolean {
  if (typeof window === 'undefined') return false
  return window.location.search.includes('debugIcons=1')
}

function logIconLayerDebug(message: string, payload: Record<string, unknown>) {
  if (!isIconLayerDebugEnabled()) return
  console.info(`[IconLayer] ${message}`, payload)
}

function logIconLayerSnapshot(reason: string) {
  if (!map || !isIconLayerDebugEnabled()) return
  const now = Date.now()
  if (now - lastIconDebugAt < 400) return
  lastIconDebugAt = now

  const zoom = map.getZoom()
  const iconLayerExists = !!map.getLayer('trees-icon')
  const circleLayerExists = !!map.getLayer('trees-circle')
  const heatLayerExists = !!map.getLayer('trees-heat')
  const iconFeatures = iconLayerExists ? map.queryRenderedFeatures(undefined, { layers: ['trees-icon'] }).length : 0
  const circleFeatures = circleLayerExists ? map.queryRenderedFeatures(undefined, { layers: ['trees-circle'] }).length : 0
  const heatFeatures = heatLayerExists ? map.queryRenderedFeatures(undefined, { layers: ['trees-heat'] }).length : 0
  const iconOpacity = iconLayerExists ? map.getPaintProperty('trees-icon', 'icon-opacity') : null
  const circleOpacity = circleLayerExists ? map.getPaintProperty('trees-circle', 'circle-opacity') : null

  logIconLayerDebug('snapshot', {
    reason,
    zoom: Number(zoom.toFixed(2)),
    iconLayerExists,
    circleLayerExists,
    heatLayerExists,
    iconFeatures,
    circleFeatures,
    heatFeatures,
    iconOpacity,
    circleOpacity,
  })
}

// Circle color: use the per-tree display_color from the tile directly.
function buildCircleColorExpression(): maplibregl.ExpressionSpecification {
  return ['coalesce', ['get', 'display_color'], '#66BB6A'] as maplibregl.ExpressionSpecification
}

// Icon: use category shape with display_color coloring.
// Icon name format: tree-{category}-{hex}
function buildIconExpression(): maplibregl.ExpressionSpecification {
  return [
    'concat', 'tree-', ['coalesce', ['get', 'category'], 'default'], '-', ['coalesce', ['get', 'display_color'], '#66BB6A'],
  ] as unknown as maplibregl.ExpressionSpecification
}

function buildHeatmapColorExpression(hexColor: string): maplibregl.ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    ['heatmap-density'],
    0, 'rgba(0,0,0,0)',
    0.05, 'rgba(0,0,0,0)',
    0.15, hexColor,
    0.55, hexColor,
    1, hexColor,
  ] as maplibregl.ExpressionSpecification
}

function buildSqrtDbhExpression(minValue: number, maxValue: number): maplibregl.ExpressionSpecification {
  const maxDbh = 42
  const sqrtMaxDbh = Math.sqrt(maxDbh)
  return [
    'interpolate',
    ['linear'],
    ['sqrt', ['coalesce', ['to-number', ['get', 'dbh']], 3]],
    0,
    minValue,
    sqrtMaxDbh,
    maxValue,
  ] as maplibregl.ExpressionSpecification
}

function buildCircleRadiusExpression(): maplibregl.ExpressionSpecification {
  return [
    'interpolate', ['linear'], ['zoom'],
    CIRCLE_ZOOM_MIN, buildSqrtDbhExpression(0.65, 1),
    CIRCLE_ZOOM_RADIUS_MID, buildSqrtDbhExpression(2.1, 5.6),
    CIRCLE_ZOOM_RADIUS_HIGH, buildSqrtDbhExpression(3.1, 9.5),
    CIRCLE_ZOOM_RADIUS_MAX, buildSqrtDbhExpression(3.7, 11.6),
  ] as maplibregl.ExpressionSpecification
}

function buildIconSizeExpression(): maplibregl.ExpressionSpecification {
  return [
    'interpolate', ['linear'], ['zoom'],
    // Match apparent size with circle layer around z15 to avoid pop.
    ICON_ZOOM_MIN, buildSqrtDbhExpression(0.04, 0.1),
    ICON_ZOOM_SIZE_MID, buildSqrtDbhExpression(0.055, 0.15),
    ICON_ZOOM_SIZE_HIGH, buildSqrtDbhExpression(0.2, 0.72),
    ICON_ZOOM_SIZE_MAX, buildSqrtDbhExpression(0.28, 1.0),
  ] as maplibregl.ExpressionSpecification
}

function formatPopupHtml(row: any, enrichment?: ReturnType<typeof getSpeciesEnrichment>): string {
  const planted = row.plant_date?.split(' ')[0] ?? null
  const evergreen = enrichment?.is_evergreen == null ? null : (enrichment.is_evergreen ? 'Yes' : 'No')
  const category = enrichment?.tree_category
    ? enrichment.tree_category.charAt(0).toUpperCase() + enrichment.tree_category.slice(1)
    : null
  const detailLines = [
    ['ID', row.tree_id],
    ['Category', category],
    ['Planted', planted],
    ['Trunk diameter', row.diameter_at_breast_height != null ? `${row.diameter_at_breast_height}"` : null],
    ['Site', row.site_info],
    ['Native', enrichment?.native_status],
    ['Evergreen', evergreen],
    ['Mature height', enrichment?.mature_height_ft != null ? `${enrichment.mature_height_ft} ft` : null],
    ['Bloom', enrichment?.bloom_season],
    ['Wildlife value', enrichment?.wildlife_value],
    ['Fire risk', enrichment?.fire_risk],
  ]
    .filter(([, value]) => value != null && value !== '' && value !== 'Unknown')
    .map(([label, value]) => `${label}: ${value}<br/>`)
    .join('')

  return `
    <strong>${row.common_name || 'Unknown tree'}</strong><br/>
    <em>${row.species || ''}</em><br/>
    ${detailLines}
  `
}

async function loadDefaultMapData() {
  if (!currentMapQuery.value) {
    publishMapQuery(DEFAULT_MAP_QUERY)
    return
  }

  loadingMessage.value = 'Counting our conifers...'
  mapQueryChangedAt = nowMs()
  firstTreesSourceLoadedLogged = false
  firstMapIdleAfterPublishLogged = false
  defaultQueryLoading.value = true
  lastVisibleRangeSigByZoom.clear()
  introLockedRangeByZoom.clear()
  await setTileQuery(currentMapQuery.value)
  await setPublishedTreeIdFilterSql(publishedTreeIdFilterSql.value)
  addTreeLayers()
}

async function showTreePopup(feature: GeoJSON.Feature, offset: number) {
  if (!map) return
  const requestToken = ++popupRequestToken
  const coords = (feature.geometry as GeoJSON.Point).coordinates.slice() as [number, number]
  const id = Number(feature.properties?.id)
  if (!Number.isFinite(id) || id <= 0) return

  try {
    const { rows } = await duckQuery(`
      SELECT tree_id, common_name, species, plant_date, site_info, diameter_at_breast_height
      FROM trees
      WHERE tree_id = ${id}
      LIMIT 1
    `)
    const row = rows[0] as any
    if (!row) return
    if (requestToken !== popupRequestToken) return
    const enrichment = getSpeciesEnrichment(row.species)

    if (activeTreePopup) {
      activeTreePopup.remove()
      activeTreePopup = null
    }

    const popup = new maplibregl.Popup({ offset, className: 'tree-popup' })
      .setLngLat(coords)
      .setHTML(formatPopupHtml(row, enrichment))
      .addTo(map)

    activeTreePopup = popup
    popup.on('close', () => {
      if (activeTreePopup === popup) activeTreePopup = null
    })
  } catch (e) {
    console.error('[Popup Query Error]', e)
  }
}

function addTreeLayers() {
  if (!map) return
  const mapInstance = map
  const t0 = nowMs()
  console.info('[Perf] map:layers:add:start')
  const treeTiles = [`duckdb://trees/{z}/{x}/{y}.pbf?r=${mapQueryRevision.value}&n=${treesSourceReloadNonce}`]
  const heatColors = activeHeatmapColors.value
  const heatLayerIds = heatColors.map((hex) => `trees-heat-${hex}`)

  const existingSource = mapInstance.getSource('trees') as any
  // Check if heatmap layer IDs match the currently active colors
  const hasHeatLayers = heatLayerIds.length > 0 && heatLayerIds.every((id) => !!mapInstance.getLayer(id))
  const hasCircleLayer = !!mapInstance.getLayer('trees-circle')
  const hasIconLayer = !!mapInstance.getLayer('trees-icon')

  // Prefer in-place source URL refresh to avoid tearing down layers, which can
  // cause visible pop during LOD transitions and query revision updates.
  const hasAllLayers = hasHeatLayers && hasCircleLayer && (props.simplified || hasIconLayer)
  if (existingSource && hasAllLayers) {
    if (typeof existingSource.setTiles === 'function') {
      existingSource.setTiles(treeTiles)
      if (typeof existingSource.reload === 'function') {
        existingSource.reload()
      }
      console.info('[Perf] map:layers:source-refresh', {
        ms: Math.round(nowMs() - t0),
        revision: mapQueryRevision.value,
      })
      return
    }
  }

  if (mapInstance.getLayer('trees-icon')) mapInstance.removeLayer('trees-icon')
  if (mapInstance.getLayer('trees-circle')) mapInstance.removeLayer('trees-circle')
  // Remove old heatmap layers (both category-based and color-based)
  removeOldHeatmapLayers(mapInstance)
  if (mapInstance.getSource('trees')) mapInstance.removeSource('trees')

  mapInstance.addSource('trees', {
    type: 'vector',
    tiles: treeTiles,
    minzoom: 0,
    // Pin detailed requests at z16 and let MapLibre overzoom above that.
    // This reduces query churn/pop when moving through z17+.
    maxzoom: TREES_SOURCE_MAXZOOM,
  })

  // Layer 1: Heatmaps per active display_color.
  for (const hexColor of heatColors) {
    mapInstance.addLayer({
      id: `trees-heat-${hexColor}`,
      type: 'heatmap',
      source: 'trees',
      'source-layer': 'trees',
      maxzoom: HEATMAP_ZOOM_OPACITY_END,
      filter: ['==', ['coalesce', ['get', 'display_color'], '#66BB6A'], hexColor],
      paint: {
        // Normalize count by grid area so heatmap color is consistent across
        // different aggregation tiers. Reference grid is 32m.
        'heatmap-weight': [
          'interpolate',
          ['linear'],
          [
            '*',
            ['coalesce', ['to-number', ['get', 'point_count']], 1],
            ['^', ['/', 32, ['coalesce', ['to-number', ['get', 'grid_m']], 32]], 2],
          ],
          1, 0.2,
          8, 0.45,
          32, 0.75,
          128, 1.05,
        ],
        'heatmap-intensity': [
          'interpolate', ['linear'], ['zoom'],
          HEATMAP_ZOOM_INTENSITY_START, 0.85,
          HEATMAP_ZOOM_INTENSITY_MID, 1.2,
          HEATMAP_ZOOM_INTENSITY_END, 1.9,
        ],
        'heatmap-radius': [
          'interpolate', ['linear'], ['zoom'],
          HEATMAP_ZOOM_RADIUS_START, 10,
          HEATMAP_ZOOM_RADIUS_MID, 14,
          HEATMAP_ZOOM_RADIUS_END, 22,
        ],
        'heatmap-color': buildHeatmapColorExpression(hexColor),
        'heatmap-opacity': [
          'interpolate', ['linear'], ['zoom'],
          HEATMAP_ZOOM_OPACITY_START, 0.48,
          HEATMAP_ZOOM_OPACITY_MID, 0.33,
          HEATMAP_ZOOM_OPACITY_END, 0,
        ],
      },
    })
  }
  lastBuiltHeatmapColors = [...heatColors]

  // Layer 2: Colored circles at medium zoom (extended to all zoom levels in simplified mode)
  mapInstance.addLayer({
    id: 'trees-circle',
    type: 'circle',
    source: 'trees',
    'source-layer': 'trees',
    minzoom: CIRCLE_ZOOM_MIN,
    ...(props.simplified ? {} : { maxzoom: CIRCLE_ZOOM_MAX }),
    paint: {
      'circle-radius': [
        ...buildCircleRadiusExpression(),
      ],
      'circle-color': buildCircleColorExpression(),
      'circle-opacity': props.simplified
        ? [
            'interpolate', ['linear'], ['zoom'],
            CIRCLE_ZOOM_OPACITY_START, 0,
            CIRCLE_ZOOM_OPACITY_START + 0.1, 0.85,
            MAX_ZOOM, 0.92,
          ] as any
        : [
            'interpolate', ['linear'], ['zoom'],
            CIRCLE_ZOOM_OPACITY_START, 0,
            CIRCLE_ZOOM_OPACITY_START+.1, 0.75,
            CIRCLE_ZOOM_OPACITY_MID, 0.92,
            CIRCLE_ZOOM_OPACITY_END - .1, .75,
            CIRCLE_ZOOM_OPACITY_END, 0,
          ],
      'circle-pitch-alignment': 'map',
      'circle-pitch-scale': 'map',
      'circle-stroke-width': 0.65,
      'circle-stroke-color': 'rgba(255,255,255,0)',
    },
  })

  // Layer 3: Tree icons at close zoom (skipped in simplified mode)
  if (!props.simplified) {
    // Register colored icon variants for all active colors before adding the icon layer.
    // Icon names are tree-{category}-{hex}, so colored variants must exist at render time.
    if (heatColors.length > 0) {
      registerCategoryColoredIcons(mapInstance, heatColors)
    }
    mapInstance.addLayer({
      id: 'trees-icon',
      type: 'symbol',
      source: 'trees',
      'source-layer': 'trees',
      minzoom: ICON_ZOOM_MIN,
      layout: {
        'icon-image': buildIconExpression(),
        'icon-size': [
          ...buildIconSizeExpression(),
        ],
        'icon-rotate': ['get', 'rotation'],
        'icon-rotation-alignment': 'viewport',
        'icon-pitch-alignment': 'viewport',
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
      paint: {
        'icon-opacity': [
          'interpolate', ['linear'], ['zoom'],
          ICON_ZOOM_OPACITY_START, 0,
          ICON_ZOOM_OPACITY_MID, 0.72,
          ICON_ZOOM_OPACITY_END, 1,
        ],
      },
    })
  }

  if (!treeInteractionsBound) {
    const interactiveLayers = props.simplified ? ['trees-circle'] : ['trees-icon', 'trees-circle']

    map.on('click', (e) => {
      if (!map) return
      const features = map.queryRenderedFeatures(e.point, { layers: interactiveLayers })
      if (!features.length) return

      const iconFeature = !props.simplified ? features.find((f) => f.layer?.id === 'trees-icon') : undefined
      const picked = (iconFeature ?? features[0]) as unknown as GeoJSON.Feature
      const offset = (iconFeature ?? features[0]).layer?.id === 'trees-icon' ? 15 : 8
      void showTreePopup(picked, offset)
    })

    for (const layer of interactiveLayers) {
      map.on('mouseenter', layer, () => { map!.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', layer, () => { map!.getCanvas().style.cursor = '' })
    }
    treeInteractionsBound = true
  }
  console.info('[Perf] map:layers:add:done', { ms: Math.round(nowMs() - t0) })
}

function ensureZoomControlLabel() {
  if (!map) return
  if (zoomControlLabelEl?.isConnected) return

  const container = map.getContainer()
  const zoomIn = container.querySelector('.maplibregl-ctrl-zoom-in') as HTMLButtonElement | null
  const zoomOut = container.querySelector('.maplibregl-ctrl-zoom-out') as HTMLButtonElement | null
  if (!zoomIn || !zoomOut) return

  const ctrlGroup = zoomIn.parentElement
  if (!ctrlGroup) return

  let label = ctrlGroup.querySelector('.maplibregl-ctrl-zoom-level') as HTMLDivElement | null
  if (!label) {
    label = document.createElement('div')
    label.className = 'maplibregl-ctrl-icon maplibregl-ctrl-zoom-level'
    label.setAttribute('aria-hidden', 'true')
    zoomIn.insertAdjacentElement('afterend', label)
  }

  zoomControlLabelEl = label
  zoomControlLabelEl.textContent = zoomLevel.value.toFixed(2)
}

function updateZoomLevel() {
  if (!map) return
  zoomLevel.value = map.getZoom()
  ensureZoomControlLabel()
  if (zoomControlLabelEl) {
    zoomControlLabelEl.textContent = zoomLevel.value.toFixed(2)
  }
  setViewportZoom(zoomLevel.value)
  const c = map.getCenter()
  setViewportCenter(c.lng, c.lat)

  const bounds = map.getBounds()
  const clampLat = (lat: number) => Math.max(-85.05112878, Math.min(85.05112878, lat))
  const computeRangeForZoom = (z: number) => {
    const n = Math.pow(2, z)
    const lonToTileX = (lon: number) => Math.floor(((lon + 180) / 360) * n)
    const latToTileY = (lat: number) => {
      const latRad = (clampLat(lat) * Math.PI) / 180
      return Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n)
    }

    let minX = Math.max(0, Math.min(n - 1, lonToTileX(bounds.getWest())))
    let maxX = Math.max(0, Math.min(n - 1, lonToTileX(bounds.getEast())))
    let minY = Math.max(0, Math.min(n - 1, latToTileY(bounds.getNorth())))
    let maxY = Math.max(0, Math.min(n - 1, latToTileY(bounds.getSouth())))

    if (introActive.value && z >= 15) {
      const locked = introLockedRangeByZoom.get(z)
      if (locked) {
        minX = locked.minX
        maxX = locked.maxX
        minY = locked.minY
        maxY = locked.maxY
      } else {
        introLockedRangeByZoom.set(z, { minX, maxX, minY, maxY })
      }
    }

    // Expanding range every animation frame during intro can create excessive
    // tile churn and stall startup. Only apply wide prefetch once intro ends.
    if (isInitialLoading.value && !introActive.value) {
      const width = Math.max(1, maxX - minX + 1)
      const height = Math.max(1, maxY - minY + 1)
      const extraX = Math.ceil((width * (INITIAL_TILE_PREFETCH_SCALE - 1)) / 2)
      const extraY = Math.ceil((height * (INITIAL_TILE_PREFETCH_SCALE - 1)) / 2)
      minX = Math.max(0, minX - extraX)
      maxX = Math.min(n - 1, maxX + extraX)
      minY = Math.max(0, minY - extraY)
      maxY = Math.min(n - 1, maxY + extraY)
    }

    return { minX, maxX, minY, maxY }
  }

  const z = Math.round(map.getZoom())
  const sourceZ = Math.min(TREES_SOURCE_MAXZOOM, z)
  if (sourceZ >= 13 && sourceZ <= 20) {
    const candidateZooms = new Set<number>([sourceZ])
    if (introActive.value && sourceZ >= 15) {
      // Intro path is a zoom-out, so prefetch current + next-coarser detailed
      // LOD only. Avoid warming finer zooms (z+1) which adds extra queries
      // (e.g. z20) without improving visible continuity.
      candidateZooms.add(Math.max(15, sourceZ - 1))
    }

    for (const targetZoom of candidateZooms) {
      if (targetZoom < 13 || targetZoom > 20) continue
      const { minX, maxX, minY, maxY } = computeRangeForZoom(targetZoom)
      const rangeSig = `${targetZoom}:${minX}-${maxX}:${minY}-${maxY}`
      if (rangeSig !== lastVisibleRangeSigByZoom.get(targetZoom)) {
        lastVisibleRangeSigByZoom.set(targetZoom, rangeSig)
        setVisibleTileRange(targetZoom, minX, maxX, minY, maxY)

        if (introActive.value && targetZoom >= 15) {
          void prefetchVisibleDetailTilesAtZoom(targetZoom, { minX, maxX, minY, maxY })
            .then((status) => {
              recordIntroPrefetchStatus(targetZoom, status)
            })
            .catch((err) => {
              console.warn('[Perf] map:intro-prefetch:failed', { z: targetZoom, err })
            })
        }
      }

      if (targetZoom === sourceZ && isInitialLoading.value) {
        const tilesVisible = Math.max(1, (maxX - minX + 1) * (maxY - minY + 1))
        logIconLayerDebug('initial-visible-tiles', {
          z: targetZoom,
          minX,
          maxX,
          minY,
          maxY,
          tilesVisible,
        })
      }
    }
  }

  logIconLayerSnapshot('zoom/move')
}

function isWebGLSupported(): boolean {
  try {
    const canvas = document.createElement('canvas')
    return !!(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'))
  } catch {
    return false
  }
}

onMounted(() => {
  if (!isWebGLSupported()) {
    mapError.value = 'Your browser does not support WebGL, which is required to display the map. Try enabling hardware acceleration in your browser settings, or use a different browser.'
    defaultQueryLoading.value = false
    return
  }

  mapInitStartedAt = nowMs()
  console.info('[Perf] map:init:start')
  map = new maplibregl.Map({
    container: mapContainer.value!,
    // Dark vector basemap (keeps vector rendering while matching prior dark theme).
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    zoom: props.simplified ? 13 : INTRO_START_ZOOM,
    center: INTRO_CENTER,
    pitch: props.simplified ? 0 : 60,
    bearing: props.simplified ? 0 : -20,
    maxPitch: props.simplified ? 0 : 70,
    maxZoom: MAX_ZOOM,
    keyboard: true,
  })

  if (!props.simplified) {
    setMapInteractions(false)
  }

  // Reduce scroll zoom sensitivity for smoother manual zooming.
  try {
    ; (map.scrollZoom as any).setWheelZoomRate?.(SCROLL_WHEEL_ZOOM_RATE)
      ; (map.scrollZoom as any).setZoomRate?.(SCROLL_ZOOM_RATE)
  } catch {
    // no-op
  }

  map.on('error', (e) => {
    const err = (e as any).error
    if (err?.type === 'webglcontextcreationerror') {
      mapError.value = 'Failed to initialize the map renderer (WebGL error). Try enabling hardware acceleration in your browser settings.'
      defaultQueryLoading.value = false
    }
  })

  map.addControl(new maplibregl.NavigationControl(), 'top-right')
  ensureZoomControlLabel()
  map.on('zoom', updateZoomLevel)
  map.on('move', updateZoomLevel)

  map.on('load', () => {
    console.info('[Perf] map:style:load', { ms: Math.round(nowMs() - mapInitStartedAt) })
    updateZoomLevel()
    if (!props.simplified) {
      try {
        registerTreeIcons(map!, categoryIcons.value)
      } catch (e) {
        console.warn('[TreeIcons] registration failed during map load', e)
      }
      console.info('[Perf] map:icons:registered', { ms: Math.round(nowMs() - mapInitStartedAt) })
    }

    map!.on('sourcedata', (e) => {
      if (!mapQueryChangedAt) return
      if (e.sourceId === 'trees') {
        logIconLayerDebug('trees-sourcedata', {
          isSourceLoaded: e.isSourceLoaded,
          sourceDataType: (e as any).sourceDataType,
          coord: (e as any).coord,
          tile: (e as any).tile,
        })
      }
      if (e.sourceId === 'trees' && !firstTreesSourceLoadedLogged) {
        firstTreesSourceLoadedLogged = true
        defaultQueryLoading.value = false
        console.info('[Perf] map:trees-source:loaded', {
          msSincePublish: Math.round(nowMs() - mapQueryChangedAt),
          isSourceLoaded: e.isSourceLoaded,
        })

        if (prewarmStartedForRevision !== mapQueryRevision.value) {
          prewarmStartedForRevision = mapQueryRevision.value
          void prewarmLodCaches().catch((err) => {
            console.warn('[Perf] map:prewarm:failed', err)
          })
        }

        if (!props.simplified) {
          runIntroZoomOut()
        }
      }
      if (e.sourceId === 'carto-dark' && e.isSourceLoaded) {
        console.info('[Perf] map:basemap-source:loaded', {
          msSinceMapInit: Math.round(nowMs() - mapInitStartedAt),
        })
      }
    })

    if (!props.simplified) {
      map!.on('styleimagemissing', (e) => {
        logIconLayerDebug('style-image-missing', { id: e.id })
        if (e.id.startsWith('tree-')) {
          try {
            registerTreeIcons(map!, categoryIcons.value)
            const colors = colorLabelMap.value ? Object.keys(colorLabelMap.value) : []
            if (colors.length > 0) registerCategoryColoredIcons(map!, colors)
          } catch (err) {
            console.warn('[TreeIcons] recovery registration failed', err)
          }
        }
      })
    }

    map!.on('moveend', () => {
      logIconLayerSnapshot('moveend')
    })

    map!.on('idle', () => {
      if (!mapQueryChangedAt || firstMapIdleAfterPublishLogged) return
      firstMapIdleAfterPublishLogged = true
      console.info('[Perf] map:first-idle-after-publish', {
        msSincePublish: Math.round(nowMs() - mapQueryChangedAt),
      })
      logIconLayerSnapshot('first-idle-after-publish')
    })

    void ensureTileProtocolRegistered()
      .then(() => {
        void loadDefaultMapData()
      })
      .catch((e) => {
        mapError.value = (e as Error).message
      })
  })
})

if (!props.simplified) {
  watch(categoryIcons, (icons) => {
    if (!map?.loaded()) return
    try {
      registerTreeIcons(map, icons)
    } catch (e) {
      console.warn('[TreeIcons] registration failed after category icon update', e)
    }
  })
}

/** Remove all existing heatmap layers (both old category-based and current color-based) */
function removeOldHeatmapLayers(mapInstance: maplibregl.Map) {
  // Remove color-based heatmap layers
  for (const hex of lastBuiltHeatmapColors) {
    const id = `trees-heat-${hex}`
    if (mapInstance.getLayer(id)) mapInstance.removeLayer(id)
  }
  // Also remove any legacy category-based layers that might still exist
  for (const cat of TREE_CATEGORIES) {
    const id = `trees-heat-${cat}`
    if (mapInstance.getLayer(id)) mapInstance.removeLayer(id)
  }
  lastBuiltHeatmapColors = []
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const HEATMAP_OPACITY_NORMAL: any = [
  'interpolate', ['linear'], ['zoom'],
  HEATMAP_ZOOM_OPACITY_START, 0.48,
  HEATMAP_ZOOM_OPACITY_MID, 0.33,
  HEATMAP_ZOOM_OPACITY_END, 0,
]

// Sync layers to the current color state. Rebuilds heatmap layers when
// the set of active colors changes. Registers colored icons for all
// active colors so the icon layer always works.
function applyColorToLayers() {
  if (!map) return
  const colors = activeHeatmapColors.value

  // Register colored icons for all active colors so icon layer can reference them.
  if (colors.length > 0) {
    registerCategoryColoredIcons(map, colors)
  }

  // Update icon expression
  if (!props.simplified && map.getLayer('trees-icon')) {
    map.setLayoutProperty('trees-icon', 'icon-image', buildIconExpression())
  }

  // Update circle color expression
  if (map.getLayer('trees-circle')) {
    map.setPaintProperty('trees-circle', 'circle-color', buildCircleColorExpression())
  }

  // Rebuild heatmap layers if the active color set changed
  const colorsChanged = colors.length !== lastBuiltHeatmapColors.length
    || colors.some((c, i) => c !== lastBuiltHeatmapColors[i])
  if (colorsChanged && map.getSource('trees')) {
    removeOldHeatmapLayers(map)
    for (const hexColor of colors) {
      map.addLayer({
        id: `trees-heat-${hexColor}`,
        type: 'heatmap',
        source: 'trees',
        'source-layer': 'trees',
        maxzoom: HEATMAP_ZOOM_OPACITY_END,
        filter: ['==', ['coalesce', ['get', 'display_color'], '#66BB6A'], hexColor],
        paint: {
          'heatmap-weight': [
            'interpolate', ['linear'],
            ['*',
              ['coalesce', ['to-number', ['get', 'point_count']], 1],
              ['^', ['/', 32, ['coalesce', ['to-number', ['get', 'grid_m']], 32]], 2],
            ],
            1, 0.2, 8, 0.45, 32, 0.75, 128, 1.05,
          ],
          'heatmap-intensity': [
            'interpolate', ['linear'], ['zoom'],
            HEATMAP_ZOOM_INTENSITY_START, 0.85,
            HEATMAP_ZOOM_INTENSITY_MID, 1.2,
            HEATMAP_ZOOM_INTENSITY_END, 1.9,
          ],
          'heatmap-radius': [
            'interpolate', ['linear'], ['zoom'],
            HEATMAP_ZOOM_RADIUS_START, 10,
            HEATMAP_ZOOM_RADIUS_MID, 14,
            HEATMAP_ZOOM_RADIUS_END, 22,
          ],
          'heatmap-color': buildHeatmapColorExpression(hexColor),
          'heatmap-opacity': [...HEATMAP_OPACITY_NORMAL],
        },
      })
    }
    lastBuiltHeatmapColors = [...colors]
    // Move heatmap layers below circle/icon
    if (map.getLayer('trees-circle')) {
      for (const hex of colors) {
        map.moveLayer(`trees-heat-${hex}`, 'trees-circle')
      }
    }
  } else {
    // Just refresh heatmap opacity
    for (const hex of lastBuiltHeatmapColors) {
      const id = `trees-heat-${hex}`
      if (!map.getLayer(id)) continue
      map.setPaintProperty(id, 'heatmap-opacity', [...HEATMAP_OPACITY_NORMAL])
    }
  }
}

// If data loads after map is ready
watch([currentMapQuery, publishedTreeIdFilterSql, mapQueryRevision], async ([query, filterSql]) => {
  if (!map?.loaded()) return
  loadingMessage.value = 'Counting our conifers...'
  mapQueryChangedAt = nowMs()
  firstTreesSourceLoadedLogged = false
  firstMapIdleAfterPublishLogged = false
  defaultQueryLoading.value = true
  lastVisibleRangeSigByZoom.clear()
  introLockedRangeByZoom.clear()
  await setColorOverrideSql(colorOverrideSql.value)
  await setTileQuery(query)
  await setPublishedTreeIdFilterSql(filterSql)
  addTreeLayers()
  applyColorToLayers()
})

// When the worker posts new color info (init or agent override), rebuild layers.
watch(workerDistinctColors, () => {
  if (!map?.loaded()) return
  applyColorToLayers()
  // Force a source reload so tiles with new display_color propagate.
  const src = map.getSource('trees') as any
  if (src && typeof src.reload === 'function') {
    src.reload()
  }
})

// Compute bearing from current map center to a target point
function bearingTo(from: [number, number], to: [number, number]): number {
  const toRad = (d: number) => (d * Math.PI) / 180
  const toDeg = (r: number) => (r * 180) / Math.PI
  const dLon = toRad(to[0] - from[0])
  const lat1 = toRad(from[1])
  const lat2 = toRad(to[1])
  const y = Math.sin(dLon) * Math.cos(lat2)
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon)
  return (toDeg(Math.atan2(y, x)) + 360) % 360
}

// Swoop camera to landmark — pivot to face the target first, then fly
watch(flyToTarget, (t) => {
  if (!map || !t) return

  if (pendingSwoopFlyTimeout != null) {
    window.clearTimeout(pendingSwoopFlyTimeout)
    pendingSwoopFlyTimeout = null
  }
  map.stop()

  if (props.simplified) {
    // Simple direct fly for mobile/simplified mode
    map.flyTo({
      center: [t.lng, t.lat],
      zoom: t.zoom ?? 16,
      pitch: 0,
      duration: 1500,
      essential: true,
    })
    return
  }

  const center = map.getCenter()
  const targetBearing = bearingTo([center.lng, center.lat], [t.lng, t.lat])

  // Rotate to face the destination, then blend into the swoop
  map.easeTo({
    bearing: targetBearing,
    duration: 3000,
    easing: (x) => x * (2 - x), // ease-out quad
  })
  // Kick off the fly before the rotation fully settles so they overlap
  pendingSwoopFlyTimeout = window.setTimeout(() => {
    map!.flyTo({
      center: [t.lng, t.lat],
      zoom: t.zoom ?? 16,
      pitch: 60,
      bearing: targetBearing,
      duration: 3200,
      essential: true,
    })
    pendingSwoopFlyTimeout = null
  }, 2200)
})

onUnmounted(() => {
  introCancelled = true
  if (introRafId != null) cancelAnimationFrame(introRafId)
  if (pendingSwoopFlyTimeout != null) {
    window.clearTimeout(pendingSwoopFlyTimeout)
    pendingSwoopFlyTimeout = null
  }
  if (activeTreePopup) {
    activeTreePopup.remove()
    activeTreePopup = null
  }
  zoomControlLabelEl = null
  map?.remove()
  map = null
})
</script>

<style scoped>
.tree-map {
  width: 100%;
  height: 100%;
}

:deep(.maplibregl-ctrl-group .maplibregl-ctrl-zoom-level) {
  display: grid;
  place-items: center;
  width: 29px;
  min-height: 29px;
  color: #4fc3f7;
  background: #1a1a2e;
  border-top: 1px solid rgba(79, 195, 247, 0.24);
  border-bottom: 1px solid rgba(79, 195, 247, 0.24);
  font-size: 0.68rem;
  font-weight: 700;
  line-height: 1;
  pointer-events: none;
  user-select: none;
}

.map-loading,
.map-error {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  padding: 12px 24px;
  border-radius: 8px;
  font-size: 0.875rem;
  z-index: 5;
}

.map-loading {
  background: rgba(22, 33, 62, 0.96);
  color: #4fc3f7;
}

.map-error {
  background: rgba(183, 28, 28, 0.9);
  color: #ffcdd2;
}

.map-legend {
  position: absolute;
  bottom: 28px;
  right: 8px;
  background: rgba(22, 33, 62, 0.88);
  border: 1px solid rgba(79, 195, 247, 0.2);
  border-radius: 8px;
  padding: 8px 12px;
  z-index: 4;
  display: flex;
  flex-direction: column;
  gap: 5px;
  pointer-events: none;
}

.legend-entry {
  display: flex;
  align-items: center;
  gap: 7px;
}

.legend-swatch {
  display: inline-block;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  flex-shrink: 0;
  opacity: 0.9;
}

.legend-label {
  font-size: 0.72rem;
  color: #c8cfe8;
  white-space: nowrap;
}
</style>

<style>
.tree-popup .maplibregl-popup-content {
  background: #16213e;
  color: #e0e0e0;
  border: 1px solid #0f3460;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 0.8rem;
  line-height: 1.5;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.tree-popup .maplibregl-popup-close-button {
  color: #7a7a9e;
  font-size: 1rem;
  padding: 2px 6px;
}

.tree-popup .maplibregl-popup-tip {
  border-top-color: #16213e;
}
</style>
