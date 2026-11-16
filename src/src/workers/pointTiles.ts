/**
 * Vector tiles of points, encoded here from the rows a tile query returns.
 *
 * The worker used DuckDB's spatial extension for this (`ST_AsMVTGeom` and
 * `ST_AsMVT`) and for nothing else. That was 23 MB of wasm, 6 MB on the wire,
 * fetched and compiled after the engine and before the first tree: about six
 * seconds of a phone's first load on a 9 Mbps link. A tree tile is points
 * with a handful of properties, which is a few dozen lines of protobuf.
 *
 * One source layer per colour. The map draws one heatmap and one circle layer
 * per colour (useMapLayers.ts); with every feature in a single source layer
 * and a colour filter on each, MapLibre's worker evaluates every layer's
 * filter against every feature of the tile. With a source layer per colour
 * each style layer reads its own features only, and the colour is the layer's
 * name rather than a property on each feature.
 */

/** The tile's side in tile units, and how far past it a point is still written. */
export const TILE_EXTENT = 4096
export const TILE_BUFFER = 64

const EARTH_HALF_CIRCUMFERENCE = 20037508.342789244

/** One column of a query result: an Arrow vector, or anything shaped like one. */
export type TileColumn = {
  get(index: number): unknown
  /** Null values in the column, where it knows; with none, `toArray` is the whole column. */
  nullCount?: number
  toArray?(): ArrayLike<unknown>
}

/** A query result read by column: an Arrow table, or anything shaped like one. */
export type TileRowColumns = {
  numRows: number
  getChild(name: string): TileColumn | null
}

/**
 * A column as a function of the row. An Arrow vector's `get` finds the row's
 * batch and dispatches on its type every call, 5 µs a value here: a third of
 * a second for the eight columns of a street view's 8,000 trees. A column
 * with no nulls is read once as an array instead.
 */
function reader(column: TileColumn): (index: number) => unknown {
  if (column.nullCount === 0 && column.toArray) {
    const values = column.toArray()
    return (index) => values[index]
  }
  return (index) => column.get(index)
}

/**
 * A property written on each feature. `column` names where it is read from
 * (the property's own name unless given); `value` is a constant instead.
 * A null is left out of the feature, and a `flag` is written only when true.
 */
export type PointProperty = {
  name: string
  kind: 'string' | 'double' | 'int' | 'flag'
  column?: string
  value?: number
}

export type EncodedTile = { xtile: number; ytile: number; mvt: Uint8Array }

/** A growable byte buffer with the protobuf primitives a tile needs. */
class Bytes {
  buf = new Uint8Array(256)
  length = 0

  private room(extra: number): void {
    if (this.length + extra <= this.buf.length) return
    const grown = new Uint8Array(Math.max(this.buf.length * 2, this.length + extra))
    grown.set(this.buf.subarray(0, this.length))
    this.buf = grown
  }

  byte(value: number): void {
    this.room(1)
    this.buf[this.length++] = value
  }

  /** An unsigned integer below 2^53. */
  varint(value: number): void {
    this.room(8)
    while (value >= 0x80) {
      this.buf[this.length++] = (value % 0x80) | 0x80
      value = Math.floor(value / 0x80)
    }
    this.buf[this.length++] = value
  }

  bytes(source: Uint8Array, length = source.length): void {
    this.room(length)
    this.buf.set(length === source.length ? source : source.subarray(0, length), this.length)
    this.length += length
  }

  double(value: number): void {
    this.room(8)
    new DataView(this.buf.buffer, this.buf.byteOffset).setFloat64(this.length, value, true)
    this.length += 8
  }

  /** A length-delimited field holding `body`. */
  field(tag: number, body: Bytes | Uint8Array): void {
    const length = body.length
    this.byte((tag << 3) | 2)
    this.varint(length)
    this.bytes(body instanceof Bytes ? body.buf : body, length)
  }

  reset(): void {
    this.length = 0
  }

  done(): Uint8Array {
    return this.buf.slice(0, this.length)
  }
}

const utf8 = new TextEncoder()
const zigzag = (value: number) => (value < 0 ? -2 * value - 1 : 2 * value)

/** One colour's layer of one tile, as it is built: its features and its value table. */
class Layer {
  readonly features = new Bytes()
  readonly values = new Bytes()
  private readonly numbers = new Map<string, number>()
  private valueCount = 0
  private readonly scratch = new Bytes()

  private add(write: (value: Bytes) => void): number {
    this.scratch.reset()
    write(this.scratch)
    this.values.field(4, this.scratch)
    return this.valueCount++
  }

  string(text: string): number {
    // A tree's id: one value per feature, so nothing to look up.
    return this.add((v) => v.field(1, utf8.encode(text)))
  }

  /** A double, an integer or a flag, written once per distinct value. */
  number(kind: 'double' | 'int' | 'flag', value: number): number {
    const key = `${kind}${value}`
    let index = this.numbers.get(key)
    if (index === undefined) {
      index = this.add((v) => {
        if (kind === 'double') { v.byte((3 << 3) | 1); v.double(value) }
        else if (kind === 'flag') { v.byte(7 << 3); v.byte(1) }
        else if (value < 0) { v.byte(6 << 3); v.varint(zigzag(value)) }
        else { v.byte(4 << 3); v.varint(value) }
      })
      this.numbers.set(key, index)
    }
    return index
  }
}

/**
 * Rows of points as tiles at zoom `z`, one per (xtile, ytile), each holding a
 * source layer per value of `layer`. Reads `xtile`, `ytile`, `layer` and the
 * point's Web Mercator `x` and `y` in metres, then `properties`. A point
 * farther than the buffer outside its row's tile is dropped, which only an
 * aggregate cell's centre can be.
 */
export function encodePointTiles(rows: TileRowColumns, z: number, properties: PointProperty[], fallbackLayer: string): Map<string, EncodedTile> {
  const column = (name: string) => {
    const found = rows.getChild(name)
    if (!found) throw new Error(`tile rows have no ${name} column`)
    return reader(found)
  }
  const xtiles = column('xtile'), ytiles = column('ytile'), layers = column('layer'), xs = column('x'), ys = column('y')
  const readers = properties.map((property) => ({ ...property, from: property.value === undefined ? column(property.column ?? property.name) : null }))

  const size = (2 * EARTH_HALF_CIRCUMFERENCE) / 2 ** z
  const scale = TILE_EXTENT / size
  const tiles = new Map<string, { xtile: number; ytile: number; layers: Map<string, Layer> }>()
  const feature = new Bytes()
  const tags = new Bytes()
  const geometry = new Bytes()

  for (let i = 0; i < rows.numRows; i += 1) {
    const xtile = Number(xtiles(i))
    const ytile = Number(ytiles(i))
    const px = Math.round((Number(xs(i)) + EARTH_HALF_CIRCUMFERENCE - xtile * size) * scale)
    const py = Math.round((EARTH_HALF_CIRCUMFERENCE - ytile * size - Number(ys(i))) * scale)
    if (!(px >= -TILE_BUFFER && px <= TILE_EXTENT + TILE_BUFFER && py >= -TILE_BUFFER && py <= TILE_EXTENT + TILE_BUFFER)) continue

    const key = `${xtile}/${ytile}`
    let tile = tiles.get(key)
    if (!tile) tiles.set(key, tile = { xtile, ytile, layers: new Map() })
    const name = layers(i)
    const layerName = name == null ? fallbackLayer : String(name)
    let layer = tile.layers.get(layerName)
    if (!layer) tile.layers.set(layerName, layer = new Layer())

    tags.reset()
    for (const [index, property] of readers.entries()) {
      const raw = property.from ? property.from(i) : property.value
      if (raw == null) continue
      if (property.kind === 'string') {
        tags.varint(index)
        tags.varint(layer.string(String(raw)))
        continue
      }
      if (property.kind === 'flag') {
        if (!raw) continue
        tags.varint(index)
        tags.varint(layer.number('flag', 1))
        continue
      }
      const value = Number(raw)
      if (!Number.isFinite(value)) continue
      tags.varint(index)
      tags.varint(layer.number(property.kind === 'int' ? 'int' : 'double', property.kind === 'int' ? Math.round(value) : value))
    }
    geometry.reset()
    geometry.varint(9) // MoveTo, one point
    geometry.varint(zigzag(px))
    geometry.varint(zigzag(py))
    feature.reset()
    feature.field(2, tags)
    feature.byte(3 << 3)
    feature.byte(1) // POINT
    feature.field(4, geometry)
    layer.features.field(2, feature)
  }

  const keys = new Bytes()
  for (const property of properties) keys.field(3, utf8.encode(property.name))
  const out = new Map<string, EncodedTile>()
  const body = new Bytes()
  for (const [key, tile] of tiles) {
    const mvt = new Bytes()
    for (const [name, layer] of tile.layers) {
      body.reset()
      body.byte(15 << 3)
      body.byte(2) // version
      body.field(1, utf8.encode(name))
      body.bytes(layer.features.buf, layer.features.length)
      body.bytes(keys.buf, keys.length)
      body.bytes(layer.values.buf, layer.values.length)
      body.byte(5 << 3)
      body.varint(TILE_EXTENT)
      mvt.field(3, body)
    }
    out.set(key, { xtile: tile.xtile, ytile: tile.ytile, mvt: mvt.done() })
  }
  return out
}
