/**
 * Test-only data seam for the Playwright suite.
 *
 * Achievements are derived from a signed-in user's Firestore contributions, so
 * nothing in the badge UI can be reached from a browser without either a
 * Firestore emulator or a seam. This is the seam: an e2e build reads a fixture
 * object off `window.__treeE2E` — injected by `page.addInitScript` before any
 * app code runs — and serves it in place of the auth session, the two
 * contribution list queries, and the submission photo URLs.
 *
 * It cannot reach production. `import.meta.env.VITE_E2E` is statically replaced
 * at build time, so `e2eEnabled` is the literal `false` in a normal build and
 * every branch guarded by it is dead code. Only `pnpm build:e2e` (which loads
 * `.env.e2e`) turns it on.
 */
import type { User } from 'firebase/auth'
import type { Checkin, Submission, SubmissionStatus } from '../composables/useSubmissions'

export const e2eEnabled = import.meta.env.VITE_E2E === '1'

export interface E2EUserFixture {
  uid: string
  displayName?: string | null
  email?: string | null
  isAnonymous?: boolean
  /** e.g. `['google.com']` to model a Google-linked account. */
  providerIds?: string[]
}

export interface E2ESubmissionFixture {
  id?: string
  city: string
  species?: string | null
  /** ISO 8601. Without a zone suffix this is read as browser-local time. */
  submittedAt?: string | null
  /** Main photo plus extras; 3 or more earns the multi-photo badge. */
  photoCount?: number
  status?: SubmissionStatus
  lat?: number
  lng?: number
}

export interface E2ECheckinFixture {
  id?: string
  treeId?: string
  city: string
  /** ISO 8601. Without a zone suffix this is read as browser-local time. */
  at?: string | null
  hasPhoto?: boolean
  species?: string | null
  treeForm?: string | null
  dbhInches?: number | null
  plantYear?: number | null
  speciesCityCount?: number | null
  distanceMeters?: number | null
}

export interface E2EFixtures {
  /** `null` models a signed-out visitor; omitting it leaves real auth in charge. */
  user?: E2EUserFixture | null
  submissions?: E2ESubmissionFixture[]
  checkins?: E2ECheckinFixture[]
}

declare global {
  interface Window {
    __treeE2E?: E2EFixtures
  }
}

/** A 1x1 transparent PNG, so seeded thumbnails render without a network call. */
const PLACEHOLDER_PHOTO =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAoMBgDTD2qgAAAAASUVORK5CYII='

export function e2eFixtures(): E2EFixtures | null {
  if (!e2eEnabled || typeof window === 'undefined') return null
  return window.__treeE2E ?? null
}

function parseDate(value?: string | null): Date | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

/**
 * The seeded auth session: a `User` when signed in, `null` when the fixture
 * models a signed-out visitor, and `undefined` when no fixture applies — the
 * caller must fall through to real Firebase auth only in that last case.
 */
export function e2eUser(): User | null | undefined {
  const fixtures = e2eFixtures()
  if (!fixtures || fixtures.user === undefined) return undefined
  if (fixtures.user === null) return null
  const u = fixtures.user
  // Only the fields the profile and contribution views read.
  return {
    uid: u.uid,
    displayName: u.displayName ?? null,
    email: u.email ?? null,
    isAnonymous: u.isAnonymous ?? true,
    providerData: (u.providerIds ?? []).map((providerId) => ({ providerId })),
  } as unknown as User
}

export function e2eSubmissions(): Submission[] | null {
  const fixtures = e2eFixtures()
  if (!fixtures || fixtures.user == null) return null
  const uid = fixtures.user.uid
  return (fixtures.submissions ?? []).map((s, i) => {
    const extras = Math.max(0, (s.photoCount ?? 1) - 1)
    return {
      id: s.id ?? `e2e-submission-${i}`,
      userId: uid,
      city: s.city,
      photoPath: `e2e/${i}.jpg`,
      additionalPhotoPaths: Array.from({ length: extras }, (_, n) => `e2e/${i}-${n + 1}.jpg`),
      initialLat: s.lat ?? 37.7749,
      initialLng: s.lng ?? -122.4194,
      initialAccuracy: null,
      lat: s.lat ?? 37.7749,
      lng: s.lng ?? -122.4194,
      refinedByUser: false,
      species: s.species ?? null,
      notes: null,
      submittedAt: parseDate(s.submittedAt),
      status: s.status ?? 'pending',
    }
  })
}

export function e2eCheckins(): Checkin[] | null {
  const fixtures = e2eFixtures()
  if (!fixtures || fixtures.user == null) return null
  const uid = fixtures.user.uid
  return (fixtures.checkins ?? []).map((c, i) => ({
    id: c.id ?? `e2e-checkin-${i}`,
    userId: uid,
    treeId: c.treeId ?? `e2e-tree-${i}`,
    lat: 37.7749,
    lng: -122.4194,
    city: c.city,
    distanceMeters: c.distanceMeters ?? 12,
    photoPath: c.hasPhoto ? `e2e/checkin-${i}.jpg` : null,
    at: parseDate(c.at),
    species: c.species ?? null,
    treeForm: c.treeForm ?? null,
    dbhInches: c.dbhInches ?? null,
    plantYear: c.plantYear ?? null,
    speciesCityCount: c.speciesCityCount ?? null,
  }))
}

/** Seeded photo paths resolve locally; anything else falls through to Storage. */
export function e2ePhotoUrl(photoPath: string): string | null {
  if (!e2eFixtures() || !photoPath.startsWith('e2e/')) return null
  return PLACEHOLDER_PHOTO
}
