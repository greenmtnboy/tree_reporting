import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  approvedNotableText,
  assertModificationPublishable,
  authoredNotableModification,
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

test('an update is publishable on a cleared DBH alone, but not with a value too', () => {
  assert.doesNotThrow(() => assertModificationPublishable(pending({ clearFields: ['dbhInches'] })))
  assert.throws(
    () => assertModificationPublishable(pending({ clearFields: ['dbhInches'], proposedDbhInches: 12 })),
    /both a value and cleared/,
  )
  assert.throws(() => assertModificationPublishable(pending({ clearFields: ['species'] })), /no change/)
})

test('the export row carries a cleared DBH in clearFields with no value', () => {
  const cleared = modificationExportRow('m1', { treeId: 'sf-1', city: 'ussfo', kind: 'update', dbhInches: 30, clearFields: ['dbhInches', 'bogus'] }, null)
  assert.deepEqual(cleared.clearFields, ['dbhInches'])
  assert.equal(cleared.dbhInches, null)
  const plain = modificationExportRow('m2', { treeId: 'sf-1', city: 'USSFO', kind: 'update', dbhInches: 30 }, null)
  assert.deepEqual(plain.clearFields, [])
  assert.equal(plain.dbhInches, 30)
  const missing = modificationExportRow('m3', { treeId: 'sf-1', city: 'USSFO', kind: 'missing', clearFields: ['dbhInches'] }, null)
  assert.deepEqual(missing.clearFields, [])
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
    treeName: null,
    significance: null,
    significanceSource: null,
    clearFields: [],
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
  const base = {
    treeId: 't', kind: 'missing' as const, missingReason: null, latitude: null, longitude: null, species: null, dbhInches: null,
    treeName: null, significance: null, significanceSource: null, clearFields: [],
  }
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

// --- A notable tree's name, blurb and source --------------------------------

test('an update is publishable on a proposed name or blurb alone, but not on a source alone', () => {
  assert.doesNotThrow(() => assertModificationPublishable(pending({ proposedTreeName: 'The Senator' })))
  assert.doesNotThrow(() => assertModificationPublishable(pending({ proposedSignificance: 'Planted in 1871.' })))
  assert.throws(
    () => assertModificationPublishable(pending({ proposedSignificanceSource: 'https://example.org' })),
    /needs a name or a blurb/,
  )
  assert.throws(() => assertModificationPublishable(pending({ proposedTreeName: '   ' })), /no change/)
})

test('notable text over its limit, or a source that is not an http link, is refused', () => {
  assert.throws(() => assertModificationPublishable(pending({ proposedTreeName: 'x'.repeat(61) })), /limit is 60/)
  assert.throws(() => assertModificationPublishable(pending({ proposedSignificance: 'x'.repeat(501) })), /limit is 500/)
  assert.throws(
    () => assertModificationPublishable(pending({ proposedTreeName: 'Elm', proposedSignificanceSource: 'javascript:alert(1)' })),
    /http or https/,
  )
  // A register's name is a source too.
  assert.doesNotThrow(() => assertModificationPublishable(pending({ proposedTreeName: 'Elm', proposedSignificanceSource: 'Tree Boston' })))
})

test('the reviewer\'s edit replaces the proposal, and an edit to blank drops that field', () => {
  const report = { proposedTreeName: 'the senatr', proposedSignificance: 'old  tree', proposedSignificanceSource: 'https://a.example' }
  assert.deepEqual(approvedNotableText(report), { treeName: 'the senatr', significance: 'old tree', significanceSource: 'https://a.example' })
  assert.deepEqual(approvedNotableText(report, { treeName: 'The Senator', significanceSource: '' }), {
    treeName: 'The Senator', significance: 'old tree', significanceSource: null,
  })
})

test('the export row carries approved notable text, capped, and a reviewer\'s clear', () => {
  const named = modificationExportRow('m1', {
    treeId: 'mil-1', city: 'ITMIL', kind: 'update', treeName: '  Il   Platano ', significance: 'x'.repeat(600), significanceSource: 'https://a.example',
  }, null)
  assert.equal(named.treeName, 'Il Platano')
  assert.equal(named.significance?.length, 500)
  assert.equal(named.significanceSource, 'https://a.example')
  const cleared = modificationExportRow('m2', {
    treeId: 'mil-1', city: 'ITMIL', kind: 'update', treeName: 'ignored', clearFields: ['treeName', 'significance'],
  }, null)
  assert.deepEqual(cleared.clearFields, ['treeName', 'significance'])
  assert.equal(cleared.treeName, null)
  const missing = modificationExportRow('m3', { treeId: 't', city: 'USSFO', kind: 'missing', treeName: 'Elm' }, null)
  assert.equal(missing.treeName, null)
})

test('a client document can never clear a tree\'s name; only the reviewer can', () => {
  assert.throws(() => assertModificationPublishable(pending({ clearFields: ['treeName'] })), /no change/)
})

test('a reviewer-authored change names a tree, or clears one, in a known city', () => {
  assert.deepEqual(
    authoredNotableModification({ treeId: ' mil-40459 ', city: 'itmil', treeName: 'Il Platano del 1773', significance: '', significanceSource: 'Comune di Milano' }),
    { treeId: 'mil-40459', city: 'ITMIL', kind: 'update', treeName: 'Il Platano del 1773', significance: null, significanceSource: 'Comune di Milano', clearFields: [] },
  )
  const clear = authoredNotableModification({ treeId: 'sf-1', city: 'USSFO', treeName: 'kept out', clearFields: ['treeName', 'dbhInches'] })
  assert.deepEqual(clear.clearFields, ['treeName'])
  assert.equal(clear.treeName, null)
  assert.throws(() => authoredNotableModification({ treeId: 'sf-1', city: 'USSFO' }), /Give a name/)
  assert.throws(() => authoredNotableModification({ treeId: 'sf-1', city: 'XXXXX', treeName: 'Elm' }), /not a supported city/)
  assert.throws(() => authoredNotableModification({ city: 'USSFO', treeName: 'Elm' }), /tree id is required/)
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
