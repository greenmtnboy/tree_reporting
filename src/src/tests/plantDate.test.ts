import { describe, expect, it } from 'vitest'
import { formatPlantDate, formatTreeAge } from '../lib/plantDate'

describe('formatPlantDate', () => {
  it('treats a numeric plant_date as epoch milliseconds, even before 2001', () => {
    // lax-54642: the parquet holds DATE '1991-05-07' (7796 days); Arrow hands
    // the worker 7796 * 86400000 ms. This once rendered as "+023314-09".
    expect(formatPlantDate(7796 * 86_400_000)).toBe('1991-05-07')
    expect(formatPlantDate(Date.UTC(2015, 2, 9))).toBe('2015-03-09')
    // Pre-1970 plantings are negative epoch values.
    expect(formatPlantDate(Date.UTC(1932, 0, 1))).toBe('1932-01-01')
    expect(formatPlantDate(String(Date.UTC(1991, 4, 7)))).toBe('1991-05-07')
  })

  it('passes ISO date strings through and strips a time component', () => {
    expect(formatPlantDate('1991-05-07')).toBe('1991-05-07')
    expect(formatPlantDate('1991-05-07T00:00:00.000Z')).toBe('1991-05-07')
    expect(formatPlantDate('1991-05-07 00:00:00')).toBe('1991-05-07')
  })

  it('returns null for missing or unparseable values', () => {
    expect(formatPlantDate(null)).toBeNull()
    expect(formatPlantDate(undefined)).toBeNull()
    expect(formatPlantDate('')).toBeNull()
    expect(formatPlantDate(Number.NaN)).toBeNull()
  })
})

describe('formatTreeAge', () => {
  const now = new Date(2026, 8, 14)

  it('counts whole years since planting from an epoch-ms date', () => {
    expect(formatTreeAge(7796 * 86_400_000, now)).toBe('35 years')
    expect(formatTreeAge('2025-12-01', now)).toBe('< 1 year')
    expect(formatTreeAge('2025-09-14', now)).toBe('1 year')
    expect(formatTreeAge('2025-09-15', now)).toBe('< 1 year')
  })

  it('returns null when there is no plant date', () => {
    expect(formatTreeAge(null, now)).toBeNull()
  })
})
