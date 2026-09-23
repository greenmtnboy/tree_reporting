import assert from 'node:assert/strict'
import { test } from 'node:test'

import { checkinPhotoObjectPath, MAX_TREE_PHOTOS, nextTreePhotos, treeDocKey } from '../checkinPhotos.ts'

test('tree doc keys escape slashes and percents the way the client and rules do', () => {
  assert.equal(treeDocKey('sf-123'), 'sf-123')
  assert.equal(treeDocKey('osm/node/1'), 'osm%2Fnode%2F1')
  assert.equal(treeDocKey('a%2Fb'), 'a%252Fb')
})

test('object paths are URL-safe and unique per check-in', () => {
  assert.equal(checkinPhotoObjectPath('osm/node 1', 'abc'), 'community/tree_photos/osm_node_1/abc.jpg')
})

test('a first photo starts the list', () => {
  assert.deepEqual(nextTreePhotos(undefined, 'u1'), { photoUrls: ['u1'], count: 1 })
})

test('newer photos go first and the count keeps growing past the cap', () => {
  let state: { photoUrls: string[]; count: number } | undefined
  for (let i = 0; i < MAX_TREE_PHOTOS + 3; i++) state = nextTreePhotos(state, `u${i}`)
  assert.equal(state!.photoUrls.length, MAX_TREE_PHOTOS)
  assert.equal(state!.photoUrls[0], `u${MAX_TREE_PHOTOS + 2}`)
  assert.equal(state!.count, MAX_TREE_PHOTOS + 3)
})

test('republishing the same photo does not double count it', () => {
  const once = nextTreePhotos(undefined, 'u1')
  assert.deepEqual(nextTreePhotos(once, 'u1'), once)
})
