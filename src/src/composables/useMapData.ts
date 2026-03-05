import { ref } from 'vue'
import type { ColorLabelMap } from '../types'

export const DEFAULT_MAP_QUERY = `
SELECT
  tree_id,
  species,
  latitude,
  longitude,
  diameter_at_breast_height
FROM trees
WHERE latitude IS NOT NULL AND longitude IS NOT NULL
`

const currentMapQuery = ref<string>(DEFAULT_MAP_QUERY)
const publishedTreeIdFilterSql = ref<string | null>(null)
const colorOverrideSql = ref<string | null>(null)
const colorLabelMap = ref<ColorLabelMap | null>(null)
const mapQueryRevision = ref(0)

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

  return {
    currentMapQuery,
    publishedTreeIdFilterSql,
    colorOverrideSql,
    colorLabelMap,
    mapQueryRevision,
    publishMapQuery,
    publishMapTreeIdFilterSql,
    clearMapTreeIdFilter,
    publishColorOverride,
    clearColorOverride,
  }
}
