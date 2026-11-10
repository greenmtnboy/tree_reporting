/**
 * Labels for the city selector: always "City, Country", with the state or
 * province between them only when two cities share a name
 * ("Burlington, VT, United States" / "Burlington, ON, Canada").
 *
 * The country comes from the code's ISO 3166 prefix, so a new city never
 * needs a list edited here. A config name may carry a region suffix
 * ("Stoughton, WI"); it is dropped when the bare name is unique. "DC" is
 * part of Washington's name, not a disambiguator, so it always stays.
 */

const KEEP_SUFFIX = new Set(['DC'])

const regionNames = new Intl.DisplayNames(['en'], { type: 'region' })

function countryFor(code: string): string | undefined {
  try {
    return regionNames.of(code.slice(0, 2).toUpperCase())
  } catch {
    return undefined
  }
}

function splitName(name: string): { base: string; region: string | null } {
  const m = /^(.*),\s*([A-Z]{2,3})$/.exec(name.trim())
  if (!m || KEEP_SUFFIX.has(m[2])) return { base: name.trim(), region: null }
  return { base: m[1].trim(), region: m[2] }
}

export function cityOptionLabels(config: Record<string, { name: string }>): Record<string, string> {
  const parts = Object.fromEntries(
    Object.entries(config).map(([code, { name }]) => [code, splitName(name)]),
  )
  const baseCounts = new Map<string, number>()
  for (const { base } of Object.values(parts)) {
    const key = base.toLocaleLowerCase()
    baseCounts.set(key, (baseCounts.get(key) ?? 0) + 1)
  }
  return Object.fromEntries(
    Object.entries(parts).map(([code, { base, region }]) => {
      const ambiguous = (baseCounts.get(base.toLocaleLowerCase()) ?? 0) > 1
      const country = countryFor(code)
      const segments = [base]
      if (ambiguous && region) segments.push(region)
      if (country && country !== code.slice(0, 2).toUpperCase()) segments.push(country)
      return [code, segments.join(', ')]
    }),
  )
}
