import { describe, expect, it } from 'vitest'
import { safeJsonStringify, toJsonSafeRows } from '../composables/chatToolExecution'

/** An Arrow Vector as the studio library's DuckDB connection hands it over. */
function fakeVector<T>(values: T[]) {
  return {
    _offsets: [0, values.length],
    data: [{ values, nullBitmap: [] }],
    length: values.length,
    toJSON() {
      return [...values]
    },
  }
}

describe('toJsonSafeRows', () => {
  it('unwraps Arrow list cells instead of copying their internals', () => {
    const rows = toJsonSafeRows([
      { species: 'Quercus agrifolia', bloom_months: fakeVector([3, 4, 5]), n: 30246n },
      { species: 'Unknown', bloom_months: null, n: 27428n },
    ])
    expect(rows).toEqual([
      { species: 'Quercus agrifolia', bloom_months: [3, 4, 5], n: 30246 },
      { species: 'Unknown', bloom_months: null, n: 27428 },
    ])
    expect(safeJsonStringify({ rows })).not.toContain('_offsets')
  })

  it('still converts dates, bigints and typed arrays', () => {
    expect(safeJsonStringify({ d: new Date(Date.UTC(2026, 8, 14)), big: 2n ** 60n, f: new Float32Array([1]) })).toBe(
      '{"d":"2026-09-14T00:00:00.000Z","big":"1152921504606846976","f":[1]}',
    )
  })
})
