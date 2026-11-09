import { describe, expect, it } from 'vitest'
import { DuckDBInstance } from '@duckdb/node-api'
import {
  CHUNK_SIZE_M, CHUNK_ZOOM, MAX_POINT_PALETTE, POINT_FALLBACK_COLOR, bucketByChunk, gridOrderChunks, chunkFromKey, chunkKey, chunkOrigin,
  chunksInBox, paletteIndexSql, pointChunkColorsSql, pointChunkDirectorySql, pointChunksSql, pointPalette,
} from './cityPoints'
import { CELL_ZOOM } from './cityWindow'
import { WEB_MERCATOR_MAX, WEB_MERCATOR_WORLD } from './cityTables'

const toX = (lng: number) => (lng * WEB_MERCATOR_MAX) / 180
const toY = (lat: number) => 6378137 * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))
const tile = (lng: number, lat: number, z: number) => {
  const n = 2 ** z
  const r = (lat * Math.PI) / 180
  return [Math.floor(((lng + 180) / 360) * n), Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n)]
}

describe('chunk grid', () => {
  it('is one global grid that nests the phone window cells', () => {
    expect(CHUNK_SIZE_M).toBeCloseTo(WEB_MERCATOR_WORLD / 4096, 6)
    expect(CELL_ZOOM - CHUNK_ZOOM).toBe(2)
    for (const [lng, lat] of [[-71.06, 42.36], [151.2, -33.87], [-0.1, 51.5], [139.7, 35.7]]) {
      const [tx, ty] = tile(lng, lat, CHUNK_ZOOM)
      const key = chunkKey(tx, ty)
      expect(chunkFromKey(key)).toEqual({ x: tx, y: ty })
      const [ox, oy] = chunkOrigin(key)
      const x = toX(lng), y = toY(lat)
      expect(x - ox).toBeGreaterThanOrEqual(0)
      expect(x - ox).toBeLessThan(CHUNK_SIZE_M)
      expect(y - oy).toBeGreaterThanOrEqual(0)
      expect(y - oy).toBeLessThan(CHUNK_SIZE_M)
    }
  })

  it('lists a box’s chunks nearest its centre first', () => {
    const [x, y] = [toX(-71.06), toY(42.36)]
    const keys = chunksInBox(x - CHUNK_SIZE_M, y - CHUNK_SIZE_M, x + CHUNK_SIZE_M, y + CHUNK_SIZE_M)
    expect(keys.length).toBe(9)
    const [tx, ty] = tile(-71.06, 42.36, CHUNK_ZOOM)
    expect(keys[0]).toBe(chunkKey(tx, ty))
  })
})

describe('pointPalette', () => {
  it('puts the fallback first, once, and caps at one byte', () => {
    expect(pointPalette(['#111111', POINT_FALLBACK_COLOR, '#111111', '#222222'])).toEqual([POINT_FALLBACK_COLOR, '#111111', '#222222'])
    const many = Array.from({ length: 400 }, (_, i) => `#${i.toString(16).padStart(6, '0')}`)
    expect(pointPalette(many)).toHaveLength(MAX_POINT_PALETTE + 1)
    expect(paletteIndexSql('tf.c', [POINT_FALLBACK_COLOR])).toBe('CAST(0 AS UTINYINT)')
  })
})

describe('bucketByChunk', () => {
  it('groups rows by chunk in the order asked, without losing any', () => {
    const { offsets, columns } = bucketByChunk([7, 3, 7, 9, 3, 7], [3, 7, 5], { v: new Uint32Array([0, 1, 2, 3, 4, 5]) })
    expect([...offsets]).toEqual([0, 2, 5, 5])
    expect([...columns.v]).toEqual([1, 4, 0, 2, 5])
  })
})

describe('gridOrderChunks', () => {
  it('stores each chunk’s rows in its draw grid’s cell order, columns kept together', () => {
    let seed = 7
    const rnd = () => { seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; return seed / 2 ** 32 }
    const n = [3000, 0, 1200]
    const offsets = new Uint32Array([0, n[0], n[0], n[0] + n[2]])
    const total = n[0] + n[2]
    const x = Float32Array.from({ length: total }, () => rnd() * CHUNK_SIZE_M)
    const y = Float32Array.from({ length: total }, () => rnd() * CHUNK_SIZE_M)
    const id = Uint32Array.from({ length: total }, (_, i) => i)
    const before = new Map([...id].map((i) => [i, [x[i], y[i]]]))
    const grids = gridOrderChunks(offsets, x, y, { id })
    expect(grids).toHaveLength(3)
    for (let s = 0; s < 3; s++) {
      const g = grids[s]
      expect(g.start[g.cols * g.rows]).toBe(offsets[s + 1] - offsets[s])
      for (let c = 0; c < g.cols * g.rows; c++) {
        for (let k = g.start[c]; k < g.start[c + 1]; k++) {
          const i = offsets[s] + k
          expect(Math.min(g.cols - 1, Math.floor(x[i] / g.cell)) + Math.min(g.rows - 1, Math.floor(y[i] / g.cell)) * g.cols).toBe(c)
          expect(before.get(id[i])).toEqual([x[i], y[i]])
        }
      }
    }
  })
})

describe('point chunk SQL', () => {
  it('reads the directory, the chunks’ points from their origins, and their colours', async () => {
    const db = await DuckDBInstance.create(':memory:')
    const conn = await db.connect()
    // Two trees in one Boston chunk, one in the chunk east of it, one in Sydney.
    const trees: [string, number, number, number | null, string][] = [
      ['a', -71.0600, 42.3600, 3, '#111111'], ['b', -71.0601, 42.3601, 400, '#222222'],
      ['c', -71.0600 + 360 / 4096, 42.3600, null, '#999999'], ['d', 151.2, -33.87, 10, '#111111'],
    ]
    const values = trees.map(([id, lng, lat, dbh, c]) => {
      const [x20, y20] = tile(lng, lat, 20)
      return `('${id}', ${toX(lng)}, ${toY(lat)}, ${x20}, ${y20}, ${dbh ?? 'NULL'}, '${c}')`
    })
    await conn.run(`CREATE TABLE trees_fast (tree_id VARCHAR, x_3857 DOUBLE, y_3857 DOUBLE, xtile_z20 INTEGER, ytile_z20 INTEGER, dbh DOUBLE, c VARCHAR)`)
    await conn.run(`INSERT INTO trees_fast VALUES ${values.join(', ')}`)
    const src = { prelude: 'WITH', from: 'trees_fast tf', where: '', dbh: 'COALESCE(tf.dbh, 3)' }
    const keyOf = (lng: number, lat: number) => { const [x, y] = tile(lng, lat, CHUNK_ZOOM); return chunkKey(x, y) }
    const boston = keyOf(-71.06, 42.36), east = keyOf(-71.06 + 360 / 4096, 42.36), sydney = keyOf(151.2, -33.87)

    const dir = (await conn.runAndReadAll(pointChunkDirectorySql(src))).getRowObjectsJS()
    expect(Object.fromEntries(dir.map((r) => [Number(r.chunk), Number(r.n)]))).toEqual({ [boston]: 2, [east]: 1, [sydney]: 1 })

    const palette = pointPalette(['#111111', '#222222'])
    const rows = (await conn.runAndReadAll(pointChunksSql(src, [boston, east], 'tf.c', palette))).getRowObjectsJS()
    expect(rows).toHaveLength(3)
    for (const r of rows) {
      const [ox, oy] = chunkOrigin(Number(r.chunk))
      const tree = trees[Number(r.rowid)]
      expect(Number(r.x)).toBeCloseTo(toX(tree[1]) - ox, 1)
      expect(Number(r.y)).toBeCloseTo(toY(tree[2]) - oy, 1)
    }
    expect(rows.map((r) => [r.rowid, r.dbh, r.color]).sort()).toEqual([[0, 3, 1], [1, 255, 2], [2, 3, 0]])

    const colors = (await conn.runAndReadAll(pointChunkColorsSql(src, [sydney], 'tf.c', palette))).getRowObjectsJS()
    expect(colors.map((r) => [Number(r.chunk), r.rowid, r.color])).toEqual([[sydney, 3, 1]])
  })
})
