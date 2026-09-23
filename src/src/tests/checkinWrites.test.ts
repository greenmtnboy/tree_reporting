import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * Deploy-order safety for the check-in write. The frontend ships on merge; the
 * Firestore rules ship on a separate `terraform apply`. Until the rules admit
 * `treeCheckinStats`, the counter write denies the batch it rides in, and a
 * check-in must still be recorded.
 */

const commit = vi.fn()
const batchSet = vi.fn()
const setDoc = vi.fn()

vi.mock('firebase/firestore', () => ({
  collection: vi.fn((_db: unknown, name: string) => ({ name })),
  doc: vi.fn((parent: { name?: string }, name?: string, id?: string) =>
    name === undefined ? { path: `${parent.name}/auto-id`, id: 'auto-id' } : { path: `${name}/${id}`, id }),
  getDoc: vi.fn(),
  getDocs: vi.fn(),
  increment: vi.fn((n: number) => ({ increment: n })),
  limit: vi.fn(),
  orderBy: vi.fn(),
  query: vi.fn(),
  serverTimestamp: vi.fn(() => 'server-time'),
  setDoc: (...args: unknown[]) => setDoc(...args),
  writeBatch: vi.fn(() => ({ set: batchSet, commit })),
  Timestamp: class {},
  where: vi.fn(),
}))
vi.mock('firebase/storage', () => ({ getDownloadURL: vi.fn(), ref: vi.fn(), uploadBytesResumable: vi.fn() }))
vi.mock('../lib/firebase', () => ({ db: {}, storage: {} }))
vi.mock('../composables/useAuth', () => ({
  signInIfNeeded: vi.fn(async () => ({ uid: 'u1' })),
  useAuth: vi.fn(),
}))

const { recordCheckin, submitTreeModification, treeStatsKey } = await import('../composables/useSubmissions')

const checkin = {
  treeId: 'osm/node/1',
  treeLat: 37.77,
  treeLng: -122.42,
  userLat: 37.77,
  userLng: -122.42,
  distanceMeters: 3,
  city: 'USSFO',
}

function denied(): Error {
  return Object.assign(new Error('Missing or insufficient permissions.'), { code: 'permission-denied' })
}

describe('recordCheckin', () => {
  beforeEach(() => {
    commit.mockReset()
    batchSet.mockReset()
    setDoc.mockReset()
  })

  it('writes the check-in and bumps the counter in one batch', async () => {
    commit.mockResolvedValue(undefined)
    await recordCheckin(checkin)
    const paths = batchSet.mock.calls.map(([ref]) => (ref as { path: string }).path)
    expect(paths).toEqual(['checkins/auto-id', `treeCheckinStats/${treeStatsKey(checkin.treeId)}`])
    expect(batchSet.mock.calls[1][1]).toMatchObject({ treeId: checkin.treeId, lastCheckinId: 'auto-id' })
    expect(setDoc).not.toHaveBeenCalled()
  })

  it('still records the check-in when the rules refuse the counter', async () => {
    commit.mockRejectedValue(denied())
    await expect(recordCheckin(checkin)).resolves.toBe('auto-id')
    expect(setDoc).toHaveBeenCalledTimes(1)
    expect(setDoc.mock.calls[0][0]).toMatchObject({ path: 'checkins/auto-id' })
    expect(setDoc.mock.calls[0][1]).toMatchObject({ treeId: checkin.treeId, userId: 'u1' })
  })

  it('does not paper over other failures', async () => {
    commit.mockRejectedValue(Object.assign(new Error('offline'), { code: 'unavailable' }))
    await expect(recordCheckin(checkin)).rejects.toThrow('offline')
    expect(setDoc).not.toHaveBeenCalled()
  })

  it('only offers a photo for review when the user asked', async () => {
    commit.mockResolvedValue(undefined)
    await recordCheckin(checkin)
    expect(batchSet.mock.calls[0][1]).not.toHaveProperty('photoReview')
  })
})

describe('submitTreeModification', () => {
  it('turns a rules denial into a message a person can act on', async () => {
    commit.mockReset()
    commit.mockRejectedValue(denied())
    await expect(
      submitTreeModification({ ...checkin, kind: 'missing', missingReason: 'stump' }),
    ).rejects.toThrow(/wait a minute and try again/)
  })
})

describe('treeStatsKey', () => {
  it('escapes the characters a document id cannot hold, the way the rules do', () => {
    expect(treeStatsKey('sf-123')).toBe('sf-123')
    expect(treeStatsKey('osm/node/1')).toBe('osm%2Fnode%2F1')
    expect(treeStatsKey('a%2Fb')).toBe('a%252Fb')
  })
})
