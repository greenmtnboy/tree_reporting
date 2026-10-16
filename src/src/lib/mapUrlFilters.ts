import type { LocationQuery } from 'vue-router'

// Public hash-query contract. Only these fields become SQL identifiers.
export const MAP_FILTER_FIELDS = ['species', 'genus'] as const
export type MapFilterField = typeof MAP_FILTER_FIELDS[number]
export type MapUrlFilters = Record<MapFilterField, string[]>

export function readMapUrlFilters(query: LocationQuery): MapUrlFilters {
  const values = (field: MapFilterField) => {
    const raw = query[`filter.${field}`]
    return [...new Set((Array.isArray(raw) ? raw : [raw])
      .filter((value): value is string => typeof value === 'string' && !!value.trim())
      .map(value => value.trim()))]
  }
  return { species: values('species'), genus: values('genus') }
}

export function hasMapUrlFilters(filters: MapUrlFilters): boolean {
  return MAP_FILTER_FIELDS.some(field => filters[field].length > 0)
}

export function readMapColorBy(query: LocationQuery, filters: MapUrlFilters): 'dbh' | 'tree_form' {
  if (query.colorBy === 'dbh' || query.colorBy === 'tree_form') return query.colorBy
  return hasMapUrlFilters(filters) ? 'dbh' : 'tree_form'
}

const literal = (value: string) => `'${value.replace(/'/g, "''")}'`

/** Intersect the URL scope with the current query, including agent queries.
 * Use the original trees relation: an agent's projection need not contain species.
 * An empty match stays empty; it must never fall back to the whole city.
 */
export function applyMapUrlFilters(query: string, filters: MapUrlFilters): string {
  const predicates = MAP_FILTER_FIELDS.flatMap(field => {
    if (!filters[field].length) return []
    const column = field === 'genus' ? "split_part(species, ' ', 1)" : 'species'
    return [`${column} IN (${filters[field].map(literal).join(', ')})`]
  })
  if (!predicates.length) return query
  return `SELECT map_scope.* FROM (${query.trim().replace(/;+$/, '')}) map_scope
WHERE map_scope.tree_id IN (SELECT tree_id FROM trees WHERE ${predicates.join(' AND ')})`
}

// Published diameter_at_breast_height is in inches. Read it before the renderer
// substitutes a default size, so missing measurements remain visibly unknown.
export const DBH_LEGEND = {
  '#c7e9b4': 'Under 6 in (15 cm)',
  '#7fcdbb': '6–<12 in (15–<30 cm)',
  '#41b6c4': '12–<24 in (30–<61 cm)',
  '#225ea8': '24 in or more (61 cm)',
  '#9a9a9a': 'Unknown trunk diameter',
}

export const DBH_COLOR_SQL = `SELECT tree_id, CASE
  WHEN diameter_at_breast_height IS NULL OR NOT isfinite(diameter_at_breast_height)
    OR diameter_at_breast_height <= 0 THEN '#9a9a9a'
  WHEN diameter_at_breast_height < 6 THEN '#c7e9b4'
  WHEN diameter_at_breast_height < 12 THEN '#7fcdbb'
  WHEN diameter_at_breast_height < 24 THEN '#41b6c4'
  ELSE '#225ea8' END AS override_color FROM trees`
