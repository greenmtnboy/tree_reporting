import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { SparseBlock, type RangeFetch, type RangeRequest } from './sparseBlocks'

/**
 * A block held in part, read by the engine as if it were the file: whatever
 * a box's read needs must be there, and what the engine does not read must
 * not have been fetched. The file is shaped like a block the pipeline writes,
 * in ten row groups so the requests below can be counted.
 */
describe('a block held in part', () => {
  const dir = mkdtempSync(join(tmpdir(), 'sparse-block-')).replace(/\\/g, '/')
  const file = `${dir}/block.parquet`
  let conn: DuckDBConnection
  let whole: Uint8Array
  let asked: RangeRequest[] = []
  const BOX = { west: -71.171, east: -71.158, south: 42.30, north: 42.34 }
  const TAIL = 8 * 1024
  const inBox = (path: string, box = BOX) => `SELECT tree_id FROM read_parquet('${path}', file_row_number = true)
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
      AND latitude BETWEEN ${box.south} AND ${box.north} AND longitude BETWEEN ${box.west} AND ${box.east} ORDER BY tree_id`
  const ids = async (sql: string) => ((await conn.runAndReadAll(sql)).getRowObjects() as { tree_id: string }[]).map((row) => row.tree_id)

  const fetchRange: RangeFetch = async (_name, range) => {
    asked.push(range)
    const start = 'suffix' in range ? Math.max(0, whole.length - range.suffix) : range.start
    return { bytes: whole.slice(start, 'suffix' in range ? whole.length : range.end), start, total: whole.length }
  }
  /** The held bytes on disk, for the engine to read as the block's file. */
  const onDisk = (block: SparseBlock, name: string) => {
    writeFileSync(`${dir}/${name}`, block.bytes)
    return `${dir}/${name}`
  }

  beforeAll(async () => {
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    await conn.run(`COPY (
      SELECT 'tree-' || i AS tree_id,
        CAST(42.28 + (i % 400) * 0.0004 AS DOUBLE) AS latitude,
        CAST(-71.20 + (i // 400) * 0.0004 AS DOUBLE) AS longitude,
        ['Acer rubrum', 'Quercus alba', 'Tilia cordata'][1 + i % 3] AS species,
        i % 97 = 0 AS notable
      FROM range(100000) t(i)
      ORDER BY longitude, latitude
    ) TO '${file}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 10240)`)
    whole = new Uint8Array(readFileSync(file))
  })
  afterAll(() => {
    conn.closeSync()
    rmSync(dir, { recursive: true, force: true })
  })

  test('opens on the footer alone, then fetches the row groups a box reaches in one request per run of them', async () => {
    asked = []
    // The file is a tenth of a megabyte, so the footer is asked for in less than the default guess, which would be most of it.
    const block = await SparseBlock.open('block', fetchRange, TAIL)
    expect(asked).toEqual([{ suffix: TAIL }])
    expect(block.total).toBe(whole.length)
    expect(block.groups).toHaveLength(10)

    await block.cover(BOX)
    // The box lies in a strip of longitude: neighbouring row groups, one request.
    expect(asked).toHaveLength(2)
    expect(block.requests).toBe(2)
    expect(block.fetched).toBeLessThan(whole.length / 2)

    // The engine reads the box's rows from what is held exactly as from the file.
    const expected = await ids(inBox(file))
    expect(expected.length).toBeGreaterThan(1000)
    expect(await ids(inBox(onDisk(block, 'held.parquet')))).toEqual(expected)

    // Asking again fetches nothing; a box elsewhere fetches only what it adds.
    await block.cover(BOX)
    expect(asked).toHaveLength(2)
    const east = { west: -71.12, east: -71.11, south: 42.30, north: 42.34 }
    const before = block.fetched
    await block.cover(east)
    expect(asked).toHaveLength(3)
    expect(block.fetched).toBeGreaterThan(before)
    expect(block.fetched).toBeLessThanOrEqual(whole.length)
    expect(await ids(inBox(onDisk(block, 'held2.parquet'), east))).toEqual(await ids(inBox(file, east)))
    expect(await ids(inBox(`${dir}/held2.parquet`))).toEqual(expected)
  })

  test('what was not fetched is not readable: the engine fails on it rather than answering', async () => {
    const block = await SparseBlock.open('block', fetchRange, TAIL)
    await block.cover(BOX)
    const path = onDisk(block, 'partial.parquet')
    // A read with no box prunes nothing and runs into the fill.
    await expect(conn.runAndReadAll(`SELECT count(DISTINCT tree_id) FROM read_parquet('${path}')`)).rejects.toThrow()
    // Zeros would have been a risk the fill avoids: nothing unfetched is zero.
    const untouched = block.groups.find((group) => block.bytes[group.start] === 0xff)!
    expect(untouched).toBeDefined()
    expect(block.bytes.subarray(untouched.start, untouched.end).every((byte) => byte === 0xff)).toBe(true)
  })

  test('reads a footer longer than its first guess in a second request', async () => {
    asked = []
    const block = await SparseBlock.open('block', fetchRange, 256)
    expect(asked).toHaveLength(2)
    expect(asked[0]).toEqual({ suffix: 256 })
    expect(asked[1]).toMatchObject({ end: whole.length - 256 })
    expect(block.groups).toHaveLength(10)
    await block.cover(BOX)
    expect(await ids(inBox(onDisk(block, 'held3.parquet')))).toEqual(await ids(inBox(file)))
  })

  test('two loads at once share a request for the row groups they both need', async () => {
    asked = []
    const block = await SparseBlock.open('block', fetchRange, TAIL)
    await Promise.all([block.cover(BOX), block.cover(BOX), block.cover({ ...BOX, north: 42.33 })])
    expect(asked).toHaveLength(2)
  })

  test('a file smaller than the footer guess is held whole from the one request', async () => {
    asked = []
    const block = await SparseBlock.open('block', fetchRange, whole.length + 1000)
    await block.cover(BOX)
    expect(asked).toHaveLength(1)
    expect([...block.bytes]).toEqual([...whole])
  })

  test('refuses an answer that is not the bytes it asked for', async () => {
    const short: RangeFetch = async (name, range) => {
      const answer = await fetchRange(name, range)
      return 'suffix' in range ? answer : { ...answer, bytes: answer.bytes.subarray(0, answer.bytes.length - 1) }
    }
    const block = await SparseBlock.open('block', short, TAIL)
    await expect(block.cover(BOX)).rejects.toThrow(/asked for/)
    // And it can be asked again once the answers are right.
    const mended = await SparseBlock.open('block', fetchRange)
    await mended.cover(BOX)
    expect(await ids(inBox(onDisk(mended, 'held4.parquet')))).toEqual(await ids(inBox(file)))
  })
})
