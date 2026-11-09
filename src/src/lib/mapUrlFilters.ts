import type { LocationQuery } from 'vue-router'
import { DBH_COLORS } from '../workers/cityTables'

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

export type MapColorBy = 'dbh' | 'tree_form' | 'foliage'

export function readMapColorBy(query: LocationQuery, filters: MapUrlFilters): MapColorBy {
  if (query.colorBy === 'dbh' || query.colorBy === 'tree_form' || query.colorBy === 'foliage') return query.colorBy
  return hasMapUrlFilters(filters) ? 'dbh' : 'foliage'
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

// The trunk-diameter colours are a rule the worker applies to every row
// (cityTables.ts `dbhColorSql`); this is their legend. Published
// diameter_at_breast_height is in inches, and a missing measurement stays
// visibly unknown rather than taking a default size's colour.
export const DBH_LEGEND = {
  [DBH_COLORS.under6]: 'Under 6 in (15 cm)',
  [DBH_COLORS.under12]: '6–<12 in (15–<30 cm)',
  [DBH_COLORS.under24]: '12–<24 in (30–<61 cm)',
  [DBH_COLORS.over24]: '24 in or more (61 cm)',
  [DBH_COLORS.unknown]: 'Unknown trunk diameter',
}
