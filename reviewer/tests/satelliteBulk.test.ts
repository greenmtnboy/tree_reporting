import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { bulkOverviewPath, listBulkSets } from '../satelliteBulk.ts'

function modelDir(): string {
  const root = mkdtempSync(path.join(tmpdir(), 'bulk-'))
  const set = (name: string, plan: object, overview: boolean) => {
    const dir = path.join(root, 'artifacts', 'bulk', name)
    mkdirSync(dir, { recursive: true })
    writeFileSync(path.join(dir, 'plan.json'), JSON.stringify(plan))
    if (overview) writeFileSync(path.join(dir, 'overview.json'), '{}')
  }
  set('usbos-v13', { city: 'USBOS', run: 'run-v13', tiles: [{}, {}] }, true)
  set('ussfo-v13', { city: 'USSFO', run: 'run-v13', tiles: [] }, false)
  mkdirSync(path.join(root, 'artifacts', 'bulk', 'no-plan'), { recursive: true })
  return root
}

test('lists the sets with a plan, filtered by city', () => {
  const dir = modelDir()
  assert.deepEqual(listBulkSets(dir).map((s) => s.set), ['usbos-v13', 'ussfo-v13'])
  const [boston] = listBulkSets(dir, 'usbos')
  assert.equal(boston.run, 'run-v13')
  assert.equal(boston.plannedTiles, 2)
  assert.ok(boston.overviewAt)
  assert.equal(listBulkSets(dir, 'USSFO')[0].overviewAt, null)
})

test('serves only a listed set with an overview', () => {
  const dir = modelDir()
  assert.ok(bulkOverviewPath(dir, 'usbos-v13')?.endsWith('overview.json'))
  assert.equal(bulkOverviewPath(dir, 'ussfo-v13'), null)
  assert.equal(bulkOverviewPath(dir, '../usbos-v13'), null)
  assert.equal(bulkOverviewPath(dir, 'no-plan'), null)
})

test('no bulk directory is no sets', () => {
  assert.deepEqual(listBulkSets(mkdtempSync(path.join(tmpdir(), 'bulk-empty-'))), [])
})
