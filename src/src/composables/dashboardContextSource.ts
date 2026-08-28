import type { CityCode } from './useMapData'

export type DashboardContextSource = {
  alias: string
  contents: string
}

export type DashboardContextParameters = {
  active_city: string
  active_city_ecoregion: number
  active_city_usda_zone: number
  active_city_biome: string
  active_city_realm: string
}

const UNKNOWN_ECOREGION_ID = -1
const UNKNOWN_USDA_ZONE = -1

const CITY_DASHBOARD_CONTEXT: Record<
  CityCode,
  { ecoregionId: number; usdaZone: number; biome: string; realm: string }
> = {
  USSFO: {
    ecoregionId: 423,
    usdaZone: 10,
    biome: 'Mediterranean Forests, Woodlands & Scrub',
    realm: 'nearctic',
  },
  USNYC: {
    ecoregionId: 339,
    usdaZone: 7,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'nearctic',
  },
  USBOS: {
    ecoregionId: 339,
    usdaZone: 7,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'nearctic',
  },
  FRPAR: {
    ecoregionId: 664,
    usdaZone: 8,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'palearctic',
  },
  USBTV: {
    ecoregionId: 334,
    usdaZone: 5,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'nearctic',
  },
  CAVAN: {
    ecoregionId: 524,
    usdaZone: 8,
    biome: 'Temperate Conifer Forests',
    realm: 'nearctic',
  },
  DEBER: {
    ecoregionId: 680,
    usdaZone: 7,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'palearctic',
  },
  NLAMS: {
    ecoregionId: 661,
    usdaZone: 8,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'palearctic',
  },
  GBLON: {
    ecoregionId: 660,
    usdaZone: 9,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'palearctic',
  },
  AUMEL: {
    ecoregionId: 801,
    usdaZone: 9,
    biome: 'Temperate Broadleaf & Mixed Forests',
    realm: 'australasian',
  },
  ARBUE: {
    ecoregionId: 576,
    usdaZone: 10,
    biome: 'Temperate Grasslands, Savannas & Shrublands',
    realm: 'neotropical',
  },
  USLAX: {
    ecoregionId: 422,
    usdaZone: 10,
    biome: 'Mediterranean Forests, Woodlands & Scrub',
    realm: 'nearctic',
  },
  USWAS: {
    ecoregionId: 399,
    usdaZone: 7,
    biome: 'Temperate Grasslands, Savannas & Shrublands',
    realm: 'nearctic',
  },
  USTEM: {
    ecoregionId: 435,
    usdaZone: 10,
    biome: 'Deserts & Xeric Shrublands',
    realm: 'nearctic',
  },
  GRATH: {
    ecoregionId: 785,
    usdaZone: 10,
    biome: 'Mediterranean Forests, Woodlands & Scrub',
    realm: 'palearctic',
  },
  GRMLO: {
    ecoregionId: 785,
    usdaZone: 10,
    biome: 'Mediterranean Forests, Woodlands & Scrub',
    realm: 'palearctic',
  },
}

function escapeStringLiteral(value: string) {
  return value.replace(/'/g, "''")
}

export function getCityEcoregionId(city: CityCode | null): number {
  if (!city) {
    return UNKNOWN_ECOREGION_ID
  }
  return CITY_DASHBOARD_CONTEXT[city]?.ecoregionId ?? UNKNOWN_ECOREGION_ID
}

export function getCityUsdaZone(city: CityCode | null): number {
  if (!city) {
    return UNKNOWN_USDA_ZONE
  }
  return CITY_DASHBOARD_CONTEXT[city]?.usdaZone ?? UNKNOWN_USDA_ZONE
}

export function getCityBiome(city: CityCode | null): string {
  if (!city) {
    return 'Unknown'
  }
  return CITY_DASHBOARD_CONTEXT[city]?.biome ?? 'Unknown'
}

export function getCityRealm(city: CityCode | null): string {
  if (!city) {
    return 'unknown'
  }
  return CITY_DASHBOARD_CONTEXT[city]?.realm ?? 'unknown'
}

export function buildDashboardContextParameters(city: CityCode | null): DashboardContextParameters {
  return {
    active_city: city ?? 'ALL',
    active_city_ecoregion: getCityEcoregionId(city),
    active_city_usda_zone: getCityUsdaZone(city),
    active_city_biome: getCityBiome(city),
    active_city_realm: getCityRealm(city),
  }
}

export function buildDashboardContextSource(city: CityCode | null): DashboardContextSource {
  const context = buildDashboardContextParameters(city)
  const escapedCity = escapeStringLiteral(context.active_city)
  const activeCityBiome = escapeStringLiteral(context.active_city_biome)
  const activeCityRealm = escapeStringLiteral(context.active_city_realm)

  return {
    alias: 'dashboard_context',
    contents: `import tree_enrichment;
import ecoregion_info;
import std.display;

constant active_city <- '${escapedCity}';
constant active_city_ecoregion <- ${context.active_city_ecoregion};
constant active_city_usda_zone <- ${context.active_city_usda_zone};
constant active_city_biome <- '${activeCityBiome}';
constant active_city_realm <- '${activeCityRealm}';

def is_full_sun(x) -> x = 'full_sun';
def is_partial_shade(x) -> x = 'partial_shade';
def is_shade(x) -> x = 'shade';


property native_locality_bucket <- CASE
    WHEN ecoregion_id=active_city_ecoregion THEN 'Native'
    WHEN biome = active_city_biome THEN 'Same biome, non-native'
    WHEN realm = active_city_realm THEN 'Native Region, Different Biome'
  else 'Non-Native, Different Biome'
end::string;

auto hardiness_fit_bucket <- CASE
  when usda_zone_min is null or usda_zone_max is null then 'Unknown'
  when active_city_usda_zone < usda_zone_min or active_city_usda_zone > usda_zone_max then 'Outside zone'
  when active_city_usda_zone = usda_zone_min or active_city_usda_zone = usda_zone_max then 'Edge of tolerance'
  else 'Well within zone'
end::string;

auto water_resilience_bucket <- CASE
  when water_needs = 'high' and drought_tolerance = 'low' then 'High water / low drought tolerance'
  when water_needs = 'low' and drought_tolerance = 'high' then 'Low water / high drought tolerance'
  when water_needs is null or drought_tolerance is null then 'Unknown'
  else 'Moderate / mixed'
end::string;

auto lifespan_bucket <- CASE
  when lifespan_max_years is null and lifespan_min_years is null then 'Unknown'
  when coalesce(lifespan_max_years, lifespan_min_years) < 50 then 'Short-lived (<50y)'
  when coalesce(lifespan_max_years, lifespan_min_years) < 150 then 'Medium-lived (50-149y)'
  else 'Long-lived (150+y)'
end::string;

auto sun_exposure_label <- CASE
  when len(array_filter(sun_exposure, @is_shade)) > 0 then 'Shade'
  when len(array_filter(sun_exposure, @is_partial_shade)) > 0 then 'Partial shade'
  when len(array_filter(sun_exposure, @is_full_sun)) > 0 then 'Full sun'
end::string;

auto dominance_rank <- rank(species) over (order by count(tree_id) by species desc, species asc);

auto cumulative_tree_share_pct <- ((sum count(tree_id) by species order by dominance_rank asc) / (count(tree_id) by *))::float::percent;

`,
  }
}
