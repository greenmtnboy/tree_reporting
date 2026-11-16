/**
 * A parquet file's row groups, read from its footer without the engine: where
 * each one's bytes are, and the range of latitude and longitude it holds.
 *
 * A phone's first view reads two of a block's sixteen cells, and the block's
 * file (4 MB for central Boston) was fetched whole for them: DuckDB's own
 * range reads of it were two dozen requests in a row. With the footer read
 * here, the worker can fetch just the row groups a view's cells fall in, side
 * by side, before the engine that will read them has finished starting
 * (sparseBlocks.ts).
 *
 * The footer is Thrift's compact protocol. This reads it generically, every
 * struct as a map of field number to value, and picks out the few fields of
 * `FileMetaData` the row groups need; the numbers are parquet.thrift's.
 */

/** One row group: its bytes in the file, its rows, and its numeric columns' ranges. */
export type RowGroupSpan = {
  /** First byte of the row group's column chunks, and one past the last. */
  start: number
  end: number
  rows: number
  /** Minimum and maximum of each DOUBLE or FLOAT column that carries statistics. */
  ranges: Map<string, { min: number; max: number }>
}

type ThriftValue = number | boolean | Uint8Array | ThriftValue[] | ThriftStruct
type ThriftStruct = Map<number, ThriftValue>

const BOOL_TRUE = 1, BOOL_FALSE = 2, BYTE = 3, I16 = 4, I32 = 5, I64 = 6, DOUBLE = 7, BINARY = 8, LIST = 9, SET = 10, MAP = 11, STRUCT = 12

class Reader {
  pos = 0
  constructor(private readonly buf: Uint8Array) {}

  byte(): number {
    if (this.pos >= this.buf.length) throw new Error('parquet footer: truncated')
    return this.buf[this.pos++]
  }

  varint(): number {
    let value = 0
    let scale = 1
    for (;;) {
      const b = this.byte()
      value += (b & 0x7f) * scale
      if (b < 0x80) return value
      scale *= 128
    }
  }

  /** A signed integer below 2^52: every offset and count in a file this size. */
  zigzag(): number {
    const n = this.varint()
    return n % 2 === 0 ? n / 2 : -(n + 1) / 2
  }

  binary(): Uint8Array {
    const length = this.varint()
    if (this.pos + length > this.buf.length) throw new Error('parquet footer: truncated')
    const out = this.buf.subarray(this.pos, this.pos + length)
    this.pos += length
    return out
  }

  value(type: number): ThriftValue {
    switch (type) {
      case BOOL_TRUE: return true
      case BOOL_FALSE: return false
      case BYTE: return this.byte()
      case I16: case I32: case I64: return this.zigzag()
      case DOUBLE: {
        const out = new DataView(this.buf.buffer, this.buf.byteOffset + this.pos, 8).getFloat64(0, true)
        this.pos += 8
        return out
      }
      case BINARY: return this.binary()
      case LIST: case SET: {
        const header = this.byte()
        const size = header >> 4 === 15 ? this.varint() : header >> 4
        const elementType = header & 0x0f
        const out: ThriftValue[] = []
        // A list of booleans holds a byte each, where a boolean field is its type alone.
        for (let i = 0; i < size; i += 1) out.push(elementType === BOOL_TRUE || elementType === BOOL_FALSE ? this.byte() === 1 : this.value(elementType))
        return out
      }
      case MAP: {
        const size = this.varint()
        const out: ThriftValue[] = []
        if (size === 0) return out
        const types = this.byte()
        for (let i = 0; i < size; i += 1) out.push([this.value(types >> 4), this.value(types & 0x0f)])
        return out
      }
      case STRUCT: return this.struct()
      default: throw new Error(`parquet footer: unknown thrift type ${type}`)
    }
  }

  struct(): ThriftStruct {
    const out: ThriftStruct = new Map()
    let field = 0
    for (;;) {
      const header = this.byte()
      if (header === 0) return out
      const delta = header >> 4
      field = delta === 0 ? this.zigzag() : field + delta
      out.set(field, this.value(header & 0x0f))
    }
  }
}

const utf8 = new TextDecoder()
const PARQUET_FLOAT = 4, PARQUET_DOUBLE = 5

/** How many bytes the footer and its trailer take, read from the file's last eight bytes. */
export function footerLength(tail: Uint8Array): number {
  const n = tail.length
  if (n < 8 || utf8.decode(tail.subarray(n - 4)) !== 'PAR1') throw new Error('parquet footer: not a parquet file')
  return new DataView(tail.buffer, tail.byteOffset + n - 8, 4).getUint32(0, true) + 8
}

/**
 * The row groups of the file whose last bytes are `tail`, which must hold the
 * whole footer (`footerLength`). Throws on anything it does not understand:
 * the caller falls back to reading the file whole.
 */
export function parquetRowGroups(tail: Uint8Array): RowGroupSpan[] {
  const length = footerLength(tail)
  if (length > tail.length) throw new Error('parquet footer: not all of it was read')
  const meta = new Reader(tail.subarray(tail.length - length, tail.length - 8)).struct()
  const groups = meta.get(4)
  if (!Array.isArray(groups)) throw new Error('parquet footer: no row groups')
  return groups.map((group) => {
    const columns = (group as ThriftStruct).get(1)
    const rows = (group as ThriftStruct).get(3)
    if (!Array.isArray(columns) || typeof rows !== 'number') throw new Error('parquet footer: malformed row group')
    let start = Infinity
    let end = 0
    const ranges: RowGroupSpan['ranges'] = new Map()
    for (const chunk of columns) {
      const column = (chunk as ThriftStruct).get(3) as ThriftStruct | undefined
      const dataPage = column?.get(9), dictionaryPage = column?.get(11), size = column?.get(7)
      if (!column || typeof dataPage !== 'number' || typeof size !== 'number') throw new Error('parquet footer: malformed column chunk')
      // A chunk starts at its dictionary page where it has one, as the engine reads it.
      const first = typeof dictionaryPage === 'number' && dictionaryPage > 0 ? Math.min(dictionaryPage, dataPage) : dataPage
      start = Math.min(start, first)
      end = Math.max(end, first + size)
      const type = column.get(1)
      const path = column.get(3)
      const stats = column.get(12) as ThriftStruct | undefined
      const max = stats?.get(5), min = stats?.get(6)
      if (!Array.isArray(path) || !(max instanceof Uint8Array) || !(min instanceof Uint8Array)) continue
      const number = (bytes: Uint8Array) => type === PARQUET_DOUBLE && bytes.length === 8
        ? new DataView(bytes.buffer, bytes.byteOffset, 8).getFloat64(0, true)
        : type === PARQUET_FLOAT && bytes.length === 4 ? new DataView(bytes.buffer, bytes.byteOffset, 4).getFloat32(0, true) : NaN
      const range = { min: number(min), max: number(max) }
      if (Number.isFinite(range.min) && Number.isFinite(range.max)) ranges.set(path.map((part) => utf8.decode(part as Uint8Array)).join('.'), range)
    }
    if (!(end > start)) throw new Error('parquet footer: empty row group')
    return { start, end, rows, ranges }
  })
}

/**
 * The row groups a read of a longitude and latitude box touches: every one
 * whose ranges reach the box, and every one that cannot say. This is the rule
 * the engine prunes row groups by for the same predicate, so what is left out
 * here is never read there.
 */
export function rowGroupsInBox(groups: RowGroupSpan[], box: { west: number; east: number; south: number; north: number }): number[] {
  const hits: number[] = []
  for (const [index, group] of groups.entries()) {
    const lat = group.ranges.get('latitude'), lng = group.ranges.get('longitude')
    if (!lat || !lng || (lat.max >= box.south && lat.min <= box.north && lng.max >= box.west && lng.min <= box.east)) hits.push(index)
  }
  return hits
}
