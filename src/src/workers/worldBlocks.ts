/**
 * The world's trees as the pipeline publishes them for the map: one parquet
 * per geo block, and a manifest that lists them (data/world/publish_blocks.py,
 * docs/SEAMLESS_WORLD.md).
 *
 * A block is a Web Mercator tile at BLOCK_ZOOM, the grid the desktop's point
 * chunks already use (cityPoints.ts), and holds every tree in it whichever
 * inventory published it. Nothing here is a city: the map loads the blocks
 * under the camera, and a city is a bookmark, a name and a centre the URL
 * carries. The manifest's per-block city counts are what say which bookmark
 * the camera is over.
 *
 * Blocks and the vocabulary are content-addressed, so everything one manifest
 * names is from one build; a tab keeps the manifest it opened with.
 */

/** The zoom of a block; the manifest states its own and the loader refuses another. */
export const BLOCK_ZOOM = 12
const BLOCKS_PER_AXIS = 2 ** BLOCK_ZOOM
export const WORLD_MANIFEST_FORMAT = 1

export type WorldCity = {
  code: string
  name: string
  /** [lng, lat] */
  center: [number, number]
  trees: number
  blocks: number
}

export type WorldBlock = {
  key: number
  rows: number
  /** The file is `blocks/{key}-{hash}.parquet`. */
  hash: string
  /** City index and its rows in this block, flattened: [index, rows, index, rows, ...]. */
  cities: number[]
}

export type WorldManifest = {
  /** Identifies the build: the generation time and the vocabulary. */
  id: string
  generatedAt: string
  trees: number
  /** The vocabulary file's name under the world's base URL. */
  vocab: string
  cities: WorldCity[]
  blocks: Map<number, WorldBlock>
}

/** One integer per block: the desktop's chunk key (cityPoints.ts `chunkKey`). */
export function blockKey(x: number, y: number): number {
  return x * BLOCKS_PER_AXIS + y
}

export function blockFromKey(key: number): { x: number; y: number } {
  return { x: Math.floor(key / BLOCKS_PER_AXIS), y: key % BLOCKS_PER_AXIS }
}

/** The block a longitude and latitude fall in. */
export function blockAt(lng: number, lat: number): { x: number; y: number } {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat))
  const rad = (clamped * Math.PI) / 180
  const clamp = (v: number) => Math.max(0, Math.min(BLOCKS_PER_AXIS - 1, v))
  return {
    x: clamp(Math.floor(((lng + 180) / 360) * BLOCKS_PER_AXIS)),
    y: clamp(Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * BLOCKS_PER_AXIS)),
  }
}

/** A block's centre as [lng, lat]. */
export function blockCenter(key: number): [number, number] {
  const { x, y } = blockFromKey(key)
  const lng = ((x + 0.5) / BLOCKS_PER_AXIS) * 360 - 180
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * (y + 0.5)) / BLOCKS_PER_AXIS))) * 180) / Math.PI
  return [lng, lat]
}

/** The block that holds a cell of `cellZoom` (a finer or equal zoom). */
export function blockOfCell(cellKey: number, cellZoom: number): number {
  const n = 2 ** cellZoom
  const shift = cellZoom - BLOCK_ZOOM
  return blockKey(Math.floor(cellKey / n) >> shift, (cellKey % n) >> shift)
}

/** Read a manifest as fetched. Throws on a shape this build of the map does not read. */
export function parseWorldManifest(json: unknown): WorldManifest {
  const m = json as {
    format?: number; generated_at?: string; block_zoom?: number; vocab?: string; trees?: number
    cities?: { code: string; name: string; center: [number, number]; trees: number; blocks: number }[]
    blocks?: [number, number, string, number[]][]
  }
  if (!m || m.format !== WORLD_MANIFEST_FORMAT) throw new Error(`World manifest format ${m?.format} is not ${WORLD_MANIFEST_FORMAT}`)
  if (m.block_zoom !== BLOCK_ZOOM) throw new Error(`World blocks are z${m.block_zoom}; this map reads z${BLOCK_ZOOM}`)
  if (!m.vocab || !Array.isArray(m.blocks) || !Array.isArray(m.cities)) throw new Error('World manifest is incomplete')
  const blocks = new Map<number, WorldBlock>()
  for (const [key, rows, hash, cities] of m.blocks) blocks.set(key, { key, rows, hash, cities })
  return {
    id: `${m.generated_at}|${m.vocab}`,
    generatedAt: String(m.generated_at),
    trees: Number(m.trees),
    vocab: m.vocab,
    cities: m.cities.map((c) => ({ code: c.code, name: c.name, center: c.center, trees: c.trees, blocks: c.blocks })),
    blocks,
  }
}

export function blockFile(block: WorldBlock): string {
  return `blocks/${block.key}-${block.hash}.parquet`
}

/**
 * The world as the main thread needs it: the bookmarks, and each block's
 * tree count and leading city, flattened as [key, rows, city index, ...].
 * Enough to draw where the trees are at any zoom and to say which bookmark
 * the camera is over, without a tree loaded.
 */
export type WorldDirectory = {
  id: string
  trees: number
  cities: WorldCity[]
  blocks: number[]
}

export function worldDirectory(manifest: WorldManifest): WorldDirectory {
  const blocks: number[] = []
  for (const block of manifest.blocks.values()) {
    let city = -1, most = -1
    for (let i = 0; i + 1 < block.cities.length; i += 2) if (block.cities[i + 1] > most) { city = block.cities[i]; most = block.cities[i + 1] }
    blocks.push(block.key, block.rows, city)
  }
  return { id: manifest.id, trees: manifest.trees, cities: manifest.cities, blocks }
}

/**
 * The bookmark a point is over, from a directory: the leading city of its
 * block, or of the fullest block within `reach` blocks (a park, a river or
 * a gap in an inventory is still that city). Null out in the open, where the
 * map keeps whichever bookmark it had.
 */
export function bookmarkAt(directory: WorldDirectory, index: Map<number, number>, lng: number, lat: number, reach = 2): string | null {
  const { x, y } = blockAt(lng, lat)
  for (let r = 0; r <= reach; r += 1) {
    let best = -1, bestRows = -1
    for (let dx = -r; dx <= r; dx += 1) {
      for (let dy = -r; dy <= r; dy += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue
        const at = index.get(blockKey(x + dx, y + dy))
        if (at !== undefined && directory.blocks[at + 1] > bestRows) { best = directory.blocks[at + 2]; bestRows = directory.blocks[at + 1] }
      }
    }
    if (best >= 0) return directory.cities[best]?.code ?? null
  }
  return null
}

/** Each block key's offset into a directory's flattened `blocks`. */
export function directoryIndex(directory: WorldDirectory): Map<number, number> {
  const index = new Map<number, number>()
  for (let i = 0; i < directory.blocks.length; i += 3) index.set(directory.blocks[i], i)
  return index
}

/** True when a tile holds any block with trees, so a tile outside every inventory is answered without a read. */
export function tileHasBlocks(manifest: WorldManifest, z: number, x: number, y: number): boolean {
  if (z >= BLOCK_ZOOM) {
    const shift = z - BLOCK_ZOOM
    return manifest.blocks.has(blockKey(x >> shift, y >> shift))
  }
  const span = 2 ** (BLOCK_ZOOM - z)
  // A coarse tile spans many blocks; the manifest is the shorter list past a few hundred.
  if (span * span > manifest.blocks.size) {
    for (const key of manifest.blocks.keys()) {
      const b = blockFromKey(key)
      if (Math.floor(b.x / span) === x && Math.floor(b.y / span) === y) return true
    }
    return false
  }
  for (let bx = x * span; bx < (x + 1) * span; bx += 1) {
    for (let by = y * span; by < (y + 1) * span; by += 1) if (manifest.blocks.has(blockKey(bx, by))) return true
  }
  return false
}
