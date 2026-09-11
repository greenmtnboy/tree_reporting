import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { test } from 'node:test'

import {
  assertBundle,
  buildManifest,
  buildObservation,
  exportRow,
  lonLatToPixel,
  nearestInventory,
  observationId,
  parseObservationInput,
  pixelToLonLat,
  presentBundle,
  publishedTreeFrom,
  TileStore,
  type TilePredictionBundle,
} from '../satellite.ts'

const FIXTURES = path.join(import.meta.dirname, '..', 'fixtures', 'tiles')

function fixture(): TilePredictionBundle {
  const name = readdirSync(FIXTURES).find((f) => f.startsWith('USSFO') && f.endsWith('.json'))
  assert.ok(name, 'an SF fixture tile is committed under reviewer/fixtures/tiles')
  return assertBundle(JSON.parse(readFileSync(path.join(FIXTURES, name), 'utf8')))
}

test('the committed fixtures satisfy the bundle contract', () => {
  const store = new TileStore(FIXTURES)
  const tiles = store.list()
  assert.ok(tiles.length >= 1)
  for (const summary of tiles) {
    const bundle = store.get(summary.tileId)
    assert.ok(bundle)
    assert.equal(bundle.schemaVersion, 1)
    assert.ok(store.imagePath(summary.tileId), `${summary.tileId} has its PNG beside it`)
    // Sealed test-split chips must never reach the reviewer.
    assert.notEqual(bundle.predictionLayer.cohort, 'test')
  }
})

test('assertBundle refuses what the exporter refuses', () => {
  const good = fixture()
  assert.throws(() => assertBundle({ ...good, schemaVersion: 2 }), /schemaVersion/)
  assert.throws(() => assertBundle({ ...good, image: { ...good.image, affine: [1, 2, 3] } }), /affine/)
  assert.throws(() => assertBundle({ ...good, city: 'sf' }), /five-letter/)
  const dup = { ...good, predictionLayer: { ...good.predictionLayer, predictions: [good.predictionLayer.predictions[0], good.predictionLayer.predictions[0]] } }
  assert.throws(() => assertBundle(dup), /repeats/)
  assert.throws(() => assertBundle({ ...good, tileId: '../etc/passwd' }), /characters/)
})

test('pixel and lon/lat round-trip through the tile affine', () => {
  const bundle = fixture()
  const p = bundle.predictionLayer.predictions[0]
  const geo = pixelToLonLat(bundle, p.xPx, p.yPx)
  // The exporter computed these through pyproj; the fitted affine agrees to ~1 cm.
  assert.ok(Math.abs(geo.longitude - p.longitude) < 2e-7, `${geo.longitude} vs ${p.longitude}`)
  assert.ok(Math.abs(geo.latitude - p.latitude) < 2e-7)
  const px = lonLatToPixel(bundle, p.longitude, p.latitude)
  assert.ok(Math.abs(px.xPx - p.xPx) < 0.05 && Math.abs(px.yPx - p.yPx) < 0.05)
  // An inventory tree lands inside (or within the margin of) the tile.
  for (const tree of presentBundle(bundle).inventoryTrees) {
    assert.ok(tree.xPx > -40 && tree.xPx < bundle.image.width + 40, tree.treeId)
  }
})

test('observation ids are stable and scoped to tile, run and prediction', () => {
  const id = observationId('USSFO-naip2022-x-r1_c1', 'run-a', 'r1_c1:1:2')
  assert.equal(id, observationId('USSFO-naip2022-x-r1_c1', 'run-a', 'r1_c1:1:2'))
  assert.match(id, /^sat-[0-9a-f]{20}$/)
  assert.notEqual(id, observationId('USSFO-naip2022-x-r1_c1', 'run-b', 'r1_c1:1:2'))
  assert.notEqual(id, observationId('USSFO-naip2022-y-r1_c1', 'run-a', 'r1_c1:1:2'))
})

test('an accepted detection publishes at its crown centre with the confirmed species', () => {
  const bundle = fixture()
  const p = bundle.predictionLayer.predictions[0]
  const record = buildObservation(bundle, p, { decision: 'accept', species: p.species }, 'tester', 1)
  assert.equal(record.positionRole, 'crown_center')
  assert.equal(record.latitude, p.latitude)
  assert.equal(record.longitude, p.longitude)
  assert.equal(record.speciesSource, 'model')
  assert.equal(record.predictedDbhInches, p.dbhInches)
  assert.equal(record.measuredDbhInches, null)
  assert.equal(record.status, 'pending')
  assert.equal(record.acquisitionDate, bundle.acquisitionEnd)

  const typed = buildObservation(bundle, p, { decision: 'accept', species: 'Quercus agrifolia' }, 'tester', 1)
  assert.equal(typed.speciesSource, 'reviewer')
  const blank = buildObservation(bundle, p, { decision: 'accept' }, 'tester', 1)
  assert.equal(blank.species, null)
  assert.equal(blank.speciesSource, null)
})

test('a moved crown centre changes the published position and never the trunk', () => {
  const bundle = fixture()
  const p = bundle.predictionLayer.predictions[0]
  const moved = buildObservation(bundle, p, { decision: 'accept', crownCenter: { xPx: p.xPx + 4, yPx: p.yPx } }, 'tester', 1)
  assert.equal(moved.crownCenter.xPx, p.xPx + 4)
  // 4 px at 0.6 m is 2.4 m east: longitude grows by ~2.7e-5 degrees, and
  // latitude moves only by the UTM grid convergence (a few centimetres).
  assert.ok(moved.longitude - p.longitude > 2e-5)
  assert.ok(Math.abs(moved.latitude - p.latitude) < 2e-6)
  assert.throws(() => buildObservation(bundle, p, { decision: 'accept', crownCenter: { xPx: -1, yPx: 0 } }, 'tester', 1), /outside/)
})

test('a duplicate publishes at the linked inventory tree, and only to a tree on the tile', () => {
  const bundle = fixture()
  const p = bundle.predictionLayer.predictions[0]
  const target = nearestInventory(bundle, p.longitude, p.latitude, 1)[0]
  assert.ok(target)
  const record = buildObservation(bundle, p, { decision: 'duplicate', duplicateOfTreeId: target.treeId }, 'tester', 1)
  assert.equal(record.positionRole, 'linked_trunk')
  assert.equal(record.duplicateOfTreeId, target.treeId)
  const tree = bundle.inventoryTrees.find((t) => t.treeId === target.treeId)!
  assert.equal(record.latitude, tree.latitude)
  assert.equal(record.longitude, tree.longitude)
  // The crown centre is still recorded, as an image observation.
  assert.equal(record.crownCenter.xPx, p.xPx)
  assert.throws(() => buildObservation(bundle, p, { decision: 'duplicate', duplicateOfTreeId: 'nope' }, 'tester', 1), /not an inventory tree/)
  assert.throws(() => buildObservation(bundle, p, { decision: 'duplicate' }, 'tester', 1), /needs duplicateOfTreeId/)
  assert.throws(() => buildObservation(bundle, p, { decision: 'accept', duplicateOfTreeId: target.treeId }, 'tester', 1), /only makes sense/)
})

test('nearest inventory is ordered by distance and capped', () => {
  const bundle = fixture()
  const p = bundle.predictionLayer.predictions[0]
  const nearest = nearestInventory(bundle, p.longitude, p.latitude, 3)
  assert.ok(nearest.length <= 3)
  for (let i = 1; i < nearest.length; i += 1) assert.ok(nearest[i - 1].distanceM <= nearest[i].distanceM)
  // The exporter's candidate list, when present, names the same nearest tree.
  if (p.candidateTreeIds?.length) assert.equal(nearest[0].treeId, p.candidateTreeIds[0])
})

test('parseObservationInput validates what the page sends', () => {
  assert.throws(() => parseObservationInput({ decision: 'delete' }), /decision must be one of/)
  assert.throws(() => parseObservationInput({ decision: 'accept', measuredDbhInches: -3 }), /positive/)
  assert.throws(() => parseObservationInput({ decision: 'accept', notes: 'x'.repeat(501) }), /500/)
  assert.throws(() => parseObservationInput({ decision: 'accept', crownCenter: { xPx: 'a' } }), /crownCenter/)
  const ok = parseObservationInput({ decision: 'accept', species: '  Acer rubrum ', measuredDbhInches: '12.5', baseRevision: 2 })
  assert.deepEqual([ok.species, ok.measuredDbhInches, ok.baseRevision], ['Acer rubrum', 12.5, 2])
})

test('the public export carries no notes or actor and keeps estimate and measurement apart', () => {
  const bundle = fixture()
  const p = bundle.predictionLayer.predictions[0]
  const record = buildObservation(bundle, p, { decision: 'accept', species: p.species, notes: 'private', measuredDbhInches: 9 }, 'someone@example.com', 1)
  const published = { ...publishedTreeFrom(record), publishedAt: '2026-09-11T12:00:00.000Z' }
  const row = exportRow(published)
  const text = JSON.stringify(row)
  assert.ok(!text.includes('private') && !text.includes('someone@example.com'))
  assert.equal(row.schemaVersion, 1)
  assert.equal(row.treeId, record.id)
  assert.equal(row.predictedDbhInches, p.dbhInches)
  assert.equal(row.measuredDbhInches, 9)
  assert.equal(row.city, 'USSFO')
  assert.deepEqual(Object.keys(row).sort(), [
    'acquisitionDate', 'city', 'duplicateOfTreeId', 'imageryVersion', 'latitude', 'longitude',
    'measuredDbhInches', 'positionRole', 'predictedCrownWidthM', 'predictedDbhInches', 'predictionId',
    'publishedAt', 'runId', 'schemaVersion', 'species', 'speciesSource', 'tileId', 'treeId',
  ])
})

test('the manifest tracks the latest publish per city', () => {
  const rows = [
    { city: 'USSFO', publishedAt: '2026-09-01T00:00:00.000Z' },
    { city: 'USBOS', publishedAt: '2026-09-03T00:00:00.000Z' },
    { city: 'USSFO', publishedAt: '2026-09-02T00:00:00.000Z' },
    { city: 'USSFO', publishedAt: null },
  ] as Parameters<typeof buildManifest>[0]
  const manifest = buildManifest(rows)
  assert.equal(manifest.count, 4)
  assert.equal(manifest.latestPublishedAt, '2026-09-03T00:00:00.000Z')
  assert.deepEqual(manifest.latestPublishedAtByCity, { USSFO: '2026-09-02T00:00:00.000Z', USBOS: '2026-09-03T00:00:00.000Z' })
})
