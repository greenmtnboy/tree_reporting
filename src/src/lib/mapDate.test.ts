import { describe, expect, it } from 'vitest'
import { parseMapDate } from './mapDate'

describe('parseMapDate', () => {
  it('reads a YYYY-MM-DD as a local date', () => {
    const date = parseMapDate('2027-01-15')!
    expect([date.getFullYear(), date.getMonth(), date.getDate()]).toEqual([2027, 0, 15])
  })
  it('refuses anything else, and impossible days', () => {
    for (const value of [null, '', 'winter', '2027-1-15', '2027-02-30', '15/01/2027']) expect(parseMapDate(value)).toBeNull()
  })
})
