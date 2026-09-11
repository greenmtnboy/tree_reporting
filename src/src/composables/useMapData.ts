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

/**
 * How far from a city's configured center a tree may still be submitted as that
 * city's tree.
 *
 * Measured rather than picked: the furthest any city's `CITY_BOUNDS` corner sits
 * from its `cityConfig.json` center is Halifax at 106 km, so 150 clears every
 * wired city and leaves room for the next one. It is a sanity bound, not a
 * boundary — `CITY_TERRITORY` in `data/raw/_ingest_shared.py` is what actually
 * decides which city an unattributed tree belongs to, and it runs downstream.
 */
export const CITY_RADIUS_KM = 150

/** Distance from a city's configured center, or null for an unknown city code. */
export function distanceToCityKm(city: CityCode, lat: number, lng: number): number | null {
  const cfg = CITY_CONFIG[city]
  if (!cfg) return null
  return haversineKm(lat, lng, cfg.center[1], cfg.center[0])
}

/** Whether coordinates are near enough to `city` to be recorded as its tree. */
export function isWithinCity(city: CityCode, lat: number, lng: number): boolean {
  const distance = distanceToCityKm(city, lat, lng)
  return distance !== null && distance <= CITY_RADIUS_KM
}

/**
 * The city a point is actually in, or null when the nearest one is too far.
 *
 * Deliberately separate from `closestCityTo`, which is total and answers a
 * different question — "which city should the map show this person?" — where the
 * nearest city beats none however far away it is. Use this one wherever the
 * answer gets written down as data: a submission made in Milos before Milos was
 * on the map was recorded as Berlin, 1,955 km away, and the ingest then dropped
 * it for falling outside Berlin's territory, so the tree reached neither city.
 */
export function cityAt(lat: number, lng: number): CityCode | null {
  const city = closestCityTo(lat, lng)
  return isWithinCity(city, lat, lng) ? city : null
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

const { contextCity, requestedCity } = useMapLifecycle()

/**
 * The city the UI should present *right now*.
 *
 * During a city switch the globe swoop runs for several seconds before the new
 * city's data context is committed, so `selectedCity` still reports the old
 * city for the whole animation. Navigation links, titles, and anything else the
 * user can act on mid-swoop must follow the requested city instead — otherwise
 * clicking through to another view lands on the city we're flying away from.
 *
 * Use `selectedCity` only when you need the city whose data context is actually
 * committed (queries, DuckDB context).
 */
const displayCity = computed<CityCode>(() => (requestedCity.value ?? selectedCity.value) as CityCode)

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
    displayCity,
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
