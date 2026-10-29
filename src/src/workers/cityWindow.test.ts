import { describe, expect, test } from 'vitest'
import { CELL_ZOOM, cellAt, cellFromKey, cellKey, cellsBounds, cellsForTile, chunksToEvict, type Chunk } from './cityWindow'

describe('cells', () => {
  test('a key round-trips to its cell', () => {
    for (const [x, y] of [[0, 0], [4956, 6060], [16383, 16383]]) {
      expect(cellFromKey(cellKey(x, y))).toEqual({ x, y })
    }
  })

  test('a street tile sits in one cell; an overview tile spans its quarter-tree', () => {
    expect(cellsForTile(16, 19826, 24242)).toEqual([cellKey(19826 >> 2, 24242 >> 2)])
    expect(cellsForTile(CELL_ZOOM, 10, 20)).toEqual([cellKey(10, 20)])
    expect(cellsForTile(13, 5, 7).sort((a, b) => a - b)).toEqual(
      [cellKey(10, 14), cellKey(10, 15), cellKey(11, 14), cellKey(11, 15)].sort((a, b) => a - b))
  })

  test("a cell's box contains the points that fall in it", () => {
    const point = { lng: -71.0948, lat: 42.2341 }
    const cell = cellAt(point.lng, point.lat)
    const box = cellsBounds([cellKey(cell.x, cell.y)])
    expect(point.lng).toBeGreaterThanOrEqual(box.west)
    expect(point.lng).toBeLessThan(box.east)
    expect(point.lat).toBeGreaterThan(box.south)
    expect(point.lat).toBeLessThanOrEqual(box.north)
    // About 1.8 km across at Boston's latitude.
    expect((box.east - box.west) * 111.32 * Math.cos((point.lat * Math.PI) / 180)).toBeCloseTo(1.8, 1)
  })
})

describe('eviction', () => {
  const at = (x: number, rows: number): Chunk => ({ table: `t${x}`, cells: [cellKey(x, 100)], rows })
  const camera = { x: 0, y: 100 }

  test('holds everything under the budget', () => {
    expect(chunksToEvict([at(1, 10), at(9, 10)], camera, 100, 2)).toEqual([])
  })

  test('drops the farthest first, only as many as the budget needs', () => {
    expect(chunksToEvict([at(1, 50), at(5, 50), at(9, 50)], camera, 100, 2)).toEqual(['t9'])
    expect(chunksToEvict([at(1, 50), at(5, 50), at(9, 50)], camera, 40, 2)).toEqual(['t9', 't5'])
  })

  test('never drops what is near the camera, even over budget', () => {
    expect(chunksToEvict([at(0, 500), at(2, 500)], camera, 100, 2)).toEqual([])
  })

  test('drops nothing before the camera is known', () => {
    expect(chunksToEvict([at(9, 500)], null, 100, 2)).toEqual([])
  })
})
