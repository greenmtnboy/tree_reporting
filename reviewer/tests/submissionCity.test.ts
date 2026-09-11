import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  assertCoordinatesAreInCity,
  CITY_CODES,
  CITY_CONFIG,
  CITY_RADIUS_KM,
} from '../submissionCity.ts'

// The real submission that motivated this gate: made on Milos 34 minutes before
// Milos reached cityConfig.json, recorded as Berlin, approved, and then dropped
// by the ingest for being outside Berlin's territory.
const MILOS_LAT = 36.76573485105567
const MILOS_LNG = 24.522211532312724

test('a tree far from the city it claims is refused at the approval gate', () => {
  assert.throws(
    () => assertCoordinatesAreInCity('DEBER', MILOS_LAT, MILOS_LNG),
    /1955 km from Berlin \(DEBER\)/,
  )
})

test('the same coordinates are accepted for the city they are actually in', () => {
  assert.doesNotThrow(() => assertCoordinatesAreInCity('GRMLO', MILOS_LAT, MILOS_LNG))
})

test('every city accepts its own configured center', () => {
  for (const code of CITY_CODES) {
    const [lng, lat] = CITY_CONFIG[code].center
    assert.doesNotThrow(() => assertCoordinatesAreInCity(code, lat, lng), `${code} rejected its own center`)
  }
})

test('missing or unusable coordinates are refused, not treated as (0, 0)', () => {
  for (const bad of [null, undefined, Number.NaN, '42.3', Infinity]) {
    assert.throws(() => assertCoordinatesAreInCity('USBOS', bad, -71.1), /no usable coordinates/)
    assert.throws(() => assertCoordinatesAreInCity('USBOS', 42.3, bad), /no usable coordinates/)
  }
})

test('an unknown city code is refused before the distance is considered', () => {
  assert.throws(() => assertCoordinatesAreInCity('ZZZZZ', 42.3, -71.1), /not a supported city code/)
})

test('the radius matches the frontend constant it is kept in step with', () => {
  assert.equal(CITY_RADIUS_KM, 150)
})
