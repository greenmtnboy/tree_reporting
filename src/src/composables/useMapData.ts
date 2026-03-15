import { ref } from 'vue'
import type { ColorLabelMap } from '../types'

export const CITY_CONFIG = {
  USSFO: { name: 'San Francisco', center: [-122.4194, 37.7749] as [number, number] },
  USNYC: { name: 'New York City', center: [-73.9665, 40.7812] as [number, number] },
  USBOS: { name: 'Boston', center: [-71.0589, 42.3601] as [number, number] },
} as const

export type CityCode = keyof typeof CITY_CONFIG

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
const userLocation = ref<{ lat: number; lng: number } | null>(null)

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

  function setSelectedCity(city: CityCode) {
    selectedCity.value = city
    currentMapQuery.value = buildDefaultQueryForCity(city)
    publishedTreeIdFilterSql.value = null
    colorOverrideSql.value = null
    colorLabelMap.value = null
    mapQueryRevision.value += 1
  }

  function setUserLocation(lat: number, lng: number) {
    userLocation.value = { lat, lng }
  }

  function clearUserLocation() {
    userLocation.value = null
  }

  return {
    selectedCity,
    setSelectedCity,
    currentMapQuery,
    publishedTreeIdFilterSql,
    colorOverrideSql,
    colorLabelMap,
    mapQueryRevision,
    userLocation,
    setUserLocation,
    clearUserLocation,
    publishMapQuery,
    publishMapTreeIdFilterSql,
    clearMapTreeIdFilter,
    publishColorOverride,
    clearColorOverride,
  }
}
