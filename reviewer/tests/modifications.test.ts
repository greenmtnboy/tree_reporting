import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  assertModificationPublishable,
  modificationExportRow,
  modificationListItem,
  modificationManifest,
  type PendingModification,
} from '../modifications.ts'
import { CITY_CONFIG } from '../submissionCity.ts'

const [SF_LNG, SF_LAT] = CITY_CONFIG.USSFO.center

function pending(overrides: Partial<PendingModification> = {}): PendingModification {
  return {
    userId: 'u1',
    kind: 'update',
    treeId: 'sf-123',
    city: 'USSFO',
    treeLat: SF_LAT,
    treeLng: SF_LNG,
    status: 'pending',
    ...overrides,
  }
}

test('a missing-tree report is publishable without any proposed values', () => {
  assert.doesNotThrow(() => assertModificationPublishable(pending({ kind: 'missing', missingReason: 'stump' })))
})

test('an update that changes nothing is refused', () => {
  assert.throws(() => assertModificationPublishable(pending()), /proposes no change/)
})

test('an update is publishable on species alone', () => {
  assert.doesNotThrow(() => assertModificationPublishable(pending({ proposedSpecies: 'Quercus agrifolia' })))
})

test('a proposed position outside the city is refused', () => {
  assert.throws(
    () => assertModificationPublishable(pending({ proposedLat: 40.7, proposedLng: -74.0 })),
    /km from/,
  )
})

test('half a coordinate pair is refused', () => {
  assert.throws(
    () => assertModificationPublishable(pending({ proposedLat: SF_LAT, proposedLng: null })),
    /both latitude and longitude/,
  )
})

test('an out-of-range DBH is refused', () => {
  assert.throws(() => assertModificationPublishable(pending({ proposedDbhInches: 9000 })), /out of range/)
})

test('an already-decided modification is refused', () => {
  assert.throws(
    () => assertModificationPublishable(pending({ status: 'published', proposedSpecies: 'Acer rubrum' })),
    /already published/,
  )
})

test('an unknown city is refused', () => {
  assert.throws(
    () => assertModificationPublishable(pending({ city: 'XXXXX', proposedSpecies: 'Acer rubrum' })),
    /not a supported city code/,
  )
})

test('the export row carries the change and nothing about the reporter', () => {
  const row = modificationExportRow(
    'm1',
    {
      treeId: 'sf-123',
      city: 'ussfo',
      kind: 'update',
      latitude: SF_LAT,
      longitude: SF_LNG,
      species: ' Acer rubrum ',
      dbhInches: 12,
      userId: 'u1',
      notes: 'private',
    },
    '2026-09-01T00:00:00.000Z',
  )
  assert.deepEqual(row, {
    modificationId: 'm1',
    treeId: 'sf-123',
    city: 'USSFO',
    kind: 'update',
    missingReason: null,
    latitude: SF_LAT,
    longitude: SF_LNG,
    species: 'Acer rubrum',
    dbhInches: 12,
    publishedAt: '2026-09-01T00:00:00.000Z',
  })
})

test('a missing row never carries proposed values', () => {
  const row = modificationExportRow(
    'm2',
    { treeId: 't', city: 'USSFO', kind: 'missing', missingReason: 'removed', species: 'Acer rubrum', latitude: 1 },
    null,
  )
  assert.equal(row.species, null)
  assert.equal(row.latitude, null)
  assert.equal(row.missingReason, 'removed')
})

test('the manifest keeps the latest publish per city', () => {
  const base = { treeId: 't', kind: 'missing' as const, missingReason: null, latitude: null, longitude: null, species: null, dbhInches: null }
  const manifest = modificationManifest([
    { ...base, modificationId: 'a', city: 'USSFO', publishedAt: '2026-09-01T00:00:00.000Z' },
    { ...base, modificationId: 'b', city: 'USBOS', publishedAt: '2026-09-02T00:00:00.000Z' },
    { ...base, modificationId: 'c', city: 'USSFO', publishedAt: '2026-09-03T00:00:00.000Z' },
    { ...base, modificationId: 'd', city: 'USSFO', publishedAt: null },
  ])
  assert.equal(manifest.count, 4)
  assert.equal(manifest.latestPublishedAt, '2026-09-03T00:00:00.000Z')
  assert.deepEqual(manifest.latestPublishedAtByCity, {
    USSFO: '2026-09-03T00:00:00.000Z',
    USBOS: '2026-09-02T00:00:00.000Z',
  })
})

test('a missing-tree report with an unknown reason is refused, and never exported', () => {
  assert.throws(
    () => assertModificationPublishable(pending({ kind: 'missing', missingReason: 'x'.repeat(5000) })),
    /Unknown missing reason/,
  )
  const row = modificationExportRow('m1', { kind: 'missing', treeId: 't', city: 'ussfo', missingReason: '<b>hi</b>' }, null)
  assert.equal(row.missingReason, null)
})

test('the queue row keeps the document id even when the document stores its own', () => {
  const row = modificationListItem('real-id', { ...pending(), id: 'other-id' }, (p) => p)
  assert.equal(row.id, 'real-id')
})

test('the queue row carries only typed values and the owner\'s own photo', () => {
  const hostile = '1"><img src=x onerror=alert(1)>'
  const row = modificationListItem('m1', {
    ...pending(),
    treeLat: hostile,
    proposedLat: hostile,
    proposedDbhInches: hostile,
    missingReason: hostile,
    photoPath: 'checkins/u2/p.jpg',
    extra: 'field',
  }, (p) => p)
  assert.equal(row.treeLat, null)
  assert.equal(row.proposedLat, null)
  assert.equal(row.proposedDbhInches, null)
  assert.equal(row.missingReason, null)
  assert.equal(row.photoUrl, null)
  assert.equal('extra' in row, false)
  assert.equal(
    modificationListItem('m2', { ...pending(), photoPath: 'modifications/u1/m2.jpg' }, (p) => p).photoUrl,
    'modifications/u1/m2.jpg',
  )
})
