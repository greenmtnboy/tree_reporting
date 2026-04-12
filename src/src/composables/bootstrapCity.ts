import type { CityCode } from './useMapData'

export type BootstrapCitySource = 'route' | 'shared-location' | 'default'

export interface ResolveBootstrapCityOptions {
  routeCity: CityCode | null
  defaultCity: CityCode
  resolveSharedLocationCity: () => Promise<CityCode | null>
}

export interface BootstrapCityResolution {
  city: CityCode
  source: BootstrapCitySource
}

export async function resolveBootstrapCity(
  options: ResolveBootstrapCityOptions,
): Promise<BootstrapCityResolution> {
  if (options.routeCity) {
    return { city: options.routeCity, source: 'route' }
  }

  const sharedLocationCity = await options.resolveSharedLocationCity()
  if (sharedLocationCity) {
    return { city: sharedLocationCity, source: 'shared-location' }
  }

  return { city: options.defaultCity, source: 'default' }
}