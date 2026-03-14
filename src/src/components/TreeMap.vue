<template>
  <div ref="mapContainer" class="tree-map"></div>
  <div v-if="isInitialLoading" class="map-loading">{{ loadingMessage }}</div>
  <div v-if="displayError" class="map-error">{{ displayError }}</div>
  <div v-if="!isInitialLoading" class="map-legend">
    <div v-for="entry in legendEntries" :key="entry.color" class="legend-entry">
      <span class="legend-swatch" :style="{ background: entry.color }"></span>
      <span class="legend-label">{{ entry.label }}</span>
    </div>
  </div>
  <div v-if="!props.simplified" class="city-selector">
    <button
      v-for="(cfg, code) in CITY_CONFIG"
      :key="code"
      class="city-btn"
      :class="{ active: selectedCity === code }"
      :disabled="isInitialLoading"
      @click="void switchCity(code as CityCode)"
    >{{ cfg.name }}</button>
  </div>
</template>

<script setup lang="ts">
import { ref, shallowRef, onMounted, onUnmounted, watch, computed } from 'vue'
import maplibregl from 'maplibre-gl'
import { useTreeData } from '../composables/useTreeData'
import { registerCategoryColoredIcons } from '../composables/useTreeCategories'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData, CITY_CONFIG, type CityCode } from '../composables/useMapData'
import { useRoute, useRouter } from 'vue-router'
import { useDuckDB } from '../composables/useDuckDB'
import { useMapIntro } from '../composables/useMapIntro'
import { useMapLayers, TREES_SOURCE_MAXZOOM } from '../composables/useMapLayers'
import { useMapIntroAnimation, INTRO_START_ZOOM } from '../composables/useMapIntroAnimation'

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
let popupRequestToken = 0
let zoomControlLabelEl: HTMLDivElement | null = null
let pendingSwoopFlyTimeout: number | null = null
const lastVisibleRangeSigByZoom = new Map<number, string>()
const introLockedRangeByZoom = new Map<number, { minX: number; maxX: number; minY: number; maxY: number }>()

const INITIAL_TILE_PREFETCH_SCALE = 3.5
const SCROLL_WHEEL_ZOOM_RATE = 1 / 5800
const SCROLL_ZOOM_RATE = 1 / 400

// --- Composables ---

const {
  query: duckQuery,
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
  workerDistinctColors,
  workerColorLabelMap,
} = useDuckDB()

const { loading, error, getSpeciesEnrichment } = useTreeData()
const { target: flyToTarget } = useFlyTo()
const route = useRoute()
const router = useRouter()
const { selectedCity, setSelectedCity, currentMapQuery, publishedTreeIdFilterSql, colorOverrideSql, colorLabelMap, mapQueryRevision } = useMapData()

// Initialise city from URL on first load
const urlCity = route.query.city
if (urlCity && urlCity in CITY_CONFIG) setSelectedCity(urlCity as CityCode)

const introCenterRef = computed((): [number, number] => CITY_CONFIG[selectedCity.value].center)

// --- Computed ---

const displayError = computed(() => error.value ?? mapError.value)
const isInitialLoading = computed(() => loading.value || defaultQueryLoading.value || introActive.value)

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

async function showTreePopup(feature: GeoJSON.Feature, offset: number) {
  if (!mapRef.value) return
  const requestToken = ++popupRequestToken
  const coords = (feature.geometry as GeoJSON.Point).coordinates.slice() as [number, number]
  const id = Number(feature.properties?.id)
  if (!Number.isFinite(id) || id <= 0) return
  try {
    const { rows } = await duckQuery(`
      SELECT tree_id, common_name, species, plant_date, diameter_at_breast_height
      FROM trees
      WHERE tree_id = ${id}
      LIMIT 1
    `)
    const row = rows[0] as any
    if (!row || requestToken !== popupRequestToken) return
    const enrichment = getSpeciesEnrichment(row.species)
    if (activeTreePopup) { activeTreePopup.remove(); activeTreePopup = null }
    const popup = new maplibregl.Popup({ offset, className: 'tree-popup' })
      .setLngLat(coords)
      .setHTML(formatPopupHtml(row, enrichment))
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

// --- City switching ---

let citySwitchInProgress = false

async function switchCity(city: CityCode) {
  if (city === selectedCity.value || citySwitchInProgress) return
  citySwitchInProgress = true

  try {
    const { center, name } = CITY_CONFIG[city]

    await runGlobeSwoopTo(center, name)

    // State updates after landing — triggers the query/filter watcher which reloads tiles.
    setSelectedCity(city)
    void router.replace({ query: { ...route.query, city } })
    void setCityContext(city)
  } finally {
    citySwitchInProgress = false
  }
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
watch([currentMapQuery, publishedTreeIdFilterSql, mapQueryRevision], async ([query, filterSql]) => {
  if (!mapRef.value?.loaded()) return
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

// When the worker posts new color info (init or agent override), rebuild layers
watch(workerDistinctColors, () => {
  if (!mapRef.value?.loaded()) return
  applyColorToLayers()
  const src = mapRef.value.getSource('trees') as any
  if (src && typeof src.reload === 'function') src.reload()
})

// --- Lifecycle ---

onMounted(() => {
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

    void ensureTileProtocolRegistered()
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
      })
      .catch((e) => {
        mapError.value = (e as Error).message
      })
  })
})

onUnmounted(() => {
  cancelIntro()
  if (pendingSwoopFlyTimeout != null) {
    window.clearTimeout(pendingSwoopFlyTimeout)
    pendingSwoopFlyTimeout = null
  }
  if (activeTreePopup) { activeTreePopup.remove(); activeTreePopup = null }
  zoomControlLabelEl = null
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

.city-selector {
  position: absolute;
  top: 10px;
  left: 10px;
  display: flex;
  gap: 4px;
  z-index: 4;
}

.city-btn {
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid rgba(79, 195, 247, 0.3);
  background: rgba(22, 33, 62, 0.88);
  color: #8ca0c8;
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.city-btn:hover {
  background: rgba(30, 50, 90, 0.95);
  color: #c8daf8;
  border-color: rgba(79, 195, 247, 0.55);
}

.city-btn.active {
  background: rgba(15, 52, 96, 0.96);
  color: #4fc3f7;
  border-color: rgba(79, 195, 247, 0.7);
}

.city-btn:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
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
