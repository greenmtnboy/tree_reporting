import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { test } from 'node:test'

import { exportArgs, listRuns, parseExportRequest, resolveMosaic } from '../satelliteModels.ts'

const ITEMS = ['ma_b', 'ma_a']

/** A model directory with two runs over the same NAIP items; only the older run's VRT is local. */
function modelDir(): string {
  const root = mkdtempSync(path.join(tmpdir(), 'imagery-model-'))
  const write = (rel: string, body: unknown) => {
    const file = path.join(root, rel)
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(file, typeof body === 'string' ? body : JSON.stringify(body))
    return file
  }
  for (const [run, mtime] of [['old-30cm-v2', 1_000], ['new-30cm-v12', 2_000]] as const) {
    const config = {
      inventory: { city: 'USBOS' },
      evaluation: { confidence_threshold: 0.35 },
      imagery: { local_raster: `/remote/usbos/2023/usbos-${run}.vrt`, item_ids: ITEMS, resolution_m: 0.3 },
    }
    for (const [cohort, split] of [['validation-usbos', 'validation'], ['train-usbos', 'train'], ['test-usbos', 'test']]) {
      write(`artifacts/runs/${run}/evaluation/${cohort}/evaluation-metadata.json`, {
        city: 'USBOS', cohort, split, config, checkpoint: `/remote/runs/${run}/checkpoints/001.ckpt`,
      })
    }
    write(`artifacts/runs/${run}/evaluation/validation-usbos/inference-coverage.json`, { chip_count: 637 })
    write(`artifacts/runs/${run}/checkpoints/001.ckpt`, '')
    utimesSync(write(`artifacts/runs/${run}/COMPLETE`, 'done'), mtime, mtime)
  }
  mkdirSync(path.join(root, 'artifacts/runs/unfinished/evaluation'), { recursive: true })
  const manifest = (items: string[], resolution: number) => ({ sources: items.map((item_id) => ({ item_id })), raster: { resolution: [resolution, resolution] } })
  write('artifacts/imagery/usbos/2023/usbos-old-30cm-v2.vrt', '<VRTDataset/>')
  write('artifacts/imagery/usbos/2023/usbos-old-30cm-v2.manifest.json', manifest(['ma_a', 'ma_b'], 0.3))
  write('artifacts/imagery/usbos/2023/usbos-60cm.vrt', '<VRTDataset/>')
  write('artifacts/imagery/usbos/2023/usbos-60cm.manifest.json', manifest(['ma_a', 'ma_b'], 0.6))
  return root
}

test('runs are the complete ones, newest first, without their sealed test cohorts', () => {
  const root = modelDir()
  try {
    const runs = listRuns(root)
    assert.deepEqual(runs.map((r) => r.runId), ['new-30cm-v12', 'old-30cm-v2'])
    assert.deepEqual(runs[0].cohorts.map((c) => [c.cohort, c.split, c.chips]), [['train-usbos', 'train', null], ['validation-usbos', 'validation', 637]])
    assert.equal(runs[0].checkpoint, true)
    assert.equal(runs[0].confidenceThreshold, 0.35)
    assert.deepEqual(listRuns(path.join(root, 'nowhere')), [])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test("a run without its own mosaic uses the local one over the same items and resolution", () => {
  const root = modelDir()
  try {
    const own = resolveMosaic(root, 'old-30cm-v2', 'validation-usbos')
    assert.equal(path.basename(own!), 'usbos-old-30cm-v2.vrt')
    const shared = resolveMosaic(root, 'new-30cm-v12', 'validation-usbos')
    assert.equal(path.basename(shared!), 'usbos-old-30cm-v2.vrt', 'the 60 cm mosaic over the same items is not a match')

    // San Francisco's config lists no items: the run VRT's base mosaic name decides.
    const sf = (run: string) => ({ city: 'USSFO', cohort: 'validation', split: 'validation', config: { imagery: { local_raster: `/remote/ussfo-2022-mosaic-${run}-30cm.vrt`, item_ids: [], resolution_m: 0.3 } } })
    const write = (rel: string, body: unknown) => {
      mkdirSync(path.dirname(path.join(root, rel)), { recursive: true })
      writeFileSync(path.join(root, rel), typeof body === 'string' ? body : JSON.stringify(body))
    }
    write('artifacts/runs/new-30cm-v12/evaluation/validation/evaluation-metadata.json', sf('new-30cm-v12'))
    write('artifacts/imagery/ussfo/2022/ussfo-2022-mosaic-old-30cm-v2-30cm.vrt', '<VRTDataset/>')
    write('artifacts/imagery/ussfo/2022/ussfo-2022-mosaic-old-30cm-v2-30cm.manifest.json', { sources: [{ item_id: 'ca_a' }], raster: { resolution: [0.3, 0.3] } })
    write('artifacts/imagery/ussfo/2022/ussfo-2021-mosaic-old-30cm-v2-30cm.vrt', '<VRTDataset/>')
    write('artifacts/imagery/ussfo/2022/ussfo-2021-mosaic-old-30cm-v2-30cm.manifest.json', { sources: [{ item_id: 'ca_b' }], raster: { resolution: [0.3, 0.3] } })
    assert.equal(path.basename(resolveMosaic(root, 'new-30cm-v12', 'validation')!), 'ussfo-2022-mosaic-old-30cm-v2-30cm.vrt')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('an export request names a complete run, one of its cohorts and a plain set name', () => {
  const root = modelDir()
  try {
    const runs = listRuns(root)
    const request = parseExportRequest({ runId: 'new-30cm-v12', cohort: 'validation-usbos', set: 'usbos-30cm-v12', threshold: '0.13', minScore: '' }, runs)
    assert.deepEqual(request, { runId: 'new-30cm-v12', cohort: 'validation-usbos', set: 'usbos-30cm-v12', threshold: 0.13, minScore: undefined })
    assert.throws(() => parseExportRequest({ ...request, runId: 'unfinished' }, runs), /not a complete run/)
    assert.throws(() => parseExportRequest({ ...request, cohort: 'test-usbos' }, runs), /no exportable cohort/)
    assert.throws(() => parseExportRequest({ ...request, set: '../escape' }, runs), /plain directory/)
    assert.throws(() => parseExportRequest({ ...request, threshold: 2 }, runs), /between 0 and 1/)
    const args = exportArgs(request, 'tiles', '/m.vrt', null)
    assert.deepEqual(args.slice(args.indexOf('--run')), [
      '--run', 'new-30cm-v12', '--cohort', 'validation-usbos', '--out', path.resolve('tiles', 'usbos-30cm-v12'),
      '--min-score', '0.1', '--threshold', '0.13', '--raster', '/m.vrt',
    ])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
