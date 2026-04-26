import { computed, ref, watch } from 'vue'
import type { ColorLabelMap } from '../types'
import cityConfigData from '../cityConfig.json'
import { useMapLifecycle } from './useMapLifecycle'
import { clearSharedPosition, setSharedPosition, useSharedPosition } from '../lib/geo'

export const CITY_CONFIG = cityConfigData as unknown as Record<string, { name: string; center: [number, number] }>
export type CityCode = string

export function haversineKm(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLng = ((lng2 - lng1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

/** Returns the city code whose center is closest to the given coordinates. Pure, no Vue state. */
export function closestCityTo(lat: number, lng: number): CityCode {
  let closest: CityCode = Object.keys(CITY_CONFIG)[0]
  let minDist = Infinity
  for (const [code, cfg] of Object.entries(CITY_CONFIG)) {
    const dist = haversineKm(lat, lng, cfg.center[1], cfg.center[0])
    if (dist < minDist) { minDist = dist; closest = code }
  }
  return closest
}

export function buildDefaultQueryForCity(city: CityCode): string {
  return `
SELECT
  tree_id,
  species,
  latitude,
  longitude,
  diameter_at_breast_height
FROM trees
WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND city = '${city}'
`
}

export const DEFAULT_MAP_QUERY = buildDefaultQueryForCity('USSFO')

const selectedCity = ref<CityCode>('USSFO')
const currentMapQuery = ref<string>(DEFAULT_MAP_QUERY)
const publishedTreeIdFilterSql = ref<string | null>(null)
const colorOverrideSql = ref<string | null>(null)
const colorLabelMap = ref<ColorLabelMap | null>(null)
const mapQueryRevision = ref(0)
const initialUserCityDetectionDone = ref(false)
const { sharedPosition } = useSharedPosition()
const userLocation = computed(() => (
  sharedPosition.value
    ? { lat: sharedPosition.value.lat, lng: sharedPosition.value.lng }
    : null
))
const userLocationAccuracy = computed(() => sharedPosition.value?.accuracy ?? null)

function applyCommittedCity(city: CityCode) {
  selectedCity.value = city
  currentMapQuery.value = buildDefaultQueryForCity(city)
  publishedTreeIdFilterSql.value = null
  colorOverrideSql.value = null
  colorLabelMap.value = null
  mapQueryRevision.value += 1
}

function commitResolvedCity(city: CityCode) {
  if (selectedCity.value === city && currentMapQuery.value === buildDefaultQueryForCity(city)) return
  applyCommittedCity(city)
}

const { contextCity } = useMapLifecycle()

watch(
  contextCity,
  (city) => {
    if (!city || city === selectedCity.value) return
    applyCommittedCity(city)
  },
  { immediate: true },
)

export function useMapData() {
  function publishMapQuery(query: string) {
    currentMapQuery.value = query.trim()
    mapQueryRevision.value += 1
  }

  function publishMapTreeIdFilterSql(sql: string) {
    publishedTreeIdFilterSql.value = sql.trim()
    mapQueryRevision.value += 1
  }

  function clearMapTreeIdFilter() {
    publishedTreeIdFilterSql.value = null
    colorOverrideSql.value = null
    colorLabelMap.value = null
    mapQueryRevision.value += 1
  }

  // Set an agent-driven per-tree color override. The sql must return (tree_id, override_color).
  // Does NOT increment mapQueryRevision — call publishMapTreeIdFilterSql afterwards to trigger reload.
  function publishColorOverride(sql: string | null, labels: ColorLabelMap | null) {
    colorOverrideSql.value = sql
    colorLabelMap.value = labels
  }

  function clearColorOverride() {
    colorOverrideSql.value = null
    colorLabelMap.value = null
    mapQueryRevision.value += 1
  }

  function setUserLocation(lat: number, lng: number, accuracy?: number | null) {
    setSharedPosition({
      lat,
      lng,
      accuracy: accuracy ?? null,
      heading: null,
      speed: null,
    })
  }

  function clearUserLocation() {
    clearSharedPosition()
  }

  function markInitialUserCityDetectionDone() {
    initialUserCityDetectionDone.value = true
  }

  return {
    selectedCity,
    currentMapQuery,
    publishedTreeIdFilterSql,
    colorOverrideSql,
    colorLabelMap,
    mapQueryRevision,
    userLocation,
    userLocationAccuracy,
    initialUserCityDetectionDone,
    setUserLocation,
    clearUserLocation,
    commitResolvedCity,
    markInitialUserCityDetectionDone,
    publishMapQuery,
    publishMapTreeIdFilterSql,
    clearMapTreeIdFilter,
    publishColorOverride,
    clearColorOverride,
  }
}
