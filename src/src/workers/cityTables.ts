/**
 * The SQL that builds the in-browser tree tables.
 *
 * Kept out of the worker so it can run under native DuckDB in tests. The
 * shapes here are the memory budget: the rows are the largest allocation in
 * the browser, DuckDB-wasm never returns heap to the OS, and iOS Safari
 * reloads a tab that outgrows its process budget (roughly 1 GB on a first
 * visit, a few hundred MB after reloads). Measured 2026-10-23, NYC's 1.16M
 * rows cost 393 MB as a 33-column table and 145 MB in this shape; see
 * docs/MOBILE_LOAD_BUDGET.md.
 *
 * The rows come from the world's block files (worldBlocks.ts), never from a
 * city: `trees_fast` is a view over one table per held cell, and a cell is
 * loaded when the camera nears it and dropped when it is far and the budget
 * is spent (cityWindow.ts). A desktop's cell is a whole block; a phone's is a
 * sixteenth of one.
 *
 * What makes the row narrow:
 * - Low-cardinality strings (species, names, cultivar, source, city) are ENUM
 *   columns: two bytes a row instead of a sixteen-byte string slot plus heap.
 *   The vocabularies are the world's, read once from the manifest's
 *   vocabulary file (a few hundred KB), so every cell shares one set of types
 *   and a value first met in the fiftieth cell is already in its ENUM.
 * - One tile index pair, at z20; coarser zooms shift it (`tileIndexSql`).
 * - The published crown prediction is a column, not a second table.
 * - No per-tree colour table: the base colour is a CASE over `tree_form`, a
 *   lookup in a per-species shade table of a few thousand rows, or a CASE
 *   over the trunk diameter (`colorSql`); a chat override is a sparse table
 *   that exists only while it is active.
 *
 * The `trees` view presents the published schema with ENUMs cast back to
 * VARCHAR, so chat SQL and the URL filters see exactly the types the parquet
 * has. Readers of `trees_fast` inside the app may compare an ENUM column to a
 * string literal directly: a value outside the vocabulary compares unequal
 * rather than erroring (DuckDB 1.4.3, checked).
 */
import { CATEGORY_COLORS as DEFAULT_CATEGORY_COLORS } from '../treeFormColors'

export const TREES_TABLE = 'trees_fast'
export const TREES_VIEW = 'trees'
export const COLOR_OVERRIDE_TABLE = '__tree_color_override'
/** (species VARCHAR, shades VARCHAR[]): the leaf-shade scheme's colours, one row per species that has any. */
export const SPECIES_SHADE_TABLE = '__species_shade'
/** The leaf-shade scheme's colour for a tree whose species has no recorded shade. */
export const UNKNOWN_SHADE_COLOR = '#9e9e9e'
export const TILE_INDEX_ZOOM = 20

export const WEB_MERCATOR_MAX = 20037508.342789244
export const WEB_MERCATOR_WORLD = WEB_MERCATOR_MAX * 2

/** The icon categories; `default` is every form the atlas has no icon for. */
export const TREE_FORMS = ['default', 'palm', 'broadleaf', 'conifer', 'spreading', 'columnar', 'ornamental', 'weeping', 'multi_trunk'] as const

/** ENUM types the tree tables declare, in creation order; dropped in reverse. */
export const CITY_ENUM_TYPES = {
  form: 'tree_form_t',
  city: 'tree_city_t',
  source: 'tree_source_t',
  species: 'tree_species_t',
  name: 'tree_name_t',
  sourceName: 'tree_source_name_t',
  cultivar: 'tree_cultivar_t',
} as const

/** Each ENUM's `kind` in the world's vocabulary file (VOCAB_COLUMNS in data/world/publish_blocks.py). */
export const VOCAB_KINDS: Record<Exclude<keyof typeof CITY_ENUM_TYPES, 'form'>, string> = {
  city: 'city',
  source: 'data_source',
  species: 'species',
  name: 'display_name',
  sourceName: 'tree_name',
  cultivar: 'cultivar',
}

/**
 * The columns of a block file, in order (COLUMNS in
 * data/world/publish_blocks.py; data/raw/tests/test_publish_world_blocks.py
 * holds the two lists together).
 */
export const WORLD_BLOCK_COLUMNS = [
  'tree_id', 'city', 'data_source', 'species', 'cultivar', 'tree_name', 'display_name', 'tree_form', 'plant_date',
  'diameter_at_breast_height', 'crown_width_m', 'predicted_crown_width_m', 'height_m', 'predicted_height_m',
  'latitude', 'longitude', 'submission_photo_url',
] as const

const sqlString = (value: string) => `'${value.replace(/'/g, "''")}'`

/** The empty table `trees_fast` always unions in, for its shape. */
export const WINDOW_SHAPE_TABLE = 'trees_window_shape'
/**
 * Every tree of the bookmarked city, for the few reads that are about a city
 * and not the ground in view (a deep-linked tree, a species count): the
 * city's projection file, read in place. Readers use tree_id, species,
 * latitude and longitude.
 */
export const PUBLISHED_TREES_VIEW = 'trees_published'
/**
 * In a `query` sent with a point (`at`), the table of the cell under that
 * point: a read about one tree names this instead of `trees_fast`, and the
 * worker loads the cell and puts its table here.
 */
export const TREES_AT_TOKEN = '__trees_at__'

/** Statements that remove the world's tables, in dependency order. */
export function dropWorldTablesSql(cellTables: string[]): string[] {
  return [
    `DROP VIEW IF EXISTS ${TREES_VIEW}`,
    `DROP VIEW IF EXISTS ${PUBLISHED_TREES_VIEW}`,
    `DROP TABLE IF EXISTS ${COLOR_OVERRIDE_TABLE}`,
    `DROP VIEW IF EXISTS ${TREES_TABLE}`,
    ...cellTables.map((table) => `DROP TABLE IF EXISTS ${table}`),
    `DROP TABLE IF EXISTS ${WINDOW_SHAPE_TABLE}`,
    ...Object.values(CITY_ENUM_TYPES).reverse().map((type) => `DROP TYPE IF EXISTS ${type}`),
  ]
}

/**
 * Statements that set the world up, to run once the vocabulary file is
 * readable at `vocabFile`: the ENUM types over the world's vocabulary, an
 * empty `trees_fast` of the right shape, and the `trees` view over it. Rows
 * arrive later, a cell at a time.
 */
export function worldTablesSql(vocabFile: string): string[] {
  const kinds = Object.entries(VOCAB_KINDS) as [keyof typeof VOCAB_KINDS, string][]
  return [
    `CREATE TYPE ${CITY_ENUM_TYPES.form} AS ENUM (${TREE_FORMS.map(sqlString).join(', ')})`,
    ...kinds.map(([key, kind]) => `CREATE TYPE ${CITY_ENUM_TYPES[key]} AS ENUM (
      SELECT value FROM read_parquet(${sqlString(vocabFile)}) WHERE kind = ${sqlString(kind)} AND value IS NOT NULL ORDER BY value)`),
    `CREATE TABLE ${WINDOW_SHAPE_TABLE} AS ${worldRowsSql(null, '')} LIMIT 0`,
    windowViewSql([]),
    treesViewSql(),
  ]
}

/** `trees_published` over a city's projection file; with no city it reads no rows. */
export function publishedTreesViewSql(projectionUrl: string | null): string {
  return `CREATE OR REPLACE VIEW ${PUBLISHED_TREES_VIEW} AS ${projectionUrl
    ? `SELECT * FROM read_parquet(${sqlString(projectionUrl)})`
    : `SELECT * FROM ${TREES_TABLE} LIMIT 0`}`
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

/** A cell that is a whole block: its table straight from the block's file. */
export function blockCellSql(cellKey: number, file: string): string {
  return `CREATE TABLE ${windowCellTable(cellKey)} AS ${worldRowsSql([file], '')}`
}

/**
 * One load of cells finer than a block, as three steps: the rows of
 * `cellKeys` out of the block `files` that hold them into `staging` in one
 * scan, a count per cell, and then `windowCellSplitSql` per cell with rows.
 * The box is what the parquet reader prunes row groups on (a block's rows
 * are in Morton order, so a cell is a few adjacent groups); the cell list is
 * what makes the load exact. Each cell gets its own table because a cell is
 * what the window drops.
 */
export function windowStagingSql(
  staging: string,
  files: string[],
  cellKeys: number[],
  box: { west: number; east: number; south: number; north: number },
  cellZoom: number,
): { create: string; counts: string } {
  const where = `AND latitude BETWEEN ${box.south} AND ${box.north} AND longitude BETWEEN ${box.west} AND ${box.east}`
  return {
    create: `CREATE TABLE ${staging} AS
    SELECT * FROM (${worldRowsSql(files, where)}) chunk
    WHERE ${windowCellSql('chunk', cellZoom)} IN (${cellKeys.join(', ')})`,
    counts: `SELECT ${windowCellSql('s', cellZoom)} AS cell, count(*) AS n FROM ${staging} s GROUP BY 1`,
  }
}

export function windowCellSplitSql(staging: string, cellKey: number, cellZoom: number): string {
  return `CREATE TABLE ${windowCellTable(cellKey)} AS SELECT * FROM ${staging} s WHERE ${windowCellSql('s', cellZoom)} = ${cellKey}`
}

/** `trees_fast`: the empty shape and every cell held. */
export function windowViewSql(cellTables: string[]): string {
  return `CREATE OR REPLACE VIEW ${TREES_TABLE} AS ${scopeSelectSql(cellTables)}`
}

function scopeSelectSql(cellTables: string[]): string {
  return [WINDOW_SHAPE_TABLE, ...cellTables].map((table) => `SELECT * FROM ${table}`).join('\n    UNION ALL ')
}

/**
 * `trees_fast` narrowed to some held cells, as a relation to put in a FROM
 * clause. DuckDB plans a union one branch at a time, about a third of a
 * millisecond each, so a read over the whole view costs a desktop holding
 * four hundred blocks over a hundred milliseconds before it touches a row; a
 * read that knows the ground it is about names only those cells.
 */
export function scopeSql(cellTables: string[]): string {
  if (cellTables.length === 0) return WINDOW_SHAPE_TABLE
  return cellTables.length === 1 ? cellTables[0] : `(${cellTables.map((table) => `SELECT * FROM ${table}`).join(' UNION ALL ')})`
}

/**
 * The rows of block files in `trees_fast`'s shape; `files` null is the shape
 * alone, cast from nulls. `where` is appended to the scan's own filter, so it
 * reaches the parquet reader. `rn` is the row's position in its block file:
 * with the block's key it names a tree for as long as the manifest lasts,
 * whether or not the cell is still held, which is what the resident layer
 * keeps instead of a tree id.
 */
function worldRowsSql(files: string[] | null, where: string): string {
  const clampedLat = 'LEAST(GREATEST(CAST(t.latitude AS DOUBLE), -85.05112878), 85.05112878)'
  const lng = 'CAST(t.longitude AS DOUBLE)'
  const n = 2 ** TILE_INDEX_ZOOM
  const source = files === null
    ? `SELECT ${WORLD_BLOCK_COLUMNS.map((column) => `NULL AS ${column}`).join(', ')}, CAST(0 AS BIGINT) AS file_row_number`
    : `SELECT ${WORLD_BLOCK_COLUMNS.join(', ')}, file_row_number
      FROM read_parquet([${files.map(sqlString).join(', ')}], file_row_number = true)
      WHERE latitude IS NOT NULL AND longitude IS NOT NULL ${where}`
  return `
    WITH source AS (
      ${source}
    )
    SELECT
      CAST(t.tree_id AS VARCHAR) AS tree_id,
      CAST(t.city AS ${CITY_ENUM_TYPES.city}) AS city,
      CAST(t.data_source AS ${CITY_ENUM_TYPES.source}) AS data_source,
      CAST(t.submission_photo_url AS VARCHAR) AS submission_photo_url,
      CAST(t.display_name AS ${CITY_ENUM_TYPES.name}) AS tree_name,
      CAST(t.tree_name AS ${CITY_ENUM_TYPES.sourceName}) AS source_tree_name,
      CAST(t.plant_date AS DATE) AS plant_date,
      CAST(t.species AS ${CITY_ENUM_TYPES.species}) AS species,
      CAST(t.cultivar AS ${CITY_ENUM_TYPES.cultivar}) AS cultivar,
      CAST(t.crown_width_m AS DOUBLE) AS crown_width_m,
      CAST(t.predicted_crown_width_m AS FLOAT) AS predicted_crown_width_m,
      CAST(t.height_m AS FLOAT) AS height_m,
      CAST(t.predicted_height_m AS FLOAT) AS predicted_height_m,
      CAST(t.latitude AS DOUBLE) AS latitude,
      ${lng} AS longitude,
      CAST(t.diameter_at_breast_height AS DOUBLE) AS diameter_at_breast_height,
      COALESCE(CAST(t.diameter_at_breast_height AS DOUBLE), 3) AS dbh,
      -- A form outside the vocabulary (a drifted enrichment value) is 'default'
      -- rather than a cast error that fails the whole load.
      CAST(CASE lower(trim(CAST(t.tree_form AS VARCHAR)))
        ${TREE_FORMS.filter((form) => form !== 'default').map((form) => `WHEN '${form}' THEN '${form}'`).join('\n        ')}
        ELSE 'default'
      END AS ${CITY_ENUM_TYPES.form}) AS tree_form,
      ((${lng} * ${WEB_MERCATOR_MAX}) / 180.0) AS x_3857,
      (6378137.0 * ln(tan(pi() / 4.0 + radians(${clampedLat}) / 2.0))) AS y_3857,
      CAST(floor(((${lng} + 180.0) / 360.0) * ${n}) AS INTEGER) AS xtile_z${TILE_INDEX_ZOOM},
      CAST(floor((1.0 - ln(tan(radians(${clampedLat})) + 1.0 / cos(radians(${clampedLat}))) / pi()) / 2.0 * ${n}) AS INTEGER) AS ytile_z${TILE_INDEX_ZOOM},
      CAST(t.file_row_number AS UINTEGER) AS rn
    FROM source t`
}

/**
 * The published schema, as the chat, the URL filters and the default map
 * query address it. A view costs nothing per row; the casts run only for
 * the columns a query actually reads.
 */
function treesViewSql(): string {
  return `CREATE VIEW ${TREES_VIEW} AS ${treesSelectSql(TREES_TABLE)}`
}

/**
 * The published schema over any relation of `trees_fast`'s shape. A read
 * scoped to the ground it is about (`scopeSql`) names this as a CTE called
 * `trees`, which a query's own `FROM trees` then resolves to: a filter
 * written against the published schema runs over a few blocks, not the world.
 */
export function treesSelectSql(from: string): string {
  return `
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
  FROM ${from}`
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

/** The category colour of a tree form, as SQL over a `tree_form` expression. */
export function defaultColorSql(formExpr: string, colors: Record<string, string> = DEFAULT_CATEGORY_COLORS): string {
  const branches = TREE_FORMS.filter((form) => form !== 'default' && colors[form])
    .map((form) => `WHEN '${form}' THEN '${colors[form]}'`)
  return `CASE ${formExpr} ${branches.join(' ')} ELSE '${colors.default}' END`
}

/**
 * The trunk-diameter scheme: a colour per size band, grey where the diameter
 * is not recorded. Published diameters are inches. An expression over the
 * row, like the tree-form colours, so a tree paged in a minute from now is
 * coloured by the same rule; as a per-tree table it covered only the trees
 * held when it was built.
 */
export const DBH_COLORS = {
  unknown: '#9a9a9a',
  under6: '#c7e9b4',
  under12: '#7fcdbb',
  under24: '#41b6c4',
  over24: '#225ea8',
} as const

export function dbhColorSql(dbhExpr: string): string {
  return `CASE
    WHEN ${dbhExpr} IS NULL OR NOT isfinite(${dbhExpr}) OR ${dbhExpr} <= 0 THEN '${DBH_COLORS.unknown}'
    WHEN ${dbhExpr} < 6 THEN '${DBH_COLORS.under6}'
    WHEN ${dbhExpr} < 12 THEN '${DBH_COLORS.under12}'
    WHEN ${dbhExpr} < 24 THEN '${DBH_COLORS.under24}'
    ELSE '${DBH_COLORS.over24}' END`
}

/**
 * A tree id as the chat's published tables hold it: a 64-bit hash, not the
 * string. The chat's ids come from a whole city's file, not the cells held,
 * so a publish can hold every tree in the city: 11 MB for Boston's 1.4M as
 * hashes. Two ids sharing a hash among a city's 1.6M has odds near 1 in
 * 10^7, and would mark one extra tree.
 */
export function treeKeySql(expr: string): string {
  return `hash(CAST(${expr} AS VARCHAR))`
}

/** Which rule gives a tree its colour before any chat override. */
export type BaseColorScheme = 'form' | 'shade' | 'dbh'

/**
 * How a query reads a tree's display colour. The base colour is the tree-form
 * CASE; with the leaf-shade scheme on, one of the species' shades picked by
 * tree id (a two-shade species renders mixed) and grey where the species has
 * none; with the trunk scheme on, the diameter's band. With a chat override
 * active its table is joined and wins where it has a row. `join` goes after
 * the `trees_fast` alias in the FROM clause.
 *
 * `aggregated` is for the overview zooms, where rows are grid cells grouped
 * by colour: the species' first shade for every tree, so a two-shade species
 * does not split each cell in two (the hash split is a point-zoom effect).
 */
export function colorSql(alias: string, overrideActive: boolean, scheme: BaseColorScheme | boolean = 'form', { aggregated = false } = {}): { join: string; expr: string } {
  const joins: string[] = []
  // The scheme was a `shadeActive` flag before the trunk scheme became one.
  const baseScheme: BaseColorScheme = scheme === true ? 'shade' : scheme === false ? 'form' : scheme
  let base = defaultColorSql(`${alias}.tree_form`)
  if (baseScheme === 'shade') {
    joins.push(`LEFT JOIN ${SPECIES_SHADE_TABLE} __ss ON CAST(${alias}.species AS VARCHAR) = __ss.species`)
    const pick = aggregated ? '1' : `1 + CAST(hash(${alias}.tree_id) % len(__ss.shades) AS INTEGER)`
    base = `COALESCE(list_extract(__ss.shades, ${pick}), '${UNKNOWN_SHADE_COLOR}')`
  } else if (baseScheme === 'dbh') {
    base = dbhColorSql(`${alias}.diameter_at_breast_height`)
  }
  if (!overrideActive) return { join: joins.join('\n  '), expr: base }
  joins.push(`LEFT JOIN ${COLOR_OVERRIDE_TABLE} __co ON ${treeKeySql(`${alias}.tree_id`)} = __co.tree_key`)
  return { join: joins.join('\n  '), expr: `COALESCE(__co.display_color, ${base})` }
}
