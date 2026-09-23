import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  assertCheckinPhotoPublishable,
  checkinPhotoListItem,
  checkinPhotoObjectPath,
  isOwnUpload,
  MAX_TREE_PHOTOS,
  nextTreePhotos,
  treeDocKey,
} from '../checkinPhotos.ts'

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

const localUrl = (p: string) => `/api/photos?path=${encodeURIComponent(p)}`

test('only a single object in the owner\'s own folder counts as their upload', () => {
  assert.equal(isOwnUpload('checkins/u1/a.jpg', 'checkins', 'u1'), true)
  assert.equal(isOwnUpload('checkins/u2/a.jpg', 'checkins', 'u1'), false)
  assert.equal(isOwnUpload('submissions/u1/a.jpg', 'checkins', 'u1'), false)
  assert.equal(isOwnUpload('modifications/u1/a.jpg', 'checkins', 'u1'), false)
  assert.equal(isOwnUpload('checkins/u1/../u2/a.jpg', 'checkins', 'u1'), false)
  assert.equal(isOwnUpload('checkins/u1/', 'checkins', 'u1'), false)
  assert.equal(isOwnUpload('checkins/u1/a.jpg', 'checkins', ''), false)
  assert.equal(isOwnUpload(42, 'checkins', 'u1'), false)
})

test('a pending photo in its own folder is publishable', () => {
  assert.deepEqual(
    assertCheckinPhotoPublishable({ userId: 'u1', treeId: 't/1', photoPath: 'checkins/u1/p.jpg', photoReview: 'pending' }),
    { photoPath: 'checkins/u1/p.jpg', treeId: 't/1' },
  )
})

test('approving a photo twice, or one that was rejected, is refused', () => {
  const base = { userId: 'u1', treeId: 't', photoPath: 'checkins/u1/p.jpg' }
  assert.throws(() => assertCheckinPhotoPublishable({ ...base, photoReview: 'published' }), /already published/)
  assert.throws(() => assertCheckinPhotoPublishable({ ...base, photoReview: 'rejected' }), /already rejected/)
  assert.throws(() => assertCheckinPhotoPublishable(base), /not offered/)
  assert.throws(() => assertCheckinPhotoPublishable(undefined), /not found/)
})

test('a check-in pointing at someone else\'s upload is never published', () => {
  for (const photoPath of ['checkins/u2/p.jpg', 'submissions/u2/s.jpg', 'modifications/u1/m.jpg']) {
    assert.throws(
      () => assertCheckinPhotoPublishable({ userId: 'u1', treeId: 't', photoPath, photoReview: 'pending' }),
      /own user's check-in folder/,
      photoPath,
    )
  }
})

test('the queue row carries only typed values, whatever the document holds', () => {
  const hostile = '1"><img src=x onerror=alert(1)>'
  const row = checkinPhotoListItem('c1', {
    userId: 'u1',
    treeId: 't',
    treeLat: hostile,
    treeLng: 2,
    distanceMeters: hostile,
    city: 7,
    photoPath: 'checkins/u2/p.jpg',
    id: 'someone-else',
  }, localUrl)
  assert.equal(row.id, 'c1')
  assert.equal(row.treeLat, null)
  assert.equal(row.treeLng, 2)
  assert.equal(row.distanceMeters, null)
  assert.equal(row.city, null)
  // Not the owner's upload, so the reviewer is not even shown it.
  assert.equal(row.photoUrl, null)
})
