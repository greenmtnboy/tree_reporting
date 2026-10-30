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
  /**
   * The projection's vocabulary file, when it is published and its tree
   * counts sum to the projection's row count: the ENUMs are built from it
   * instead of a SELECT DISTINCT per column over the projection, which in
   * DuckDB-wasm reads most of the file. Null scans the projection (or the
   * assembled source) as before. Only read with `projectionUrl`.
   */
  vocabUrl?: string | null
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

/** The empty table a windowed city's `trees_fast` view always unions in, for its shape. */
export const WINDOW_SHAPE_TABLE = 'trees_window_shape'
/**
 * Every tree of the city, for the few reads a phone's window cannot answer (a
 * deep-linked tree, a species count). A whole city is `trees_fast` itself; a
 * windowed one reads the projection file. Readers use only the columns both
 * share: tree_id, species, latitude, longitude.
 */
export const PUBLISHED_TREES_VIEW = 'trees_published'

/**
 * Statements that remove a loaded city, in dependency order. A windowed city
 * has `trees_fast` as a view over its chunk tables; a whole city has it as
 * one table.
 */
export function dropCityTablesSql(windowed: { chunkTables: string[] } | null = null): string[] {
  return [
    `DROP VIEW IF EXISTS ${TREES_VIEW}`,
    `DROP VIEW IF EXISTS ${PUBLISHED_TREES_VIEW}`,
    `DROP TABLE IF EXISTS ${AGG_CACHE_TABLE}`,
    `DROP TABLE IF EXISTS ${COLOR_OVERRIDE_TABLE}`,
    ...windowed
      ? [`DROP VIEW IF EXISTS ${TREES_TABLE}`, ...windowed.chunkTables.map((table) => `DROP TABLE IF EXISTS ${table}`), `DROP TABLE IF EXISTS ${WINDOW_SHAPE_TABLE}`]
      : [`DROP TABLE IF EXISTS ${TREES_TABLE}`],
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
  const shape = cityShape(options)
  return [
    ...shape.types,
    `CREATE TABLE ${TREES_TABLE} AS ${shape.rows('')}`,
    treesViewSql(),
    `CREATE VIEW ${PUBLISHED_TREES_VIEW} AS SELECT * FROM ${TREES_TABLE}`,
  ]
}

/**
 * Statements that set up a windowed city: the vocabularies over the whole
 * file (a phone pans into ground it has not loaded, and a value first met in
 * the fifth cell must already be in the ENUM), an empty `trees_fast` view of
 * the right shape, the `trees` view over it, and `trees_published` over the
 * file. Rows arrive later, one `windowChunkSql` at a time. Only the browser
 * projection is windowed; the assembled fallback holds the whole city.
 */
export function windowedCityTablesSql(options: CityTableOptions): string[] {
  if (!options.projectionUrl) throw new Error('Only the browser projection can be loaded in windows')
  const shape = cityShape(options)
  return [
    ...shape.types,
    `CREATE TABLE ${WINDOW_SHAPE_TABLE} AS ${shape.rows('')} LIMIT 0`,
    windowViewSql([]),
    treesViewSql(),
    `CREATE VIEW ${PUBLISHED_TREES_VIEW} AS SELECT * FROM read_parquet(${sqlString(options.projectionUrl)})`,
  ]
}

/** A row's cell at `cellZoom` as one integer (cityWindow.ts `cellKey`), from its stored z20 index. */
export function windowCellSql(alias: string, cellZoom: number): string {
  const shift = TILE_INDEX_ZOOM - cellZoom
  return `((${alias}.xtile_z${TILE_INDEX_ZOOM} >> ${shift}) * ${2 ** cellZoom} + (${alias}.ytile_z${TILE_INDEX_ZOOM} >> ${shift}))`
}

/** The table one loaded cell is held in. */
export function windowCellTable(cellKey: number): string {
  return `trees_cell_${cellKey}`
}

/**
 * One load of a windowed city, as three steps: the rows of `cellKeys`
 * (see cityWindow.ts) into `staging` in one scan, a count per cell, and
 * then `windowCellSplitSql` per cell with rows. The box is what the parquet
 * reader prunes row groups on; the cell list is what makes the load exact,
 * so a tree on a box edge is never loaded twice. Each cell gets its own
 * table because a cell is what the window drops.
 */
export function windowStagingSql(
  staging: string,
  options: CityTableOptions,
  cellKeys: number[],
  box: { west: number; east: number; south: number; north: number },
  cellZoom: number,
): { create: string; counts: string } {
  const where = `AND latitude BETWEEN ${box.south} AND ${box.north} AND longitude BETWEEN ${box.west} AND ${box.east}`
  return {
    create: `CREATE TABLE ${staging} AS
    SELECT * FROM (${cityShape(options).rows(where)}) chunk
    WHERE ${windowCellSql('chunk', cellZoom)} IN (${cellKeys.join(', ')})`,
    counts: `SELECT ${windowCellSql('s', cellZoom)} AS cell, count(*) AS n FROM ${staging} s GROUP BY 1`,
  }
}

export function windowCellSplitSql(staging: string, cellKey: number, cellZoom: number): string {
  return `CREATE TABLE ${windowCellTable(cellKey)} AS SELECT * FROM ${staging} s WHERE ${windowCellSql('s', cellZoom)} = ${cellKey}`
}

/** `trees_fast` for a windowed city: the empty shape and every chunk held. */
export function windowViewSql(chunkTables: string[]): string {
  return `CREATE OR REPLACE VIEW ${TREES_TABLE} AS
    ${[WINDOW_SHAPE_TABLE, ...chunkTables].map((table) => `SELECT * FROM ${table}`).join('\n    UNION ALL ')}`
}

/**
 * The ENUM types a city declares and its row select. `rows(where)` is the
 * select that produces `trees_fast`'s rows; `where` is appended to the
 * source scan's own filter (the window's box), so it reaches the parquet
 * reader.
 */
function cityShape(options: CityTableOptions): { types: string[]; rows: (where: string) => string } {
  const { projectionUrl, vocabUrl, parquetUrl, predictionsUrl, predictionsAreUnique, city, hasDedupFlag, hasCultivar, hasCrownWidth } = options
  const clampedLat = 'LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878)'
  const n = 2 ** TILE_INDEX_ZOOM

  // The rows, the display name, the growth form and the predicted width: from
  // the projection as columns, or assembled here from the published inventory,
  // the enrichment table and the predictions.
  let source: (where: string) => string
  let displayName: string
  let formExpr: string
  let predictedWidth: string
  let joins: string
  if (projectionUrl) {
    source = (where) => `SELECT
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
      WHERE latitude IS NOT NULL AND longitude IS NOT NULL ${where}`
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
    source = (where) => `SELECT
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
        ${dedupFilter} ${where}`
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
    `SELECT DISTINCT ${expr} AS value FROM (${source('')}) t ${join} WHERE ${expr} IS NOT NULL ORDER BY 1`
  // From the pipeline's vocabulary file when there is one: the same values,
  // a superset in fact (it also counts trees with no position, which the
  // scan above filters out), for a few KB instead of the projection.
  const fromVocab = (column: string) =>
    `SELECT DISTINCT ${column} AS value FROM read_parquet(${sqlString(vocabUrl!)}) WHERE ${column} IS NOT NULL ORDER BY 1`
  const nameJoin = projectionUrl ? '' : 'LEFT JOIN species_enrichment se ON t.species = se.species'
  const useVocab = Boolean(projectionUrl && vocabUrl)
  const enumSources: Array<[keyof typeof CITY_ENUM_TYPES, string]> = [
    ['city', useVocab ? fromVocab('city') : vocabulary('t.city')],
    ['source', useVocab ? fromVocab('data_source') : vocabulary('t.data_source')],
    ['species', useVocab ? fromVocab('species') : vocabulary('t.species')],
    ['name', useVocab ? fromVocab('display_name') : vocabulary(displayName, nameJoin)],
    ['sourceName', useVocab ? fromVocab('tree_name') : vocabulary('t.tree_name')],
    ['cultivar', useVocab ? fromVocab('cultivar') : vocabulary('t.cultivar')],
  ]
  return {
    types: [
      `CREATE TYPE ${CITY_ENUM_TYPES.form} AS ENUM (${TREE_FORMS.map(sqlString).join(', ')})`,
      ...enumSources.map(([key, query]) => `CREATE TYPE ${CITY_ENUM_TYPES[key]} AS ENUM (${query})`),
    ],
    rows: (where) => `
    WITH source AS (
      ${source(where)}
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
  }
}

/**
 * The published schema, as the chat, the URL filters and the default map
 * query address it. A view costs nothing per row; the casts run only for
 * the columns a query actually reads.
 */
function treesViewSql(): string {
  return `CREATE VIEW ${TREES_VIEW} AS
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
  FROM ${TREES_TABLE}`
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
 * A tree id as the chat's published tables hold it: a 64-bit hash, not the
 * string. The chat's ids come from the whole city's file, not the phone's
 * window, so a publish can hold every tree in the city: 11 MB for Boston's
 * 1.4M as hashes. Two ids sharing a hash among a city's 1.6M has odds near
 * 1 in 10^7, and would mark one extra tree.
 */
export function treeKeySql(expr: string): string {
  return `hash(CAST(${expr} AS VARCHAR))`
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
    join: `LEFT JOIN ${COLOR_OVERRIDE_TABLE} __co ON ${treeKeySql(`${alias}.tree_id`)} = __co.tree_key`,
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
