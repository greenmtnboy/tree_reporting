<template>
  <div ref="mapContainer" class="tree-map"></div>
  <MapCompass
    v-if="!displayError"
    :bearing="mapBearing"
    :disabled="cameraLocked"
    :collapsible="!!props.simplified"
    @select="swoopToBearing"
  />
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

  <!-- Three-pane tree info card -->
  <div v-if="selectedTree" ref="treeCardEl" class="tree-card" :style="treeCardStyle" @click.stop>
    <div class="tree-card-header">
      <div class="tree-card-header-main">
        <div class="tree-card-title-wrap">
          <div class="tree-card-title">{{ selectedTree.tree_name || 'Unknown tree' }}</div>
          <div v-if="selectedTree.species" class="tree-card-species">{{ selectedTree.species }}</div>
        </div>
        <div class="tree-card-header-actions">
          <button
            v-if="canCheckInToSelectedTree"
            class="tree-card-checkin"
            @click="openCheckin"
          >Check in</button>
          <button class="tree-card-close" @click="closeTreeCard" aria-label="Close">&#x2715;</button>
        </div>
      </div>
    </div>

    <div class="tree-card-body">
      <!-- Left pane: this tree -->
      <div class="tree-card-pane tree-card-pane--tree">
        <div class="tree-card-section-label">This tree</div>
        <div class="tc-grid">
          <template v-if="selectedTree.tree_id">
            <span class="tc-label">ID</span><span class="tc-value">{{ selectedTree.tree_id }}</span>
          </template>
          <template v-if="formatDataSource(selectedTree.data_source)">
            <span class="tc-label">Source</span><span class="tc-value">{{ formatDataSource(selectedTree.data_source) }}</span>
          </template>
          <template v-if="formatPlantDate(selectedTree.plant_date)">
            <span class="tc-label">Planted</span><span class="tc-value">{{ formatPlantDate(selectedTree.plant_date) }}</span>
          </template>
          <template v-if="formatTreeAge(selectedTree.plant_date)">
            <span class="tc-label">Age</span><span class="tc-value">{{ formatTreeAge(selectedTree.plant_date) }}</span>
          </template>
          <template v-if="formatDbh(selectedTree.dbh)">
            <span class="tc-label">Trunk diameter</span><span class="tc-value">{{ formatDbh(selectedTree.dbh) }}</span>
          </template>
          <template v-if="selectedTree.ecological_fit">
            <span class="tc-label">Ecological fit</span><span class="tc-value">{{ selectedTree.ecological_fit }}</span>
          </template>
        </div>
      </div>

      <!-- Center pane: species info -->
      <div class="tree-card-pane tree-card-pane--species">
        <div class="tree-card-section-label">Species</div>

        <p v-if="selectedTree.description" class="tc-description">{{ selectedTree.description }}</p>

        <div class="tc-grid">
          <template v-if="formatTitleCase(selectedTree.tree_form)">
            <span class="tc-label">Form</span><span class="tc-value">{{ formatTitleCase(selectedTree.tree_form) }}</span>
          </template>
          <template v-if="selectedTree.is_evergreen != null">
            <span class="tc-label">Evergreen</span><span class="tc-value">{{ selectedTree.is_evergreen ? 'Yes' : 'No' }}</span>
          </template>
          <template v-if="selectedTree.mature_height_max_ft != null">
            <span class="tc-label">Mature height</span><span class="tc-value">{{ formatRange(selectedTree.mature_height_min_ft, selectedTree.mature_height_max_ft, 'ft') }}</span>
          </template>
          <template v-if="selectedTree.canopy_spread_max_ft != null">
            <span class="tc-label">Canopy spread</span><span class="tc-value">{{ formatRange(selectedTree.canopy_spread_min_ft, selectedTree.canopy_spread_max_ft, 'ft') }}</span>
          </template>
          <template v-if="selectedTree.growth_rate != null">
            <span class="tc-label">Growth rate</span><span class="tc-value">{{ formatTitleCase(selectedTree.growth_rate) }}</span>
          </template>
          <template v-if="selectedTree.lifespan_max_years != null">
            <span class="tc-label">Lifespan</span><span class="tc-value">{{ formatRange(selectedTree.lifespan_min_years, selectedTree.lifespan_max_years, 'years') }}</span>
          </template>
          <template v-if="selectedTree.water_needs != null">
            <span class="tc-label">Water needs</span><span class="tc-value">{{ formatTitleCase(selectedTree.water_needs) }}</span>
          </template>
          <template v-if="selectedTree.drought_tolerance != null">
            <span class="tc-label">Drought tol.</span><span class="tc-value">{{ formatTitleCase(selectedTree.drought_tolerance) }}</span>
          </template>
          <template v-if="selectedTree.sun_exposure?.length">
            <span class="tc-label">Sun exposure</span><span class="tc-value">{{ formatSunExposure(selectedTree.sun_exposure) }}</span>
          </template>
          <template v-if="selectedTree.bloom_months?.length">
            <span class="tc-label">Bloom period</span><span class="tc-value">{{ formatBloomMonths(selectedTree.bloom_months) }}</span>
          </template>
          <template v-if="selectedTree.wildlife_value != null">
            <span class="tc-label">Wildlife value</span><span class="tc-value">{{ formatTitleCase(selectedTree.wildlife_value) }}</span>
          </template>
          <template v-if="selectedTree.fire_risk != null">
            <span class="tc-label">Fire risk</span><span class="tc-value">{{ formatTitleCase(selectedTree.fire_risk) }}</span>
          </template>
        </div>
      </div>

      <!-- Right pane: photo. A community submission has a photo of this exact
           tree; everything else can only show a stock photo of the species. -->
      <div class="tree-card-pane tree-card-pane--photos">
        <div class="tree-card-section-label">
          {{ selectedTree.submission_photo_url ? 'Photo of this tree' : 'Example species photo' }}
        </div>
        <div v-if="selectedTree.submission_photo_url" class="tc-photo-wrap">
          <img
            :src="selectedTree.submission_photo_url"
            :alt="`Submitted photo of ${selectedTree.species || 'this tree'}`"
            class="tc-photo"
            loading="lazy"
          />
          <div class="tc-photo-footer">
            <span class="tc-photo-attr">Submitted by a community contributor</span>
          </div>
        </div>
        <div v-else-if="selectedTree.photo_url" class="tc-photo-wrap">
          <img
            :src="selectedTree.photo_url"
            :alt="selectedTree.species || 'tree photo'"
            class="tc-photo"
            loading="lazy"
          />
          <div v-if="selectedTree.photo_attribution" class="tc-photo-footer">
            <span class="tc-photo-attr">{{ selectedTree.photo_attribution }}</span>
          </div>
        </div>
        <div v-else class="tc-photo-placeholder">No photo available</div>
      </div>
    </div>
  </div>

  <CheckinDialog
    v-if="checkinDialog"
    :tree-id="checkinDialog.treeId"
    :tree-lat="checkinDialog.lat"
    :tree-lng="checkinDialog.lng"
    :species="checkinDialog.species"
    :tree-form="checkinDialog.treeForm"
    :dbh-inches="checkinDialog.dbhInches"
    :plant-year="checkinDialog.plantYear"
    @close="checkinDialog = null"
    @success="checkinDialog = null"
  />
</template>

<script setup lang="ts">
import { ref, shallowRef, onMounted, onUnmounted, watch, computed, nextTick } from 'vue'
import maplibregl from 'maplibre-gl'
import { registerCategoryColoredIcons } from '../composables/useTreeCategories'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData, CITY_CONFIG, closestCityTo, haversineKm, type CityCode } from '../composables/useMapData'
import { getCityBiome, getCityEcoregionId } from '../composables/dashboardContextSource'
import { useRoute, useRouter } from 'vue-router'
import { useDuckDB } from '../composables/useDuckDB'
import { useMapIntro } from '../composables/useMapIntro'
import { useMapLifecycle } from '../composables/useMapLifecycle'
import { useMapLayers, TREES_SOURCE_MAXZOOM, addLandmarkLayer, removeLandmarkLayer, registerLandmarkEyeIcon } from '../composables/useMapLayers'
import { useLandmarkData } from '../composables/useLandmarkData'
import { addCityMarkers, updateCityMarkersSelected, removeCityMarkers, bindCityMarkerInteractions } from '../composables/useGlobeCityMarkers'
import { useMapIntroAnimation, INTRO_START_ZOOM, INTRO_START_BEARING, INTRO_END_BEARING } from '../composables/useMapIntroAnimation'
import { resolveBootstrapCity } from '../composables/bootstrapCity'
import CitySelector from './CitySelector.vue'
import MapCompass from './MapCompass.vue'
import CheckinDialog from './CheckinDialog.vue'
import { firebaseAvailable } from '../lib/firebase'
import { formatDataSource } from '../data/dataSources'
import { plantYearFrom } from '../lib/achievements'
import {
  acquireSharedPositionWatch,
  getGeolocationPermissionState,
  refreshSharedPosition,
} from '../lib/geo'
import { THINKING_PHRASES } from '../constants/loadingPhrases'

const props = defineProps<{
  simplified?: boolean
}>()

// --- Refs ---

const mapContainer = ref<HTMLDivElement>()
const mapRef = shallowRef<maplibregl.Map | null>(null)
const zoomLevel = ref(13)
const mapBearing = ref(0)
const mapError = ref<string | null>(null)
const {
  phase: lifecyclePhase,
  requestedCity: lifecycleRequestedCity,
  renderedCity: lifecycleRenderedCity,
  showLoadingOverlay,
  initialize: lifecycleInitialize,
  requestCity: lifecycleRequestCity,
  setManualCitySelectionReady: lifecycleSetManualCitySelectionReady,
  currentSnapshot: lifecycleCurrentSnapshot,
  startLoading: lifecycleStartLoading,
  commitContextCity: lifecycleCommitContextCity,
  tilesLoaded: lifecycleTilesLoaded,
  introFinished: lifecycleIntroFinished,
  startCitySwitch: lifecycleStartCitySwitch,
  citySwitchReady: lifecycleCitySwitchReady,
  matches: lifecycleMatches,
  forceReady: lifecycleForceReady,
} = useMapLifecycle()
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
let activeLandmarkPopup: maplibregl.Popup | null = null
let popupRequestToken = 0
let landmarkInteractionsBound = false
let globeMarkersBound = false
let zoomControlLabelEl: HTMLDivElement | null = null
let pendingSwoopFlyTimeout: number | null = null
let releaseSharedPositionWatch: (() => void) | null = null
const lastVisibleRangeSigByZoom = new Map<number, string>()
const introLockedRangeByZoom = new Map<number, { minX: number; maxX: number; minY: number; maxY: number }>()

const INITIAL_TILE_PREFETCH_SCALE = 3.5
const SCROLL_WHEEL_ZOOM_RATE = 1 / 5800
const SCROLL_ZOOM_RATE = 1 / 400
const WASD_ACCEL = 3
const WASD_MAX_SPEED = 22
const WASD_FRICTION = 0.82
const MOBILE_TREE_CARD_EDGE_MARGIN = 12
const MOBILE_TREE_CARD_POINTER_GAP = 18
const COMPASS_SWOOP_MS = 1600

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
const {
  selectedCity,
  currentMapQuery,
  publishedTreeIdFilterSql,
  colorOverrideSql,
  colorLabelMap,
  mapQueryRevision,
  userLocation,
  initialUserCityDetectionDone,
  setUserLocation,
  commitResolvedCity,
  markInitialUserCityDetectionDone,
} = useMapData()

function readRouteCity(value: unknown): CityCode | null {
  const city = Array.isArray(value) ? value[0] : value
  return typeof city === 'string' && city in CITY_CONFIG ? (city as CityCode) : null
}

// Initialise city from URL on first load.
const initialRouteCity = readRouteCity(route.query.city)
lifecycleInitialize(initialRouteCity ?? selectedCity.value)

const mapDisplayCity = computed((): CityCode => (lifecycleRequestedCity.value ?? selectedCity.value) as CityCode)
const introCenterRef = computed((): [number, number] => CITY_CONFIG[mapDisplayCity.value].center)

// --- Computed ---

const displayError = computed(() => mapError.value)
const isInitialLoading = computed(() => showLoadingOverlay.value)
// The compass is always on screen and always tracks the live bearing, but while a
// scripted camera move owns the camera (intro sweep, globe swoop, city switch)
// its buttons are inert — a mid-flight easeTo would fight the animation.
const cameraLocked = computed(() => introActive.value || lifecyclePhase.value !== 'ready')

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

function jumpMapToCity(city: CityCode) {
  if (!mapRef.value) return
  mapRef.value.stop()
  mapRef.value.jumpTo({
    center: CITY_CONFIG[city].center,
    zoom: props.simplified ? 13 : INTRO_START_ZOOM,
    pitch: props.simplified ? 0 : 60,
    bearing: props.simplified ? INTRO_END_BEARING : INTRO_START_BEARING,
  })
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
  mapBearing.value = mapRef.value.getBearing()
  updateTreeCardPosition()
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

  // floor, not round: MapLibre floors the camera zoom to pick the displayed
  // tile level (zoom 15.7 renders z15 tiles), and the visible range we report
  // to the worker must track the tile level actually on screen.
  const z = Math.floor(mapRef.value.getZoom())
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
  onIntroFinished: lifecycleIntroFinished,
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
  plant_date: string | number | null
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
  photo_url: string | null
  photo_license: string | null
  photo_attribution: string | null
  data_source: string | null
  submission_photo_url: string | null
}

const selectedTree = ref<PopupTreeRow | null>(null)
const selectedTreeAnchor = ref<[number, number] | null>(null)
const selectedTreeScreenPoint = ref<{ x: number; y: number } | null>(null)
const treeCardEl = ref<HTMLElement | null>(null)
const checkinDialog = ref<{
  treeId: string
  lat: number
  lng: number
  species: string | null
  treeForm: string | null
  dbhInches: number | null
  plantYear: number | null
} | null>(null)

const CHECKIN_MAX_METERS = 50

const canCheckInToSelectedTree = computed(() => {
  if (!firebaseAvailable) return false
  if (!selectedTreeAnchor.value) return false
  const loc = userLocation.value
  if (!loc) return true
  const [lng, lat] = selectedTreeAnchor.value
  const meters = haversineKm(loc.lat, loc.lng, lat, lng) * 1000
  return meters <= CHECKIN_MAX_METERS
})

function openCheckin(): void {
  if (!selectedTree.value || !selectedTreeAnchor.value) return
  const [lng, lat] = selectedTreeAnchor.value
  checkinDialog.value = {
    treeId: selectedTree.value.tree_id,
    lat,
    lng,
    species: selectedTree.value.species,
    treeForm: selectedTree.value.tree_form,
    dbhInches: selectedTree.value.dbh,
    plantYear: plantYearFrom(selectedTree.value.plant_date),
  }
}

// Gap between the card and the map container edge when the anchor is close
// enough that the card would otherwise overflow.
const TREE_CARD_EDGE_MARGIN = 12
// Matches the `translate(-50%, calc(-100% - 18px))` offset in .tree-card.
const TREE_CARD_ANCHOR_GAP = 18

function updateTreeCardPosition(): void {
  if (!mapRef.value || !selectedTreeAnchor.value) {
    selectedTreeScreenPoint.value = null
    return
  }
  const point = mapRef.value.project(selectedTreeAnchor.value)
  selectedTreeScreenPoint.value = clampTreeCardPoint(point.x, point.y)
}

/**
 * The card is absolutely positioned at the tree's screen point and drawn above
 * it. Without clamping, a tree near the top or side of the map pushes the card
 * header — including the close button — outside the viewport, which reads as
 * "the popup didn't open". Keep the whole card inside the map container.
 */
function clampTreeCardPoint(x: number, y: number): { x: number; y: number } {
  const container = mapRef.value?.getContainer()
  const card = treeCardEl.value
  if (!container || !card) return { x, y }

  const { clientWidth: containerWidth, clientHeight: containerHeight } = container
  const halfCard = card.offsetWidth / 2
  const margin = TREE_CARD_EDGE_MARGIN

  const minX = halfCard + margin
  const maxX = containerWidth - halfCard - margin
  const minY = card.offsetHeight + TREE_CARD_ANCHOR_GAP + margin
  const maxY = containerHeight - margin

  return {
    x: maxX >= minX ? Math.min(Math.max(x, minX), maxX) : containerWidth / 2,
    y: maxY >= minY ? Math.min(Math.max(y, minY), maxY) : minY,
  }
}

const treeCardStyle = computed(() => {
  const point = selectedTreeScreenPoint.value
  if (!point) return {}
  return {
    left: `${point.x}px`,
    top: `${point.y}px`,
  }
})

function selectTree(row: PopupTreeRow, coords: [number, number]): void {
  selectedTree.value = row
  selectedTreeAnchor.value = coords
  updateTreeCardPosition()
  // The card element does not exist yet on the first call, so clamping has no
  // dimensions to work with. Re-run once it has been rendered and measured.
  void nextTick(() => updateTreeCardPosition())
  void ensureTreeCardVisibleOnMobile(coords)
}

async function ensureTreeCardVisibleOnMobile(coords: [number, number]): Promise<void> {
  if (!props.simplified || !mapRef.value || typeof window === 'undefined') return
  if (!window.matchMedia('(max-width: 640px)').matches) return

  await nextTick()
  if (!mapRef.value) return

  const container = mapRef.value.getContainer()
  const viewportHeight = container.clientHeight
  if (!viewportHeight) return

  const containerRect = container.getBoundingClientRect()
  const cardEl = container.parentElement?.querySelector('.tree-card') as HTMLElement | null
  const headerEl = container.parentElement?.querySelector('.city-selector--mobile') as HTMLElement | null
  const bottomBarEl = document.querySelector('.mobile-bottom-bar') as HTMLElement | null

  const topSafe = headerEl
    ? (headerEl.getBoundingClientRect().bottom - containerRect.top) + MOBILE_TREE_CARD_EDGE_MARGIN
    : MOBILE_TREE_CARD_EDGE_MARGIN
  const bottomSafe = bottomBarEl
    ? (bottomBarEl.getBoundingClientRect().top - containerRect.top) - MOBILE_TREE_CARD_EDGE_MARGIN
    : viewportHeight - MOBILE_TREE_CARD_EDGE_MARGIN
  const cardHeight = cardEl?.offsetHeight ?? Math.round(viewportHeight * 0.65)
  const minAnchorY = topSafe + cardHeight + MOBILE_TREE_CARD_POINTER_GAP
  const maxAnchorY = Math.max(MOBILE_TREE_CARD_EDGE_MARGIN, bottomSafe)
  const desiredAnchorY = Math.max(minAnchorY, maxAnchorY)

  const targetYOffset = Math.round(desiredAnchorY - viewportHeight / 2)
  mapRef.value.easeTo({
    center: coords,
    offset: [0, targetYOffset],
    duration: 400,
    essential: true,
  })
}

function closeTreeCard(): void {
  selectedTree.value = null
  selectedTreeAnchor.value = null
  selectedTreeScreenPoint.value = null
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

function formatPlantDate(value: string | number | null) {
  if (value == null || value === '') return null

  const normalizeDate = (date: Date): string | null => {
    if (Number.isNaN(date.getTime())) return null
    return date.toISOString().slice(0, 10)
  }

  if (typeof value === 'number' && Number.isFinite(value)) {
    const timestamp = value < 1e12 ? value * 1000 : value
    return normalizeDate(new Date(timestamp))
  }

  const normalized = String(value).trim()
  if (!normalized) return null

  if (/^\d+$/.test(normalized)) {
    const numeric = Number(normalized)
    if (Number.isFinite(numeric)) {
      const timestamp = numeric < 1e12 ? numeric * 1000 : numeric
      return normalizeDate(new Date(timestamp))
    }
  }

  const simpleDate = normalized.split('T')[0]?.split(' ')[0]
  if (simpleDate && /^\d{4}-\d{2}-\d{2}$/.test(simpleDate)) return simpleDate

  return normalizeDate(new Date(normalized)) ?? normalized
}

function formatTreeAge(value: string | number | null) {
  const dateStr = formatPlantDate(value)
  if (!dateStr) return null
  const planted = new Date(dateStr)
  if (Number.isNaN(planted.getTime())) return null
  const now = new Date()
  let years = now.getFullYear() - planted.getFullYear()
  if (
    now.getMonth() < planted.getMonth() ||
    (now.getMonth() === planted.getMonth() && now.getDate() < planted.getDate())
  ) {
    years--
  }
  if (years < 1) return '< 1 year'
  return `${years} year${years !== 1 ? 's' : ''}`
}

async function showTreeCard(feature: GeoJSON.Feature, fallbackCoords: [number, number]) {
  if (!mapRef.value) return
  const requestToken = ++popupRequestToken
  const id = feature.properties?.id
  if (!id || id === 'unkwn') return
  const featureCoords = feature.geometry?.type === 'Point'
    ? (feature.geometry.coordinates as [number, number])
    : fallbackCoords
  const safeId = String(id).replace(/'/g, "''")
  const cityBiome = getCityBiome(selectedCity.value).replace(/'/g, "''")
  const cityEcoregionId = getCityEcoregionId(selectedCity.value)
  try {
    const { rows } = await duckQuery(`
      SELECT
        tf.tree_id,
        tf.tree_name,
        tf.species,
        tf.plant_date,
        tf.dbh,
        tf.tree_form,
        CASE
          WHEN tf.native_ecoregions IS NULL OR len(tf.native_ecoregions) = 0 THEN NULL
          WHEN list_contains(tf.native_ecoregions, ${cityEcoregionId}) THEN 'Native here'
          WHEN EXISTS (
            SELECT 1
            FROM ecoregion_info ei
            WHERE list_contains(tf.native_ecoregions, ei.ecoregion_id)
              AND ei.biome = '${cityBiome}'
          ) THEN 'Biome match'
          ELSE 'Different biome'
        END AS ecological_fit,
        tf.is_evergreen,
        tf.mature_height_min_ft,
        tf.mature_height_max_ft,
        tf.canopy_spread_min_ft,
        tf.canopy_spread_max_ft,
        tf.growth_rate,
        tf.lifespan_min_years,
        tf.lifespan_max_years,
        tf.drought_tolerance,
        tf.water_needs,
        tf.sun_exposure,
        tf.bloom_months,
        tf.wildlife_value,
        tf.fire_risk,
        tf.data_source,
        tf.submission_photo_url,
        se.description,
        se.photo_url,
        se.photo_license,
        se.photo_attribution
      FROM trees_fast tf
      LEFT JOIN species_enrichment se ON tf.species = se.species
      WHERE tf.tree_id = '${safeId}'
      LIMIT 1
    `)
    const row = rows[0] as unknown as PopupTreeRow | undefined
    if (!row || requestToken !== popupRequestToken) return
    selectTree(row, featureCoords)
  } catch (e) {
    console.error('[Tree Card Query Error]', e)
  }
}

// --- Tree interaction binding (one-time after layers are first added) ---

function bindTreeInteractions() {
  if (!mapRef.value || treeInteractionsBound) return
  const interactiveLayers = props.simplified ? ['trees-circle'] : ['trees-icon', 'trees-circle']
  const updateTreeCursor = (point?: maplibregl.PointLike) => {
    if (!mapRef.value) return
    if (!point) {
      mapRef.value.getCanvas().style.cursor = ''
      return
    }
    const features = mapRef.value.queryRenderedFeatures(point, { layers: interactiveLayers })
    mapRef.value.getCanvas().style.cursor = features.length > 0 ? 'pointer' : ''
  }

  mapRef.value.on('click', (e) => {
    if (!mapRef.value) return
    const features = mapRef.value.queryRenderedFeatures(e.point, { layers: interactiveLayers })
    if (!features.length) return
    const iconFeature = !props.simplified ? features.find((f) => f.layer?.id === 'trees-icon') : undefined
    const picked = (iconFeature ?? features[0]) as unknown as GeoJSON.Feature
    void showTreeCard(picked, [e.lngLat.lng, e.lngLat.lat])
  })
  mapRef.value.on('mousemove', (e) => { updateTreeCursor(e.point) })
  mapRef.value.on('mouseout', () => { updateTreeCursor() })
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

async function switchCity(city: CityCode, landingCoords?: [number, number]) {
  if (city === lifecycleRequestedCity.value && city === lifecycleRenderedCity.value) return
  closeTreeCard()
  const transition = lifecycleStartCitySwitch(city)

  try {
    const { center, name } = CITY_CONFIG[city]
    mapRef.value?.stop()

    if (props.simplified) {
      // On mobile, use a smooth but shorter animation than the globe swoop
      await new Promise<void>((resolve) => {
        if (!mapRef.value) return resolve()
        mapRef.value.flyTo({
          center: landingCoords ?? center,
          zoom: 13.5,
          bearing: INTRO_END_BEARING,
          duration: 5000,
          essential: true,
        })
        const onEnd = () => { mapRef.value?.off('moveend', onEnd); resolve() }
        mapRef.value.once('moveend', onEnd)
      })
    } else {
      await runGlobeSwoopTo(center, name, landingCoords)
    }

    if (!lifecycleMatches(transition)) return

    // Load the new city's parquet before accepting the transition so stale
    // switch completions cannot overwrite the lifecycle's current request.
    await setCityContext(city)
    if (!lifecycleCommitContextCity(transition)) return
    if (readRouteCity(route.query.city) !== city) {
      void router.replace({ query: { ...route.query, city } })
    }
  } catch (e) {
    if (!lifecycleMatches(transition)) return
    console.error('[CitySwitch] failed', e)
    // On failure, transition back to ready so the UI isn't stuck
    lifecycleForceReady(transition)
    return
  }
  // NOTE: we don't transition to ready here — the sourcedata handler does
  // that when tiles actually render (via lifecycleTilesLoaded / lifecycleCitySwitchReady).
}

// React to URL city changes driven by the sidebar CitySelector.
// During initialization there is no meaningful in-flight city to preserve, so
// update selectedCity immediately. Once the map is live, newer requests should
// preempt older ones rather than wait behind them.
watch(
  () => route.query.city,
  (newCity) => {
    const city = Array.isArray(newCity) ? newCity[0] : newCity
    if (typeof city !== 'string' || !(city in CITY_CONFIG)) return
    if (city === lifecycleRequestedCity.value && city === selectedCity.value) return
    // A switch to this city is already mid-flight (its swoop hasn't committed the
    // context yet) — a link that echoes the destination city must not restart it.
    if (city === lifecycleRequestedCity.value && lifecyclePhase.value === 'switching') return
    lifecycleRequestCity(city)
    if (lifecyclePhase.value === 'initializing' || lifecyclePhase.value === 'loading') {
      jumpMapToCity(city as CityCode)
      return
    }
    void switchCity(city as CityCode)
  },
)

// --- Geolocation ---

async function restoreGrantedUserLocation(applyCity: boolean): Promise<void> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) return
  try {
    const permissionState = await getGeolocationPermissionState()
    if (permissionState !== 'granted') return
    if (!releaseSharedPositionWatch) {
      releaseSharedPositionWatch = acquireSharedPositionWatch()
    }
    const location = await refreshSharedPosition()
    setUserLocation(location.lat, location.lng, location.accuracy)
    if (applyCity) {
      silentlyApplyCity(location.lat, location.lng)
    }
  } catch {
    // best-effort, ignore errors
  }
}

/**
 * Update the city based on coordinates without triggering an animated camera transition.
 * Used for IP detection and background geolocation restores.
 */
function silentlyApplyCity(lat: number, lng: number): void {
  const city = closestCityTo(lat, lng)
  if (city !== lifecycleRequestedCity.value) {
    lifecycleRequestCity(city)
    void router.replace({ query: { ...route.query, city } })
  }
}

/**
 * Set the user location pin and navigate: switches city with the globe swoop if the
 * closest city differs from the current one, otherwise pans to the coordinates.
 * This is the single authoritative path for GPS-driven navigation.
 */
function navigateToLocation(lat: number, lng: number): void {
  const city = closestCityTo(lat, lng)
  if (city !== selectedCity.value) {
    void switchCity(city, [lng, lat])
  } else {
    flyTo({ lat, lng, zoom: props.simplified ? 18 : 15 })
  }
}

let userLocationMarker: maplibregl.Marker | null = null

watch([userLocation, mapRef], ([loc, map]) => {
  if (!map) return
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
  userLocationMarker.setLngLat([loc.lng, loc.lat]).addTo(map)
}, { immediate: true })

function toggleUserLocation() {
  if (!navigator.geolocation) return
  if (!releaseSharedPositionWatch) {
    releaseSharedPositionWatch = acquireSharedPositionWatch()
  }
  if (userLocation.value) {
    // Already have a location — re-navigate so a city change is detected if needed.
    navigateToLocation(userLocation.value.lat, userLocation.value.lng)
    return
  }
  refreshSharedPosition()
    .then((location) => navigateToLocation(location.lat, location.lng))
    .catch((err) => { console.warn('[Geolocation]', (err as Error).message) })
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

/**
 * Rotate the camera to face `bearing`, pivoting around the ground point the
 * camera is currently centred on so the view swings in place rather than
 * translating. Driven by the compass overlay's cardinal buttons.
 */
function swoopToBearing(bearing: number) {
  if (!mapRef.value) return
  if (pendingSwoopFlyTimeout != null) {
    window.clearTimeout(pendingSwoopFlyTimeout)
    pendingSwoopFlyTimeout = null
  }
  mapRef.value.stop()
  mapRef.value.easeTo({
    bearing,
    around: mapRef.value.getCenter(),
    duration: COMPASS_SWOOP_MS,
    easing: (x) => x * (2 - x),
    essential: true,
  })
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
  // Only show the tile-refresh spinner for filter-only changes (not city switches,
  // which are managed by the lifecycle state machine).
  const isQueryChange = query !== oldQuery
  if (isQueryChange) {
    loadingMessage.value = 'Counting our conifers...'
  } else {
    startTileRefreshMessage()
  }
  lastVisibleRangeSigByZoom.clear()
  introLockedRangeByZoom.clear()
  await setColorOverrideSql(colorOverrideSql.value)
  await setTileQuery(query)
  await setPublishedTreeIdFilterSql(filterSql)
  // Re-arm the "trees source loaded" one-shot only once the worker is actually
  // configured for the new query and the refetch is about to be issued. Arming it
  // before the awaits let tiles still in flight from the PREVIOUS query satisfy it —
  // on a city switch that meant the lifecycle reached 'ready' (overlay hidden, chat
  // unlocked) on an empty tile pass built from the old city's data.
  mapQueryChangedAt = nowMs()
  firstTreesSourceLoadedLogged = false
  firstMapIdleAfterPublishLogged = false
  // Use forceTreesTileRefetchPass instead of addTreeLayers so the tile URL nonce is always
  // incremented after a publish. setTiles/reload alone can leave MapLibre's internal tile
  // cache serving stale data (old filtered trees popping in); a new nonce forces a clean fetch.
  forceTreesTileRefetchPass()
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

async function initializeRequestedCity(map: maplibregl.Map): Promise<void> {
  while (true) {
    const city = (lifecycleRequestedCity.value ?? selectedCity.value) as CityCode
    const transition = lifecycleStartLoading(lifecycleCurrentSnapshot(city) ?? undefined)
    if (!transition) return

    loadingMessage.value = 'Counting our conifers...'
    mapQueryChangedAt = nowMs()
    firstTreesSourceLoadedLogged = false
    firstMapIdleAfterPublishLogged = false
    lastVisibleRangeSigByZoom.clear()
    introLockedRangeByZoom.clear()
    jumpMapToCity(city)

    await setCityContext(city)
    if (!lifecycleCommitContextCity(transition)) continue
    await setTileQuery(currentMapQuery.value)
    if (!lifecycleMatches(transition)) continue
    await setPublishedTreeIdFilterSql(publishedTreeIdFilterSql.value)
    if (!lifecycleMatches(transition)) continue

    addTreeLayers()
    bindTreeInteractions()

    if (!props.simplified) {
      addCityMarkers(map, city)
      if (!globeMarkersBound) {
        globeMarkersBound = true
        bindCityMarkerInteractions(map, (code) => { void switchCity(code) })
      }
    }

    if (landmarks.value.length > 0) {
      registerLandmarkEyeIcon(map)
      addLandmarkLayer(map, landmarks.value)
      bindLandmarkInteractions()
    }

    return
  }
}

// --- Lifecycle ---

onMounted(async () => {
  lifecycleSetManualCitySelectionReady(false)
  window.addEventListener('keydown', onWasdKeyDown)
  window.addEventListener('keyup', onWasdKeyUp)
  // Resolve the initial city before hydrating any map or query state.
  await router.isReady()
  const mountedRouteCity = readRouteCity(route.query.city)
  if (!initialUserCityDetectionDone.value) {
    const bootstrapResolution = await resolveBootstrapCity({
      routeCity: mountedRouteCity,
      defaultCity: selectedCity.value as CityCode,
      resolveSharedLocationCity: async () => {
        await restoreGrantedUserLocation(true)
        return lifecycleRequestedCity.value as CityCode | null
      },
    })
    commitResolvedCity(bootstrapResolution.city)
    if (bootstrapResolution.source !== 'route' && readRouteCity(route.query.city) !== bootstrapResolution.city) {
      await router.replace({ query: { ...route.query, city: bootstrapResolution.city } })
    }
    markInitialUserCityDetectionDone()
  } else {
    const resolvedBootstrapCity = (lifecycleRequestedCity.value ?? mountedRouteCity ?? selectedCity.value) as CityCode
    commitResolvedCity(resolvedBootstrapCity)
  }
  void restoreGrantedUserLocation(false)

  // Kick off DuckDB init now that the city is known so it runs in parallel with
  // map style loading instead of waiting until the map's 'load' event fires.
  preWarmForCity(mapDisplayCity.value)

  if (!isWebGLSupported()) {
    mapError.value = 'Your browser does not support WebGL, which is required to display the map. Try enabling hardware acceleration in your browser settings, or use a different browser.'
    lifecycleForceReady() // Force to ready so error message is visible
    return
  }

  mapInitStartedAt = nowMs()
  console.info('[Perf] map:init:start')

  const map = new maplibregl.Map({
    container: mapContainer.value!,
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    zoom: props.simplified ? 13 : INTRO_START_ZOOM,
    center: CITY_CONFIG[mapDisplayCity.value].center,
    pitch: props.simplified ? 0 : 60,
    bearing: props.simplified ? INTRO_END_BEARING : INTRO_START_BEARING,
    maxPitch: props.simplified ? 0 : 70,
    maxZoom: 21,
    keyboard: true,
  })
  mapRef.value = map
  // Debug / e2e handle. Playwright needs the live map to hit-test a rendered
  // tree and click its exact pixel — see e2e/tree-card.spec.ts.
  ;(window as unknown as { __treeMap?: maplibregl.Map }).__treeMap = map
  // Seed the compass before the first 'move' so it doesn't briefly read north
  // while the camera actually starts at INTRO_START_BEARING.
  mapBearing.value = map.getBearing()

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
      lifecycleForceReady() // Force to ready so error message is visible
    }
  })

  // The MapCompass overlay replaces the built-in compass button on both layouts.
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
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
        const loadedCity = (lifecycleRequestedCity.value ?? selectedCity.value) as CityCode
        const transition = lifecycleCurrentSnapshot(loadedCity)
        if (!transition) return
        // Notify the lifecycle state machine that tiles are loaded.
        // On initial load: loading → intro (desktop) or loading → ready (mobile).
        // On city switch: switching → ready.
        if (lifecyclePhase.value === 'switching') {
          lifecycleCitySwitchReady(transition)
        } else {
          lifecycleTilesLoaded(transition, !!props.simplified)
        }
        mapContainer.value?.setAttribute('data-trees-loaded-for', loadedCity)
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

    void ensureTileProtocolRegistered((lifecycleRequestedCity.value ?? selectedCity.value) as CityCode)
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

        lifecycleSetManualCitySelectionReady(true)
        await initializeRequestedCity(map)
      })
      .catch((e) => {
        mapError.value = (e as Error).message
        lifecycleForceReady(lifecycleRenderedCity.value ? { id: 0, city: lifecycleRenderedCity.value } : null) // Force to ready so error is visible
      })
  })
})

onUnmounted(() => {
  lifecycleSetManualCitySelectionReady(false)
  window.removeEventListener('keydown', onWasdKeyDown)
  window.removeEventListener('keyup', onWasdKeyUp)
  if (wasdRafId !== null) cancelAnimationFrame(wasdRafId)
  stopTileRefreshMessage()
  cancelIntro()
  if (pendingSwoopFlyTimeout != null) {
    window.clearTimeout(pendingSwoopFlyTimeout)
    pendingSwoopFlyTimeout = null
  }
  selectedTree.value = null
  if (activeLandmarkPopup) { activeLandmarkPopup.remove(); activeLandmarkPopup = null }
  if (mapRef.value) {
    removeLandmarkLayer(mapRef.value)
    removeCityMarkers(mapRef.value)
  }
  zoomControlLabelEl = null
  landmarkInteractionsBound = false
  globeMarkersBound = false
  releaseSharedPositionWatch?.()
  releaseSharedPositionWatch = null
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
/* ── Tree info card ──────────────────────────────────────────── */

.tree-card {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, calc(-100% - 18px));
  z-index: 10;
  width: min(840px, calc(100% - 24px));
  max-height: min(420px, 55vh);
  background: linear-gradient(160deg, rgba(30, 34, 41, 0.98), rgba(20, 24, 29, 0.98));
  border: 1px solid rgba(167, 227, 178, 0.18);
  border-radius: 14px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45);
  color: rgba(237, 242, 235, 0.92);
  font-size: 0.8rem;
  display: flex;
  flex-direction: column;
  overflow: visible;
  pointer-events: auto;
}

.tree-card::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: -9px;
  width: 16px;
  height: 16px;
  transform: translateX(-50%) rotate(45deg);
  background: rgba(20, 24, 29, 0.98);
  border-right: 1px solid rgba(167, 227, 178, 0.18);
  border-bottom: 1px solid rgba(167, 227, 178, 0.18);
}

.tree-card-close {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(167, 227, 178, 0.14);
  border-radius: 50%;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  color: rgba(237, 242, 235, 0.58);
  font-size: 0.72rem;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.tree-card-close:hover {
  background: rgba(255, 255, 255, 0.12);
  color: rgba(237, 242, 235, 0.92);
}

.tree-card-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.tree-card-checkin {
  background: var(--color-leaf);
  color: #0b0f0d;
  border: 1px solid var(--color-leaf);
  font-family: var(--font-display);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 6px 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.tree-card-checkin:hover {
  background: transparent;
  color: var(--color-leaf);
}

.tree-card-header {
  padding: 14px 16px 10px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.1);
  flex-shrink: 0;
}

.tree-card-header-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.tree-card-title-wrap {
  min-width: 0;
  flex: 1;
}

.tree-card-title {
  font-size: 1.08rem;
  font-weight: 700;
  color: var(--color-ink, #edf2eb);
  line-height: 1.2;
}

.tree-card-species {
  margin-top: 3px;
  font-size: 0.78rem;
  font-style: italic;
  color: rgba(237, 242, 235, 0.6);
}

/* Three-pane body — side by side on desktop, stacked on mobile */
.tree-card-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 0;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

.tree-card-pane {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
}

.tree-card-pane--tree,
.tree-card-pane--species {
  border-right: 1px solid rgba(167, 227, 178, 0.08);
}

.tree-card-section-label {
  color: rgba(167, 227, 178, 0.52);
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 2px;
}

/* Shared grid for label/value pairs */
.tc-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 10px;
  align-items: start;
}

.tc-label {
  color: rgba(154, 166, 154, 0.7);
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  white-space: nowrap;
  padding-top: 1px;
}

.tc-value {
  color: rgba(237, 242, 235, 0.92);
  font-size: 0.8rem;
  font-weight: 600;
  line-height: 1.35;
}

/* Photo carousel */
.tc-photo-wrap {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  line-height: 0;
}

.tc-photo {
  width: 100%;
  height: 160px;
  object-fit: cover;
  display: block;
}

.tc-photo-footer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 4px 8px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.6));
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
}

.tc-photo-count {
  color: rgba(255, 255, 255, 0.7);
  font-size: 0.65rem;
  font-weight: 600;
}

.tc-photo-attr {
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.6rem;
  line-height: 1.3;
  overflow-wrap: break-word;
}

.tc-photo-loading {
  color: rgba(167, 227, 178, 0.6);
  font-size: 0.62rem;
  font-style: italic;
}

.tc-photo-placeholder {
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(237, 242, 235, 0.32);
  font-size: 0.76rem;
  font-style: italic;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.03);
}

.tc-description {
  color: rgba(237, 242, 235, 0.72);
  font-size: 0.76rem;
  line-height: 1.5;
  margin: 0;
}

/* ── Mobile: stack panes vertically ────────────────────────── */
@media (max-width: 640px) {
  .tree-card {
    width: calc(100% - 16px);
    max-height: 85vh;
    border-radius: 12px;
    overflow: hidden;
  }
  .tree-card-header {
    padding: 10px 12px 8px;
    position: sticky;
    top: 0;
    z-index: 2;
    background: linear-gradient(160deg, rgba(30, 34, 41, 0.98), rgba(20, 24, 29, 0.98));
  }
  .tree-card-header-main {
    gap: 10px;
  }
  .tree-card-title {
    font-size: 0.96rem;
    line-height: 1.15;
  }
  .tree-card-species {
    margin-top: 2px;
    font-size: 0.72rem;
  }
  .tree-card-close {
    width: 22px;
    height: 22px;
    font-size: 0.64rem;
  }
  .tree-card-body {
    grid-template-columns: 1fr;
    overflow-y: auto;
    min-height: 0;
  }
  .tree-card-pane {
    padding: 10px 12px;
    gap: 6px;
    overflow-y: visible;
  }
  .tree-card-section-label {
    font-size: 0.58rem;
    letter-spacing: 0.1em;
  }
  .tc-grid {
    gap: 3px 8px;
  }
  .tc-label {
    font-size: 0.62rem;
  }
  .tc-value {
    font-size: 0.76rem;
    line-height: 1.3;
  }
  .tc-description {
    font-size: 0.72rem;
    line-height: 1.42;
  }
  .tree-card-pane--tree,
  .tree-card-pane--species {
    border-right: none;
    border-bottom: 1px solid rgba(167, 227, 178, 0.08);
  }
  .tc-photo-wrap {
    overflow: visible;
  }
  .tc-photo {
    height: auto;
    min-height: 196px;
    aspect-ratio: 4 / 3;
  }
  .tc-photo-placeholder {
    min-height: 156px;
    height: auto;
  }
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
