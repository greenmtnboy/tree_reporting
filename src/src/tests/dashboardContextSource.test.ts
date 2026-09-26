import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import cityConfig from '../cityConfig.json'
import {
  buildDashboardContextParameters,
  buildDashboardContextSource,
  getCityBiome,
  getCityEcoregionId,
} from '../composables/dashboardContextSource'

// The city ecoregions the data model assigns, from `city.ecoregion_id` in
// data/raw/core.preql.
const CORE_ECOREGIONS = Object.fromEntries(
  [
    ...readFileSync(new URL('../../../data/raw/core.preql', import.meta.url), 'utf-8').matchAll(
      /when city = '(\w+)' then (\d+)/g,
    ),
  ].map(([, city, id]) => [city, Number(id)]),
)

describe('dashboardContextSource', () => {
  it('maps known cities to their ecoregions', () => {
    expect(getCityEcoregionId('USBOS')).toBe(339)
    expect(getCityEcoregionId('USSFO')).toBe(423)
  })

  // A city missing here falls back to 'Unknown': every tree lands in
  // "Non-Native, Different Biome" and the backdrop draws the broadleaf default.
  it.each(Object.keys(cityConfig))('gives %s the ecoregion the data model uses', (city) => {
    expect(getCityBiome(city)).not.toBe('Unknown')
    expect(getCityEcoregionId(city)).toBe(CORE_ECOREGIONS[city])
  })

  it('uses a safe fallback when no city is active', () => {
    const source = buildDashboardContextSource(null)
    expect(source.alias).toBe('dashboard_context')
    expect(source.contents).toContain("constant active_city <- 'ALL';")
    expect(source.contents).toContain('constant active_city_ecoregion <- -1;')
  })

  it('renders the active city context as constants', () => {
    const source = buildDashboardContextSource('USBOS')
    expect(source.contents).toContain("constant active_city <- 'USBOS';")
    expect(source.contents).toContain('constant active_city_ecoregion <- 339;')
  })

  it('builds execution parameters for the active city context', () => {
    expect(buildDashboardContextParameters('USBOS')).toEqual({
      active_city: 'USBOS',
      active_city_ecoregion: 339,
      active_city_usda_zone: 7,
      active_city_biome: 'Temperate Broadleaf & Mixed Forests',
      active_city_realm: 'nearctic',
    })
    expect(buildDashboardContextParameters(null)).toEqual({
      active_city: 'ALL',
      active_city_ecoregion: -1,
      active_city_usda_zone: -1,
      active_city_biome: 'Unknown',
      active_city_realm: 'unknown',
    })
  })
})
