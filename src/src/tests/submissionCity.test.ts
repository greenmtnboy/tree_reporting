import { describe, expect, it } from 'vitest'
import {
  CITY_CONFIG,
  CITY_RADIUS_KM,
  cityAt,
  closestCityTo,
  distanceToCityKm,
  isWithinCity,
} from '../composables/useMapData'

// 36.7657 N, 24.5222 E — the coordinates of a real submission made on Milos on
// 2026-08-28 at 09:13 UTC, 34 minutes before the commit that put Milos in
// cityConfig.json. `closestCityTo` answered Berlin, 1,955 km away, and the
// approved row was dropped by the ingest for falling outside Berlin's
// territory, so the tree appeared on no city's map at all.
const MILOS: [number, number] = [36.76573485105567, 24.522211532312724]

describe('cityAt', () => {
  it('returns the city a point is actually in', () => {
    expect(cityAt(...MILOS)).toBe('GRMLO')
    expect(cityAt(42.3088, -71.11)).toBe('USBOS')
  })

  it('returns null rather than a city on the far side of a continent', () => {
    // Somewhere in the Sahara, thousands of km from every wired city.
    expect(cityAt(23.4, 12.8)).toBeNull()
    // And the regression itself: without Milos on the map, Berlin must not
    // stand in for it.
    const withoutMilos = Object.fromEntries(
      Object.entries(CITY_CONFIG).filter(([code]) => !code.startsWith('GR')),
    )
    const nearest = Object.entries(withoutMilos).reduce(
      (best, [code]) => {
        const d = distanceToCityKm(code, ...MILOS)!
        return d < best.km ? { code, km: d } : best
      },
      { code: '', km: Infinity },
    )
    expect(nearest.code).toBe('DEBER')
    expect(nearest.km).toBeGreaterThan(CITY_RADIUS_KM)
    expect(isWithinCity('DEBER', ...MILOS)).toBe(false)
  })

  it('leaves closestCityTo total, because navigation wants a nearest city', () => {
    // The map still has to show someone something; only data writes are capped.
    expect(closestCityTo(23.4, 12.8)).toBeTruthy()
    expect(cityAt(23.4, 12.8)).toBeNull()
  })
})

describe('isWithinCity', () => {
  it('accepts every city its own configured center', () => {
    for (const [code, cfg] of Object.entries(CITY_CONFIG)) {
      expect(isWithinCity(code, cfg.center[1], cfg.center[0])).toBe(true)
    }
  })

  it('rejects an unknown city code', () => {
    expect(isWithinCity('ZZZZZ', 0, 0)).toBe(false)
    expect(distanceToCityKm('ZZZZZ', 0, 0)).toBeNull()
  })
})
