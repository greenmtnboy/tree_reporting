import { describe, expect, it } from 'vitest'
import { DuckDBInstance } from '@duckdb/node-api'
import { MAX_POINT_PALETTE, POINT_FALLBACK_COLOR, cityPointColorsSql, cityPointsSql, paletteIndexSql, pointPalette } from './cityPoints'

describe('pointPalette', () => {
  it('puts the fallback first, once, and caps at one byte', () => {
    expect(pointPalette(['#111111', POINT_FALLBACK_COLOR, '#111111', '#222222'])).toEqual([POINT_FALLBACK_COLOR, '#111111', '#222222'])
    const many = Array.from({ length: 400 }, (_, i) => `#${i.toString(16).padStart(6, '0')}`)
    expect(pointPalette(many)).toHaveLength(MAX_POINT_PALETTE + 1)
  })
})

describe('city point SQL', () => {
  it('reads positions, sizes and palette indices, and the colours alone for the same rows', async () => {
    const db = await DuckDBInstance.create(':memory:')
    const conn = await db.connect()
    await conn.run(`CREATE TABLE trees_fast AS SELECT * FROM (VALUES
      ('a', 1000.5, 2000.25, 3.0, '#111111'), ('b', 1010.0, 2005.0, 400.0, '#222222'), ('c', 1020.0, 2010.0, NULL, '#999999'))
      t(tree_id, x_3857, y_3857, dbh, c)`)
    const src = { prelude: 'WITH', from: 'trees_fast tf', where: '', dbh: 'COALESCE(tf.dbh, 3)' }
    const palette = pointPalette(['#111111', '#222222'])
    const rows = (await conn.runAndReadAll(cityPointsSql(src, 'tf.c', palette, 1000, 2000))).getRowObjectsJS()
    expect(rows.map((r) => [r.rowid, r.x, r.y, r.dbh, r.color])).toEqual([
      [0, 0.5, 0.25, 3, 1], [1, 10, 5, 255, 2], [2, 20, 10, 3, 0],
    ])
    const colors = (await conn.runAndReadAll(cityPointColorsSql(src, 'tf.c', palette))).getRowObjectsJS()
    expect(colors.map((r) => [r.rowid, r.color])).toEqual([[0, 1], [1, 2], [2, 0]])
    expect(paletteIndexSql('tf.c', [POINT_FALLBACK_COLOR])).toBe('CAST(0 AS UTINYINT)')
  })
})
