import assert from 'node:assert/strict'
import path from 'node:path'
import { test } from 'node:test'

import { parsePoint, pointPredictArgs } from '../satellitePredict.ts'

test('a point is two finite coordinates in range', () => {
  assert.deepEqual(parsePoint({ lat: '42.34', lon: -71.109 }), [42.34, -71.109])
  assert.throws(() => parsePoint({ lat: 91, lon: 0 }), /latitude/)
  assert.throws(() => parsePoint({ lat: 0, lon: 'x' }), /longitude/)
  assert.throws(() => parsePoint(null), /latitude/)
})

test('the script gets the point, the tile directory and only the options given', () => {
  const args = pointPredictArgs([42.34, -71.109], { modelDir: '.', tileDir: 'tiles', threshold: 0.13 })
  assert.deepEqual(args.slice(-6), ['--point', '42.34,-71.109', '--out', path.resolve('tiles'), '--threshold', '0.13'])
  assert.ok(!args.includes('--run'))
  assert.ok(!args.includes('--min-score'))
})
