import { readFileSync } from 'node:fs'
import path from 'node:path'

/**
 * Which city a community submission may be recorded as, and how far from that
 * city's center its coordinates are allowed to sit.
 *
 * The city codes are read from the frontend's city config so the reviewer has
 * no list of its own to fall behind: cityConfig.json is what
 * data/raw/tests/test_city_wiring.py holds every city to, alongside the `city`
 * enum in data/raw/core.preql. The ingest silently drops rows for an unknown
 * city, so approval and publish reject them here where a human can see it.
 */
const CITY_CONFIG_PATH = path.join(import.meta.dirname, '..', 'src', 'src', 'cityConfig.json')

export const CITY_CONFIG = JSON.parse(readFileSync(CITY_CONFIG_PATH, 'utf8')) as Record<
  string,
  { name: string; center: [number, number] }
>

export const CITY_CODES = new Set<string>(Object.keys(CITY_CONFIG))
if (![...CITY_CODES].every((code) => /^[A-Z]{5}$/.test(code))) {
  throw new Error(`${CITY_CONFIG_PATH} has a key that is not a five-letter city code`)
}

/**
 * How far from its city's center an approved tree may sit.
 *
 * Kept in step with CITY_RADIUS_KM in src/src/composables/useMapData.ts, which
 * is what the submit form enforces; data/raw/tests/test_submission_city_radius.py
 * pins the two together and to the measurement that justifies the number — the
 * furthest any city's CITY_BOUNDS corner sits from its configured center is
 * Halifax at 106 km.
 *
 * This is a coordinate sanity bound, not a boundary. CITY_TERRITORY in
 * data/raw/_ingest_shared.py remains the authority on which city a tree is in;
 * what this catches is the case that authority handles by dropping the row.
 */
export const CITY_RADIUS_KM = 150

export function haversineKm(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLng = ((lng2 - lng1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

/**
 * Throw unless a submission's coordinates are near enough to the city it claims.
 *
 * The submit form can only refuse what it knows about: a tree submitted before
 * its city was on the map is recorded as the nearest city that *was* on it, and
 * the ingest then drops it for falling outside that city's territory — after
 * approval, silently, so the tree reaches no city at all. A Milos submission
 * made 34 minutes before Milos was added to cityConfig.json was published as
 * Berlin, 1,955 km away, and was invisible on both maps until it was found by
 * hand.
 */
export function assertCoordinatesAreInCity(city: string, lat: unknown, lng: unknown): void {
  const center = CITY_CONFIG[city]?.center
  if (!center) throw new Error(`Submission city ${city} is not a supported city code`)
  if (
    typeof lat !== 'number' || typeof lng !== 'number' ||
    !Number.isFinite(lat) || !Number.isFinite(lng)
  ) {
    throw new Error(`Submission has no usable coordinates (lat ${String(lat)}, lng ${String(lng)})`)
  }
  const distance = haversineKm(lat, lng, center[1], center[0])
  if (distance > CITY_RADIUS_KM) {
    throw new Error(
      `Submission is ${Math.round(distance)} km from ${CITY_CONFIG[city].name} (${city}), past the ` +
      `${CITY_RADIUS_KM} km limit. The ingest would drop it for being outside that city's ` +
      `territory. Repoint it at the city it is actually in before approving.`,
    )
  }
}
