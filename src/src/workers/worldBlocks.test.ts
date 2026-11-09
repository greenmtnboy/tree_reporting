import { describe, expect, it } from 'vitest'
import {
  BLOCK_ZOOM, blockAt, blockCenter, blockFile, blockFromKey, blockKey, blockOfCell, bookmarkAt, directoryIndex,
  parseWorldManifest, tileHasBlocks, worldDirectory,
} from './worldBlocks'
import { CHUNK_ZOOM, chunkKey } from './cityPoints'
import { CELL_ZOOM, cellAt, cellKey, cellsInBox, parentCell } from './cityWindow'

const BOSTON: [number, number] = [-71.0589, 42.3601]
const BURLINGTON: [number, number] = [-73.2121, 44.4759]
const keyAt = ([lng, lat]: [number, number], dx = 0, dy = 0) => { const b = blockAt(lng, lat); return blockKey(b.x + dx, b.y + dy) }

/** A manifest as data/world/publish_blocks.py writes it: Boston over three blocks, one shared with a neighbour, and Burlington. */
function manifestJson() {
  return {
    format: 1,
    generated_at: '2026-11-09T06:00:00+00:00',
    block_zoom: 12,
    columns: ['tree_id'],
    vocab: 'vocab-0123456789abcdef.parquet',
    trees: 1000 + 400 + 50 + 30 + 200,
    cities: [
      { code: 'USBOS', name: 'Boston', center: BOSTON, trees: 1430, blocks: 3, generation: 1 },
      { code: 'USSTO', name: 'Stoughton', center: [-71.1, 42.12], trees: 50, blocks: 1, generation: 2 },
      { code: 'USBTV', name: 'Burlington, VT', center: BURLINGTON, trees: 200, blocks: 1, generation: 3 },
    ],
    blocks: [
      [keyAt(BOSTON), 1000, 'aaaaaaaaaaaaaaaa', [0, 1000]],
      [keyAt(BOSTON, 1, 0), 400, 'bbbbbbbbbbbbbbbb', [0, 400]],
      [keyAt(BOSTON, 0, 3), 80, 'cccccccccccccccc', [0, 30, 1, 50]],
      [keyAt(BURLINGTON), 200, 'dddddddddddddddd', [2, 200]],
    ],
  }
}

describe('world blocks', () => {
  it('is the desktop chunk grid, and holds a phone cell whole', () => {
    expect(BLOCK_ZOOM).toBe(CHUNK_ZOOM)
    const b = blockAt(...BOSTON)
    expect(blockKey(b.x, b.y)).toBe(chunkKey(b.x, b.y))
    expect(blockFromKey(blockKey(b.x, b.y))).toEqual(b)
    const cell = cellAt(...BOSTON)
    expect(blockOfCell(cellKey(cell.x, cell.y), CELL_ZOOM)).toBe(blockKey(b.x, b.y))
    expect(parentCell(cellKey(cell.x, cell.y), CELL_ZOOM, BLOCK_ZOOM)).toBe(blockKey(b.x, b.y))
    // A block is its own cell on a desktop.
    expect(blockOfCell(blockKey(b.x, b.y), BLOCK_ZOOM)).toBe(blockKey(b.x, b.y))
    const [lng, lat] = blockCenter(blockKey(b.x, b.y))
    expect(blockAt(lng, lat)).toEqual(b)
  })

  it('reads a manifest into blocks by key, and refuses one it cannot read', () => {
    const manifest = parseWorldManifest(manifestJson())
    expect(manifest.trees).toBe(1680)
    expect(manifest.blocks.size).toBe(4)
    const block = manifest.blocks.get(keyAt(BOSTON))!
    expect(blockFile(block)).toBe(`blocks/${keyAt(BOSTON)}-aaaaaaaaaaaaaaaa.parquet`)
    expect(manifest.id).toContain('vocab-0123456789abcdef.parquet')
    expect(() => parseWorldManifest({ ...manifestJson(), format: 2 })).toThrow(/format/)
    expect(() => parseWorldManifest({ ...manifestJson(), block_zoom: 11 })).toThrow(/z11/)
    expect(() => parseWorldManifest(null)).toThrow()
  })

  it('says which bookmark the camera is over from the blocks alone', () => {
    const directory = worldDirectory(parseWorldManifest(manifestJson()))
    const index = directoryIndex(directory)
    expect(directory.blocks).toHaveLength(12)
    expect(bookmarkAt(directory, index, ...BOSTON)).toBe('USBOS')
    expect(bookmarkAt(directory, index, ...BURLINGTON)).toBe('USBTV')
    // A block two inventories share goes to the one with more trees in it.
    const [sharedLng, sharedLat] = blockCenter(keyAt(BOSTON, 0, 3))
    expect(bookmarkAt(directory, index, sharedLng, sharedLat)).toBe('USSTO')
    // A gap in an inventory (a park, a harbour) is still that city: the nearest ring's fullest block.
    const [gapLng, gapLat] = blockCenter(keyAt(BOSTON, -1, -1))
    expect(bookmarkAt(directory, index, gapLng, gapLat)).toBe('USBOS')
    // Half way to Vermont there is no bookmark, and the map keeps the one it had.
    expect(bookmarkAt(directory, index, -72.1, 43.4)).toBeNull()
  })

  it('answers whether a tile holds any trees at every zoom', () => {
    const manifest = parseWorldManifest(manifestJson())
    const b = blockAt(...BOSTON)
    expect(tileHasBlocks(manifest, 12, b.x, b.y)).toBe(true)
    expect(tileHasBlocks(manifest, 12, b.x - 1, b.y)).toBe(false)
    expect(tileHasBlocks(manifest, 16, b.x << 4, b.y << 4)).toBe(true)
    expect(tileHasBlocks(manifest, 16, (b.x - 1) << 4, b.y << 4)).toBe(false)
    expect(tileHasBlocks(manifest, 10, b.x >> 2, b.y >> 2)).toBe(true)
    // Coarse enough that the manifest is the shorter list to walk.
    expect(tileHasBlocks(manifest, 3, b.x >> 9, b.y >> 9)).toBe(true)
    expect(tileHasBlocks(manifest, 3, 0, 0)).toBe(false)
    expect(tileHasBlocks(manifest, 0, 0, 0)).toBe(true)
  })

  it('lists the cells under a view, and declines a view too wide to list', () => {
    const [lng, lat] = blockCenter(keyAt(BOSTON))
    const box = { west: lng - 0.01, east: lng + 0.01, south: lat - 0.005, north: lat + 0.005 }
    const cells = cellsInBox(box, CELL_ZOOM)!
    const at = cellAt(lng, lat)
    expect(cells).toContain(cellKey(at.x, at.y))
    expect(cells.every((key) => blockOfCell(key, CELL_ZOOM) === keyAt(BOSTON))).toBe(true)
    expect(cellsInBox(box, BLOCK_ZOOM)).toEqual([keyAt(BOSTON)])
    expect(cellsInBox({ west: -80, east: -60, south: 30, north: 50 }, CELL_ZOOM)).toBeNull()
  })
})
