import { polygonPredicate } from '../lib/viewFootprint'
import { CROWN_DISTANCE_MAX, MAX_CROWNS, MAX_TREE_SPRITES, MAX_CROWN_WIDTH, MAX_TREE_HEIGHT, MERCATOR_WORLD_METERS, mercatorPoint, metersPerMercatorUnit, type TreeRenderView } from '../lib/treeCrowns'

function boundsPredicate({ west, east, north, south }: NonNullable<TreeRenderView['bounds']>): string {
  if (![west, east, north, south].every(Number.isFinite) || north < south || east < west) throw new Error('Invalid sprite viewport')
  // Normalize only the west edge; a span of a whole world includes all longitudes.
  const span = east - west, w = ((west + 180) % 360 + 360) % 360 - 180, e = w + span
  const longitude = span >= 360 ? 'true' : e > 180
    ? `(tf.longitude >= ${w} OR tf.longitude <= ${e - 360})`
    : `tf.longitude BETWEEN ${w} AND ${e}`
  return `${longitude} AND tf.latitude BETWEEN ${Math.max(-85.05112878, south)} AND ${Math.min(85.05112878, north)}`
}

/** How the query reads a tree's colour; see `colorSql` in cityTables.ts. */
export type CrownColor = { join: string; expr: string }

/**
 * Where the query reads its trees: `from` is `trees_fast` or the held cells
 * under the view (`scopeSql`), and `trees`, when given, is the published
 * schema over the same cells, which the base query's own `FROM trees` then
 * reads instead of the whole world's view.
 */
export type CrownSource = { from: string; trees?: string | null }

function spriteLimit(camera: TreeRenderView): number {
  const limit = camera.spriteLimit ?? MAX_TREE_SPRITES
  if (!Number.isInteger(limit) || limit < 1) throw new Error('Invalid sprite limit')
  return limit
}

/**
 * The base query already includes the current chat/species filters. The
 * published crown and height predictions are columns of `trees_fast`
 * (`predicted_crown_width_m`, `predicted_height_m`), folded in at city load;
 * a valid measurement wins over either.
 *
 * `baseQuery` null means the default: every row of `source.from` is in
 * scope, and the filter is skipped. A filter is a semi join on the tree id;
 * the DISTINCT join it replaced built the whole city's id set every query
 * (~170 of ~190 ms on San Francisco).
 */
export function crownQuery(camera: TreeRenderView, baseQuery: string | null, color: CrownColor, source: CrownSource = { from: 'trees_fast' }): string {
  if (![camera.lng, camera.lat, camera.altitude].every(Number.isFinite) || Math.abs(camera.lat) > 85.05112878 || camera.altitude < 0) {
    throw new Error('Invalid crown camera')
  }
  const [mx, my] = mercatorPoint(camera.lng, camera.lat)
  const x = (mx - 0.5) * MERCATOR_WORLD_METERS
  const y = (0.5 - my) * MERCATOR_WORLD_METERS
  const cosLat = metersPerMercatorUnit(camera.lat) / MERCATOR_WORLD_METERS
  const range = CROWN_DISTANCE_MAX / cosLat
  let extent = `tf.x_3857 BETWEEN ${x - range} AND ${x + range} AND tf.y_3857 BETWEEN ${y - range} AND ${y + range}`
  if (camera.bounds) {
    extent = boundsPredicate(camera.bounds)
    if (camera.footprint) extent += ` AND ${polygonPredicate(camera.footprint, 'tf.x_3857', 'tf.y_3857')}`
  }
  const valid = (col: string, max = MAX_CROWN_WIDTH) => `isfinite(${col}) AND ${col} > 0 AND ${col} <= ${max}`
  return `
WITH ${baseQuery === null ? '' : `${source.trees ? `trees AS (${source.trees}), ` : ''}base AS (${baseQuery}), `}nearby AS (
  SELECT tf.tree_id AS id, tf.longitude AS lng, tf.latitude AS lat,
    CASE WHEN ${valid('tf.crown_width_m')} THEN tf.crown_width_m
      WHEN ${valid('tf.predicted_crown_width_m')} THEN tf.predicted_crown_width_m END AS width,
    COALESCE(${valid('tf.crown_width_m')}, false) AS measured,
    ${color.expr} AS color,
    CASE WHEN ${valid('tf.height_m', MAX_TREE_HEIGHT)} THEN tf.height_m
      WHEN ${valid('tf.predicted_height_m', MAX_TREE_HEIGHT)} THEN tf.predicted_height_m END AS height,
    CAST(tf.tree_form AS VARCHAR) AS category, tf.dbh,
    ((tf.x_3857 - ${x}) * ${cosLat}) ** 2 +
    ((tf.y_3857 - ${y}) * ${cosLat}) ** 2 + ${camera.altitude ** 2} AS distance_squared
  FROM ${source.from} tf
  ${color.join}
  WHERE ${extent}${baseQuery === null ? '' : `
    AND EXISTS (SELECT 1 FROM base WHERE base.tree_id = tf.tree_id)`}
)
SELECT id, lng, lat, width, measured, color, category, dbh, height FROM nearby
${camera.bounds ? '' : `WHERE width IS NOT NULL AND distance_squared < ${CROWN_DISTANCE_MAX ** 2}`}
ORDER BY distance_squared, id LIMIT ${camera.bounds ? spriteLimit(camera) : MAX_CROWNS}`
}
