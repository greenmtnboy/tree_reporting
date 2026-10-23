import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  confirmationFromCheckin,
  confirmationFromReport,
  firstConfirmations,
  frozenTarget,
  frozenTreeFrom,
  frozenTreeId,
  mergedTreePhotos,
  planFreeze,
  type Confirmation,
} from '../frozenPredictions.ts'
import { MAX_TREE_PHOTOS } from '../checkinPhotos.ts'
import { exportRow, isSatelliteTreeId } from '../satellite.ts'

const CITIES = new Set(['USBOS', 'USSFO'])

function checkin(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    userId: 'u1', treeId: 'pred-0123456789abcdef0123', lat: 42.30001, lng: -71.10001,
    treeLat: 42.3, treeLng: -71.1, city: 'USBOS', species: 'Acer', at: '2026-10-20T12:00:00.000Z',
    ...overrides,
  }
}

function confirmation(overrides: Partial<Confirmation> = {}): Confirmation {
  return { ...(confirmationFromCheckin('c1', checkin()) as Confirmation), ...overrides }
}

test('a check-in on a predicted tree confirms it at the tree, not where the person stood', () => {
  const c = confirmationFromCheckin('c1', checkin())
  assert.deepEqual(c, {
    predictedTreeId: 'pred-0123456789abcdef0123', kind: 'checkin', id: 'c1',
    city: 'USBOS', latitude: 42.3, longitude: -71.1, species: 'Acer', at: '2026-10-20T12:00:00.000Z',
  })
})

test('only predicted trees with a position are confirmed by a check-in', () => {
  assert.equal(confirmationFromCheckin('c1', checkin({ treeId: 'sat-abc' })), null)
  assert.equal(confirmationFromCheckin('c1', checkin({ treeId: 'USBOS-123' })), null)
  assert.equal(confirmationFromCheckin('c1', checkin({ treeLat: undefined })), null)
})

test('an update report confirms; a missing or rejected report does not', () => {
  const report = { treeId: 'pred-x', kind: 'update', status: 'pending', city: 'usbos', treeLat: 42.3, treeLng: -71.1, currentSpecies: 'Quercus rubra', submittedAt: '2026-10-21T00:00:00.000Z' }
  assert.deepEqual(confirmationFromReport('r1', report), {
    predictedTreeId: 'pred-x', kind: 'report', id: 'r1', city: 'USBOS', latitude: 42.3, longitude: -71.1,
    species: 'Quercus rubra', at: '2026-10-21T00:00:00.000Z',
  })
  assert.equal(confirmationFromReport('r1', { ...report, kind: 'missing' }), null)
  assert.equal(confirmationFromReport('r1', { ...report, status: 'rejected' }), null)
})

test('the earliest confirmation of a tree is the one that freezes it', () => {
  const later = confirmation({ id: 'later', at: '2026-10-21T00:00:00.000Z' })
  const earlier = confirmation({ id: 'earlier', at: '2026-10-19T00:00:00.000Z' })
  const other = confirmation({ predictedTreeId: 'pred-other', id: 'other' })
  assert.deepEqual(firstConfirmations([later, other, earlier]).map((c) => c.id), ['earlier', 'other'])
})

test('a frozen tree has a stable satellite id distinct from the prediction', () => {
  const id = frozenTreeId('pred-0123456789abcdef0123')
  assert.equal(id, frozenTreeId('pred-0123456789abcdef0123'))
  assert.notEqual(id, frozenTreeId('pred-0123456789abcdef0124'))
  assert.ok(isSatelliteTreeId(id))
  assert.match(id, /^sat-[0-9a-f]{20}$/)
})

test('a frozen tree exports as a satellite row the ingest can read, with no measurement and no "undefined"', () => {
  const row = exportRow({ ...frozenTreeFrom(confirmation()), publishedAt: '2026-10-22T00:00:00.000Z' })
  assert.equal(row.treeId, frozenTreeId('pred-0123456789abcdef0123'))
  assert.equal(row.city, 'USBOS')
  assert.equal(row.positionRole, 'crown_center')
  assert.equal(row.species, 'Acer')
  assert.equal(row.speciesSource, 'model')
  assert.equal(row.measuredDbhInches, null)
  assert.equal(row.origin, 'bulk-confirmed')
  for (const field of ['tileId', 'imageryVersion', 'runId', 'predictionId', 'acquisitionDate'] as const) {
    assert.equal(row[field], null, field)
  }
  assert.ok(!JSON.stringify(row).includes('undefined'))
})

test('the sweep freezes new confirmations, skips frozen ones and unknown cities, and defers to a nearby satellite tree', () => {
  const near = confirmation({ predictedTreeId: 'pred-near' })
  const fresh = confirmation({ predictedTreeId: 'pred-fresh', latitude: 42.31 })
  const done = confirmation({ predictedTreeId: 'pred-done', latitude: 42.32 })
  const abroad = confirmation({ predictedTreeId: 'pred-abroad', city: 'XXXXX' })
  const published = [
    // 2 m north of `near`: the same tree, already reviewed.
    { id: 'sat-reviewed', data: { city: 'USBOS', latitude: 42.30002, longitude: -71.1 } },
    // A withdrawn tree right on `fresh` does not cover it.
    { id: 'sat-gone', data: { city: 'USBOS', latitude: 42.31, longitude: -71.1, withdrawnAt: '2026-10-01' } },
    { id: frozenTreeId('pred-done'), data: { city: 'USBOS', latitude: 42.32, longitude: -71.1 } },
  ]
  const plan = planFreeze([near, fresh, done, abroad], published, CITIES)
  assert.deepEqual(plan.freeze.map((c) => c.predictedTreeId), ['pred-fresh'])
  assert.deepEqual(plan.covered.map((c) => [c.confirmation.predictedTreeId, c.treeId]), [['pred-near', 'sat-reviewed']])
  assert.equal(plan.alreadyFrozen, 1)
  assert.deepEqual(plan.skipped.map((s) => s.predictedTreeId), ['pred-abroad'])
})

test('a withdrawn frozen record still counts as frozen, so a tree the reviewer withdrew stays withdrawn', () => {
  const published = [{ id: frozenTreeId('pred-x'), data: { city: 'USBOS', latitude: 42.3, longitude: -71.1, withdrawnAt: '2026-10-21' } }]
  const plan = planFreeze([confirmation({ predictedTreeId: 'pred-x' })], published, CITIES)
  assert.equal(plan.freeze.length, 0)
  assert.equal(plan.alreadyFrozen, 1)
})

test('photos and counters follow a frozen prediction to its tree, or to the tree that covered it', () => {
  assert.equal(frozenTarget(frozenTreeFrom(confirmation())), frozenTreeId('pred-0123456789abcdef0123'))
  assert.equal(frozenTarget({ ...frozenTreeFrom(confirmation()), duplicateOfTreeId: 'sat-reviewed' }), 'sat-reviewed')
})

test('moved photos join the target newest first, without duplicates, capped', () => {
  assert.deepEqual(mergedTreePhotos({ photoUrls: ['a', 'b'] }, { photoUrls: ['b', 'c'] }), ['a', 'b', 'c'])
  assert.deepEqual(mergedTreePhotos(undefined, { photoUrls: ['c'] }), ['c'])
  const many = Array.from({ length: 20 }, (_, i) => `u${i}`)
  assert.equal(mergedTreePhotos({ photoUrls: many }, { photoUrls: ['new'] }).length, MAX_TREE_PHOTOS)
})
