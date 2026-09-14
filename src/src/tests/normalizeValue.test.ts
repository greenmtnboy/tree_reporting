import { describe, expect, it } from 'vitest'
import { normalizeValue } from '../workers/normalizeValue'

/**
 * Stand-in for an Arrow `Vector`: the shape a LIST cell arrives in from
 * DuckDB-wasm. Structured clone would copy every field below verbatim, which
 * is what the chat model used to be sent for `bloom_months`.
 */
function fakeVector<T>(values: T[]) {
  return {
    _offsets: [0, values.length],
    data: [{ type: { typeId: 2, isSigned: true, bitWidth: 32 }, values, nullBitmap: [] }],
    type: { typeId: 2 },
    length: values.length,
    get(i: number) {
      return values[i]
    },
    toJSON() {
      return [...values]
    },
  }
}

describe('normalizeValue', () => {
  it('unwraps an Arrow list cell to a plain array', () => {
    expect(normalizeValue(fakeVector([3, 4, 5]))).toEqual([3, 4, 5])
    expect(JSON.stringify(normalizeValue({ bloom_months: fakeVector([1, 2, 12]), tree_count: 7n }))).toBe(
      '{"bloom_months":[1,2,12],"tree_count":7}',
    )
  })

  it('recurses into nested vectors and struct rows', () => {
    const structRow = {
      toJSON() {
        return { months: fakeVector([9, 10]), label: 'autumn' }
      },
    }
    expect(normalizeValue(fakeVector([structRow, null]))).toEqual([{ months: [9, 10], label: 'autumn' }, null])
  })

  it('keeps the scalar conversions', () => {
    expect(normalizeValue(42n)).toBe(42)
    expect(normalizeValue(2n ** 60n)).toBe((2n ** 60n).toString())
    expect(normalizeValue(new Date(Date.UTC(1991, 4, 7)))).toBe('1991-05-07T00:00:00.000Z')
    expect(normalizeValue(new Uint8Array([1, 2]))).toEqual([1, 2])
    expect(normalizeValue(new Float64Array([1.5]))).toEqual([1.5])
    expect(normalizeValue(null)).toBeNull()
    expect(normalizeValue('x')).toBe('x')
    expect(normalizeValue(() => 1)).toBeUndefined()
  })
})
