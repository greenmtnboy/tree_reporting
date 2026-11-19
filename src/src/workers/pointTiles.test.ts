import { describe, expect, test } from 'vitest'
import { createRequire } from 'node:module'
import { COLOR_PROPERTY, encodePointTiles, TREE_SOURCE_LAYER, type PointProperty, type TileRowColumns } from './pointTiles'

const require = createRequire(createRequire(import.meta.url).resolve('maplibre-gl'))
const { VectorTile } = require('@mapbox/vector-tile')
const Pbf = require('pbf')

const HALF = 20037508.342789244

/** Rows as a query result hands them over: read by column. */
function columns(rows: Record<string, unknown>[]): TileRowColumns {
  return {
    numRows: rows.length,
    getChild: (name) => (rows.length === 0 || name in rows[0] ? { get: (i) => rows[i][name] } : null),
  }
}

/** A point `fx`, `fy` of the way across tile `x`, `y` at zoom `z`, in metres. */
function inTile(z: number, x: number, y: number, fx: number, fy: number) {
  const size = (2 * HALF) / 2 ** z
  return { xtile: x, ytile: y, x: -HALF + (x + fx) * size, y: HALF - (y + fy) * size }
}

/** What MapLibre's own decoder reads back: each layer's features with their properties and tile position. */
function decode(buf: Uint8Array) {
  const t = new VectorTile(new Pbf(buf))
  return Object.fromEntries(Object.entries(t.layers).map(([name, layer]: [string, any]) => [
    name,
    Array.from({ length: layer.length }, (_, i) => {
      const feature = layer.feature(i)
      return { type: feature.type, extent: layer.extent, ...feature.properties, xy: feature.loadGeometry()[0][0] }
    }),
  ]))
}

const POINT_PROPERTIES: PointProperty[] = [
  { name: 'id', kind: 'string' },
  { name: 'dbh', kind: 'double' },
  { name: 'notable', kind: 'flag' },
]

describe('point tiles', () => {
  test('a tree is a point at its place in its tile, with its id and diameter', () => {
    const tiles = encodePointTiles(columns([
      { ...inTile(16, 19838, 24243, 0.25, 0.5), layer: '#4CAF50', id: 'usbos-1', dbh: 12.5, notable: null },
    ]), 16, POINT_PROPERTIES, '#66BB6A')
    expect([...tiles.keys()]).toEqual(['19838/24243'])
    expect(decode(tiles.get('19838/24243')!.mvt)).toEqual({
      '#4CAF50': [{ type: 1, extent: 4096, id: 'usbos-1', dbh: 12.5, xy: { x: 1024, y: 2048 } }],
    })
  })

  test('each colour is its own source layer, and a row with no colour takes the fallback', () => {
    const at = inTile(15, 9919, 12121, 0.5, 0.5)
    const tiles = encodePointTiles(columns([
      { ...at, layer: '#111111', id: 'a', dbh: 1, notable: null },
      { ...at, layer: '#222222', id: 'b', dbh: 2, notable: null },
      { ...at, layer: '#111111', id: 'c', dbh: 1, notable: null },
      { ...at, layer: null, id: 'd', dbh: null, notable: null },
    ]), 15, POINT_PROPERTIES, '#66BB6A')
    const layers = decode(tiles.get('9919/12121')!.mvt)
    expect(Object.keys(layers)).toEqual(['#111111', '#222222', '#66BB6A'])
    expect(layers['#111111'].map((f) => f.id)).toEqual(['a', 'c'])
    expect(layers['#222222'].map((f) => [f.id, f.dbh])).toEqual([['b', 2]])
  })

  test('a null property is left off the feature, and notable is written only on a notable tree', () => {
    const at = inTile(16, 5, 6, 0.1, 0.1)
    const tiles = encodePointTiles(columns([
      { ...at, layer: '#111111', id: 'plain', dbh: null, notable: false },
      { ...at, layer: '#111111', id: 'famous', dbh: 40, notable: true },
    ]), 16, POINT_PROPERTIES, '#66BB6A')
    const [plain, famous] = decode(tiles.get('5/6')!.mvt)['#111111']
    expect(Object.keys(plain).sort()).toEqual(['extent', 'id', 'type', 'xy'])
    expect(famous).toMatchObject({ id: 'famous', dbh: 40, notable: true })
  })

  test('a single-layer tile holds every tree in one layer, its colour on the feature', () => {
    const at = inTile(16, 5, 6, 0.1, 0.1)
    const tiles = encodePointTiles(columns([
      { ...at, layer: '#111111', id: 'a', dbh: 3, notable: null },
      { ...at, layer: '#222222', id: 'b', dbh: 4, notable: true },
      { ...at, layer: '#111111', id: 'c', dbh: 5, notable: null },
      { ...at, layer: null, id: 'd', dbh: 6, notable: null },
    ]), 16, POINT_PROPERTIES, '#66BB6A', { singleLayer: true })
    const layers = decode(tiles.get('5/6')!.mvt)
    expect(Object.keys(layers)).toEqual([TREE_SOURCE_LAYER])
    expect(layers[TREE_SOURCE_LAYER].map((f) => [f.id, f[COLOR_PROPERTY] ?? null, f.notable ?? false])).toEqual([
      ['a', '#111111', false], ['b', '#222222', true], ['c', '#111111', false], ['d', null, false],
    ])
  })

  test('a colour shared by many trees is one value in the tile', () => {
    const at = inTile(16, 5, 6, 0.5, 0.5)
    const rows = Array.from({ length: 200 }, (_, i) => ({ ...at, layer: i % 2 ? '#111111' : '#222222', id: `t${i}`, dbh: 3, notable: null }))
    const single = encodePointTiles(columns(rows), 16, POINT_PROPERTIES, '#66BB6A', { singleLayer: true }).get('5/6')!.mvt
    const bare = encodePointTiles(columns(rows.map((r) => ({ ...r, layer: '#111111' }))), 16, POINT_PROPERTIES, '#66BB6A').get('5/6')!.mvt
    // Two colour strings and a few bytes of tag a feature, not a string a feature.
    expect(single.length - bare.length).toBeLessThan(200 * 3 + 64)
  })

  test('rows of several tiles come back as one tile each', () => {
    const tiles = encodePointTiles(columns([
      { ...inTile(16, 1, 1, 0.5, 0.5), layer: '#111111', id: 'a', dbh: 1, notable: null },
      { ...inTile(16, 2, 1, 0.5, 0.5), layer: '#111111', id: 'b', dbh: 1, notable: null },
      { ...inTile(16, 1, 1, 0.75, 0.5), layer: '#111111', id: 'c', dbh: 1, notable: null },
    ]), 16, POINT_PROPERTIES, '#66BB6A')
    expect([...tiles.keys()].sort()).toEqual(['1/1', '2/1'])
    expect(tiles.get('2/1')).toMatchObject({ xtile: 2, ytile: 1 })
    expect(decode(tiles.get('1/1')!.mvt)['#111111'].map((f) => f.id)).toEqual(['a', 'c'])
  })

  test('an overview cell carries its count and a constant grid size as integers', () => {
    const tiles = encodePointTiles(columns([
      { ...inTile(13, 2479, 3030, 0.5, 0.5), layer: '#2E7D32', dbh: 14, point_count: 37 },
      { ...inTile(13, 2479, 3030, 0.6, 0.5), layer: '#2E7D32', dbh: null, point_count: 2 },
    ]), 13, [
      { name: 'dbh', kind: 'int' },
      { name: 'point_count', kind: 'int' },
      { name: 'grid_m', kind: 'int', value: 32 },
    ], '#66BB6A')
    const cells = decode(tiles.get('2479/3030')!.mvt)['#2E7D32']
    expect(cells[0]).toMatchObject({ dbh: 14, point_count: 37, grid_m: 32 })
    expect(cells[1]).toMatchObject({ point_count: 2, grid_m: 32 })
    expect('dbh' in cells[1]).toBe(false)
  })

  test('a cell centre just past its tile edge is kept inside the buffer and dropped beyond it', () => {
    const size = (2 * HALF) / 2 ** 13
    const edge = inTile(13, 10, 10, 0, 0.5)
    const tiles = encodePointTiles(columns([
      { ...edge, x: edge.x - size * (32 / 4096), layer: '#111111', point_count: 1 },
      { ...edge, x: edge.x - size * (200 / 4096), layer: '#111111', point_count: 2 },
    ]), 13, [{ name: 'point_count', kind: 'int' }], '#66BB6A')
    const cells = decode(tiles.get('10/10')!.mvt)['#111111']
    expect(cells.map((f) => [f.point_count, f.xy.x])).toEqual([[1, -32]])
  })

  test('integers that come back as bigints, and a tile of many trees, decode whole', () => {
    const rows = Array.from({ length: 5000 }, (_, i) => ({
      ...inTile(16, 7, 7, (i % 100) / 100, Math.floor(i / 100) / 50),
      xtile: 7n, ytile: 7n, layer: i % 2 ? '#111111' : '#222222', id: `tree-${i}`, dbh: i % 30, notable: i === 4999,
    }))
    const layers = decode(encodePointTiles(columns(rows), 16, POINT_PROPERTIES, '#66BB6A').get('7/7')!.mvt)
    expect(layers['#111111']).toHaveLength(2500)
    expect(layers['#222222']).toHaveLength(2500)
    expect(layers['#111111'][2499]).toMatchObject({ id: 'tree-4999', dbh: 19, notable: true })
    expect(layers['#222222'][1]).toMatchObject({ id: 'tree-2', dbh: 2, xy: { x: 82, y: 0 } })
  })

  test('no rows is no tiles', () => {
    expect(encodePointTiles(columns([]), 16, [], '#66BB6A').size).toBe(0)
  })
})
