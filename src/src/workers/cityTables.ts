/**
 * The SQL that builds a city's in-browser tables.
 *
 * Kept out of the worker so it can run under native DuckDB in tests. The
 * shapes here are the memory budget: a city's rows are the largest allocation
 * in the browser, DuckDB-wasm never returns heap to the OS, and iOS Safari
 * reloads a tab that outgrows its process budget (roughly 1 GB on a first
 * visit, a few hundred MB after reloads). Measured 2026-10-23, NYC's 1.16M
 * rows cost 393 MB as a 33-column table and 145 MB in this shape; see
 * docs/MOBILE_LOAD_BUDGET.md.
 *
 * What makes the row narrow:
 * - Low-cardinality strings (species, names, cultivar, source, city) are ENUM
 *   columns: two bytes a row instead of a sixteen-byte string slot plus heap.
 *   A city has a few hundred to a few thousand distinct species and names.
 *   The ENUM vocabularies are built from the loaded rows, so they are per city
 *   and are dropped with the table on a city switch.
 * - One tile index pair, at z20; coarser zooms shift it (`tileIndexSql`).
 *   The old table stored eight pairs.
 * - The published crown prediction is a column, not a second table, so the
 *   crown query has no join and the city switch has one fewer table to drop.
 * - No per-tree colour table: the default colour is a CASE over `tree_form`
 *   (`colorSql`); a chat override is a sparse table that exists only while
 *   it is active.
 *
 * The `trees` view presents the published schema with ENUMs cast back to
 * VARCHAR, so chat SQL and the URL filters see exactly the types the parquet
 * has. Readers of `trees_fast` inside the app may compare an ENUM column to a
 * string literal directly: a value outside the vocabulary compares unequal
 * rather than erroring (DuckDB 1.4.3, checked).
 */
import { sentinelLabelSql, sentinelTreeFormSql } from '../data/species'
import { CATEGORY_COLORS as DEFAULT_CATEGORY_COLORS } from '../treeFormColors'

export const TREES_TABLE = 'trees_fast'
export const TREES_VIEW = 'trees'
export const COLOR_OVERRIDE_TABLE = '__tree_color_override'
export const AGG_CACHE_TABLE = 'agg_z14_cache'
export const AGG_CACHE_ZOOM = 14
export const TILE_INDEX_ZOOM = 20

export const WEB_MERCATOR_MAX = 20037508.342789244
export const WEB_MERCATOR_WORLD = WEB_MERCATOR_MAX * 2

/** The icon categories; `default` is every form the atlas has no icon for. */
export const TREE_FORMS = ['default', 'palm', 'broadleaf', 'conifer', 'spreading', 'columnar', 'ornamental', 'weeping', 'multi_trunk'] as const

/** ENUM types the city tables declare, in creation order; dropped in reverse. */
export const CITY_ENUM_TYPES = {
  form: 'tree_form_t',
  city: 'tree_city_t',
  source: 'tree_source_t',
  species: 'tree_species_t',
  name: 'tree_name_t',
  sourceName: 'tree_source_name_t',
  cultivar: 'tree_cultivar_t',
} as const

export type CityTableOptions = {
  /**
   * The city's browser projection (data/raw/tree_browser_city.preql), when
   * it is published: the pipeline has already resolved the display name, the
   * growth form and the crown prediction, so the load is one scan of this
   * file and nothing below is read. Null falls back to assembling the same
   * shape here from the published inventory.
   */
  projectionUrl: string | null
  /** The city's published parquet (or the cross-city rollup as a last resort). */
  parquetUrl: string
  /** The crown predictions parquet to fold in, or null when none is reachable. */
  predictionsUrl: string | null
  /**
   * True when `predictionsUrl` holds one row per tree (a per-city slice,
   * `grain (tree_id)`). The all-city file is not guaranteed that, so it is
   * aggregated first; the aggregate's hash table is the single largest
   * transient of the fallback load, and the wasm heap keeps whatever it
   * grows to.
   */
  predictionsAreUnique: boolean
  city: string
  /** Parquet built before the prune still carries `is_duplicate`. */
  hasDedupFlag: boolean
  /** Parquet built before September 2026 lacks these columns. */
  hasCultivar: boolean
  hasCrownWidth: boolean
}

const sqlString = (value: string) => `'${value.replace(/'/g, "''")}'`

/** Statements that remove a loaded city, in dependency order. */
export function dropCityTablesSql(): string[] {
  return [
    `DROP VIEW IF EXISTS ${TREES_VIEW}`,
    `DROP TABLE IF EXISTS ${AGG_CACHE_TABLE}`,
    `DROP TABLE IF EXISTS ${COLOR_OVERRIDE_TABLE}`,
    `DROP TABLE IF EXISTS ${TREES_TABLE}`,
    ...Object.values(CITY_ENUM_TYPES).reverse().map((type) => `DROP TYPE IF EXISTS ${type}`),
  ]
}

/**
 * Statements that load a city into `trees_fast` and define the `trees` view,
 * to run in order after `dropCityTablesSql()`. The ENUM vocabularies are
 * read first, as projections of the parquet's string columns, and the one
 * row scan casts into them, so the wide VARCHAR table never exists.
 */
export function loadCityTablesSql(options: CityTableOptions): string[] {
  const { projectionUrl, parquetUrl, predictionsUrl, predictionsAreUnique, city, hasDedupFlag, hasCultivar, hasCrownWidth } = options
  const clampedLat = 'LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878)'
  const n = 2 ** TILE_INDEX_ZOOM

  // The rows, the display name, the growth form and the predicted width: from
  // the projection as columns, or assembled here from the published inventory,
  // the enrichment table and the predictions.
  let source: string
  let displayName: string
  let formExpr: string
  let predictedWidth: string
  let joins: string
  if (projectionUrl) {
    source = `SELECT
        tree_id,
        city,
        data_source,
        tree_name,
        display_name,
        tree_form,
        plant_date,
        species,
        cultivar,
        crown_width_m,
        predicted_crown_width_m,
        latitude,
        longitude,
        diameter_at_breast_height,
        submission_photo_url
      FROM read_parquet(${sqlString(projectionUrl)})
      WHERE latitude IS NOT NULL AND longitude IS NOT NULL`
    displayName = 't.display_name'
    formExpr = 't.tree_form'
    predictedWidth = 'CAST(t.predicted_crown_width_m AS FLOAT)'
    joins = ''
  } else {
    const dedupFilter = hasDedupFlag ? 'AND NOT COALESCE(is_duplicate, false)' : ''
    const cultivarColumn = hasCultivar ? 'cultivar' : 'CAST(NULL AS VARCHAR) AS cultivar'
    const crownWidthColumn = hasCrownWidth ? 'crown_width_m' : 'CAST(NULL AS DOUBLE) AS crown_width_m'
    const predictionRows = predictionsUrl
      ? predictionsAreUnique
        ? `SELECT tree_id, predicted_crown_width_m AS width
          FROM read_parquet(${sqlString(predictionsUrl)})
          WHERE city = ${sqlString(city)}`
        : `SELECT tree_id, max(predicted_crown_width_m) AS width
          FROM read_parquet(${sqlString(predictionsUrl)})
          WHERE city = ${sqlString(city)}
          GROUP BY tree_id`
      : null
    source = `SELECT
        tree_id,
        city,
        data_source,
        tree_name,
        plant_date,
        species,
        ${cultivarColumn},
        ${crownWidthColumn},
        latitude,
        longitude,
        diameter_at_breast_height,
        submission_photo_url
      FROM read_parquet(${sqlString(parquetUrl)})
      WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ${dedupFilter}`
    // The display name: enrichment's first common name, else the source's own
    // name, else the sentinel's label, else the scientific name. A placeholder
    // species has no enrichment row by design, so it is named here rather
    // than falling through to the bare sentinel string.
    displayName = `COALESCE(
        NULLIF(TRIM(CAST(list_extract(se.common_names, 1) AS VARCHAR)), ''),
        t.tree_name,
        ${sentinelLabelSql('t.species')},
        t.species
      )`
    // "Palm"/"Shrub"/"Cactus" name a growth form the source recorded without
    // a species; that is worth an icon even with no enrichment row.
    formExpr = `COALESCE(se.tree_form, ${sentinelTreeFormSql('t.species')}, 'default')`
    predictedWidth = predictionsUrl ? 'CAST(p.width AS FLOAT)' : 'CAST(NULL AS FLOAT)'
    joins = `LEFT JOIN species_enrichment se
      ON t.species = se.species
    ${predictionRows ? `LEFT JOIN (${predictionRows}) p ON t.tree_id = p.tree_id` : ''}`
  }

  // Each vocabulary is a projection of the parquet's string columns, read
  // before the rows so the one row scan can cast straight into the ENUMs.
  // The alternative, loading VARCHAR columns and retyping them in place,
  // materialises the wide table first, and that peak is what the wasm heap
  // keeps for the life of the tab.
  const vocabulary = (expr: string, join = '') =>
    `SELECT DISTINCT ${expr} AS value FROM (${source}) t ${join} WHERE ${expr} IS NOT NULL ORDER BY 1`
  const nameJoin = projectionUrl ? '' : 'LEFT JOIN species_enrichment se ON t.species = se.species'
  const enumSources: Array<[keyof typeof CITY_ENUM_TYPES, string]> = [
    ['city', vocabulary('t.city')],
    ['source', vocabulary('t.data_source')],
    ['species', vocabulary('t.species')],
    ['name', vocabulary(displayName, nameJoin)],
    ['sourceName', vocabulary('t.tree_name')],
    ['cultivar', vocabulary('t.cultivar')],
  ]
  const statements = [
    `CREATE TYPE ${CITY_ENUM_TYPES.form} AS ENUM (${TREE_FORMS.map(sqlString).join(', ')})`,
    ...enumSources.map(([key, query]) => `CREATE TYPE ${CITY_ENUM_TYPES[key]} AS ENUM (${query})`),
    `CREATE TABLE ${TREES_TABLE} AS
    WITH source AS (
      ${source}
    )
    SELECT
      t.tree_id,
      CAST(t.city AS ${CITY_ENUM_TYPES.city}) AS city,
      CAST(t.data_source AS ${CITY_ENUM_TYPES.source}) AS data_source,
      t.submission_photo_url,
      CAST(${displayName} AS ${CITY_ENUM_TYPES.name}) AS tree_name,
      CAST(t.tree_name AS ${CITY_ENUM_TYPES.sourceName}) AS source_tree_name,
      t.plant_date,
      CAST(t.species AS ${CITY_ENUM_TYPES.species}) AS species,
      CAST(t.cultivar AS ${CITY_ENUM_TYPES.cultivar}) AS cultivar,
      t.crown_width_m,
      ${predictedWidth} AS predicted_crown_width_m,
      t.latitude,
      t.longitude,
      t.diameter_at_breast_height,
      COALESCE(t.diameter_at_breast_height, 3) AS dbh,
      -- A form outside the vocabulary (a drifted enrichment value) is 'default'
      -- rather than a cast error that fails the whole load.
      CAST(CASE lower(trim(${formExpr}))
        ${TREE_FORMS.filter((form) => form !== 'default').map((form) => `WHEN '${form}' THEN '${form}'`).join('\n        ')}
        ELSE 'default'
      END AS ${CITY_ENUM_TYPES.form}) AS tree_form,
      ((t.longitude * ${WEB_MERCATOR_MAX}) / 180.0) AS x_3857,
      (6378137.0 * ln(tan(pi() / 4.0 + radians(${clampedLat}) / 2.0))) AS y_3857,
      CAST(floor(((t.longitude + 180.0) / 360.0) * ${n}) AS INTEGER) AS xtile_z${TILE_INDEX_ZOOM},
      CAST(floor((1.0 - ln(tan(radians(${clampedLat})) + 1.0 / cos(radians(${clampedLat}))) / pi()) / 2.0 * ${n}) AS INTEGER) AS ytile_z${TILE_INDEX_ZOOM}
    FROM source t
    ${joins}`,
  ]

  // The published schema, as the chat, the URL filters and the default map
  // query address it. A view costs nothing per row; the casts run only for
  // the columns a query actually reads.
  statements.push(`CREATE VIEW ${TREES_VIEW} AS
    SELECT
      tree_id,
      CAST(city AS VARCHAR) AS city,
      CAST(data_source AS VARCHAR) AS data_source,
      CAST(source_tree_name AS VARCHAR) AS tree_name,
      plant_date,
      CAST(species AS VARCHAR) AS species,
      CAST(cultivar AS VARCHAR) AS cultivar,
      crown_width_m,
      latitude,
      longitude,
      diameter_at_breast_height,
      submission_photo_url
    FROM ${TREES_TABLE}`)
  return statements
}

/**
 * The tile index of a row at zoom `z`, from its stored z20 index. Every zoom
 * up to 20 is a right shift of the stored pair; beyond it the index is
 * recomputed from the row's mercator coordinates.
 */
export function tileIndexSql(alias: string, axis: 'x' | 'y', z: number): string {
  if (z <= TILE_INDEX_ZOOM) return `(${alias}.${axis}tile_z${TILE_INDEX_ZOOM} >> ${TILE_INDEX_ZOOM - z})`
  const span = `(${WEB_MERCATOR_WORLD} / pow(2, ${z}))`
  return axis === 'x'
    ? `CAST(floor(((${alias}.x_3857) + ${WEB_MERCATOR_MAX}) / ${span}) AS INTEGER)`
    : `CAST(floor((${WEB_MERCATOR_MAX} - (${alias}.y_3857)) / ${span}) AS INTEGER)`
}

/** The city's tile extent at every zoom the tile indices cover, one row. */
export function cityTileBoundsSql(zooms: number[]): string {
  const columns = zooms.flatMap((z) => [
    `MIN(${tileIndexSql('tf', 'x', z)}) AS min_x${z}`, `MAX(${tileIndexSql('tf', 'x', z)}) AS max_x${z}`,
    `MIN(${tileIndexSql('tf', 'y', z)}) AS min_y${z}`, `MAX(${tileIndexSql('tf', 'y', z)}) AS max_y${z}`,
  ])
  return `SELECT ${columns.join(', ')} FROM ${TREES_TABLE} tf`
}

/** The category colour of a tree form, as SQL over a `tree_form` expression. */
export function defaultColorSql(formExpr: string, colors: Record<string, string> = DEFAULT_CATEGORY_COLORS): string {
  const branches = TREE_FORMS.filter((form) => form !== 'default' && colors[form])
    .map((form) => `WHEN '${form}' THEN '${colors[form]}'`)
  return `CASE ${formExpr} ${branches.join(' ')} ELSE '${colors.default}' END`
}

/**
 * How a query reads a tree's display colour. With no override active there is
 * no join at all; with one, the override table is joined and wins where it
 * has a row. `join` goes after the `trees_fast` alias in the FROM clause.
 */
export function colorSql(alias: string, overrideActive: boolean): { join: string; expr: string } {
  const fallback = defaultColorSql(`${alias}.tree_form`)
  if (!overrideActive) return { join: '', expr: fallback }
  return {
    join: `LEFT JOIN ${COLOR_OVERRIDE_TABLE} __co ON ${alias}.tree_id = __co.tree_id`,
    expr: `COALESCE(__co.display_color, ${fallback})`,
  }
}

/** The z14 aggregate the overview zooms read when no filter is active. */
export function aggCacheSql(gridM: number, overrideActive: boolean): string {
  const color = colorSql('tf', overrideActive)
  const z = AGG_CACHE_ZOOM
  return `CREATE OR REPLACE TABLE ${AGG_CACHE_TABLE} AS
    SELECT
      ${tileIndexSql('tf', 'x', z)} AS xtile,
      ${tileIndexSql('tf', 'y', z)} AS ytile,
      floor(tf.x_3857 / ${gridM}) * ${gridM} + ${gridM} / 2.0 AS gx,
      floor(tf.y_3857 / ${gridM}) * ${gridM} + ${gridM} / 2.0 AS gy,
      tf.tree_form AS category,
      ${color.expr} AS display_color,
      AVG(tf.dbh) AS dbh,
      COUNT(*) AS point_count
    FROM ${TREES_TABLE} tf
    ${color.join}
    GROUP BY 1, 2, 3, 4, 5, 6`
}
