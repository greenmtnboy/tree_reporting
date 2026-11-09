import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  SPECIES_SHADE_TABLE,
  UNKNOWN_SHADE_COLOR,
  CITY_ENUM_TYPES,
  COLOR_OVERRIDE_TABLE,
  DBH_COLORS,
  PUBLISHED_TREES_VIEW,
  VOCAB_KINDS,
  WORLD_BLOCK_COLUMNS,
  treeKeySql,
  TILE_INDEX_ZOOM,
  TREES_TABLE,
  TREES_VIEW,
  WEB_MERCATOR_MAX,
  WEB_MERCATOR_WORLD,
  blockCellSql,
  colorSql,
  dropWorldTablesSql,
  publishedTreesViewSql,
  scopeSql,
  tileIndexSql,
  treesSelectSql,
  windowCellSplitSql,
  windowCellSql,
  windowCellTable,
  windowStagingSql,
  windowViewSql,
  worldTablesSql,
} from './cityTables'
import { CELL_ZOOM, cellAt, cellKey, cellsBounds } from './cityWindow'
import { BLOCK_ZOOM, blockAt, blockKey } from './worldBlocks'
import { CATEGORY_COLORS } from '../treeFormColors'

/**
 * The world's tables, run under native DuckDB against fixture files shaped
 * like the pipeline's blocks and vocabulary (data/world/publish_blocks.py).
 * Native and wasm share the storage format and the SQL dialect (both 1.4.x),
 * so what passes here is what the worker runs.
 *
 * Two blocks: one in San Francisco, one in Boston. Nothing in the tables is
 * a city, so the two are held side by side under one set of types.
 */
describe('world tables', () => {
  let conn: DuckDBConnection
  let dir: string
  let vocab: string
  const sql = (path: string) => path.replace(/\\/g, '/')
  const SF = [-122.43, 37.76] as const
  const BOS = [-71.06, 42.36] as const
  const key = ([lng, lat]: readonly [number, number]) => { const b = blockAt(lng, lat); return blockKey(b.x, b.y) }
  const file = (name: string) => sql(join(dir, name))
  let held: string[] = []

  const rows = async (query: string) => (await conn.runAndReadAll(query)).getRowObjects()
  const describeTable = async (table: string) =>
    Object.fromEntries((await rows(`DESCRIBE ${table}`)).map((row) => [String(row.column_name), String(row.column_type)]))

  /** Set the world up and hold both blocks, as a desktop does. */
  const load = async () => {
    for (const statement of dropWorldTablesSql(held)) await conn.run(statement)
    for (const statement of worldTablesSql(vocab)) await conn.run(statement)
    await conn.run(blockCellSql(key(SF), file('sf.parquet')))
    await conn.run(blockCellSql(key(BOS), file('bos.parquet')))
    held = [windowCellTable(key(SF)), windowCellTable(key(BOS))]
    await conn.run(windowViewSql(held))
  }

  beforeAll(async () => {
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    dir = mkdtempSync(join(tmpdir(), 'world-tables-'))
    vocab = file('vocab.parquet')
    const block = (values: string) => `
      SELECT * REPLACE (
        CAST(diameter_at_breast_height AS DOUBLE) AS diameter_at_breast_height, CAST(latitude AS DOUBLE) AS latitude,
        CAST(longitude AS DOUBLE) AS longitude, CAST(crown_width_m AS DOUBLE) AS crown_width_m,
        CAST(predicted_crown_width_m AS FLOAT) AS predicted_crown_width_m,
        CAST(height_m AS FLOAT) AS height_m, CAST(predicted_height_m AS FLOAT) AS predicted_height_m
      ) FROM (VALUES ${values}) v(${WORLD_BLOCK_COLUMNS.join(', ')})`
    // id, city, source, species, cultivar, source name, display name, form, planted, dbh, crown, predicted crown,
    // height, predicted height, lat, lon, photo
    await conn.run(`COPY (${block(`
      ('sf-1', 'USSFO', 'SF_OPENDATA', 'Quercus agrifolia', NULL, 'Oak', 'Coast live oak', 'spreading', DATE '2001-03-04', 12.5, 9.5, 7.0, 14.5, 11.0, 37.760, -122.430, NULL),
      ('sf-2', 'USSFO', 'SF_OPENDATA', 'Washingtonia robusta', NULL, NULL, 'Mexican fan palm', 'palm', NULL, NULL, NULL, 6.0, NULL, 10.0, 37.7601, -122.4301, NULL),
      ('sf-3', 'USSFO', 'COMMUNITY_USSFO', 'Cupressus macrocarpa', 'Goldcrest', 'Monterey cypress', 'Monterey cypress', 'default', NULL, 30, NULL, NULL, NULL, NULL, 37.7602, -122.4302, 'https://p/3.jpg'),
      ('sf-4', 'USSFO', 'OSM_USSFO', 'Palm', NULL, NULL, 'Palm (species not recorded)', 'palm', NULL, 4, NULL, NULL, NULL, NULL, 37.7603, -122.4303, NULL),
      ('sf-5', 'USSFO', 'OSM_USSFO', 'Acer rubrum', 'October Glory', NULL, 'Acer rubrum', 'drifted_form', NULL, 8, NULL, 3.0, NULL, 8.5, 37.7604, -122.4304, NULL),
      ('sf-nowhere', 'USSFO', 'OSM_USSFO', 'Acer rubrum', NULL, NULL, 'Acer rubrum', 'default', NULL, 8, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
    `)}) TO '${file('sf.parquet')}' (FORMAT PARQUET)`)
    await conn.run(`COPY (${block(`
      ('bos-1', 'USBOS', 'BOS_OPENDATA', 'Acer rubrum', NULL, 'Red maple', 'Red maple', 'broadleaf', NULL, 14, NULL, 8.0, 12.0, 11.0, 42.360, -71.060, NULL),
      ('bos-2', 'USBOS', 'PREDICTED_USBOS', 'Unknown', NULL, NULL, 'Unknown', 'default', NULL, NULL, NULL, 5.0, NULL, NULL, 42.361, -71.061, NULL)
    `)}) TO '${file('bos.parquet')}' (FORMAT PARQUET)`)
    // The world's vocabulary: every value of the ENUM columns over every block.
    const both = `read_parquet(['${file('sf.parquet')}', '${file('bos.parquet')}'])`
    await conn.run(`COPY (${Object.values(VOCAB_KINDS).map((column) =>
      `SELECT '${column}' AS kind, ${column} AS value FROM ${both} WHERE ${column} IS NOT NULL GROUP BY 2`).join(' UNION ALL ')}
    ) TO '${vocab}' (FORMAT PARQUET)`)
    await load()
  })
  afterAll(() => {
    conn.closeSync()
    rmSync(dir, { recursive: true, force: true })
  })

  test('the row keeps low-cardinality strings as world ENUMs, one tile index pair, and its place in its block', async () => {
    const columns = await describeTable(TREES_TABLE)
    for (const column of ['species', 'tree_name', 'source_tree_name', 'cultivar', 'data_source', 'city']) expect(columns[column]).toMatch(/^ENUM\(/)
    expect(columns.tree_form).toMatch(/^ENUM\('default', 'palm'/)
    expect(columns.predicted_crown_width_m).toBe('FLOAT')
    expect(columns.rn).toBe('UINTEGER')
    expect(Object.keys(columns).filter((c) => /tile_z/.test(c)).sort()).toEqual([`xtile_z${TILE_INDEX_ZOOM}`, `ytile_z${TILE_INDEX_ZOOM}`])
    // The measurements the card and the chat print stay exact.
    expect(columns.diameter_at_breast_height).toBe('DOUBLE')
    expect(columns.crown_width_m).toBe('DOUBLE')
    expect(columns.latitude).toBe('DOUBLE')
    const types = (await rows(`SELECT type_name FROM duckdb_types() WHERE logical_type = 'ENUM' AND NOT internal ORDER BY 1`)).map((row) => String(row.type_name))
    expect(types).toEqual([...Object.values(CITY_ENUM_TYPES)].sort())
    // One vocabulary for the world: both cities are values of one type.
    expect((await rows(`SELECT unnest(enum_range(NULL::${CITY_ENUM_TYPES.city})) AS v ORDER BY 1`)).map((r) => r.v)).toEqual(['USBOS', 'USSFO'])
  })

  test('a row is named by its block and its position in the block file', async () => {
    const sf = await rows(`SELECT tree_id, rn FROM ${windowCellTable(key(SF))} ORDER BY rn`)
    // The file's own order, the unplaced row's slot left out.
    expect(sf.map((r) => [r.tree_id, r.rn])).toEqual([['sf-1', 0], ['sf-2', 1], ['sf-3', 2], ['sf-4', 3], ['sf-5', 4]])
    const bos = await rows(`SELECT tree_id FROM ${windowCellTable(key(BOS))} WHERE rn = 1`)
    expect(bos.map((r) => r.tree_id)).toEqual(['bos-2'])
  })

  test('the trees view presents the published schema as VARCHAR over every block held and drops unplaced rows', async () => {
    expect(await describeTable(TREES_VIEW)).toEqual({
      tree_id: 'VARCHAR', city: 'VARCHAR', data_source: 'VARCHAR', tree_name: 'VARCHAR', plant_date: 'DATE',
      species: 'VARCHAR', cultivar: 'VARCHAR', crown_width_m: 'DOUBLE', latitude: 'DOUBLE', longitude: 'DOUBLE',
      diameter_at_breast_height: 'DOUBLE', submission_photo_url: 'VARCHAR',
    })
    expect((await rows(`SELECT tree_id FROM ${TREES_VIEW} ORDER BY 1`)).map((r) => r.tree_id))
      .toEqual(['bos-1', 'bos-2', 'sf-1', 'sf-2', 'sf-3', 'sf-4', 'sf-5'])
    // The view's tree_name is the source's own name, as published.
    const names = await rows(`SELECT tree_id, tree_name, cultivar FROM ${TREES_VIEW} WHERE city = 'USSFO' ORDER BY 1`)
    expect(names.map((r) => [r.tree_id, r.tree_name, r.cultivar])).toEqual([
      ['sf-1', 'Oak', null], ['sf-2', null, null], ['sf-3', 'Monterey cypress', 'Goldcrest'], ['sf-4', null, null], ['sf-5', null, 'October Glory'],
    ])
  })

  test('display names, forms and predictions are the block\'s columns; a form outside the vocabulary is default', async () => {
    const got = (await rows(
      `SELECT tree_id, CAST(tree_name AS VARCHAR) AS name, CAST(tree_form AS VARCHAR) AS form, dbh,
              predicted_crown_width_m AS width, height_m AS h, predicted_height_m AS p
       FROM ${TREES_TABLE} WHERE city = 'USSFO' ORDER BY 1`,
    )).map((r) => [r.tree_id, r.name, r.form, r.dbh, r.width, r.h, r.p])
    expect(got).toEqual([
      ['sf-1', 'Coast live oak', 'spreading', 12.5, 7, 14.5, 11],
      ['sf-2', 'Mexican fan palm', 'palm', 3, 6, null, 10],
      ['sf-3', 'Monterey cypress', 'default', 30, null, null, null],
      ['sf-4', 'Palm (species not recorded)', 'palm', 4, null, null, null],
      ['sf-5', 'Acer rubrum', 'default', 8, 3, null, 8.5],
    ])
  })

  test('every zoom up to 20 shifts the stored index to the value the formula gives', async () => {
    for (const z of [0, 5, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]) {
      const span = `(${WEB_MERCATOR_WORLD} / pow(2, ${z}))`
      const got = await rows(`
        SELECT
          ${tileIndexSql('tf', 'x', z)} AS x, ${tileIndexSql('tf', 'y', z)} AS y,
          CAST(floor((tf.x_3857 + ${WEB_MERCATOR_MAX}) / ${span}) AS INTEGER) AS fx,
          CAST(floor((${WEB_MERCATOR_MAX} - tf.y_3857) / ${span}) AS INTEGER) AS fy
        FROM ${TREES_TABLE} tf`)
      for (const row of got) expect([z, row.x, row.y]).toEqual([z, row.fx, row.fy])
    }
  })

  test('a row\'s block is the key its file is published under', async () => {
    const got = await rows(`SELECT DISTINCT CAST(city AS VARCHAR) AS city, ${windowCellSql('tf', BLOCK_ZOOM)} AS block FROM ${TREES_TABLE} tf ORDER BY 1`)
    expect(got.map((r) => [r.city, Number(r.block)])).toEqual([['USBOS', key(BOS)], ['USSFO', key(SF)]])
  })

  test('colours are a CASE over the form until an override table exists, which then wins per tree', async () => {
    const plain = colorSql('tf', false)
    expect(plain.join).toBe('')
    const sf = `${windowCellTable(key(SF))} tf`
    const defaults = await rows(`SELECT tree_id, ${plain.expr} AS c FROM ${sf} ORDER BY 1`)
    expect(defaults.map((r) => r.c)).toEqual([
      CATEGORY_COLORS.spreading, CATEGORY_COLORS.palm, CATEGORY_COLORS.default, CATEGORY_COLORS.palm, CATEGORY_COLORS.default,
    ])
    await conn.run(`CREATE TEMP TABLE ${COLOR_OVERRIDE_TABLE} AS SELECT ${treeKeySql("'sf-1'")} AS tree_key, '#ff0000' AS display_color`)
    const over = colorSql('tf', true)
    const colours = await rows(`SELECT tf.tree_id, ${over.expr} AS c FROM ${sf} ${over.join} ORDER BY 1`)
    expect(colours.map((r) => r.c)).toEqual([
      '#ff0000', CATEGORY_COLORS.palm, CATEGORY_COLORS.default, CATEGORY_COLORS.palm, CATEGORY_COLORS.default,
    ])
    await conn.run(`DROP TABLE ${COLOR_OVERRIDE_TABLE}`)
  })

  test('the leaf-shade scheme picks a shade by tree id per point and the first shade per overview cell', async () => {
    await conn.run(`CREATE TEMP TABLE ${SPECIES_SHADE_TABLE} AS SELECT 'Washingtonia robusta' AS species, ['#e8c547', '#c0392b'] AS shades`)
    const sf = `${windowCellTable(key(SF))} tf`
    const points = colorSql('tf', false, 'shade')
    expect(points.join).toContain(SPECIES_SHADE_TABLE)
    const perTree = await rows(`SELECT tf.tree_id, ${points.expr} AS c FROM ${sf} ${points.join} ORDER BY 1`)
    // The one Washingtonia takes one of its two shades; every other tree is grey.
    expect(perTree.filter((r) => ['#e8c547', '#c0392b'].includes(String(r.c)))).toHaveLength(1)
    expect(perTree.filter((r) => r.c === UNKNOWN_SHADE_COLOR)).toHaveLength(4)
    const cells = colorSql('tf', false, 'shade', { aggregated: true })
    const perCell = await rows(`SELECT DISTINCT ${cells.expr} AS c FROM ${sf} ${cells.join} ORDER BY 1`)
    expect(perCell.map((r) => r.c)).toEqual([UNKNOWN_SHADE_COLOR, '#e8c547'])
    await conn.run(`DROP TABLE ${SPECIES_SHADE_TABLE}`)
  })

  test('the trunk scheme colours a row by its recorded diameter, and a missing one as unknown, with no table', async () => {
    const scheme = colorSql('tf', false, 'dbh')
    expect(scheme.join).toBe('')
    const got = await rows(`SELECT tree_id, ${scheme.expr} AS c FROM ${TREES_TABLE} tf ORDER BY 1`)
    expect(Object.fromEntries(got.map((r) => [r.tree_id, r.c]))).toEqual({
      'bos-1': DBH_COLORS.under24, 'bos-2': DBH_COLORS.unknown,
      // sf-2 has no recorded diameter: unknown, not the default size's band.
      'sf-1': DBH_COLORS.under24, 'sf-2': DBH_COLORS.unknown, 'sf-3': DBH_COLORS.over24, 'sf-4': DBH_COLORS.under6, 'sf-5': DBH_COLORS.under12,
    })
  })

  test('chat SQL over the view and literal comparisons on the table behave as with VARCHAR', async () => {
    const chat = await rows(`
      SELECT COALESCE(species, 'Unknown') AS s, count(*) AS n FROM ${TREES_VIEW}
      WHERE city = 'USSFO' AND (species LIKE '%a%' OR species IS NULL) GROUP BY 1 ORDER BY 1`)
    expect(chat.map((r) => [r.s, Number(r.n)])).toEqual([
      ['Cupressus macrocarpa', 1], ['Palm', 1], ['Quercus agrifolia', 1], ['Washingtonia robusta', 1],
    ])
    // A species no block has is unequal, not an error.
    expect(Number((await rows(`SELECT count(*) AS n FROM ${TREES_TABLE} WHERE species = 'Ginkgo biloba'`))[0].n)).toBe(0)
    expect(Number((await rows(`SELECT count(*) AS n FROM ${TREES_TABLE} WHERE species = 'Palm' AND city = 'USSFO'`))[0].n)).toBe(1)
  })

  test('a scoped read names only its cells, and a query\'s own FROM trees follows it there', async () => {
    const boston = scopeSql([windowCellTable(key(BOS))])
    expect(boston).toBe(windowCellTable(key(BOS)))
    expect(scopeSql([])).not.toMatch(/trees_cell_/)
    expect(Number((await rows(`SELECT count(*) AS n FROM ${scopeSql(held)} tf`))[0].n)).toBe(7)
    // The URL filter's shape: written against the published schema, run over Boston alone.
    const filtered = await rows(`
      WITH trees AS (${treesSelectSql(boston)}),
      base AS (SELECT * FROM trees WHERE tree_id IN (SELECT tree_id FROM trees WHERE species IN ('Acer rubrum')))
      SELECT tf.tree_id FROM base INNER JOIN ${boston} tf ON base.tree_id = tf.tree_id ORDER BY 1`)
    // sf-5 is an Acer rubrum too, and is not read.
    expect(filtered.map((r) => r.tree_id)).toEqual(['bos-1'])
  })

  test('a phone cuts its finer cells out of a block in one scan, each into its own table', async () => {
    const at = cellAt(SF[0], SF[1])
    const cell = cellKey(at.x, at.y)
    const staging = windowStagingSql('staging_test', [file('sf.parquet')], [cell], cellsBounds([cell]), CELL_ZOOM)
    await conn.run(staging.create)
    const counts = await rows(staging.counts)
    const expected = Number((await rows(`SELECT count(*) AS n FROM ${windowCellTable(key(SF))} tf WHERE ${windowCellSql('tf', CELL_ZOOM)} = ${cell}`))[0].n)
    expect(expected).toBeGreaterThan(0)
    expect(counts.map((r) => [Number(r.cell), Number(r.n)])).toEqual([[cell, expected]])
    // The cell's table is the block table's shape; the two cannot coexist under one name, so check it under the staging name.
    expect(await describeTable('staging_test')).toEqual(await describeTable(windowCellTable(key(SF))))
    expect(windowCellSplitSql('staging_test', cell, CELL_ZOOM)).toContain(windowCellTable(cell))
    await conn.run('DROP TABLE staging_test')
  })

  test('the bookmark\'s city-wide reads come from its projection file, or from nothing', async () => {
    await conn.run(publishedTreesViewSql(file('sf.parquet')))
    expect(Number((await rows(`SELECT count(*) AS n FROM ${PUBLISHED_TREES_VIEW}`))[0].n)).toBe(6)
    await conn.run(publishedTreesViewSql(null))
    expect(Number((await rows(`SELECT count(*) AS n FROM ${PUBLISHED_TREES_VIEW}`))[0].n)).toBe(0)
  })

  test('a reset drops and rebuilds every table and type', async () => {
    await load()
    expect(Number((await rows(`SELECT count(*) AS n FROM ${TREES_VIEW}`))[0].n)).toBe(7)
  })
})
