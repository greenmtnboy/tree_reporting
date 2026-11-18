import { ref } from 'vue'

/**
 * The date the map is drawn for: today, unless the map's route names one (`?date=2027-01-15`,
 * which the debug panel also sets). It decides the season: which deciduous trees are drawn bare
 * (lib/crownBatch) and the "Leaf shade" colours' phase (lib/foliageSeason).
 */
export const MAP_DATE_PARAM = 'date'
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

/** A `YYYY-MM-DD` as a local date, or null for anything else. */
export function parseMapDate(value: unknown): Date | null {
  const m = typeof value === 'string' ? ISO_DATE.exec(value.trim()) : null
  if (!m) return null
  const date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return date.getMonth() === Number(m[2]) - 1 ? date : null
}

/** The chosen `YYYY-MM-DD`, null for today; TreeMap keeps it in step with the route. */
export const mapDateChoice = ref<string | null>(null)

export function mapDate(): Date {
  return parseMapDate(mapDateChoice.value) ?? new Date()
}
