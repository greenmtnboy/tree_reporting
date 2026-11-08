import { describe, expect, test } from 'vitest'
import { createRequire } from 'node:module'
import { assembleLayeredTiles, renameMvtLayers } from './mvtLayers'

const require = createRequire(createRequire(import.meta.url).resolve('maplibre-gl'))
const { VectorTile } = require('@mapbox/vector-tile')
const Pbf = require('pbf')

/** A one-layer tile as ST_AsMVT writes it: version, name, one point feature with properties, keys, values, extent. */
function tile(name: string, props: Record<string, number>, x = 10, y = 20): Uint8Array {
  const pbf = new Pbf()
  pbf.writeMessage(3, (_: unknown, p: typeof pbf) => {
    p.writeVarintField(15, 2)
    p.writeStringField(1, name)
    const keys = Object.keys(props)
    p.writeMessage(2, (_f: unknown, fp: typeof pbf) => {
      fp.writeVarintField(3, 1)
      fp.writePackedVarint(2, keys.flatMap((_k, i) => [i, i]))
      fp.writePackedVarint(4, [9, x * 2, y * 2])
    }, null)
    for (const k of keys) p.writeStringField(3, k)
    for (const k of keys) p.writeMessage(4, (_v: unknown, vp: typeof pbf) => vp.writeSVarintField(6, props[k]), null)
    p.writeVarintField(5, 4096)
  }, null)
  return pbf.finish()
}

function decode(buf: Uint8Array) {
  const t = new VectorTile(new Pbf(buf))
  return Object.fromEntries(Object.entries(t.layers).map(([name, layer]: [string, any]) => [
    name,
    Array.from({ length: layer.length }, (_, i) => ({ ...layer.feature(i).properties, xy: layer.feature(i).loadGeometry()[0][0] })),
  ]))
}

describe('per-colour MVT source layers', () => {
  test('renaming keeps every feature, property and the extent', () => {
    const renamed = renameMvtLayers(tile('trees', { point_count: 3, grid_m: 64 }), '#a1b2c3')
    expect(decode(renamed)).toEqual({ '#a1b2c3': [{ point_count: 3, grid_m: 64, xy: { x: 10, y: 20 } }] })
    expect(new VectorTile(new Pbf(renamed)).layers['#a1b2c3'].extent).toBe(4096)
  })

  test('a long name changes the length prefix (multi-byte varint)', () => {
    const name = 'n'.repeat(300)
    expect(Object.keys(decode(renameMvtLayers(tile('trees', { a: 1 }), name)))).toEqual([name])
  })

  test('rows per tile and colour assemble into one tile per (x, y), a layer per colour', () => {
    const tiles = assembleLayeredTiles([
      { xtile: 1, ytile: 2, layer: '#111111', mvt: tile('trees', { point_count: 1 }) },
      { xtile: 1, ytile: 2, layer: '#222222', mvt: tile('trees', { point_count: 2 }, 5, 6) },
      { xtile: 3, ytile: 4, layer: null, mvt: tile('trees', { point_count: 7 }) },
      { xtile: 5, ytile: 6, layer: '#333333', mvt: null },
    ], '#66BB6A')
    expect([...tiles.keys()]).toEqual(['1/2', '3/4', '5/6'])
    expect(decode(tiles.get('1/2')!.mvt)).toEqual({
      '#111111': [{ point_count: 1, xy: { x: 10, y: 20 } }],
      '#222222': [{ point_count: 2, xy: { x: 5, y: 6 } }],
    })
    expect(Object.keys(decode(tiles.get('3/4')!.mvt))).toEqual(['#66BB6A'])
    expect(tiles.get('5/6')!.mvt.length).toBe(0)
  })
})
