import { beforeEach, describe, expect, it, vi } from 'vitest'

// recordCheckin() and submitTreeModification() against a recording stand-in
// for Firestore: what each batch holds is what firestore.rules judges, so
// these pin the shapes terraform/bootstrap/rules/tests exercises for real.

type Write = { path: string; data: Record<string, unknown>; options?: unknown }

const state = vi.hoisted(() => ({
  batches: [] as { writes: { path: string; data: Record<string, unknown>; options?: unknown }[] }[],
  commitErrors: [] as unknown[],
  marker: null as null | { countedAt: unknown },
  nextId: 0,
}))

vi.mock('../lib/firebase', () => ({ db: { fake: 'db' }, storage: { fake: 'storage' } }))
vi.mock('./useAuth', () => ({
  signInIfNeeded: async () => ({ uid: 'alice' }),
  useAuth: () => ({}),
}))
vi.mock('firebase/storage', () => ({
  getDownloadURL: vi.fn(),
  ref: (_bucket: unknown, path: string) => ({ path }),
  uploadBytesResumable: () => ({
    on: (_event: string, _progress: unknown, _error: unknown, done: () => void) => done(),
  }),
}))
vi.mock('firebase/firestore', () => {
  class Timestamp {
    constructor(private readonly ms: number) {}
    static fromMillis(ms: number) {
      return new Timestamp(ms)
    }
    toDate() {
      return new Date(this.ms)
    }
  }
  return {
    Timestamp,
    collection: (_db: unknown, name: string) => ({ collection: name }),
    doc: (parent: { collection?: string }, name?: string, id?: string) => {
      if (parent && 'collection' in parent && parent.collection) {
        state.nextId += 1
        return { path: `${parent.collection}/gen${state.nextId}`, id: `gen${state.nextId}` }
      }
      return { path: `${name}/${id}`, id }
    },
    getDoc: async (ref: { path: string }) => ({
      exists: () => ref.path.startsWith('treeCheckinMarkers/') && state.marker != null,
      data: () => (ref.path.startsWith('treeCheckinMarkers/') ? state.marker ?? undefined : undefined),
    }),
    getDocs: vi.fn(),
    increment: (n: number) => ({ increment: n }),
    limit: vi.fn(),
    orderBy: vi.fn(),
    query: vi.fn(),
    serverTimestamp: () => ({ serverTimestamp: true }),
    where: vi.fn(),
    writeBatch: () => {
      const writes: Write[] = []
      const batch = {
        writes,
        set: (ref: { path: string }, data: Record<string, unknown>, options?: unknown) => {
          writes.push({ path: ref.path, data, options })
        },
        commit: async () => {
          state.batches.push(batch)
          const error = state.commitErrors.shift()
          if (error) throw error
        },
      }
      return batch
    },
  }
})

const {
  CHECKIN_COUNT_WINDOW_MS,
  checkinCountsNow,
  recordCheckin,
  submitTreeModification,
  treeStatsKey,
} = await import('./useSubmissions')
const { Timestamp } = await import('firebase/firestore')

const CHECKIN = {
  treeId: 'osm/node/1',
  treeLat: 37.775,
  treeLng: -122.4195,
  userLat: 37.7749,
  userLng: -122.4194,
  distanceMeters: 12,
  city: 'USSFO',
}

beforeEach(() => {
  state.batches = []
  state.commitErrors = []
  state.marker = null
  state.nextId = 0
})

describe('treeStatsKey', () => {
  it('matches the reviewer and rules escape, percent first', () => {
    // Same vectors as reviewer/tests/checkinPhotos.test.ts and the rules tests.
    expect(treeStatsKey('sf-123')).toBe('sf-123')
    expect(treeStatsKey('osm/node/1')).toBe('osm%2Fnode%2F1')
    expect(treeStatsKey('a%2Fb')).toBe('a%252Fb')
    expect(treeStatsKey('osm/node/50%off')).toBe('osm%2Fnode%2F50%25off')
  })
})

describe('checkinCountsNow', () => {
  const now = new Date('2026-09-23T12:00:00Z')
  it('counts a first visit', () => {
    expect(checkinCountsNow(null, now)).toBe(true)
  })
  it('does not count again inside the window, or right at its edge', () => {
    expect(checkinCountsNow(new Date(now.getTime() - 60_000), now)).toBe(false)
    expect(checkinCountsNow(new Date(now.getTime() - CHECKIN_COUNT_WINDOW_MS), now)).toBe(false)
  })
  it('counts once the window and the clock margin have passed', () => {
    expect(checkinCountsNow(new Date(now.getTime() - CHECKIN_COUNT_WINDOW_MS - 10 * 60_000), now)).toBe(true)
  })
})

describe('recordCheckin', () => {
  it('commits the check-in, the bump and the marker together on a first visit', async () => {
    const result = await recordCheckin(CHECKIN)
    expect(result.counted).toBe(true)
    expect(state.batches).toHaveLength(1)
    const [checkin, stats, marker] = state.batches[0].writes
    expect(checkin.path).toBe(`checkins/${result.id}`)
    expect(checkin.data).not.toHaveProperty('photoReview')
    expect(stats.path).toBe('treeCheckinStats/osm%2Fnode%2F1')
    expect(stats.data).toEqual({ treeId: 'osm/node/1', count: { increment: 1 }, lastCheckinId: result.id })
    expect(stats.options).toEqual({ merge: true })
    expect(marker.path).toBe('treeCheckinMarkers/alice_osm%2Fnode%2F1')
    expect(marker.data).toEqual({
      userId: 'alice',
      treeId: 'osm/node/1',
      lastCheckinId: result.id,
      countedAt: { serverTimestamp: true },
    })
  })

  it('writes the check-in alone when this user already counted it today', async () => {
    state.marker = { countedAt: Timestamp.fromMillis(Date.now() - 60 * 60 * 1000) }
    const result = await recordCheckin(CHECKIN)
    expect(result.counted).toBe(false)
    expect(state.batches[0].writes.map((w) => w.path)).toEqual([`checkins/${result.id}`])
  })

  it('falls back to the check-in alone when the rules refuse the bump', async () => {
    state.commitErrors = [Object.assign(new Error('denied'), { code: 'permission-denied' })]
    const result = await recordCheckin(CHECKIN)
    expect(result.counted).toBe(false)
    expect(state.batches).toHaveLength(2)
    expect(state.batches[1].writes.map((w) => w.path)).toEqual([`checkins/${result.id}`])
  })

  it('does not swallow other failures', async () => {
    state.commitErrors = [Object.assign(new Error('offline'), { code: 'unavailable' })]
    await expect(recordCheckin(CHECKIN)).rejects.toThrow('offline')
  })

  it('only queues a photo for review when the user offered it', async () => {
    const photoBlob = new Blob(['x'], { type: 'image/jpeg' })
    await recordCheckin({ ...CHECKIN, photoBlob })
    const kept = state.batches[0].writes[0].data
    expect(kept.photoPath).toMatch(/^checkins\/alice\/[^/]+\.jpeg$/)
    expect(kept).not.toHaveProperty('photoReview')

    state.batches = []
    state.marker = { countedAt: Timestamp.fromMillis(Date.now()) }
    await recordCheckin({ ...CHECKIN, photoBlob, submitPhotoForTree: true })
    expect(state.batches[0].writes[0].data.photoReview).toBe('pending')

    state.batches = []
    await recordCheckin({ ...CHECKIN, submitPhotoForTree: true })
    expect(state.batches[0].writes[0].data).not.toHaveProperty('photoReview')
  })
})

describe('submitTreeModification', () => {
  const REPORT = { ...CHECKIN, treeId: 'sf-1' }

  it('a missing report drops every proposed value', async () => {
    await submitTreeModification({
      ...REPORT,
      kind: 'missing',
      missingReason: 'stump',
      proposedLat: 1,
      proposedLng: 2,
      proposedSpecies: 'Acer rubrum',
      proposedDbhInches: 10,
    })
    const [modification, rateLimit] = state.batches[0].writes
    expect(modification.data).toMatchObject({
      kind: 'missing',
      missingReason: 'stump',
      proposedLat: null,
      proposedLng: null,
      proposedSpecies: null,
      proposedDbhInches: null,
    })
    expect(rateLimit.path).toBe('modificationRateLimits/alice')
    expect(rateLimit.data.lastModificationId).toBe(modification.path.split('/')[1])
  })

  it('an update carries no missing reason, and its photo sits in the modifications folder', async () => {
    await submitTreeModification({
      ...REPORT,
      kind: 'update',
      missingReason: 'removed',
      proposedSpecies: '  Acer rubrum ',
      photoBlob: new Blob(['x'], { type: 'image/png' }),
    })
    const { data, path } = state.batches[0].writes[0]
    expect(data.missingReason).toBeNull()
    expect(data.proposedSpecies).toBe('Acer rubrum')
    expect(data.photoPath).toBe(`modifications/alice/${path.split('/')[1]}.png`)
  })

  it('turns a rules denial (the 30-second limit) into a message a person can act on', async () => {
    state.commitErrors = [Object.assign(new Error('Missing or insufficient permissions.'), { code: 'permission-denied' })]
    await expect(submitTreeModification({ ...REPORT, kind: 'missing', missingReason: 'stump' }))
      .rejects.toThrow(/wait a minute and try again/)
  })
})
