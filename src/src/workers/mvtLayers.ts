/**
 * One MVT source layer per colour.
 *
 * The map draws one heatmap layer per colour (useMapLayers.ts). With every
 * feature in a single `trees` source layer and a `display_color` filter on
 * each, MapLibre's worker evaluates every layer's filter against every
 * feature of the tile: 18 colours is 18 passes over each overview tile. With
 * one source layer per colour, each style layer reads its own features only,
 * and the colour is the layer's name rather than a property on each feature.
 *
 * `ST_AsMVT` takes the layer name as a constant, so the worker groups by
 * colour, encodes each group as `trees`, and renames the layer here before
 * concatenating a tile's layers: a protobuf message's repeated field is the
 * concatenation of its encodings, so `layers` from several tiles join into one.
 */

const TILE_LAYERS_TAG = (3 << 3) | 2
const LAYER_NAME_TAG = (1 << 3) | 2

function readVarint(buf: Uint8Array, pos: number): [value: number, next: number] {
  let value = 0
  let shift = 0
  for (;;) {
    if (pos >= buf.length) throw new Error('mvt: truncated varint')
    const b = buf[pos++]
    value += (b & 0x7f) * 2 ** shift
    if (b < 0x80) return [value, pos]
    shift += 7
  }
}

function varintBytes(value: number): number[] {
  const out: number[] = []
  while (value >= 0x80) {
    out.push((value % 0x80) | 0x80)
    value = Math.floor(value / 0x80)
  }
  out.push(value)
  return out
}

/** The end of the field whose value starts at `pos`, by wire type. */
function skipField(buf: Uint8Array, wireType: number, pos: number): number {
  switch (wireType) {
    case 0: return readVarint(buf, pos)[1]
    case 1: return pos + 8
    case 2: { const [len, next] = readVarint(buf, pos); return next + len }
    case 5: return pos + 4
    default: throw new Error(`mvt: unsupported wire type ${wireType}`)
  }
}

const encoder = new TextEncoder()

/** Every layer of an encoded tile, renamed to `name`. */
export function renameMvtLayers(tile: Uint8Array, name: string): Uint8Array {
  const nameBytes = encoder.encode(name)
  const nameField = [LAYER_NAME_TAG, ...varintBytes(nameBytes.length)]
  const parts: Uint8Array[] = []
  let pos = 0
  while (pos < tile.length) {
    const fieldStart = pos
    const [tag, afterTag] = readVarint(tile, pos)
    pos = skipField(tile, tag & 7, afterTag)
    if (tag !== TILE_LAYERS_TAG) {
      parts.push(tile.subarray(fieldStart, pos))
      continue
    }
    const [len, layerStart] = readVarint(tile, afterTag)
    const layer = tile.subarray(layerStart, layerStart + len)
    // The layer without its name field; the new name goes first (field order is free).
    const kept: Uint8Array[] = []
    let keptLength = 0
    let p = 0
    while (p < layer.length) {
      const start = p
      const [fieldTag, after] = readVarint(layer, p)
      p = skipField(layer, fieldTag & 7, after)
      if (fieldTag === LAYER_NAME_TAG) continue
      kept.push(layer.subarray(start, p))
      keptLength += p - start
    }
    const bodyLength = nameField.length + nameBytes.length + keptLength
    parts.push(new Uint8Array([TILE_LAYERS_TAG, ...varintBytes(bodyLength), ...nameField]), nameBytes, ...kept)
  }
  return concatBytes(parts)
}

export function concatBytes(parts: readonly Uint8Array[]): Uint8Array {
  let length = 0
  for (const part of parts) length += part.length
  const out = new Uint8Array(length)
  let offset = 0
  for (const part of parts) {
    out.set(part, offset)
    offset += part.length
  }
  return out
}

/**
 * Rows of (xtile, ytile, layer, mvt), one per tile and colour, as one tile
 * per (xtile, ytile) holding a source layer per colour. A colour with no
 * features (an empty or null `mvt`) adds nothing.
 */
export function assembleLayeredTiles(rows: Iterable<Record<string, unknown>>, fallbackLayer: string): Map<string, { xtile: number; ytile: number; mvt: Uint8Array }> {
  const parts = new Map<string, { xtile: number; ytile: number; parts: Uint8Array[] }>()
  for (const row of rows) {
    const xtile = Number(row.xtile)
    const ytile = Number(row.ytile)
    const key = `${xtile}/${ytile}`
    let entry = parts.get(key)
    if (!entry) {
      entry = { xtile, ytile, parts: [] }
      parts.set(key, entry)
    }
    const raw = row.mvt as Uint8Array | null | undefined
    if (!raw || raw.length === 0) continue
    const layer = row.layer == null ? fallbackLayer : String(row.layer)
    entry.parts.push(renameMvtLayers(raw, layer))
  }
  const tiles = new Map<string, { xtile: number; ytile: number; mvt: Uint8Array }>()
  for (const [key, entry] of parts) tiles.set(key, { xtile: entry.xtile, ytile: entry.ytile, mvt: concatBytes(entry.parts) })
  return tiles
}
