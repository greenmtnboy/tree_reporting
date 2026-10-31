import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  AGG_CACHE_TABLE,
  CITY_ENUM_TYPES,
  COLOR_OVERRIDE_TABLE,
  treeKeySql,
  TILE_INDEX_ZOOM,
  TREES_TABLE,
  TREES_VIEW,
  WEB_MERCATOR_MAX,
  WEB_MERCATOR_WORLD,
  aggCacheSql,
  cityTileBoundsSql,
  colorSql,
  dropCityTablesSql,
  loadCityTablesSql,
  tileIndexSql,
  type CityTableOptions,
} from './cityTables'
import { CATEGORY_COLORS } from '../treeFormColors'

/**
 * The city load, run under native DuckDB against a fixture parquet shaped
 * like a published city file. Native and wasm share the storage format and
 * the SQL dialect (both 1.4.x), so what passes here is what the worker runs.
 */
describe('city tables', () => {
  let conn: DuckDBConnection
  let dir: string
  let options: CityTableOptions
  const sql = (path: string) => path.replace(/\\/g, '/')

  const describeTable = async (table: string) =>
    Object.fromEntries((await conn.runAndReadAll(`DESCRIBE ${table}`)).getRowObjects()
      .map((row) => [String(row.column_name), String(row.column_type)]))

  const load = async (overrides: Partial<CityTableOptions> = {}) => {
    for (const statement of dropCityTablesSql()) await conn.run(statement)
    for (const statement of loadCityTablesSql({ ...options, ...overrides })) await conn.run(statement)
  }

  beforeAll(async () => {
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    dir = mkdtempSync(join(tmpdir(), 'city-tables-'))
    const trees = sql(join(dir, 'city.parquet'))
    const predictions = sql(join(dir, 'predictions.parquet'))
    options = { projectionUrl: null, parquetUrl: trees, predictionsUrl: predictions, predictionsAreUnique: false, city: 'USSFO', hasDedupFlag: true, hasCultivar: true, hasCrownWidth: true, hasHeight: true }

    await conn.run(`CREATE TABLE species_enrichment (species VARCHAR, common_names VARCHAR[], tree_form VARCHAR);
      INSERT INTO species_enrichment VALUES
        ('Quercus agrifolia', ['Coast live oak', 'California live oak'], 'spreading'),
        ('Washingtonia robusta', ['Mexican fan palm'], 'palm'),
        ('Cupressus macrocarpa', [], NULL)`)
    await conn.run(`COPY (
      SELECT * REPLACE (
        CAST(diameter_at_breast_height AS DOUBLE) AS diameter_at_breast_height, CAST(latitude AS DOUBLE) AS latitude,
        CAST(longitude AS DOUBLE) AS longitude, CAST(crown_width_m AS DOUBLE) AS crown_width_m
      ) FROM (VALUES
        -- id, city, species, source name, plant date, dbh, lat, lon, photo, cultivar, crown, source, duplicate
        ('sf-1', 'USSFO', 'Quercus agrifolia', 'Oak', DATE '2001-03-04', 12.5, 37.76, -122.43, NULL, NULL, 9.5, 'SF_OPENDATA', false),
        ('sf-2', 'USSFO', 'Washingtonia robusta', NULL, NULL, NULL, 37.77, -122.42, NULL, NULL, NULL, 'SF_OPENDATA', false),
        ('sf-3', 'USSFO', 'Cupressus macrocarpa', 'Monterey cypress', NULL, 30, 37.78, -122.41, 'https://p/3.jpg', 'Goldcrest', NULL, 'COMMUNITY_USSFO', false),
        ('sf-4', 'USSFO', 'Palm', NULL, NULL, 4, 37.79, -122.40, NULL, NULL, NULL, 'OSM_USSFO', false),
        ('sf-5', 'USSFO', 'Acer rubrum', NULL, NULL, 8, 37.80, -122.39, NULL, 'October Glory', NULL, 'OSM_USSFO', false),
        ('sf-dup', 'USSFO', 'Acer rubrum', NULL, NULL, 8, 37.80, -122.39, NULL, NULL, NULL, 'OSM_USSFO', true),
        ('sf-nowhere', 'USSFO', 'Acer rubrum', NULL, NULL, 8, NULL, NULL, NULL, NULL, NULL, 'OSM_USSFO', false)
      ) v(tree_id, city, species, tree_name, plant_date, diameter_at_breast_height, latitude, longitude,
          submission_photo_url, cultivar, crown_width_m, data_source, is_duplicate)
    ) TO '${trees}' (FORMAT PARQUET)`)
    await conn.run(`COPY (
      SELECT tree_id, city, CAST(predicted_crown_width_m AS DOUBLE) AS predicted_crown_width_m,
        CAST(height_m AS FLOAT) AS height_m, CAST(predicted_height_m AS FLOAT) AS predicted_height_m FROM (VALUES
        ('sf-1', 'USSFO', 7.0, 14.5, 11.0), ('sf-2', 'USSFO', 5.0, NULL, 9.0), ('sf-2', 'USSFO', 6.0, NULL, 10.0), ('sf-3', 'USBOS', 40.0, 30.0, 30.0)
      ) v(tree_id, city, predicted_crown_width_m, height_m, predicted_height_m)
    ) TO '${predictions}' (FORMAT PARQUET)`)
    await load()
  })
  afterAll(() => {
    conn.closeSync()
    rmSync(dir, { recursive: true, force: true })
  })

  test('the row keeps low-cardinality strings as per-city ENUMs and one tile index pair', async () => {
    const columns = await describeTable(TREES_TABLE)
    expect(columns.species).toMatch(/^ENUM\(/)
    expect(columns.tree_name).toMatch(/^ENUM\(/)
    expect(columns.source_tree_name).toMatch(/^ENUM\(/)
    expect(columns.cultivar).toMatch(/^ENUM\(/)
    expect(columns.data_source).toMatch(/^ENUM\(/)
    expect(columns.city).toMatch(/^ENUM\(/)
    expect(columns.tree_form).toMatch(/^ENUM\('default', 'palm'/)
    expect(columns.predicted_crown_width_m).toBe('FLOAT')
    expect(Object.keys(columns).filter((c) => /tile_z/.test(c)).sort()).toEqual([`xtile_z${TILE_INDEX_ZOOM}`, `ytile_z${TILE_INDEX_ZOOM}`])
    // The measurements the card and the chat print stay exact.
    expect(columns.diameter_at_breast_height).toBe('DOUBLE')
    expect(columns.crown_width_m).toBe('DOUBLE')
    expect(columns.latitude).toBe('DOUBLE')
    const types = (await conn.runAndReadAll(`SELECT type_name FROM duckdb_types() WHERE logical_type = 'ENUM' AND NOT internal ORDER BY 1`))
      .getRowObjects().map((row) => String(row.type_name))
    expect(types).toEqual([...Object.values(CITY_ENUM_TYPES)].sort())
  })

  test('the trees view presents the published schema as VARCHAR and drops unplaced and duplicate rows', async () => {
    const columns = await describeTable(TREES_VIEW)
    expect(columns).toEqual({
      tree_id: 'VARCHAR', city: 'VARCHAR', data_source: 'VARCHAR', tree_name: 'VARCHAR', plant_date: 'DATE',
      species: 'VARCHAR', cultivar: 'VARCHAR', crown_width_m: 'DOUBLE', latitude: 'DOUBLE', longitude: 'DOUBLE',
      diameter_at_breast_height: 'DOUBLE', submission_photo_url: 'VARCHAR',
    })
    const ids = (await conn.runAndReadAll(`SELECT tree_id FROM ${TREES_VIEW} ORDER BY 1`)).getRowObjects().map((r) => r.tree_id)
    expect(ids).toEqual(['sf-1', 'sf-2', 'sf-3', 'sf-4', 'sf-5'])
    // The view's tree_name is the source's own name, as published.
    const names = (await conn.runAndReadAll(`SELECT tree_id, tree_name, cultivar FROM ${TREES_VIEW} ORDER BY 1`)).getRowObjects()
    expect(names.map((r) => [r.tree_id, r.tree_name, r.cultivar])).toEqual([
      ['sf-1', 'Oak', null], ['sf-2', null, null], ['sf-3', 'Monterey cypress', 'Goldcrest'], ['sf-4', null, null], ['sf-5', null, 'October Glory'],
    ])
  })

  test('display names and forms come from enrichment, then the source, then the sentinel', async () => {
    const rows = (await conn.runAndReadAll(
      `SELECT tree_id, CAST(tree_name AS VARCHAR) AS tree_name, CAST(tree_form AS VARCHAR) AS tree_form, dbh FROM ${TREES_TABLE} ORDER BY 1`,
    )).getRowObjects().map((r) => [r.tree_id, r.tree_name, r.tree_form, r.dbh])
    expect(rows).toEqual([
      ['sf-1', 'Coast live oak', 'spreading', 12.5],
      ['sf-2', 'Mexican fan palm', 'palm', 3],
      ['sf-3', 'Monterey cypress', 'default', 30],
      ['sf-4', 'Palm (species not recorded)', 'palm', 4],
      ['sf-5', 'Acer rubrum', 'default', 8],
    ])
  })

  test('the published prediction is folded in per city, taking the largest of duplicate rows', async () => {
    const rows = (await conn.runAndReadAll(`SELECT tree_id, predicted_crown_width_m AS w FROM ${TREES_TABLE} ORDER BY 1`)).getRowObjects()
    expect(rows.map((r) => [r.tree_id, r.w])).toEqual([['sf-1', 7], ['sf-2', 6], ['sf-3', null], ['sf-4', null], ['sf-5', null]])
    await load({ predictionsUrl: null })
    const none = (await conn.runAndReadAll(`SELECT count(predicted_crown_width_m) AS n FROM ${TREES_TABLE}`)).getRowObjects()[0]
    expect(Number(none.n)).toBe(0)
    await load()
  })

  test('heights ride in with the prediction, measured and predicted side by side', async () => {
    const columns = await describeTable(TREES_TABLE)
    expect(columns.height_m).toBe('FLOAT')
    expect(columns.predicted_height_m).toBe('FLOAT')
    const rows = (await conn.runAndReadAll(`SELECT tree_id, height_m AS h, predicted_height_m AS p FROM ${TREES_TABLE} ORDER BY 1`)).getRowObjects()
    expect(rows.map((r) => [r.tree_id, r.h, r.p])).toEqual([
      ['sf-1', 14.5, 11], ['sf-2', null, 10], ['sf-3', null, null], ['sf-4', null, null], ['sf-5', null, null],
    ])
  })

  test('a per-city slice is joined as it is, without the aggregate', async () => {
    // The slice's grain is one row per tree, so the load trusts it and skips
    // the hash aggregate the all-city file needs.
    const slice = sql(join(dir, 'slice.parquet'))
    await conn.run(`COPY (SELECT tree_id, city, CAST(width AS DOUBLE) AS predicted_crown_width_m FROM (VALUES
      ('sf-1', 'USSFO', 7.5), ('sf-4', 'USSFO', 3.0), ('sf-9', 'USBOS', 40.0)) v(tree_id, city, width)) TO '${slice}' (FORMAT PARQUET)`)
    // Built before heights: no height columns, so the probe says so and the
    // load reads null heights rather than failing.
    await load({ predictionsUrl: slice, predictionsAreUnique: true, hasHeight: false })
    const rows = (await conn.runAndReadAll(`SELECT tree_id, predicted_crown_width_m AS w, height_m, predicted_height_m FROM ${TREES_TABLE} ORDER BY 1`)).getRowObjects()
    expect(rows.map((r) => [r.tree_id, r.w])).toEqual([['sf-1', 7.5], ['sf-2', null], ['sf-3', null], ['sf-4', 3], ['sf-5', null]])
    expect(rows.every((r) => r.height_m === null && r.predicted_height_m === null)).toBe(true)
    await load()
  })

  test('older parquets without the dedup flag or the cultivar column still load', async () => {
    await load({ hasDedupFlag: false, hasCultivar: false, hasCrownWidth: false })
    const rows = (await conn.runAndReadAll(`SELECT tree_id, cultivar, crown_width_m FROM ${TREES_VIEW} ORDER BY 1`)).getRowObjects()
    expect(rows.map((r) => r.tree_id)).toContain('sf-dup')
    expect(rows.every((r) => r.cultivar === null && r.crown_width_m === null)).toBe(true)
    await load()
  })

  test('every zoom up to 20 shifts the stored index to the value the formula gives', async () => {
    for (const z of [0, 5, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]) {
      const span = `(${WEB_MERCATOR_WORLD} / pow(2, ${z}))`
      const rows = (await conn.runAndReadAll(`
        SELECT
          ${tileIndexSql('tf', 'x', z)} AS x, ${tileIndexSql('tf', 'y', z)} AS y,
          CAST(floor((tf.x_3857 + ${WEB_MERCATOR_MAX}) / ${span}) AS INTEGER) AS fx,
          CAST(floor((${WEB_MERCATOR_MAX} - tf.y_3857) / ${span}) AS INTEGER) AS fy
        FROM ${TREES_TABLE} tf`)).getRowObjects()
      for (const row of rows) {
        expect([z, row.x, row.y]).toEqual([z, row.fx, row.fy])
      }
    }
  })

  test('the city bounds query reports a tile extent per requested zoom', async () => {
    const row = (await conn.runAndReadAll(cityTileBoundsSql([13, 20]))).getRowObjects()[0]
    expect(Object.keys(row).sort()).toEqual(['max_x13', 'max_x20', 'max_y13', 'max_y20', 'min_x13', 'min_x20', 'min_y13', 'min_y20'])
    expect(Number(row.min_x13)).toBeLessThanOrEqual(Number(row.max_x13))
    expect(Number(row.min_x20) >> 7).toBe(Number(row.min_x13))
  })

  test('colours are a CASE over the form until an override table exists, which then wins per tree', async () => {
    const plain = colorSql('tf', false)
    expect(plain.join).toBe('')
    const defaults = (await conn.runAndReadAll(`SELECT tree_id, ${plain.expr} AS c FROM ${TREES_TABLE} tf ORDER BY 1`)).getRowObjects()
    expect(defaults.map((r) => r.c)).toEqual([
      CATEGORY_COLORS.spreading, CATEGORY_COLORS.palm, CATEGORY_COLORS.default, CATEGORY_COLORS.palm, CATEGORY_COLORS.default,
    ])
    await conn.run(`CREATE TEMP TABLE ${COLOR_OVERRIDE_TABLE} AS SELECT ${treeKeySql("'sf-1'")} AS tree_key, '#ff0000' AS display_color`)
    const over = colorSql('tf', true)
    const colours = (await conn.runAndReadAll(`SELECT tf.tree_id, ${over.expr} AS c FROM ${TREES_TABLE} tf ${over.join} ORDER BY 1`)).getRowObjects()
    expect(colours.map((r) => r.c)).toEqual([
      '#ff0000', CATEGORY_COLORS.palm, CATEGORY_COLORS.default, CATEGORY_COLORS.palm, CATEGORY_COLORS.default,
    ])
    await conn.run(aggCacheSql(32, true))
    const agg = (await conn.runAndReadAll(`SELECT category, display_color, point_count FROM ${AGG_CACHE_TABLE} ORDER BY display_color, category`)).getRowObjects()
    expect(agg.map((r) => [String(r.category), r.display_color, Number(r.point_count)])).toEqual([
      ['default', CATEGORY_COLORS.default, 1], ['default', CATEGORY_COLORS.default, 1],
      ['palm', CATEGORY_COLORS.palm, 1], ['palm', CATEGORY_COLORS.palm, 1], ['spreading', '#ff0000', 1],
    ])
    await conn.run(`DROP TABLE ${COLOR_OVERRIDE_TABLE}`)
    await conn.run(aggCacheSql(32, false))
  })

  test('chat SQL over the view and literal comparisons on the table behave as with VARCHAR', async () => {
    const chat = (await conn.runAndReadAll(`
      SELECT COALESCE(species, 'Unknown') AS s, count(*) AS n FROM ${TREES_VIEW}
      WHERE species LIKE '%a%' OR species IS NULL GROUP BY 1 ORDER BY 1`)).getRowObjects()
    expect(chat.map((r) => [r.s, Number(r.n)])).toEqual([
      ['Cupressus macrocarpa', 1], ['Palm', 1], ['Quercus agrifolia', 1], ['Washingtonia robusta', 1],
    ])
    // A species this city has never seen is unequal, not an error.
    const none = (await conn.runAndReadAll(`SELECT count(*) AS n FROM ${TREES_TABLE} WHERE species = 'Ginkgo biloba'`)).getRowObjects()[0]
    expect(Number(none.n)).toBe(0)
    const one = (await conn.runAndReadAll(`SELECT count(*) AS n FROM ${TREES_TABLE} WHERE species = 'Palm' AND city = 'USSFO'`)).getRowObjects()[0]
    expect(Number(one.n)).toBe(1)
  })

  test('the browser projection loads with one scan, no joins, and the same row shape', async () => {
    // The pipeline's output (data/raw/tree_browser_city.preql): the display
    // name, the growth form and the prediction are columns.
    const projection = sql(join(dir, 'projection.parquet'))
    await conn.run(`COPY (
      SELECT * REPLACE (
        CAST(diameter_at_breast_height AS DOUBLE) AS diameter_at_breast_height, CAST(latitude AS DOUBLE) AS latitude,
        CAST(longitude AS DOUBLE) AS longitude, CAST(crown_width_m AS DOUBLE) AS crown_width_m,
        CAST(predicted_crown_width_m AS DOUBLE) AS predicted_crown_width_m,
        CAST(height_m AS FLOAT) AS height_m, CAST(predicted_height_m AS FLOAT) AS predicted_height_m
      ) FROM (VALUES
        ('sf-1', 'USSFO', 'SF_OPENDATA', 'Quercus agrifolia', NULL, 'Oak', 'Coast live oak', 'spreading', DATE '2001-03-04', 12.5, 9.5, 7.0, 13.0, 12.0, 37.76, -122.43, NULL),
        ('sf-2', 'USSFO', 'SF_OPENDATA', 'Washingtonia robusta', NULL, NULL, 'Mexican fan palm', 'palm', NULL, NULL, NULL, 5.0, NULL, NULL, 37.77, -122.42, NULL),
        ('sf-4', 'USSFO', 'OSM_USSFO', 'Palm', NULL, NULL, 'Palm (species not recorded)', 'palm', NULL, 4, NULL, NULL, NULL, NULL, 37.79, -122.40, NULL),
        ('sf-6', 'USSFO', 'OSM_USSFO', 'Acer rubrum', 'October Glory', NULL, 'Acer rubrum', 'drifted_form', NULL, 8, NULL, 3.0, NULL, 8.5, 37.80, -122.39, NULL),
        ('sf-nowhere', 'USSFO', 'OSM_USSFO', 'Acer rubrum', NULL, NULL, 'Acer rubrum', 'default', NULL, 8, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
      ) v(tree_id, city, data_source, species, cultivar, tree_name, display_name, tree_form, plant_date,
          diameter_at_breast_height, crown_width_m, predicted_crown_width_m, height_m, predicted_height_m,
          latitude, longitude, submission_photo_url)
    ) TO '${projection}' (FORMAT PARQUET)`)
    const statements = loadCityTablesSql({ ...options, projectionUrl: projection })
    // Nothing but the projection is read: no enrichment join, no predictions.
    expect(statements.join('\n')).not.toMatch(/species_enrichment|predictions\.parquet|city\.parquet/)
    expect(statements.join('\n').match(/read_parquet\(/g)?.length).toBeGreaterThan(0)
    for (const statement of dropCityTablesSql()) await conn.run(statement)
    for (const statement of statements) await conn.run(statement)

    const columns = await describeTable(TREES_TABLE)
    expect(columns.tree_name).toMatch(/^ENUM\(/)
    expect(columns.tree_form).toMatch(/^ENUM\('default', 'palm'/)
    expect(columns.predicted_crown_width_m).toBe('FLOAT')
    const rows = (await conn.runAndReadAll(
      `SELECT tree_id, CAST(tree_name AS VARCHAR) AS name, CAST(source_tree_name AS VARCHAR) AS source_name,
              CAST(tree_form AS VARCHAR) AS form, predicted_crown_width_m AS width, CAST(cultivar AS VARCHAR) AS cultivar
       FROM ${TREES_TABLE} ORDER BY 1`,
    )).getRowObjects().map((r) => [r.tree_id, r.name, r.source_name, r.form, r.width, r.cultivar])
    expect(rows).toEqual([
      ['sf-1', 'Coast live oak', 'Oak', 'spreading', 7, null],
      ['sf-2', 'Mexican fan palm', null, 'palm', 5, null],
      ['sf-4', 'Palm (species not recorded)', null, 'palm', null, null],
      // A form outside the vocabulary is 'default', not a failed load.
      ['sf-6', 'Acer rubrum', null, 'default', 3, 'October Glory'],
    ])
    // The view is the published schema either way.
    expect(Object.keys(await describeTable(TREES_VIEW))).toEqual([
      'tree_id', 'city', 'data_source', 'tree_name', 'plant_date', 'species', 'cultivar', 'crown_width_m',
      'latitude', 'longitude', 'diameter_at_breast_height', 'submission_photo_url',
    ])
    const heights = (await conn.runAndReadAll(`SELECT tree_id, height_m AS h, predicted_height_m AS p FROM ${TREES_TABLE} ORDER BY 1`)).getRowObjects()
    expect(heights.map((r) => [r.tree_id, r.h, r.p])).toEqual([['sf-1', 13, 12], ['sf-2', null, null], ['sf-4', null, null], ['sf-6', null, 8.5]])

    // A projection built before heights has neither column; the probe's false
    // keeps the select from naming them.
    const older = sql(join(dir, 'projection-older.parquet'))
    await conn.run(`COPY (SELECT * EXCLUDE (height_m, predicted_height_m) FROM read_parquet('${projection}')) TO '${older}' (FORMAT PARQUET)`)
    for (const statement of dropCityTablesSql()) await conn.run(statement)
    for (const statement of loadCityTablesSql({ ...options, projectionUrl: older, hasHeight: false })) await conn.run(statement)
    const none = (await conn.runAndReadAll(`SELECT count(height_m) + count(predicted_height_m) AS n, count(*) AS rows FROM ${TREES_TABLE}`)).getRowObjects()[0]
    expect([Number(none.n), Number(none.rows)]).toEqual([0, 4])
    await load()
  })

  test('a city switch drops and rebuilds every table and type', async () => {
    await load({ city: 'USBOS' })
    await load()
    const row = (await conn.runAndReadAll(`SELECT count(*) AS n FROM ${TREES_VIEW}`)).getRowObjects()[0]
    expect(Number(row.n)).toBe(5)
  })
})
