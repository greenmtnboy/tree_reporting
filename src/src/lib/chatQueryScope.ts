/**
 * What ground a map-chat query is about, read from its Trilogy text.
 *
 * The map holds the trees under the camera, not a city, so the chat's two
 * kinds of query are scoped two ways:
 *
 * - A **per-tree rule** (`select tree_id where genus = 'Quercus'`) says
 *   whether one row matches by looking at that row. The worker runs it over
 *   each cell of trees as the cell loads, so it needs no bounds: it is only
 *   ever asked about the ground the map holds, and it follows the camera.
 * - Anything that ranks, limits or aggregates (`order by ... limit 10`,
 *   `count(tree_id)`) has an answer that depends on which rows it is asked
 *   over, so it runs once against the published files and must say where:
 *   a city, or a latitude and longitude range. Unbounded, it reads every
 *   inventory in the world.
 *
 * These are checks on the text, there to catch an agent that forgot, with an
 * error that says what to add. They are not a parser: a filter hidden in an
 * `or` passes.
 */

/** The text with string literals emptied and `#` comments dropped, lower-cased. */
export function queryShape(query: string): string {
  return query
    .replace(/'(?:[^'\\]|\\.|'')*'/g, "''")
    .replace(/"(?:[^"\\]|\\.)*"/g, '""')
    .replace(/#[^\n]*/g, ' ')
    .toLowerCase()
}

const AGGREGATES = [
  'count', 'count_distinct', 'sum', 'avg', 'mean', 'min', 'max', 'median', 'stddev', 'variance',
  'array_agg', 'string_agg', 'group', 'bool_or', 'bool_and', 'any', 'mode',
]
const WINDOWS = ['rank', 'row_number', 'lag', 'lead', 'dense_rank', 'ntile', 'percent_rank', 'first_value', 'last_value']

/**
 * Why `query` is not a per-tree rule, or null if it is one. A rule's answer
 * for a tree must not depend on the other rows it was asked with.
 */
export function ruleBlocker(query: string): string | null {
  const shape = queryShape(query)
  if (/\blimit\b/.test(shape)) return 'it has a LIMIT'
  if (/\border\s+by\b/.test(shape)) return 'it has an ORDER BY'
  if (/\bhaving\b/.test(shape)) return 'it has a HAVING'
  const called = (names: string[]) => names.find((name) => new RegExp(`\\b${name}\\s*\\(`).test(shape))
  const aggregate = called(AGGREGATES)
  if (aggregate) return `it aggregates (${aggregate})`
  const window = called(WINDOWS)
  if (window) return `it ranks rows against each other (${window})`
  if (/\bover\s*\(|\)\s*(over|by)\b/.test(shape)) return 'it computes a value across rows'
  return null
}

export type QueryBounds = 'city' | 'box'

/** The bounds a query names: a city filter, a latitude and longitude range, or neither. */
export function queryBounds(query: string): QueryBounds | null {
  const shape = queryShape(query)
  if (/\bcity\s*(=|in\b)/.test(shape)) return 'city'
  const ranged = (column: string) =>
    new RegExp(`\\b${column}\\s*(between\\b|[<>]=?)|[<>]=?\\s*${column}\\b`).test(shape)
  return ranged('latitude') && ranged('longitude') ? 'box' : null
}

/** How to bound a query, for the error that refuses an unbounded one and for the prompt. */
export const BOUNDS_HELP =
  "Bound it one of two ways. A city: WHERE city = 'USSFO' (the city codes are in the prompt). " +
  'Or a place and a distance: take the centre from lookup_landmark (or the user location) and filter ' +
  'WHERE latitude BETWEEN lat - d AND lat + d AND longitude BETWEEN lng - e AND lng + e, ' +
  'with d = km / 111 and e = km / (111 * cos(lat)); 1 km is about 0.009 degrees of latitude.'

export function unboundedQueryError(tool: string): string {
  return `${tool} was not run: the query has no bounds, so it would read every city's inventory. ${BOUNDS_HELP}`
}
