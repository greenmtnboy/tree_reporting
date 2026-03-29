import { describe, expect, it } from 'vitest'
import { buildDashboardContextSource, getCityEcoregionId } from '../composables/dashboardContextSource'

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
})
