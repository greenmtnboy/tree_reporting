<template>
  <div ref="mapContainer" class="tree-map"></div>
  <div v-if="isInitialLoading" class="map-loading">{{ loadingMessage }}</div>
  <div v-else-if="tileRefreshing" class="map-loading map-refreshing">{{ tileRefreshMessage }}</div>
  <div v-if="displayError" class="map-error">{{ displayError }}</div>
  <div v-if="!isInitialLoading" class="map-legend">
    <div v-for="entry in legendEntries" :key="entry.color" class="legend-entry">
      <span class="legend-swatch" :style="{ background: entry.color }"></span>
      <span class="legend-label">{{ entry.label }}</span>
    </div>
  </div>
  <div v-if="!props.simplified && !isInitialLoading" class="cache-refresh-wrap">
    <div class="cache-refresh-btn-wrap">
      <button class="cache-refresh-btn" :disabled="cacheRefreshing" @click="void purgeAndRefresh()">&#x21BA;</button>
      <span class="cache-refresh-tooltip">Refresh map cache</span>
    </div>
    <div v-if="dbPopulatedMs != null" class="db-status-wrap">
      <span class="db-status-dot">&#x25CF;</span>
      <span class="db-status-tooltip">City DB populated; {{ dbPopulatedMs }}ms · {{ dbTreeCount?.toLocaleString() }} trees</span>
    </div>
  </div>
  <div v-if="props.simplified" class="city-selector city-selector--mobile">
    <CitySelector />
    <button
      class="city-btn locate-btn"
      :class="{ active: userLocation !== null }"
      :disabled="isInitialLoading"
      :title="userLocation ? 'Pan to my location' : 'Show my location on the map'"
      @click="toggleUserLocation"
    >&#x25CE; Find Me</button>
  </div>
  <button
    v-else
    class="locate-btn-desktop"
    :class="{ active: userLocation !== null }"
    :disabled="isInitialLoading"
    :title="userLocation ? 'Pan to my location' : 'Show my location on the map'"
    @click="toggleUserLocation"
  >&#x25CE; Find Me</button>
</template>

<script setup lang="ts">
import { ref, shallowRef, onMounted, onUnmounted, watch, computed } from 'vue'
import maplibregl from 'maplibre-gl'
import { registerCategoryColoredIcons } from '../composables/useTreeCategories'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData, CITY_CONFIG, type CityCode } from '../composables/useMapData'
import { getCityBiome, getCityEcoregionId } from '../composables/dashboardContextSource'
import { useRoute, useRouter } from 'vue-router'
import { useDuckDB } from '../composables/useDuckDB'
import { useMapIntro } from '../composables/useMapIntro'
import { useMapLayers, TREES_SOURCE_MAXZOOM, addLandmarkLayer, removeLandmarkLayer, registerLandmarkEyeIcon } from '../composables/useMapLayers'
import { useLandmarkData } from '../composables/useLandmarkData'
import { addCityMarkers, updateCityMarkersSelected, removeCityMarkers, bindCityMarkerInteractions } from '../composables/useGlobeCityMarkers'
import { useMapIntroAnimation, INTRO_START_ZOOM } from '../composables/useMapIntroAnimation'
import CitySelector from './CitySelector.vue'
import { THINKING_PHRASES } from '../constants/loadingPhrases'

const props = defineProps<{
  simplified?: boolean
}>()

// --- Refs ---

const mapContainer = ref<HTMLDivElement>()
const mapRef = shallowRef<maplibregl.Map | null>(null)
const zoomLevel = ref(13)
const mapError = ref<string | null>(null)
const defaultQueryLoading = ref(true)
const introActive = ref(!props.simplified)
const tileRefreshing = ref(false)
const tileRefreshMessage = ref(THINKING_PHRASES[0])
let tileRefreshInterval: ReturnType<typeof setInterval> | null = null

function startTileRefreshMessage() {
  let idx = Math.floor(Math.random() * THINKING_PHRASES.length)
  tileRefreshMessage.value = THINKING_PHRASES[idx]
  tileRefreshing.value = true
  tileRefreshInterval = setInterval(() => {
    idx = (idx + 1) % THINKING_PHRASES.length
    tileRefreshMessage.value = THINKING_PHRASES[idx]
  }, 2500)
}

function stopTileRefreshMessage() {
  tileRefreshing.value = false
  if (tileRefreshInterval != null) {
    clearInterval(tileRefreshInterval)
    tileRefreshInterval = null
  }
}

const { setIntroComplete } = useMapIntro()
if (props.simplified) setIntroComplete()

let mapInitStartedAt = 0
let mapQueryChangedAt = 0
let firstTreesSourceLoadedLogged = false
let firstMapIdleAfterPublishLogged = false
let treeInteractionsBound = false
let lastIconDebugAt = 0
let prewarmStartedForRevision = -1
let activeTreePopup: maplibregl.Popup | null = null
let activeLandmarkPopup: maplibregl.Popup | null = null
let popupRequestToken = 0
let landmarkInteractionsBound = false
let globeMarkersBound = false
let zoomControlLabelEl: HTMLDivElement | null = null
let pendingSwoopFlyTimeout: number | null = null
const lastVisibleRangeSigByZoom = new Map<number, string>()
const introLockedRangeByZoom = new Map<number, { minX: number; maxX: number; minY: number; maxY: number }>()

const INITIAL_TILE_PREFETCH_SCALE = 3.5
const SCROLL_WHEEL_ZOOM_RATE = 1 / 5800
const SCROLL_ZOOM_RATE = 1 / 400
const WASD_ACCEL = 3
const WASD_MAX_SPEED = 22
const WASD_FRICTION = 0.82

const WASD_DIRS: Record<string, [number, number]> = {
  w: [0, -1],
  a: [-1, 0],
  s: [0, 1],
  d: [1, 0],
}

// --- Composables ---

const {
  query: duckQuery,
  preWarmForCity,
  ensureTileProtocolRegistered,
  setTileQuery,
  setPublishedTreeIdFilterSql,
  setColorOverrideSql,
  setCityContext,
  setViewportZoom,
  setViewportCenter,
  setVisibleTileRange,
  prefetchVisibleDetailTilesAtZoom,
  prewarmLodCaches,
  setAutoTileFetchEnabled,
  invalidateTileCaches,
  workerDistinctColors,
  workerColorLabelMap,
  dbPopulatedMs,
  dbTreeCount,
} = useDuckDB()

const { landmarks } = useLandmarkData()
const { target: flyToTarget, flyTo } = useFlyTo()
const route = useRoute()
const router = useRouter()
const { selectedCity, setSelectedCity, currentMapQuery, publishedTreeIdFilterSql, colorOverrideSql, colorLabelMap, mapQueryRevision, userLocation, setUserLocation } = useMapData()

function readRouteCity(value: unknown): CityCode | null {
  const city = Array.isArray(value) ? value[0] : value
  return typeof city === 'string' && city in CITY_CONFIG ? (city as CityCode) : null
}

// Initialise city from URL on first load.
const initialRouteCity = readRouteCity(route.query.city)
if (initialRouteCity) setSelectedCity(initialRouteCity)

const introCenterRef = computed((): [number, number] => CITY_CONFIG[selectedCity.value].center)

// --- Computed ---

const displayError = computed(() => mapError.value)
const isInitialLoading = computed(() => defaultQueryLoading.value || introActive.value)

const activeHeatmapColors = computed(() => {
  return workerDistinctColors.value.length > 0 ? workerDistinctColors.value : []
})

// Legend: prefer agent-provided colorLabelMap, fall back to worker's.
// Color keys may be SQL-escaped by the agent (e.g. _RRGGBB, char_43_RRGGBB, u_0023RRGGBB).
// Normalize by extracting the trailing 6 hex digits; drop entries with no valid hex tail.
const legendEntries = computed(() => {
  const labelMap = colorLabelMap.value ?? workerColorLabelMap.value
  if (!labelMap || Object.keys(labelMap).length === 0) return []
  return Object.entries(labelMap).flatMap(([rawColor, label]) => {
    const m = rawColor.match(/([0-9a-fA-F]{6})$/i)
    if (!m) return []
    return [{ color: '#' + m[1], label }]
  })
})

// --- Utilities ---

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function isWebGLSupported(): boolean {
  try {
    const canvas = document.createElement('canvas')
    return !!(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'))
  } catch {
    return false
  }
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
  if (!mapRef.value || !isIconLayerDebugEnabled()) return
  const now = Date.now()
  if (now - lastIconDebugAt < 400) return
  lastIconDebugAt = now
  const zoom = mapRef.value.getZoom()
  const iconLayerExists = !!mapRef.value.getLayer('trees-icon')
  const circleLayerExists = !!mapRef.value.getLayer('trees-circle')
  const heatLayerExists = !!mapRef.value.getLayer('trees-heat')
  const iconFeatures = iconLayerExists ? mapRef.value.queryRenderedFeatures(undefined, { layers: ['trees-icon'] }).length : 0
  const circleFeatures = circleLayerExists ? mapRef.value.queryRenderedFeatures(undefined, { layers: ['trees-circle'] }).length : 0
  const heatFeatures = heatLayerExists ? mapRef.value.queryRenderedFeatures(undefined, { layers: ['trees-heat'] }).length : 0
  logIconLayerDebug('snapshot', {
    reason,
    zoom: Number(zoom.toFixed(2)),
    iconLayerExists, circleLayerExists, heatLayerExists,
    iconFeatures, circleFeatures, heatFeatures,
    iconOpacity: iconLayerExists ? mapRef.value.getPaintProperty('trees-icon', 'icon-opacity') : null,
    circleOpacity: circleLayerExists ? mapRef.value.getPaintProperty('trees-circle', 'circle-opacity') : null,
  })
}

function setMapInteractions(enabled: boolean) {
  if (!mapRef.value) return
  const action = enabled ? 'enable' : 'disable'
  mapRef.value.boxZoom[action]()
  mapRef.value.doubleClickZoom[action]()
  mapRef.value.dragPan[action]()
  mapRef.value.dragRotate[action]()
  mapRef.value.keyboard[action]()
  mapRef.value.scrollZoom[action]()
  mapRef.value.touchZoomRotate[action]()
  mapRef.value.touchPitch[action]()
}

// --- Viewport tracking ---

function computeVisibleTileRangeForZoom(z: number): { minX: number; maxX: number; minY: number; maxY: number } | null {
  if (!mapRef.value) return null
  const bounds = mapRef.value.getBounds()
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

function ensureZoomControlLabel() {
  if (!mapRef.value) return
  if (zoomControlLabelEl?.isConnected) return
  const container = mapRef.value.getContainer()
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
  if (!mapRef.value) return
  zoomLevel.value = mapRef.value.getZoom()
  ensureZoomControlLabel()
  if (zoomControlLabelEl) zoomControlLabelEl.textContent = zoomLevel.value.toFixed(2)

  setViewportZoom(zoomLevel.value)
  const c = mapRef.value.getCenter()
  setViewportCenter(c.lng, c.lat)

  const clampLat = (lat: number) => Math.max(-85.05112878, Math.min(85.05112878, lat))
  const bounds = mapRef.value.getBounds()

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
        minX = locked.minX; maxX = locked.maxX; minY = locked.minY; maxY = locked.maxY
      } else {
        introLockedRangeByZoom.set(z, { minX, maxX, minY, maxY })
      }
    }

    // Expanding range during intro can create excessive tile churn.
    // Only apply wide prefetch once intro ends.
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

  const z = Math.round(mapRef.value.getZoom())
  const sourceZ = Math.min(TREES_SOURCE_MAXZOOM, z)
  if (sourceZ >= 13 && sourceZ <= 20) {
    const candidateZooms = new Set<number>([sourceZ])
    if (introActive.value && sourceZ >= 15) {
      // Intro path is zoom-out: prefetch current + next-coarser LOD only.
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
            .then((status) => { recordIntroPrefetchStatus(targetZoom, status) })
            .catch((err) => { console.warn('[Perf] map:intro-prefetch:failed', { z: targetZoom, err }) })
        }
      }
      if (targetZoom === sourceZ && isInitialLoading.value) {
        const tilesVisible = Math.max(1, (maxX - minX + 1) * (maxY - minY + 1))
        logIconLayerDebug('initial-visible-tiles', { z: targetZoom, minX, maxX, minY, maxY, tilesVisible })
      }
    }
  }

  logIconLayerSnapshot('zoom/move')
}

// --- Layer management ---

const { addTreeLayers, applyColorToLayers, requestTreesSourceReload, forceTreesTileRefetchPass } = useMapLayers({
  map: mapRef,
  simplified: props.simplified ?? false,
  activeHeatmapColors,
  mapQueryRevision,
})

// --- Intro animation ---

const { loadingMessage, runIntroZoomOut, cancelIntro, runGlobeSwoopTo, recordIntroPrefetchStatus } = useMapIntroAnimation({
  map: mapRef,
  simplified: props.simplified ?? false,
  introActive,
  introLockedRangeByZoom,
  introCenter: introCenterRef,
  setIntroComplete,
  setAutoTileFetchEnabled,
  setVisibleTileRange,
  prefetchVisibleDetailTilesAtZoom,
  requestTreesSourceReload,
  forceTreesTileRefetchPass,
  updateZoomLevel,
  computeVisibleTileRangeForZoom,
  setMapInteractions,
})

// --- Tree popup ---

interface PopupTreeRow {
  tree_id: string
  tree_name: string | null
  species: string | null
  plant_date: string | null
  dbh: number | null
  tree_form: string | null
  ecological_fit: string | null
  is_evergreen: boolean | null
  mature_height_min_ft: number | null
  mature_height_max_ft: number | null
  canopy_spread_min_ft: number | null
  canopy_spread_max_ft: number | null
  growth_rate: string | null
  lifespan_min_years: number | null
  lifespan_max_years: number | null
  drought_tolerance: string | null
  water_needs: string | null
  sun_exposure: string[] | null
  bloom_months: number[] | null
  wildlife_value: string | null
  fire_risk: string | null
  description: string | null
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function formatTitleCase(value: string | null) {
  return value
    ? value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
    : null
}

function formatRange(min: number | null, max: number | null, unit: string) {
  if (min == null && max == null) return null
  if (min != null && max != null) {
    if (min === max) return `${min} ${unit}`
    return `${min}-${max} ${unit}`
  }
  return `${min ?? max} ${unit}`
}

function formatBloomMonths(months: number[] | null) {
  if (!Array.isArray(months) || months.length === 0) return null
  const normalized = Array.from(
    new Set(
      months
        .filter((value): value is number => Number.isInteger(value) && value >= 1 && value <= 12)
        .sort((a, b) => a - b),
    ),
  )
  if (normalized.length === 0) return null
  if (normalized.length === 12) return 'Year-round'

  const isContiguous = normalized.every((value, index) => {
    if (index === 0) return true
    return value === normalized[index - 1] + 1
  })

  if (isContiguous) {
    const start = MONTH_LABELS[normalized[0] - 1]
    const end = MONTH_LABELS[normalized[normalized.length - 1] - 1]
    return start === end ? start : `${start}-${end}`
  }

  return normalized.map((value) => MONTH_LABELS[value - 1]).join(', ')
}

function formatSunExposure(values: string[] | null) {
  if (!Array.isArray(values) || values.length === 0) return null
  return values
    .map((value) => formatTitleCase(value))
    .filter((value): value is string => Boolean(value))
    .join(', ')
}

function formatDbh(value: number | null) {
  if (value == null || !Number.isFinite(value)) return null
  return `${value.toFixed(2)}"`
}

function renderPopupRow(label: string, value: string | number) {
  return `<div class="tree-popup-row"><span class="tree-popup-label">${label}</span><span class="tree-popup-value">${value}</span></div>`
}

function formatPlantDate(value: string | null) {
  if (!value) return null
  const normalized = String(value)
  if (!normalized) return null
  return normalized.split('T')[0]?.split(' ')[0] ?? normalized
}

function formatPopupHtml(row: PopupTreeRow): string {
  const planted = formatPlantDate(row.plant_date)
  const evergreen = row.is_evergreen == null ? null : (row.is_evergreen ? 'Yes' : 'No')
  const category = formatTitleCase(row.tree_form)
  const description = row.description?.trim() || null
  const detailLines = [
    ['ID', row.tree_id],
    ['Form', category],
    ['Planted', planted],
    ['Trunk diameter', formatDbh(row.dbh)],
    ['Ecological fit', row.ecological_fit],
    ['Evergreen', evergreen],
    ['Mature height', formatRange(row.mature_height_min_ft, row.mature_height_max_ft, 'ft')],
    ['Canopy spread', formatRange(row.canopy_spread_min_ft, row.canopy_spread_max_ft, 'ft')],
    ['Growth rate', formatTitleCase(row.growth_rate)],
    ['Lifespan', formatRange(row.lifespan_min_years, row.lifespan_max_years, 'years')],
    ['Water needs', formatTitleCase(row.water_needs)],
    ['Drought tolerance', formatTitleCase(row.drought_tolerance)],
    ['Sun exposure', formatSunExposure(row.sun_exposure)],
    ['Bloom period', formatBloomMonths(row.bloom_months)],
    ['Wildlife value', formatTitleCase(row.wildlife_value)],
    ['Fire risk', formatTitleCase(row.fire_risk)],
  ]
    .filter(([, value]) => value != null && value !== '' && value !== 'Unknown')
    .map(([label, value]) => renderPopupRow(String(label), String(value ?? '')))
    .join('')
  return `
    <div class="tree-popup-shell">
      <div class="tree-popup-title">${row.tree_name || 'Unknown tree'}</div>
      ${row.species ? `<div class="tree-popup-species">${row.species}</div>` : ''}
      ${detailLines ? `<div class="tree-popup-grid">${detailLines}</div>` : ''}
      ${description ? `<div class="tree-popup-description">${description}</div>` : ''}
    </div>
  `
}

async function showTreePopup(feature: GeoJSON.Feature, offset: number) {
  if (!mapRef.value) return
  const requestToken = ++popupRequestToken
  const coords = (feature.geometry as GeoJSON.Point).coordinates.slice() as [number, number]
  const id = feature.properties?.id
  if (!id || id === 'unkwn') return
  // Escape single quotes to prevent SQL injection from tile data
  const safeId = String(id).replace(/'/g, "''")
  const cityBiome = getCityBiome(selectedCity.value).replace(/'/g, "''")
  const cityEcoregionId = getCityEcoregionId(selectedCity.value)
  try {
    const { rows } = await duckQuery(`
      SELECT
        tree_id,
        tree_name,
        species,
        plant_date,
        dbh,
        tree_form,
        CASE
          WHEN native_ecoregions IS NULL OR len(native_ecoregions) = 0 THEN NULL
          WHEN list_contains(native_ecoregions, ${cityEcoregionId}) THEN 'Native here'
          WHEN EXISTS (
            SELECT 1
            FROM ecoregion_info ei
            WHERE list_contains(tf.native_ecoregions, ei.ecoregion_id)
              AND ei.biome = '${cityBiome}'
          ) THEN 'Biome match'
          ELSE 'Different biome'
        END AS ecological_fit,
        is_evergreen,
        mature_height_min_ft,
        mature_height_max_ft,
        canopy_spread_min_ft,
        canopy_spread_max_ft,
        growth_rate,
        lifespan_min_years,
        lifespan_max_years,
        drought_tolerance,
        water_needs,
        sun_exposure,
        bloom_months,
        wildlife_value,
        fire_risk,
        description
      FROM trees_fast tf
      WHERE tree_id = '${safeId}'
      LIMIT 1
    `)
    const row = rows[0] as unknown as PopupTreeRow | undefined
    if (!row || requestToken !== popupRequestToken) return
    if (activeTreePopup) { activeTreePopup.remove(); activeTreePopup = null }
    const popup = new maplibregl.Popup({ offset, className: 'tree-popup' })
      .setLngLat(coords)
      .setHTML(formatPopupHtml(row))
      .addTo(mapRef.value)
    activeTreePopup = popup
    popup.on('close', () => { if (activeTreePopup === popup) activeTreePopup = null })
  } catch (e) {
    console.error('[Popup Query Error]', e)
  }
}

// --- Tree interaction binding (one-time after layers are first added) ---

function bindTreeInteractions() {
  if (!mapRef.value || treeInteractionsBound) return
  const interactiveLayers = props.simplified ? ['trees-circle'] : ['trees-icon', 'trees-circle']
  mapRef.value.on('click', (e) => {
    if (!mapRef.value) return
    const features = mapRef.value.queryRenderedFeatures(e.point, { layers: interactiveLayers })
    if (!features.length) return
    const iconFeature = !props.simplified ? features.find((f) => f.layer?.id === 'trees-icon') : undefined
    const picked = (iconFeature ?? features[0]) as unknown as GeoJSON.Feature
    const offset = (iconFeature ?? features[0]).layer?.id === 'trees-icon' ? 15 : 8
    void showTreePopup(picked, offset)
  })
  for (const layer of interactiveLayers) {
    mapRef.value.on('mouseenter', layer, () => { mapRef.value!.getCanvas().style.cursor = 'pointer' })
    mapRef.value.on('mouseleave', layer, () => { mapRef.value!.getCanvas().style.cursor = '' })
  }
  treeInteractionsBound = true
}

// --- Landmark interactions (bound once after layer exists) ---

function bindLandmarkInteractions() {
  if (!mapRef.value || landmarkInteractionsBound) return
  landmarkInteractionsBound = true

  mapRef.value.on('mouseenter', 'landmarks-eye', (e) => {
    if (!mapRef.value) return
    mapRef.value.getCanvas().style.cursor = 'pointer'
    const feature = e.features?.[0]
    if (!feature) return
    const name = feature.properties?.name as string | undefined
    if (!name) return
    const coords = (feature.geometry as GeoJSON.Point).coordinates as [number, number]
    if (activeLandmarkPopup) { activeLandmarkPopup.remove(); activeLandmarkPopup = null }
    activeLandmarkPopup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      className: 'landmark-popup',
      offset: 14,
    })
      .setLngLat(coords)
      .setHTML(`<strong>${name}</strong>`)
      .addTo(mapRef.value)
  })

  mapRef.value.on('mouseleave', 'landmarks-eye', () => {
    if (!mapRef.value) return
    mapRef.value.getCanvas().style.cursor = ''
    activeLandmarkPopup?.remove()
    activeLandmarkPopup = null
  })

  mapRef.value.on('click', 'landmarks-eye', (e) => {
    if (!mapRef.value) return
    const feature = e.features?.[0]
    if (!feature) return
    const coords = (feature.geometry as GeoJSON.Point).coordinates as [number, number]
    mapRef.value.flyTo({ center: coords, zoom: 16, pitch: 50, duration: 2000, essential: true })
  })
}

// --- WASD keyboard pan (smooth acceleration) ---

const wasdHeld = new Set<string>()
const wasdVelocity = { x: 0, y: 0 }
let wasdRafId: number | null = null

function wasdTick() {
  let dx = 0, dy = 0
  for (const key of wasdHeld) {
    const dir = WASD_DIRS[key]
    if (dir) { dx += dir[0]; dy += dir[1] }
  }
  if (dx !== 0 && dy !== 0) {
    const len = Math.sqrt(dx * dx + dy * dy)
    dx /= len; dy /= len
  }
  if (dx !== 0 || dy !== 0) {
    wasdVelocity.x += dx * WASD_ACCEL
    wasdVelocity.y += dy * WASD_ACCEL
    const speed = Math.sqrt(wasdVelocity.x ** 2 + wasdVelocity.y ** 2)
    if (speed > WASD_MAX_SPEED) {
      wasdVelocity.x = (wasdVelocity.x / speed) * WASD_MAX_SPEED
      wasdVelocity.y = (wasdVelocity.y / speed) * WASD_MAX_SPEED
    }
  } else {
    wasdVelocity.x *= WASD_FRICTION
    wasdVelocity.y *= WASD_FRICTION
  }
  if (Math.abs(wasdVelocity.x) > 0.1 || Math.abs(wasdVelocity.y) > 0.1) {
    mapRef.value?.panBy([wasdVelocity.x, wasdVelocity.y], { duration: 0 })
    wasdRafId = requestAnimationFrame(wasdTick)
  } else {
    wasdVelocity.x = 0
    wasdVelocity.y = 0
    wasdRafId = null
  }
}

function onWasdKeyDown(e: KeyboardEvent) {
  if (!mapRef.value) return
  const tag = (e.target as HTMLElement)?.tagName?.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || (e.target as HTMLElement)?.isContentEditable) return
  const key = e.key.toLowerCase()
  if (!WASD_DIRS[key]) return
  e.preventDefault()
  wasdHeld.add(key)
  if (wasdRafId === null) wasdRafId = requestAnimationFrame(wasdTick)
}

function onWasdKeyUp(e: KeyboardEvent) {
  wasdHeld.delete(e.key.toLowerCase())
}

// --- Cache purge ---

const cacheRefreshing = ref(false)

async function purgeAndRefresh() {
  if (cacheRefreshing.value) return
  cacheRefreshing.value = true
  startTileRefreshMessage()
  try {
    await invalidateTileCaches()
    forceTreesTileRefetchPass()
  } finally {
    cacheRefreshing.value = false
    stopTileRefreshMessage()
  }
}

// --- City switching ---

let citySwitchInProgress = false

async function switchCity(city: CityCode, landingCoords?: [number, number]) {
  if (city === selectedCity.value || citySwitchInProgress) return
  citySwitchInProgress = true

  try {
    const { center, name } = CITY_CONFIG[city]

    if (props.simplified) {
      // On mobile, use a smooth but shorter animation than the globe swoop
      await new Promise<void>((resolve) => {
        if (!mapRef.value) return resolve()
        mapRef.value.flyTo({
          center: landingCoords ?? center,
          zoom: 13.5,
          duration: 5000,
          essential: true,
        })
        const onEnd = () => { mapRef.value?.off('moveend', onEnd); resolve() }
        mapRef.value.once('moveend', onEnd)
      })
    } else {
      await runGlobeSwoopTo(center, name, landingCoords)
    }

    // Load the new city's parquet before updating state — the query/filter watcher fires
    // immediately on setSelectedCity, so the city context must be ready first.
    await setCityContext(city)
    setSelectedCity(city)
    void router.replace({ query: { ...route.query, city } })
  } finally {
    citySwitchInProgress = false
  }
}

// React to URL city changes driven by the sidebar CitySelector
watch(
  () => route.query.city,
  (newCity) => {
    const city = Array.isArray(newCity) ? newCity[0] : newCity
    if (typeof city === 'string' && city in CITY_CONFIG && city !== selectedCity.value) {
      void switchCity(city as CityCode)
    }
  },
)

// --- Geolocation ---

function haversineKm(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLng = ((lng2 - lng1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

async function detectCityFromIp(): Promise<void> {
  try {
    const res = await fetch('https://ipapi.co/json/')
    if (!res.ok) return
    const data = await res.json()
    const { latitude, longitude } = data as { latitude?: number; longitude?: number }
    if (!latitude || !longitude) return
    let closest: CityCode = selectedCity.value
    let minDist = Infinity
    for (const [code, cfg] of Object.entries(CITY_CONFIG) as [CityCode, (typeof CITY_CONFIG)[CityCode]][]) {
      const dist = haversineKm(latitude, longitude, cfg.center[1], cfg.center[0])
      if (dist < minDist) { minDist = dist; closest = code }
    }
    if (closest !== selectedCity.value) {
      setSelectedCity(closest)
      void router.replace({ query: { ...route.query, city: closest } })
    }
  } catch {
    // best-effort, ignore errors
  }
}

let userLocationMarker: maplibregl.Marker | null = null

watch(userLocation, (loc) => {
  if (!mapRef.value) return
  if (!loc) {
    userLocationMarker?.remove()
    userLocationMarker = null
    return
  }
  if (!userLocationMarker) {
    const el = document.createElement('div')
    el.className = 'user-location-marker'
    userLocationMarker = new maplibregl.Marker({ element: el })
  }
  userLocationMarker.setLngLat([loc.lng, loc.lat]).addTo(mapRef.value)
})

function toggleUserLocation() {
  if (!navigator.geolocation) return
  if (userLocation.value) {
    // Already sharing — just pan to current location
    flyTo({ lat: userLocation.value.lat, lng: userLocation.value.lng, zoom: 15 })
    return
  }
  // Not yet sharing — request permission; dot + pan only if granted
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      setUserLocation(pos.coords.latitude, pos.coords.longitude)
      // Check if user is closer to a different city and trigger a full city switch if so
      let closest: CityCode = selectedCity.value
      let minDist = Infinity
      for (const [code, cfg] of Object.entries(CITY_CONFIG) as [CityCode, (typeof CITY_CONFIG)[CityCode]][]) {
        const dist = haversineKm(pos.coords.latitude, pos.coords.longitude, cfg.center[1], cfg.center[0])
        if (dist < minDist) { minDist = dist; closest = code }
      }
      if (closest !== selectedCity.value) {
        void switchCity(closest, [pos.coords.longitude, pos.coords.latitude])
      } else {
        flyTo({ lat: pos.coords.latitude, lng: pos.coords.longitude, zoom: 15 })
      }
    },
    (err) => { console.warn('[Geolocation]', err.message) },
  )
}

// --- Camera fly-to ---

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
  if (!mapRef.value || !t) return
  if (pendingSwoopFlyTimeout != null) {
    window.clearTimeout(pendingSwoopFlyTimeout)
    pendingSwoopFlyTimeout = null
  }
  mapRef.value.stop()
  if (props.simplified) {
    mapRef.value.flyTo({ center: [t.lng, t.lat], zoom: t.zoom ?? 16, pitch: 0, duration: 1500, essential: true })
    return
  }
  const center = mapRef.value.getCenter()
  const targetBearing = bearingTo([center.lng, center.lat], [t.lng, t.lat])
  mapRef.value.easeTo({ bearing: targetBearing, duration: 3000, easing: (x) => x * (2 - x) })
  pendingSwoopFlyTimeout = window.setTimeout(() => {
    mapRef.value!.flyTo({ center: [t.lng, t.lat], zoom: t.zoom ?? 16, pitch: 60, bearing: targetBearing, duration: 3200, essential: true })
    pendingSwoopFlyTimeout = null
  }, 2200)
})

// --- Watchers ---

// Reload tiles when query, filter, or revision changes
watch([currentMapQuery, publishedTreeIdFilterSql, mapQueryRevision], async ([query, filterSql], [oldQuery]) => {
  if (!mapRef.value) return
  // Only show the full-screen loading overlay when the base query changes (city switch, query
  // rewrite). Filter-only publishes from the chat should not block the UI with a loading screen.
  const isQueryChange = query !== oldQuery
  if (isQueryChange) {
    loadingMessage.value = 'Counting our conifers...'
    defaultQueryLoading.value = true
  } else {
    startTileRefreshMessage()
  }
  mapQueryChangedAt = nowMs()
  firstTreesSourceLoadedLogged = false
  firstMapIdleAfterPublishLogged = false
  lastVisibleRangeSigByZoom.clear()
  introLockedRangeByZoom.clear()
  await setColorOverrideSql(colorOverrideSql.value)
  await setTileQuery(query)
  await setPublishedTreeIdFilterSql(filterSql)
  addTreeLayers()
  applyColorToLayers()
})

// When the worker posts new color info (init or agent override), rebuild layers
watch(workerDistinctColors, () => {
  if (!mapRef.value?.loaded()) return
  applyColorToLayers()
  const src = mapRef.value.getSource('trees') as any
  if (src && typeof src.reload === 'function') src.reload()
})

// Update the landmark GeoJSON source (or add the layer for the first time) when landmark data loads.
watch(landmarks, (lms) => {
  if (!mapRef.value?.loaded()) return
  if (!mapRef.value.hasImage('landmark-eye')) registerLandmarkEyeIcon(mapRef.value)
  addLandmarkLayer(mapRef.value, lms)
  if (!landmarkInteractionsBound) bindLandmarkInteractions()
})

// Keep city marker highlight in sync with selected city
watch(selectedCity, (city) => {
  if (!mapRef.value?.loaded()) return
  updateCityMarkersSelected(mapRef.value, city)
})

// --- Lifecycle ---

onMounted(async () => {
  window.addEventListener('keydown', onWasdKeyDown)
  window.addEventListener('keyup', onWasdKeyUp)
  // Resolve city from IP before initialising the map so the initial center is correct.
  await router.isReady()
  const mountedRouteCity = readRouteCity(route.query.city)
  if (mountedRouteCity && mountedRouteCity !== selectedCity.value) {
    setSelectedCity(mountedRouteCity)
  }
  if (!mountedRouteCity) {
    await Promise.race([detectCityFromIp(), new Promise<void>((r) => setTimeout(r, 2000))])
  }

  // Kick off DuckDB init now that the city is known so it runs in parallel with
  // map style loading instead of waiting until the map's 'load' event fires.
  preWarmForCity(selectedCity.value)

  // If the user already granted geolocation, silently restore their location pin (no flyTo).
  if (navigator.geolocation && navigator.permissions) {
    navigator.permissions.query({ name: 'geolocation' }).then((result) => {
      if (result.state === 'granted') {
        navigator.geolocation.getCurrentPosition(
          (pos) => setUserLocation(pos.coords.latitude, pos.coords.longitude),
          () => {},
        )
      }
    }).catch(() => {})
  }

  if (!isWebGLSupported()) {
    mapError.value = 'Your browser does not support WebGL, which is required to display the map. Try enabling hardware acceleration in your browser settings, or use a different browser.'
    defaultQueryLoading.value = false
    return
  }

  mapInitStartedAt = nowMs()
  console.info('[Perf] map:init:start')

  const map = new maplibregl.Map({
    container: mapContainer.value!,
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    zoom: props.simplified ? 13 : INTRO_START_ZOOM,
    center: CITY_CONFIG[selectedCity.value].center,
    pitch: props.simplified ? 0 : 60,
    bearing: props.simplified ? 0 : -20,
    maxPitch: props.simplified ? 0 : 70,
    maxZoom: 19,
    keyboard: true,
  })
  mapRef.value = map

  if (!props.simplified) setMapInteractions(false)

  try {
    ;(map.scrollZoom as any).setWheelZoomRate?.(SCROLL_WHEEL_ZOOM_RATE)
    ;(map.scrollZoom as any).setZoomRate?.(SCROLL_ZOOM_RATE)
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

    map.on('sourcedata', (e) => {
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
        mapContainer.value?.setAttribute('data-trees-loaded-for', selectedCity.value)
        stopTileRefreshMessage()
        console.info('[Perf] map:trees-source:loaded', {
          msSincePublish: Math.round(nowMs() - mapQueryChangedAt),
          isSourceLoaded: e.isSourceLoaded,
        })
        if (prewarmStartedForRevision !== mapQueryRevision.value) {
          prewarmStartedForRevision = mapQueryRevision.value
          void prewarmLodCaches().catch((err) => { console.warn('[Perf] map:prewarm:failed', err) })
        }
        if (!props.simplified) runIntroZoomOut()
      }
      if (e.sourceId === 'carto-dark' && e.isSourceLoaded) {
        console.info('[Perf] map:basemap-source:loaded', { msSinceMapInit: Math.round(nowMs() - mapInitStartedAt) })
      }
    })

    if (!props.simplified) {
      map.on('styleimagemissing', (e) => {
        logIconLayerDebug('style-image-missing', { id: e.id })
        if (e.id.startsWith('tree-')) {
          const colors = workerDistinctColors.value
          if (colors.length === 0) {
            console.error('[TreeIcons] styleimagemissing fired but workerDistinctColors is empty — color map not yet received from worker', { missingId: e.id })
            return
          }
          try {
            registerCategoryColoredIcons(map, colors)
          } catch (err) {
            console.warn('[TreeIcons] recovery registration failed', err)
          }
        }
      })
    }

    map.on('moveend', () => { logIconLayerSnapshot('moveend') })

    map.on('idle', () => {
      if (!mapQueryChangedAt || firstMapIdleAfterPublishLogged) return
      firstMapIdleAfterPublishLogged = true
      console.info('[Perf] map:first-idle-after-publish', { msSincePublish: Math.round(nowMs() - mapQueryChangedAt) })
      logIconLayerSnapshot('first-idle-after-publish')
    })

    void ensureTileProtocolRegistered(selectedCity.value)
      .then(async () => {
        // DuckDB init is complete — colors are available
        const colors = workerDistinctColors.value
        console.info('[Perf] map:init:colors-ready', { colors })
        if (!props.simplified) {
          if (colors.length === 0) {
            console.error('[TreeIcons] no colors from worker after init — icons will not be registered')
          } else {
            registerCategoryColoredIcons(map, colors)
            console.info('[Perf] map:icons:registered', { count: colors.length })
          }
        }

        loadingMessage.value = 'Counting our conifers...'
        mapQueryChangedAt = nowMs()
        firstTreesSourceLoadedLogged = false
        firstMapIdleAfterPublishLogged = false
        defaultQueryLoading.value = true
        lastVisibleRangeSigByZoom.clear()
        introLockedRangeByZoom.clear()

        // Set city-specific DB context (bounds, agg cache, color map) for initial city.
        await setCityContext(selectedCity.value)
        await setTileQuery(currentMapQuery.value)
        await setPublishedTreeIdFilterSql(publishedTreeIdFilterSql.value)
        addTreeLayers()
        bindTreeInteractions()

        // Globe-level city overview — visible only when zoomed out past heatmap range
        if (!props.simplified) {
          addCityMarkers(map, selectedCity.value)
          if (!globeMarkersBound) {
            globeMarkersBound = true
            bindCityMarkerInteractions(map, (code) => { void switchCity(code) })
          }
        }

        // Landmark eyes — add immediately if data is already loaded
        if (landmarks.value.length > 0) {
          registerLandmarkEyeIcon(map)
          addLandmarkLayer(map, landmarks.value)
          bindLandmarkInteractions()
        }
      })
      .catch((e) => {
        mapError.value = (e as Error).message
      })
  })
})

onUnmounted(() => {
  window.removeEventListener('keydown', onWasdKeyDown)
  window.removeEventListener('keyup', onWasdKeyUp)
  if (wasdRafId !== null) cancelAnimationFrame(wasdRafId)
  stopTileRefreshMessage()
  cancelIntro()
  if (pendingSwoopFlyTimeout != null) {
    window.clearTimeout(pendingSwoopFlyTimeout)
    pendingSwoopFlyTimeout = null
  }
  if (activeTreePopup) { activeTreePopup.remove(); activeTreePopup = null }
  if (activeLandmarkPopup) { activeLandmarkPopup.remove(); activeLandmarkPopup = null }
  if (mapRef.value) {
    removeLandmarkLayer(mapRef.value)
    removeCityMarkers(mapRef.value)
  }
  zoomControlLabelEl = null
  landmarkInteractionsBound = false
  globeMarkersBound = false
  mapRef.value?.remove()
  mapRef.value = null
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
  color: var(--color-leaf);
  background: rgba(28, 31, 36, 0.92);
  border-top: 1px solid rgba(167, 227, 178, 0.18);
  border-bottom: 1px solid rgba(167, 227, 178, 0.18);
  font-size: 0.68rem;
  font-weight: 700;
  line-height: 1;
  pointer-events: none;
  user-select: none;
}

:deep(.maplibregl-ctrl-group) {
  background: rgba(28, 31, 36, 0.82);
  border: 1px solid rgba(167, 227, 178, 0.16);
  box-shadow: 0 10px 24px rgba(7, 10, 11, 0.24);
}

:deep(.maplibregl-ctrl-group button) {
  background: transparent;
  color: rgba(237, 242, 235, 0.82);
}

:deep(.maplibregl-ctrl-group button:hover) {
  background: rgba(47, 125, 79, 0.16);
  color: var(--color-ink);
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
  background: rgba(28, 31, 36, 0.9);
  color: var(--color-leaf);
  border: 1px solid rgba(167, 227, 178, 0.18);
  box-shadow: 0 18px 36px rgba(7, 10, 11, 0.26);
}

.map-refreshing {
  background: rgba(28, 31, 36, 0.76);
  font-size: 0.8rem;
}

.map-error {
  background: rgba(183, 28, 28, 0.9);
  color: #ffcdd2;
}

.locate-btn-desktop {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 4;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid rgba(167, 227, 178, 0.16);
  background: rgba(28, 31, 36, 0.82);
  color: rgba(237, 242, 235, 0.74);
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.locate-btn-desktop:hover {
  background: rgba(47, 125, 79, 0.16);
  color: var(--color-ink);
  border-color: rgba(167, 227, 178, 0.32);
}

.locate-btn-desktop.active {
  background: rgba(47, 125, 79, 0.22);
  color: var(--color-leaf);
  border-color: rgba(167, 227, 178, 0.42);
}

.locate-btn-desktop:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}

.city-selector {
  position: absolute;
  top: 8px;
  left: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  z-index: 4;
  align-items: center;
}

.city-btn {
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid rgba(167, 227, 178, 0.16);
  background: rgba(28, 31, 36, 0.82);
  color: rgba(237, 242, 235, 0.74);
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
}

.city-btn:hover {
  background: rgba(47, 125, 79, 0.16);
  color: var(--color-ink);
  border-color: rgba(167, 227, 178, 0.32);
}

.city-btn.active {
  background: rgba(47, 125, 79, 0.22);
  color: var(--color-leaf);
  border-color: rgba(167, 227, 178, 0.42);
}

.city-btn:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}

.city-selector--mobile {
  flex-wrap: nowrap;
  gap: 6px;
}

.cache-refresh-wrap {
  position: absolute;
  bottom: 10px;
  left: 10px;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 6px;
}

.cache-refresh-btn {
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid rgba(167, 227, 178, 0.14);
  background: rgba(28, 31, 36, 0.74);
  color: rgba(237, 242, 235, 0.58);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.cache-refresh-btn:hover {
  background: rgba(47, 125, 79, 0.16);
  color: var(--color-ink);
  border-color: rgba(167, 227, 178, 0.28);
}

.cache-refresh-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.cache-refresh-btn-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.cache-refresh-tooltip {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.72rem;
  color: rgba(237, 242, 235, 0.9);
  background: rgba(28, 31, 36, 0.94);
  border: 1px solid rgba(167, 227, 178, 0.16);
  border-radius: 5px;
  padding: 3px 8px;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
}

.cache-refresh-btn-wrap:hover .cache-refresh-tooltip {
  opacity: 1;
}

.db-status-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.db-status-dot {
  font-size: 15px;
  color: var(--color-leaf);
  cursor: default;
  line-height: 1;
}

.db-status-tooltip {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.72rem;
  color: rgba(237, 242, 235, 0.9);
  background: rgba(28, 31, 36, 0.94);
  border: 1px solid rgba(167, 227, 178, 0.16);
  border-radius: 5px;
  padding: 3px 8px;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
}

.db-status-wrap:hover .db-status-tooltip {
  opacity: 1;
}

.map-legend {
  position: absolute;
  bottom: 28px;
  right: 8px;
  background: rgba(28, 31, 36, 0.84);
  border: 1px solid rgba(167, 227, 178, 0.16);
  border-radius: 8px;
  padding: 8px 12px;
  z-index: 4;
  display: flex;
  flex-direction: column;
  gap: 5px;
  pointer-events: none;
  box-shadow: 0 16px 32px rgba(7, 10, 11, 0.22);
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
  color: rgba(237, 242, 235, 0.84);
  white-space: nowrap;
}

.locate-btn {
  font-size: 0.78rem;
}

.locate-btn.active {
  background: rgba(47, 125, 79, 0.22);
  color: var(--color-leaf);
  border-color: rgba(167, 227, 178, 0.42);
}
</style>

<style>
.tree-popup .maplibregl-popup-content {
  background:
    linear-gradient(180deg, rgba(34, 38, 45, 0.98), rgba(24, 27, 32, 0.98));
  color: rgba(237, 242, 235, 0.92);
  border: 1px solid rgba(167, 227, 178, 0.18);
  border-radius: 12px;
  padding: 12px 14px;
  font-size: 0.8rem;
  line-height: 1.45;
  min-width: 220px;
  max-width: 280px;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.34);
}

.tree-popup .maplibregl-popup-close-button {
  color: rgba(154, 166, 154, 0.82);
  font-size: 1.05rem;
  padding: 4px 8px;
}

.tree-popup-shell {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tree-popup-title {
  color: var(--color-ink);
  font-size: 1.08rem;
  font-weight: 700;
  line-height: 1.2;
  padding-right: 20px;
}

.tree-popup-species {
  color: rgba(237, 242, 235, 0.82);
  font-size: 0.82rem;
  font-style: italic;
  line-height: 1.35;
}

.tree-popup-grid {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tree-popup-row {
  display: grid;
  grid-template-columns: minmax(78px, auto) 1fr;
  gap: 10px;
  align-items: start;
}

.tree-popup-label {
  color: rgba(154, 166, 154, 0.74);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.tree-popup-value {
  color: rgba(237, 242, 235, 0.96);
  font-size: 0.84rem;
  font-weight: 600;
  line-height: 1.35;
}

.tree-popup-description {
  margin-top: 2px;
  padding-top: 10px;
  border-top: 1px solid rgba(167, 227, 178, 0.1);
  color: rgba(237, 242, 235, 0.72);
  font-size: 0.76rem;
  line-height: 1.5;
}

.tree-popup .maplibregl-popup-tip {
  border-top-color: rgba(28, 31, 36, 0.96);
}

.landmark-popup .maplibregl-popup-content {
  background: rgba(28, 31, 36, 0.94);
  color: rgba(237, 242, 235, 0.92);
  border: 1px solid rgba(167, 227, 178, 0.22);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.78rem;
  font-weight: 600;
  line-height: 1.4;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
  pointer-events: none;
}

.landmark-popup .maplibregl-popup-tip {
  border-top-color: rgba(28, 31, 36, 0.94);
}

.user-location-marker {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--color-leaf);
  border: 2px solid #fff;
  box-shadow: 0 0 0 0 rgba(167, 227, 178, 0.46);
  animation: user-location-pulse 2s infinite;
}

@keyframes user-location-pulse {
  0% { box-shadow: 0 0 0 0 rgba(167, 227, 178, 0.46); }
  70% { box-shadow: 0 0 0 10px rgba(167, 227, 178, 0); }
  100% { box-shadow: 0 0 0 0 rgba(167, 227, 178, 0); }
}
</style>
