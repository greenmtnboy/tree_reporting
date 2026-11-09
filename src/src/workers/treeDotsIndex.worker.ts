// The resident tree layer's indexing, off the main thread: each chunk's heat
// pyramid and its per-cell colours (lib/treeDots.ts) cost tens to hundreds of
// milliseconds a chunk, a dropped frame or several on the page's thread.
// Keeps each chunk's pyramid, so a recolour sends only the colours.
import { DRAW_CHUNK_MAX_CELLS, DRAW_CHUNK_MIN_M, buildDotLevels, buildPickGrid, levelColors, type DotLevel, type PickGrid } from '../lib/treeDots'
import { CHUNK_LOD_CELLS } from './cityPoints'

export type DotsIndexRequest =
  | { kind: 'build'; id: number; key: number; x: Float32Array; y: Float32Array; count: number; color: Uint8Array; palette: string[] }
  | { kind: 'recolor'; id: number; key: number; color: Uint8Array; palette: string[] }
  | { kind: 'drop'; key: number }
  | { kind: 'clear' }

export interface DotsIndexResult {
  id: number
  key: number
  /** Present on a build: each level's (x, y, weight) cells, back to back. */
  positions?: Float32Array
  counts?: number[]
  cells?: number[]
  /** Each level's draw grid: its cells are stored in grid order, so a view draws a few ranges. */
  grids?: Pick<PickGrid, 'cell' | 'cols' | 'rows' | 'start'>[]
  /** Each level's RGBA cells, back to back, in the same order. */
  colors: Uint8Array
}

interface ChunkIndex { levels: DotLevel[]; orders: Uint32Array[]; count: number }
const chunks = new Map<number, ChunkIndex>()

function packColors(index: ChunkIndex, color: Uint8Array, palette: string[]): Uint8Array {
  const parts = levelColors(index.levels, color, palette, index.count)
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let base = 0
  parts.forEach((part, li) => {
    const order = index.orders[li]
    for (let k = 0; k < order.length; k++) {
      const from = order[k] * 4, to = (base + k) * 4
      out[to] = part[from]; out[to + 1] = part[from + 1]; out[to + 2] = part[from + 2]; out[to + 3] = part[from + 3]
    }
    base += order.length
  })
  return out
}

self.onmessage = (event: MessageEvent<DotsIndexRequest>) => {
  const msg = event.data
  const ctx = self as unknown as { postMessage: (m: unknown, t: Transferable[]) => void }
  if (msg.kind === 'clear') { chunks.clear(); return }
  if (msg.kind === 'drop') { chunks.delete(msg.key); return }
  if (msg.kind === 'build') {
    const levels = buildDotLevels(msg.x, msg.y, msg.count, CHUNK_LOD_CELLS)
    const grids = levels.map((l) => buildPickGrid(l.x, l.y, l.count, DRAW_CHUNK_MAX_CELLS, Math.max(DRAW_CHUNK_MIN_M, l.cell * 16)))
    const index: ChunkIndex = { levels, orders: grids.map((g) => g.order), count: msg.count }
    chunks.set(msg.key, index)
    const total = levels.reduce((n, l) => n + l.count, 0)
    const positions = new Float32Array(total * 3)
    let base = 0
    levels.forEach((level, li) => {
      const order = index.orders[li]
      for (let k = 0; k < order.length; k++) {
        const c = order[k]
        positions[(base + k) * 3] = level.x[c]
        positions[(base + k) * 3 + 1] = level.y[c]
        positions[(base + k) * 3 + 2] = level.weight[c]
      }
      base += level.count
    })
    const colors = packColors(index, msg.color, msg.palette)
    const result: DotsIndexResult = {
      id: msg.id, key: msg.key, positions, colors,
      counts: levels.map((l) => l.count), cells: levels.map((l) => l.cell),
      grids: grids.map(({ cell, cols, rows, start }) => ({ cell, cols, rows, start })),
    }
    ctx.postMessage(result, [positions.buffer, colors.buffer])
    return
  }
  const index = chunks.get(msg.key)
  if (!index) return
  const colors = packColors(index, msg.color, msg.palette)
  ctx.postMessage({ id: msg.id, key: msg.key, colors } satisfies DotsIndexResult, [colors.buffer])
}
