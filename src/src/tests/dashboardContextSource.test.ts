import { describe, expect, it } from 'vitest'
import {
  buildDashboardContextParameters,
  buildDashboardContextSource,
  getCityEcoregionId,
} from '../composables/dashboardContextSource'

describe('dashboardContextSource', () => {
  it('maps known cities to their ecoregions', () => {
    expect(getCityEcoregionId('USBOS')).toBe(339)
    expect(getCityEcoregionId('USSFO')).toBe(423)
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
