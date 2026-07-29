/**
 * Display labels for the `data_source` column carried by every tree row.
 *
 * The stored values are the per-city `{code}_source` enums declared in each
 * city's Trilogy model (see data/raw/core.preql for why the enums are per-city
 * rather than global); the value list itself lives in DATA_SOURCES in
 * data/raw/_ingest_shared.py. This map is only the picklist's display side —
 * an unmapped value falls back to a readable form of the raw value rather than
 * disappearing, so a newly added city shows something sensible before anyone
 * gets round to naming it here.
 */
export const DATA_SOURCE_LABELS: Record<string, string> = {
  SF_OPENDATA: 'SF Open Data',
  NYC_OPENDATA: 'NYC Open Data',
  CITY_OF_BOSTON: 'City of Boston',
  ARNOLD_ARBORETUM: 'Arnold Arboretum',
  CAMBRIDGE: 'City of Cambridge',
  BROOKLINE: 'Town of Brookline',
  PARIS_OPENDATA: 'Paris Open Data',
  BURLINGTON_OPENDATA: 'City of Burlington',
  VANCOUVER_OPENDATA: 'Vancouver Open Data',
  BERLIN_OPENDATA: 'Berlin GDI',
  AMSTERDAM_OPENDATA: 'City of Amsterdam',
  LONDON_OPENDATA: 'London Datastore',
  MELBOURNE_OPENDATA: 'City of Melbourne',
  BUENOSAIRES_OPENDATA: 'Buenos Aires Data',
  LOSANGELES_OPENDATA: 'LA Open Data',
  WASHINGTONDC_OPENDATA: 'Open Data DC',
  TEMPE_OPENDATA: 'City of Tempe',
}

/** True for the approved-community-submission sources (`COMMUNITY_<CITY>`). */
export function isCommunitySource(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.startsWith('COMMUNITY_')
}

/** Human-readable label for a `data_source` value; null when there is none. */
export function formatDataSource(value: string | null | undefined): string | null {
  if (!value) return null
  if (isCommunitySource(value)) return 'Community submission'
  return (
    DATA_SOURCE_LABELS[value] ??
    value
      .toLowerCase()
      .split('_')
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ')
  )
}
