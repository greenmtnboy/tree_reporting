// The resident tree layer's indexing, off the main thread: the heat pyramid
// and its per-cell colours (lib/treeDots.ts) take most of a second on a
// 1.7M-tree city, which on the page's thread is a dropped second of frames.
// Keeps the last pyramid, so a recolour sends only the colours.
import { DRAW_CHUNK_MAX_CELLS, DRAW_CHUNK_MIN_M, buildDotLevels, buildPickGrid, levelColors, type DotLevel, type PickGrid } from '../lib/treeDots'

export type DotsIndexRequest =
  | { kind: 'build'; id: number; x: Float32Array; y: Float32Array; count: number; color: Uint8Array; palette: string[] }
  | { kind: 'recolor'; id: number; color: Uint8Array; palette: string[] }

export interface DotsIndexResult {
  id: number
  /** Present on a build: each level's (x, y, weight) cells, back to back. */
  positions?: Float32Array
  counts?: number[]
  cells?: number[]
  /** Each level's draw chunks: its cells are stored in chunk order, so a view draws a few ranges. */
  chunks?: Pick<PickGrid, 'cell' | 'cols' | 'rows' | 'start'>[]
  /** Each level's RGBA cells, back to back, in the same order. */
  colors: Uint8Array
}

let levels: DotLevel[] = []
/** Each level's cells in draw order. */
let orders: Uint32Array[] = []
let pointCount = 0

function packColors(color: Uint8Array, palette: string[]): Uint8Array {
  const parts = levelColors(levels, color, palette, pointCount)
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let base = 0
  parts.forEach((part, li) => {
    const order = orders[li]
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
  if (msg.kind === 'build') {
    levels = buildDotLevels(msg.x, msg.y, msg.count)
    pointCount = msg.count
    const grids = levels.map((l) => buildPickGrid(l.x, l.y, l.count, DRAW_CHUNK_MAX_CELLS, Math.max(DRAW_CHUNK_MIN_M, l.cell * 16)))
    orders = grids.map((g) => g.order)
    const total = levels.reduce((n, l) => n + l.count, 0)
    const positions = new Float32Array(total * 3)
    let base = 0
    levels.forEach((level, li) => {
      const order = orders[li]
      for (let k = 0; k < order.length; k++) {
        const c = order[k]
        positions[(base + k) * 3] = level.x[c]
        positions[(base + k) * 3 + 1] = level.y[c]
        positions[(base + k) * 3 + 2] = level.weight[c]
      }
      base += level.count
    })
    const colors = packColors(msg.color, msg.palette)
    const chunks = grids.map(({ cell, cols, rows, start }) => ({ cell, cols, rows, start }))
    const result: DotsIndexResult = { id: msg.id, positions, counts: levels.map((l) => l.count), cells: levels.map((l) => l.cell), chunks, colors }
    ctx.postMessage(result, [positions.buffer, colors.buffer])
  } else {
    const colors = packColors(msg.color, msg.palette)
    ctx.postMessage({ id: msg.id, colors } satisfies DotsIndexResult, [colors.buffer])
  }
}
