import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { crownQuery } from './crownQuery'
import { CROWN_DISTANCE_MAX, MAX_CROWNS, MAX_TREE_SPRITES, crownZoomOpacity, spriteCrownBlend, spriteOpacity, validCrownWidth } from '../lib/treeCrowns'

describe('nearby crown query', () => {
  let conn: DuckDBConnection
  beforeAll(async () => {
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    await conn.run(`CREATE TABLE trees_fast (
      tree_id VARCHAR, city VARCHAR, longitude DOUBLE, latitude DOUBLE,
      x_3857 DOUBLE, y_3857 DOUBLE, crown_width_m DOUBLE);
      CREATE TABLE predictions (tree_id VARCHAR, city VARCHAR, width DOUBLE);
      CREATE TABLE __tree_color_map (tree_id VARCHAR, display_color VARCHAR);`)
    await conn.run(`INSERT INTO trees_fast VALUES
      ('measured', 'USSFO', 0, 0, 0, 0, 12),
      ('predicted', 'USSFO', 0, 0, 10, 0, NULL),
      ('missing', 'USSFO', 0, 0, 20, 0, NULL),
      ('invalid', 'USSFO', 0, 0, 30, 0, 'NaN'),
      ('huge', 'USSFO', 0, 0, 40, 0, 1000),
      ('far', 'USSFO', 0, 0, 10000, 0, 10),
      ('edge', 'USSFO', 0, 0, 1700, 0, 10),
      ('wrong-city', 'USSFO', 0, 0, 50, 0, NULL);
      INSERT INTO predictions VALUES
      ('measured', 'USSFO', 8), ('predicted', 'USSFO', 7),
      ('invalid', 'USSFO', 9), ('huge', 'USSFO', 'Infinity'),
      ('wrong-city', 'USBOS', 20);
      INSERT INTO __tree_color_map SELECT tree_id, '#123456' FROM trees_fast;`)
    await conn.run("ALTER TABLE trees_fast ADD COLUMN tree_form VARCHAR DEFAULT 'broadleaf'; ALTER TABLE trees_fast ADD COLUMN dbh DOUBLE DEFAULT 18")
  })
  afterAll(() => conn.closeSync())
  const camera = { lng: 0, lat: 0, altitude: 800 }
  const run = async (base = 'SELECT tree_id FROM trees_fast', eye = camera) =>
    (await conn.runAndReadAll(crownQuery(eye, base, 'predictions'))).getRowObjects().map(({ category: _category, dbh: _dbh, ...row }) => row)

  test('prefers measurements, uses published predictions, rejects missing and corrupt widths', async () => {
    expect(await run()).toEqual([
      { id: 'measured', lng: 0, lat: 0, width: 12, measured: true, color: '#123456' },
      { id: 'predicted', lng: 0, lat: 0, width: 7, measured: false, color: '#123456' },
      { id: 'invalid', lng: 0, lat: 0, width: 9, measured: false, color: '#123456' },
    ])
  })

  test('honors the visible query and deduplicates tree IDs', async () => {
    const rows = await run("SELECT 'predicted' AS tree_id UNION ALL SELECT 'predicted'")
    expect(rows.map(row => row.id)).toEqual(['predicted'])
  })

  test('bounds by 3D camera distance, not distance from the target or viewport extent', async () => {
    expect((await run(undefined, { ...camera, altitude: 0 })).map(row => row.id)).toContain('edge')
    expect(await run(undefined, { ...camera, altitude: CROWN_DISTANCE_MAX })).toEqual([])
  })

  test('measured crowns survive an absent prediction publication', async () => {
    await conn.run('CREATE TABLE empty_predictions AS SELECT * FROM predictions WHERE false')
    const rows = (await conn.runAndReadAll(crownQuery(camera, 'SELECT tree_id FROM trees_fast', 'empty_predictions'))).getRowObjects()
    expect(rows.map(row => row.id)).toEqual(['measured'])
  })

  test('desktop first batches include missing widths and distant markers with sizing already resolved', async () => {
    const rows = (await conn.runAndReadAll(crownQuery({ ...camera, altitude: 3000,
      bounds: { west: -1, east: 1, south: -1, north: 1 } }, 'SELECT tree_id FROM trees_fast', 'predictions'))).getRowObjects()
    expect(rows.find(row => row.id === 'measured')?.width).toBe(12)
    expect(rows.find(row => row.id === 'predicted')?.width).toBe(7)
    expect(rows.find(row => row.id === 'missing')?.width).toBeNull()
    expect(rows.some(row => row.id === 'far')).toBe(true)
    const empty = (await conn.runAndReadAll(crownQuery({ ...camera,
      bounds: { west: 1, east: 2, south: 1, north: 2 } }, 'SELECT tree_id FROM trees_fast', 'predictions'))).getRowObjects()
    expect(empty).toEqual([])
  })

  test('desktop density budget is finite even for flat viewports', async () => {
    await conn.run(`INSERT INTO trees_fast SELECT 'view-' || i, 'USSFO', 0, 0, i, 0, NULL, 'broadleaf', 18 FROM range(${MAX_TREE_SPRITES + 50}) t(i);
      INSERT INTO __tree_color_map SELECT 'view-' || i, '#123456' FROM range(${MAX_TREE_SPRITES + 50}) t(i);`)
    const rows = (await conn.runAndReadAll(crownQuery({ ...camera,
      bounds: { west: -180, east: 180, south: -85, north: 85 } }, "SELECT tree_id FROM trees_fast WHERE tree_id LIKE 'view-%'", 'predictions'))).getRowObjects()
    expect(rows).toHaveLength(MAX_TREE_SPRITES)
    expect(rows[0].id).toBe('view-0')
    expect(rows[rows.length - 1]?.id).toBe(`view-${MAX_TREE_SPRITES - 1}`)
  })

  test('dense neighborhoods return only the nearest bounded batch', async () => {
    await conn.run(`INSERT INTO trees_fast SELECT 'dense-' || i, 'USSFO', 0, 0, i / 10.0, 0, 5, 'broadleaf', 18 FROM range(5000) t(i);
      INSERT INTO __tree_color_map SELECT 'dense-' || i, '#123456' FROM range(5000) t(i);`)
    const rows = await run("SELECT tree_id FROM trees_fast WHERE tree_id LIKE 'dense-%'")
    expect(rows).toHaveLength(MAX_CROWNS)
    expect(rows[0].id).toBe('dense-0')
    expect(rows[rows.length - 1]?.id).toBe(`dense-${MAX_CROWNS - 1}`)
  })

  test('latitude correction preserves the metre cutoff', async () => {
    // At latitude 60, a Mercator metre is half a ground metre.
    await conn.run(`INSERT INTO trees_fast VALUES
      ('north', 'USSFO', 0, 60, 2000, 8399737.889818357, 10, 'broadleaf', 18);
      INSERT INTO __tree_color_map VALUES ('north', '#123456');`)
    expect((await run("SELECT 'north' AS tree_id", { lng: 0, lat: 60, altitude: 500 })).map(row => row.id)).toEqual(['north'])
  })
})

test('zoom fades and invalid widths are bounded', () => {
  expect([15, 16.5, 17.25, 18, 22].map(crownZoomOpacity)).toEqual([0, 0, 0.5, 1, 1])
  for (const width of [null, undefined, 0, -1, NaN, Infinity, 1000, '5']) expect(validCrownWidth(width)).toBe(false)
  expect(validCrownWidth(0.5)).toBe(true)
  expect(validCrownWidth(60)).toBe(true)
  expect(spriteCrownBlend(14.4)).toBe(0)
  expect(spriteCrownBlend(19.5)).toBe(1)
  expect(spriteCrownBlend(16)).toBeGreaterThan(0)
  expect(spriteCrownBlend(18)).toBeGreaterThan(0.5)
  expect(spriteOpacity(14.4)).toBe(0)
  expect(spriteOpacity(15)).toBe(0.72)
  expect(() => crownQuery({ lng: NaN, lat: 0, altitude: 0 }, '', '')).toThrow('Invalid crown camera')
})
