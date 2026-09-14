/**
 * Turn a DuckDB-wasm result cell into a plain JSON value that survives
 * postMessage and JSON.stringify unchanged.
 *
 * Arrow hands back LIST and STRUCT cells as `Vector` / `StructRow` objects.
 * Structured clone copies their internals -- `_offsets`, `data`, `type`,
 * `nullBitmap` -- so a `bloom_months` of [3, 4, 5] reached the chat model as
 * a 400-character object graph, and the tree card's `Array.isArray` guards
 * hid the row. Anything Arrow gives a `toJSON` is unwrapped through it first.
 */
export function normalizeValue(v: unknown): unknown {
  if (typeof v === 'function') {
    return undefined
  }
  if (typeof v === 'bigint') {
    const n = Number(v)
    return Number.isSafeInteger(n) ? n : v.toString()
  }
  if (v instanceof Date) {
    return v.toISOString()
  }
  if (
    v !== null &&
    v !== undefined &&
    (v as { isLuxonDateTime?: boolean }).isLuxonDateTime === true &&
    typeof (v as { toISO?: () => string | null }).toISO === 'function'
  ) {
    return (v as { toISO: () => string | null }).toISO() ?? null
  }
  if (ArrayBuffer.isView(v)) {
    return Array.from(v as unknown as ArrayLike<number | bigint>, (item) => normalizeValue(item))
  }
  if (Array.isArray(v)) {
    return v.map((item) => normalizeValue(item))
  }
  if (v && typeof v === 'object') {
    // Arrow Vector (a LIST cell), StructRow, MapRow: their toJSON yields plain
    // arrays and objects whose elements may be Vectors again, so recurse.
    if (typeof (v as { toJSON?: unknown }).toJSON === 'function') {
      return normalizeValue((v as { toJSON: () => unknown }).toJSON())
    }
    return Object.fromEntries(
      Object.entries(v as Record<string, unknown>).map(([key, value]) => [key, normalizeValue(value)]),
    )
  }
  return v
}
