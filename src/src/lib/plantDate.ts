/**
 * Formatting for a tree's `plant_date` as it comes back from DuckDB-WASM.
 *
 * The parquet column is a DATE. Arrow surfaces that to JS as a number of
 * epoch *milliseconds* (never seconds, never days), and a DuckDB CAST or a
 * fixture may hand us an ISO string instead. A tree planted in 1991 is about
 * 6.7e11 ms -- below the 1e12 "must be seconds" cutoff an earlier version of
 * this guessed at, which is how every pre-2001 planting date rendered as the
 * year 23314 (see plantDate.test.ts).
 */

const normalizeDate = (date: Date): string | null => {
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString().slice(0, 10)
}

export function formatPlantDate(value: string | number | null | undefined): string | null {
  if (value == null || value === '') return null

  if (typeof value === 'number') {
    return Number.isFinite(value) ? normalizeDate(new Date(value)) : null
  }

  const normalized = String(value).trim()
  if (!normalized) return null

  // A stringified epoch-ms value, e.g. from a VARCHAR cast in a fixture.
  if (/^-?\d+$/.test(normalized)) {
    const numeric = Number(normalized)
    return Number.isFinite(numeric) ? normalizeDate(new Date(numeric)) : null
  }

  const simpleDate = normalized.split('T')[0]?.split(' ')[0]
  if (simpleDate && /^\d{4}-\d{2}-\d{2}$/.test(simpleDate)) return simpleDate

  return normalizeDate(new Date(normalized)) ?? normalized
}

export function formatTreeAge(
  value: string | number | null | undefined,
  now: Date = new Date(),
): string | null {
  const dateStr = formatPlantDate(value)
  if (!dateStr) return null
  // Compare calendar fields, not Date objects: `new Date('2025-09-15')` is UTC
  // midnight, which is still the 14th in every timezone west of Greenwich, and
  // the local getters below would then read the day before it was planted.
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr)
  if (!match) return null
  const [plantedYear, plantedMonth, plantedDay] = [Number(match[1]), Number(match[2]) - 1, Number(match[3])]
  let years = now.getFullYear() - plantedYear
  if (
    now.getMonth() < plantedMonth ||
    (now.getMonth() === plantedMonth && now.getDate() < plantedDay)
  ) {
    years--
  }
  if (years < 1) return '< 1 year'
  return `${years} year${years !== 1 ? 's' : ''}`
}
