/** Number and name formatting shared by the dashboard pages' own cards. */

const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

export function formatCount(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '–' : integer.format(Math.round(value))
}

/** 1,695,266 -> "1.70M", 58,633 -> "58.6k", 842 -> "842". */
export function formatCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '–'
  const abs = Math.abs(value)
  if (abs >= 1e6) return `${(value / 1e6).toFixed(abs >= 1e7 ? 1 : 2)}M`
  if (abs >= 1e4) return `${(value / 1e3).toFixed(abs >= 1e5 ? 0 : 1)}k`
  return integer.format(Math.round(value))
}

/** A share in 0..1 as a percentage; small shares keep a decimal, tiny ones say so. */
export function formatShare(share: number | null | undefined): string {
  if (share == null || !Number.isFinite(share)) return '–'
  const pct = share * 100
  if (pct > 0 && pct < 0.1) return '<0.1%'
  if (pct < 10) return `${pct.toFixed(1)}%`
  return `${Math.round(pct)}%`
}

/** Square metres as hectares, or square kilometres once that reads better. */
export function formatArea(squareMetres: number | null | undefined): string {
  if (squareMetres == null || !Number.isFinite(squareMetres)) return '–'
  const hectares = squareMetres / 1e4
  if (hectares >= 1000) return `${(hectares / 100).toFixed(hectares >= 10000 ? 0 : 1)} km²`
  if (hectares >= 10) return `${integer.format(Math.round(hectares))} ha`
  return `${hectares.toFixed(1)} ha`
}

/** "northern red oak" -> "Northern Red Oak"; null for an empty name. */
export function formatCommonName(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  if (!normalized) return null
  return normalized
    .toLowerCase()
    .replace(/(^|[\s\-/(])([a-z])/g, (_match, prefix: string, letter: string) => `${prefix}${letter.toUpperCase()}`)
}

/** "multi_trunk" -> "Multi trunk". */
export function formatEnum(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  const spaced = value.trim().replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** The cultivar quoted inside a scientific name, if any. */
export function extractCultivar(value: unknown): string | null {
  if (typeof value !== 'string') return null
  return value.match(/'([^']+)'/)?.[1]?.trim() || null
}

/**
 * The name to lead with for a species row: its common name (with the cultivar
 * when the record names one), else the scientific name. A record identified
 * only to genus says so, since "Oak" beside "Northern red oak" reads as a
 * species otherwise.
 */
export function speciesDisplayName(species: unknown, commonName: unknown): string {
  const scientific = typeof species === 'string' ? species.trim() : ''
  const common = formatCommonName(commonName)
  if (!common) return scientific || 'Unnamed'
  const cultivar = formatCommonName(extractCultivar(scientific))
  if (cultivar) return `${common} (${cultivar})`
  return isGenusOnly(scientific) ? `${common} (species not recorded)` : common
}

/** A bare genus: one capitalised word and nothing else. */
export function isGenusOnly(species: string): boolean {
  return /^[A-Z][a-z-]+$/.test(species.trim())
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function formatBloomMonths(value: unknown): string | null {
  const list = Array.isArray(value) ? value : value && typeof (value as { toArray?: unknown }).toArray === 'function'
    ? (value as { toArray: () => unknown[] }).toArray()
    : null
  if (!list || !list.length) return null
  const months = [...new Set(list.map(Number).filter((m) => Number.isInteger(m) && m >= 1 && m <= 12))].sort((a, b) => a - b)
  if (!months.length) return null
  if (months.length === 12) return 'Year-round'
  const contiguous = months.every((m, i) => i === 0 || m === months[i - 1] + 1)
  if (contiguous) {
    const first = MONTHS[months[0] - 1]
    const last = MONTHS[months[months.length - 1] - 1]
    return first === last ? first : `${first}–${last}`
  }
  return months.map((m) => MONTHS[m - 1]).join(', ')
}

export function formatRange(min: unknown, max: unknown, unit: string): string | null {
  const a = typeof min === 'number' ? min : null
  const b = typeof max === 'number' ? max : null
  if (a == null && b == null) return null
  if (a != null && b != null) return a === b ? `${a} ${unit}` : `${a}–${b} ${unit}`
  return `${a ?? b} ${unit}`
}
