import { CROWN_DISTANCE_MAX, MAX_CROWNS, MAX_TREE_SPRITES, MAX_CROWN_WIDTH, MERCATOR_WORLD_METERS, mercatorPoint, metersPerMercatorUnit, type TreeRenderView } from '../lib/treeCrowns'

function boundsPredicate({ west, east, north, south }: NonNullable<TreeRenderView['bounds']>): string {
  if (![west, east, north, south].every(Number.isFinite) || north < south || east < west) throw new Error('Invalid sprite viewport')
  // Normalize only the west edge; a span of a whole world includes all longitudes.
  const span = east - west, w = ((west + 180) % 360 + 360) % 360 - 180, e = w + span
  const longitude = span >= 360 ? 'true' : e > 180
    ? `(tf.longitude >= ${w} OR tf.longitude <= ${e - 360})`
    : `tf.longitude BETWEEN ${w} AND ${e}`
  return `${longitude} AND tf.latitude BETWEEN ${Math.max(-85.05112878, south)} AND ${Math.min(85.05112878, north)}`
}

/** The base query already includes the current chat/species filters. */
export function crownQuery(camera: TreeRenderView, baseQuery: string, predictionsTable: string): string {
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
  }
  const valid = (col: string) => `isfinite(${col}) AND ${col} > 0 AND ${col} <= ${MAX_CROWN_WIDTH}`
  return `
WITH base AS (${baseQuery}), nearby AS (
  SELECT tf.tree_id AS id, tf.longitude AS lng, tf.latitude AS lat,
    CASE WHEN ${valid('tf.crown_width_m')} THEN tf.crown_width_m
      WHEN ${valid('p.width')} THEN p.width END AS width,
    COALESCE(${valid('tf.crown_width_m')}, false) AS measured,
    cm.display_color AS color,
    tf.tree_form AS category, tf.dbh,
    ${camera.visibleBounds ? `(${boundsPredicate(camera.visibleBounds)})` : 'true'} AS in_viewport,
    ((tf.x_3857 - ${x}) * ${cosLat}) ** 2 +
    ((tf.y_3857 - ${y}) * ${cosLat}) ** 2 + ${camera.altitude ** 2} AS distance_squared
  FROM trees_fast tf
  INNER JOIN (SELECT DISTINCT tree_id FROM base) visible ON tf.tree_id = visible.tree_id
  INNER JOIN __tree_color_map cm ON tf.tree_id = cm.tree_id
  LEFT JOIN ${predictionsTable} p ON tf.tree_id = p.tree_id AND tf.city = p.city
  WHERE ${extent}
)
SELECT id, lng, lat, width, measured, color, category, dbh FROM nearby
${camera.bounds ? '' : `WHERE width IS NOT NULL AND distance_squared < ${CROWN_DISTANCE_MAX ** 2}`}
ORDER BY in_viewport DESC, distance_squared, id LIMIT ${camera.bounds ? MAX_TREE_SPRITES : MAX_CROWNS}`
}
