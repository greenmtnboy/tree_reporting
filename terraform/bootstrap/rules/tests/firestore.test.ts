import { readFileSync } from 'node:fs'
import path from 'node:path'
import { after, before, beforeEach, describe, test } from 'node:test'
import assert from 'node:assert/strict'

import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from '@firebase/rules-unit-testing'
import {
  collection,
  doc,
  getDoc,
  increment,
  serverTimestamp,
  setDoc,
  Timestamp,
  writeBatch,
  type Firestore,
} from 'firebase/firestore'

const RULES = readFileSync(path.join(import.meta.dirname, '..', 'firestore.rules'), 'utf8')

let env: RulesTestEnvironment

before(async () => {
  env = await initializeTestEnvironment({ projectId: 'demo-tree-rules', firestore: { rules: RULES } })
})
after(async () => {
  await env?.cleanup()
})
beforeEach(async () => {
  await env.clearFirestore()
})

const as = (uid: string) => env.authenticatedContext(uid).firestore() as unknown as Firestore
const anon = () => env.unauthenticatedContext().firestore() as unknown as Firestore
const seed = (fn: (db: Firestore) => Promise<unknown>) =>
  env.withSecurityRulesDisabled((ctx) => fn(ctx.firestore() as unknown as Firestore).then(() => undefined))

// Same escape as treeStatsKey() in useSubmissions.ts and treeDocKey() in the reviewer.
const treeKey = (treeId: string) => treeId.replace(/%/g, '%25').replace(/\//g, '%2F')

/** A check-in document as recordCheckin() writes it. */
function checkinDoc(uid: string, treeId: string, extra: Record<string, unknown> = {}) {
  return {
    userId: uid,
    treeId,
    lat: 37.7749,
    lng: -122.4194,
    treeLat: 37.775,
    treeLng: -122.4195,
    city: 'USSFO',
    distanceMeters: 12,
    photoPath: null,
    species: 'Quercus agrifolia',
    treeForm: null,
    dbhInches: 14,
    plantYear: null,
    speciesCityCount: 3,
    at: serverTimestamp(),
    ...extra,
  }
}

/** recordCheckin()'s batch: the check-in, and when it counts, the bump and the marker. */
function checkinBatch(
  uid: string,
  treeId: string,
  opts: { count?: boolean; statsKey?: string; lastCheckinId?: string; bump?: unknown; checkin?: Record<string, unknown> | null } = {},
) {
  const db = as(uid)
  const batch = writeBatch(db)
  const checkinRef = doc(collection(db, 'checkins'))
  if (opts.checkin !== null) batch.set(checkinRef, checkinDoc(uid, treeId, opts.checkin ?? {}))
  if (opts.count ?? true) {
    const lastCheckinId = opts.lastCheckinId ?? checkinRef.id
    batch.set(
      doc(db, 'treeCheckinStats', opts.statsKey ?? treeKey(treeId)),
      { treeId, count: opts.bump ?? increment(1), lastCheckinId },
      { merge: true },
    )
    batch.set(doc(db, 'treeCheckinMarkers', `${uid}_${treeKey(treeId)}`), {
      userId: uid,
      treeId,
      lastCheckinId,
      countedAt: serverTimestamp(),
    })
  }
  return { db, batch, checkinId: checkinRef.id }
}

async function countFor(treeId: string): Promise<number | undefined> {
  let count: number | undefined
  await seed(async (db) => {
    count = (await getDoc(doc(db, 'treeCheckinStats', treeKey(treeId)))).data()?.count
  })
  return count
}

describe('check-ins', () => {
  test('a check-in shaped like the client writes it is accepted', async () => {
    await assertSucceeds(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('alice', 'sf-1')))
  })

  test('a photo offered for the tree enters review from the owner\'s folder', async () => {
    await assertSucceeds(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('alice', 'sf-1', {
      photoPath: 'checkins/alice/p1.jpeg',
      photoReview: 'pending',
    })))
  })

  test('a photo path outside the owner\'s check-in folder is refused', async () => {
    for (const photoPath of ['checkins/bob/p1.jpeg', 'submissions/alice/s.jpeg', 'modifications/alice/m.jpeg', 'checkins/alice/x/y.jpeg']) {
      await assertFails(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('alice', 'sf-1', {
        photoPath,
        photoReview: 'pending',
      })))
    }
  })

  test('a client cannot mark its own photo as already reviewed', async () => {
    for (const photoReview of ['published', 'rejected', null]) {
      await assertFails(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('alice', 'sf-1', {
        photoPath: 'checkins/alice/p1.jpeg',
        photoReview,
      })))
    }
  })

  test('coordinates that are not numbers are refused', async () => {
    await assertFails(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('alice', 'sf-1', {
      treeLat: '1"><img src=x onerror=alert(1)>',
    })))
    await assertFails(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('alice', 'sf-1', { lat: 91 })))
  })

  test('fields the client never writes are refused', async () => {
    await assertFails(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('alice', 'sf-1', { id: 'other' })))
  })

  test('a check-in for someone else is refused', async () => {
    await assertFails(setDoc(doc(as('alice'), 'checkins', 'c1'), checkinDoc('bob', 'sf-1')))
  })
})

describe('the public check-in counter', () => {
  test('a first check-in creates the count at one', async () => {
    await assertSucceeds(checkinBatch('alice', 'sf-1').batch.commit())
    assert.equal(await countFor('sf-1'), 1)
  })

  test('the same person checking in again within 20 hours does not count again', async () => {
    await assertSucceeds(checkinBatch('alice', 'sf-1').batch.commit())
    await assertFails(checkinBatch('alice', 'sf-1').batch.commit())
    // ...but the check-in itself is still theirs to make.
    await assertSucceeds(checkinBatch('alice', 'sf-1', { count: false }).batch.commit())
    assert.equal(await countFor('sf-1'), 1)
  })

  test('someone else checking in counts', async () => {
    await assertSucceeds(checkinBatch('alice', 'sf-1').batch.commit())
    await assertSucceeds(checkinBatch('bob', 'sf-1').batch.commit())
    assert.equal(await countFor('sf-1'), 2)
  })

  test('the same person counts again once 20 hours have passed', async () => {
    const earlier = Timestamp.fromMillis(Date.now() - 21 * 60 * 60 * 1000)
    await seed(async (db) => {
      await setDoc(doc(db, 'treeCheckinStats', 'sf-1'), { treeId: 'sf-1', count: 1, lastCheckinId: 'old' })
      await setDoc(doc(db, 'treeCheckinMarkers', 'alice_sf-1'), {
        userId: 'alice', treeId: 'sf-1', lastCheckinId: 'old', countedAt: earlier,
      })
    })
    await assertSucceeds(checkinBatch('alice', 'sf-1').batch.commit())
    assert.equal(await countFor('sf-1'), 2)
  })

  test('a bump without the check-in it names is refused', async () => {
    await assertFails(checkinBatch('alice', 'sf-1', { checkin: null }).batch.commit())
  })

  test('a bump naming an existing check-in is refused', async () => {
    await seed((db) => setDoc(doc(db, 'checkins', 'old'), { userId: 'alice', treeId: 'sf-1', at: Timestamp.now() }))
    await assertFails(checkinBatch('alice', 'sf-1', { checkin: null, lastCheckinId: 'old' }).batch.commit())
  })

  test('a bump by more than one is refused', async () => {
    await assertFails(checkinBatch('alice', 'sf-1', { bump: increment(2) }).batch.commit())
    await seed((db) => setDoc(doc(db, 'treeCheckinStats', 'sf-1'), { treeId: 'sf-1', count: 5, lastCheckinId: 'x' }))
    await assertFails(checkinBatch('alice', 'sf-1', { bump: 1000 }).batch.commit())
  })

  test('a bump for a different tree than the check-in is refused', async () => {
    await assertFails(checkinBatch('alice', 'sf-1', { checkin: { treeId: 'sf-2' } }).batch.commit())
  })

  test('a counter under another tree\'s key is refused', async () => {
    await assertFails(checkinBatch('alice', 'sf-1', { statsKey: 'sf-2' }).batch.commit())
  })

  test('tree ids with slashes and percents use the shared escape', async () => {
    const treeId = 'osm/node/50%off'
    assert.equal(treeKey(treeId), 'osm%2Fnode%2F50%25off')
    await assertSucceeds(checkinBatch('alice', treeId).batch.commit())
    assert.equal(await countFor(treeId), 1)
    // An unescaped percent is a different key, and refused.
    await assertFails(checkinBatch('bob', treeId, { statsKey: 'osm%2Fnode%2F50%off' }).batch.commit())
  })

  test('a marker cannot be moved on its own', async () => {
    await assertFails(setDoc(doc(as('alice'), 'treeCheckinMarkers', 'alice_sf-1'), {
      userId: 'alice', treeId: 'sf-1', lastCheckinId: 'c1', countedAt: serverTimestamp(),
    }))
  })

  test('a marker cannot be written for someone else', async () => {
    const db = as('alice')
    const batch = writeBatch(db)
    const checkinRef = doc(collection(db, 'checkins'))
    batch.set(checkinRef, checkinDoc('alice', 'sf-1'))
    batch.set(doc(db, 'treeCheckinStats', 'sf-1'), { treeId: 'sf-1', count: increment(1), lastCheckinId: checkinRef.id }, { merge: true })
    batch.set(doc(db, 'treeCheckinMarkers', 'bob_sf-1'), {
      userId: 'bob', treeId: 'sf-1', lastCheckinId: checkinRef.id, countedAt: serverTimestamp(),
    })
    await assertFails(batch.commit())
  })

  test('the count is public; markers are only their owner\'s', async () => {
    await assertSucceeds(checkinBatch('alice', 'sf-1').batch.commit())
    await assertSucceeds(getDoc(doc(anon(), 'treeCheckinStats', 'sf-1')))
    await assertSucceeds(getDoc(doc(as('alice'), 'treeCheckinMarkers', 'alice_sf-1')))
    await assertFails(getDoc(doc(as('bob'), 'treeCheckinMarkers', 'alice_sf-1')))
    await assertFails(getDoc(doc(anon(), 'treeCheckinMarkers', 'alice_sf-1')))
  })

  test('the counter carries no timestamp', async () => {
    const { db, batch, checkinId } = checkinBatch('alice', 'sf-1', { count: false })
    batch.set(doc(db, 'treeCheckinStats', 'sf-1'), {
      treeId: 'sf-1', count: increment(1), lastCheckinId: checkinId, lastCheckinAt: serverTimestamp(),
    }, { merge: true })
    batch.set(doc(db, 'treeCheckinMarkers', 'alice_sf-1'), {
      userId: 'alice', treeId: 'sf-1', lastCheckinId: checkinId, countedAt: serverTimestamp(),
    })
    await assertFails(batch.commit())
  })
})

/** A modification as submitTreeModification() writes it. */
function modificationDoc(uid: string, extra: Record<string, unknown> = {}) {
  return {
    userId: uid,
    kind: 'update',
    treeId: 'sf-1',
    city: 'USSFO',
    treeLat: 37.775,
    treeLng: -122.4195,
    userLat: 37.7749,
    userLng: -122.4194,
    distanceMeters: 8,
    currentSpecies: 'Quercus agrifolia',
    currentDbhInches: 14,
    missingReason: null,
    proposedLat: 37.7751,
    proposedLng: -122.4196,
    proposedSpecies: null,
    proposedDbhInches: null,
    notes: 'Pin is in the road',
    photoPath: null,
    submittedAt: serverTimestamp(),
    status: 'pending',
    ...extra,
  }
}

function modificationBatch(db: Firestore, uid: string, extra: Record<string, unknown> = {}, withMarker = true) {
  const batch = writeBatch(db)
  const ref = doc(collection(db, 'treeModifications'))
  batch.set(ref, modificationDoc(uid, extra))
  if (withMarker) {
    batch.set(doc(db, 'modificationRateLimits', uid), {
      userId: uid,
      lastModificationId: ref.id,
      submittedAt: serverTimestamp(),
    })
  }
  return batch
}

describe('tree modifications', () => {
  test('an update and a missing report shaped like the client writes them are accepted', async () => {
    await assertSucceeds(modificationBatch(as('alice'), 'alice').commit())
    await assertSucceeds(modificationBatch(as('bob'), 'bob', {
      kind: 'missing', missingReason: 'stump', proposedLat: null, proposedLng: null,
    }).commit())
  })

  test('a report without its rate-limit marker is refused', async () => {
    await assertFails(modificationBatch(as('alice'), 'alice', {}, false).commit())
  })

  test('a second report within 30 seconds is refused', async () => {
    await assertSucceeds(modificationBatch(as('alice'), 'alice').commit())
    await assertFails(modificationBatch(as('alice'), 'alice').commit())
  })

  test('a missing reason outside the fixed set is refused', async () => {
    for (const missingReason of ['burned', 'x'.repeat(5000), 7]) {
      await assertFails(modificationBatch(as('alice'), 'alice', { kind: 'missing', missingReason }).commit())
    }
  })

  test('fields the client never writes are refused', async () => {
    await assertFails(modificationBatch(as('alice'), 'alice', { id: 'someone-elses' }).commit())
  })

  test('coordinates that are not numbers are refused', async () => {
    await assertFails(modificationBatch(as('alice'), 'alice', { treeLat: '1"><img src=x onerror=alert(1)>' }).commit())
    await assertFails(modificationBatch(as('alice'), 'alice', { proposedLat: 37.7, proposedLng: null }).commit())
  })

  test('a photo path outside the owner\'s modification folder is refused', async () => {
    await assertSucceeds(modificationBatch(as('alice'), 'alice', { photoPath: 'modifications/alice/m1.jpeg' }).commit())
    await assertFails(modificationBatch(as('bob'), 'bob', { photoPath: 'modifications/alice/m1.jpeg' }).commit())
    await assertFails(modificationBatch(as('carol'), 'carol', { photoPath: 'checkins/carol/p.jpeg' }).commit())
  })

  test('only the reporter can read a report', async () => {
    await seed((db) => setDoc(doc(db, 'treeModifications', 'm1'), { userId: 'alice', status: 'pending' }))
    await assertSucceeds(getDoc(doc(as('alice'), 'treeModifications', 'm1')))
    await assertFails(getDoc(doc(as('bob'), 'treeModifications', 'm1')))
  })
})

describe('reviewer-only collections', () => {
  test('tree photos are public to read and closed to clients', async () => {
    await assertSucceeds(getDoc(doc(anon(), 'treePhotos', 'sf-1')))
    await assertFails(setDoc(doc(as('alice'), 'treePhotos', 'sf-1'), { photoUrls: ['x'], count: 1 }))
  })

  test('published modifications are closed to clients', async () => {
    await assertFails(getDoc(doc(as('alice'), 'publishedTreeModifications', 'm1')))
    await assertFails(setDoc(doc(as('alice'), 'publishedTreeModifications', 'm1'), { treeId: 'sf-1' }))
  })
})
