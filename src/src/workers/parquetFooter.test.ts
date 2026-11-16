import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { footerLength, parquetRowGroups, rowGroupsInBox } from './parquetFooter'

/**
 * The footer reader against the engine's own reading of the same file: a
 * block as the pipeline writes one (zstd, 4,096-row groups, rows in cell
 * order), with the column kinds a block has, strings and nulls included.
 */
describe('parquet footer', () => {
  const dir = mkdtempSync(join(tmpdir(), 'parquet-footer-'))
  const file = join(dir, 'block.parquet').replace(/\\/g, '/')
  let conn: DuckDBConnection
  let bytes: Uint8Array
  type Row = Record<string, unknown>
  const rows = async (sql: string): Promise<Row[]> => (await conn.runAndReadAll(sql)).getRowObjects() as Row[]

  beforeAll(async () => {
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    await conn.run('SELECT setseed(0.42)')
    await conn.run(`COPY (
      SELECT 'tree-' || i AS tree_id,
        CAST(42.28 + (i % 400) * 0.0004 AS DOUBLE) AS latitude,
        CAST(-71.20 + (i // 400) * 0.0004 AS DOUBLE) AS longitude,
        CASE WHEN i % 7 = 0 THEN NULL ELSE ['Acer rubrum', 'Quercus alba', 'Tilia cordata'][1 + i % 3] END AS species,
        CAST(random() * 40 AS FLOAT) AS dbh,
        i % 97 = 0 AS notable,
        CAST(i AS BIGINT) AS n
      FROM range(100000) t(i)
      ORDER BY longitude, latitude
    ) TO '${file}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 4096)`)
    bytes = new Uint8Array(readFileSync(file))
  })
  afterAll(() => {
    conn.closeSync()
    rmSync(dir, { recursive: true, force: true })
  })

  test('reads each row group where the engine says it is, with the ranges the engine prunes on', async () => {
    const expected = await rows(`
      SELECT row_group_id AS id, min(row_group_num_rows) AS n,
        min(COALESCE(NULLIF(dictionary_page_offset, 0), data_page_offset)) AS first,
        max(COALESCE(NULLIF(dictionary_page_offset, 0), data_page_offset) + total_compressed_size) AS last,
        max(CASE WHEN path_in_schema = 'latitude' THEN CAST(stats_min_value AS DOUBLE) END) AS lat_min,
        max(CASE WHEN path_in_schema = 'latitude' THEN CAST(stats_max_value AS DOUBLE) END) AS lat_max,
        max(CASE WHEN path_in_schema = 'longitude' THEN CAST(stats_min_value AS DOUBLE) END) AS lng_min,
        max(CASE WHEN path_in_schema = 'longitude' THEN CAST(stats_max_value AS DOUBLE) END) AS lng_max,
        max(CASE WHEN path_in_schema = 'dbh' THEN CAST(stats_max_value AS DOUBLE) END) AS dbh_max
      FROM parquet_metadata('${file}') GROUP BY 1 ORDER BY 1`)
    const groups = parquetRowGroups(bytes)
    expect(groups).toHaveLength(expected.length)
    expect(groups.length).toBeGreaterThan(5)
    for (const [i, group] of groups.entries()) {
      expect(group.start).toBe(Number(expected[i].first))
      expect(group.end).toBe(Number(expected[i].last))
      expect(group.rows).toBe(Number(expected[i].n))
      expect(group.ranges.get('latitude')).toEqual({ min: expected[i].lat_min, max: expected[i].lat_max })
      expect(group.ranges.get('longitude')).toEqual({ min: expected[i].lng_min, max: expected[i].lng_max })
      // A FLOAT column's range is read at its own width.
      expect(group.ranges.get('dbh')!.max).toBeCloseTo(Number(expected[i].dbh_max), 4)
      // Strings, booleans and integers have statistics too; only the coordinates' kind is kept.
      expect([...group.ranges.keys()].sort()).toEqual(['dbh', 'latitude', 'longitude'])
    }
    // The groups tile the file from its magic to its footer.
    expect(groups[0].start).toBe(4)
    for (let i = 1; i < groups.length; i += 1) expect(groups[i].start).toBe(groups[i - 1].end)
    expect(groups[groups.length - 1].end).toBeLessThanOrEqual(bytes.length - footerLength(bytes))
  })

  test('needs only the file\'s tail, and says how much of it', () => {
    const length = footerLength(bytes.subarray(bytes.length - 8))
    expect(length).toBeGreaterThan(1000)
    expect(length).toBeLessThan(bytes.length / 10)
    expect(parquetRowGroups(bytes.subarray(bytes.length - length))).toEqual(parquetRowGroups(bytes))
    expect(() => parquetRowGroups(bytes.subarray(bytes.length - length + 1))).toThrow(/not all of it/)
    expect(() => footerLength(new Uint8Array(64))).toThrow(/not a parquet file/)
  })

  test('a box takes the row groups the engine would read for it, and no others', async () => {
    const groups = parquetRowGroups(bytes)
    const box = { west: -71.171, east: -71.158, south: 42.30, north: 42.34 }
    const hits = rowGroupsInBox(groups, box)
    expect(hits.length).toBeGreaterThan(0)
    expect(hits.length).toBeLessThan(groups.length / 2)
    // Every row of the box is in a row group taken: the rows before each group say which group a row is in.
    const firstRow = groups.map((_, i) => groups.slice(0, i).reduce((sum, g) => sum + g.rows, 0))
    const inBox = await rows(`SELECT DISTINCT file_row_number AS rn FROM read_parquet('${file}', file_row_number = true)
      WHERE latitude BETWEEN ${box.south} AND ${box.north} AND longitude BETWEEN ${box.west} AND ${box.east}`)
    expect(inBox.length).toBeGreaterThan(1000)
    const groupOf = (rn: number) => firstRow.reduce((found, first, i) => (first <= rn ? i : found), 0)
    const needed = new Set(inBox.map((row) => groupOf(Number(row.rn))))
    for (const group of needed) expect(hits).toContain(group)
    // A group with no statistics for a coordinate is always taken.
    const blind = groups.map((group) => ({ ...group, ranges: new Map() }))
    expect(rowGroupsInBox(blind, box)).toEqual(groups.map((_, i) => i))
  })
})
