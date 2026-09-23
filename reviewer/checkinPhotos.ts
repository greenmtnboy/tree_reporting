/**
 * Check-in photos a user offered as a public photo of the tree.
 *
 * A check-in photo is private (`checkins/{uid}/…`) unless the user ticked
 * "Submit as a photo of this tree", which records `photoReview: 'pending'` on
 * the check-in. Approval re-encodes the photo into the public bucket and adds
 * its URL to `treePhotos/{treeKey}`, the public document the tree card reads.
 * The check-in itself counted the moment it was made; only the photo waits.
 */

/** How many visitor photos a tree's document keeps, newest first. */
export const MAX_TREE_PHOTOS = 12

/**
 * The Firestore document id for a tree. Must match treeStatsKey() in
 * src/src/composables/useSubmissions.ts and the escape in firestore.rules.
 */
export function treeDocKey(treeId: string): string {
  return treeId.replace(/%/g, '%25').replace(/\//g, '%2F')
}

/**
 * Where a published check-in photo lives in the public bucket. The tree id is
 * reduced to URL-safe characters so the object URL needs no escaping; the
 * check-in id keeps it unique.
 */
export function checkinPhotoObjectPath(treeId: string, checkinId: string): string {
  const safeTree = treeId.replace(/[^A-Za-z0-9._-]/g, '_')
  const safeCheckin = checkinId.replace(/[^A-Za-z0-9_-]/g, '_')
  return `community/tree_photos/${safeTree}/${safeCheckin}.jpg`
}

/** The tree's photo list after publishing `url`: newest first, deduplicated, capped. */
export function nextTreePhotos(
  existing: { photoUrls?: unknown; count?: unknown } | undefined,
  url: string,
): { photoUrls: string[]; count: number } {
  const previous = Array.isArray(existing?.photoUrls) ? existing.photoUrls.map(String) : []
  const alreadyThere = previous.includes(url)
  const photoUrls = [url, ...previous.filter((u) => u !== url)].slice(0, MAX_TREE_PHOTOS)
  const previousCount = Number(existing?.count ?? previous.length)
  const count = (Number.isFinite(previousCount) ? previousCount : previous.length) + (alreadyThere ? 0 : 1)
  return { photoUrls, count }
}

export type CheckinPhotoReview = 'pending' | 'published' | 'rejected'

export type CheckinPhotoRecord = {
  userId?: unknown
  treeId?: unknown
  photoPath?: unknown
  photoReview?: CheckinPhotoReview
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

/**
 * True when `path` is a single object directly in `{folder}/{userId}/`. The
 * rules hold new documents to this; the reviewer checks it again so a document
 * written before those rules can never make it publish someone else's upload.
 */
export function isOwnUpload(path: unknown, folder: string, userId: unknown): path is string {
  if (typeof path !== 'string' || typeof userId !== 'string' || !userId) return false
  const prefix = `${folder}/${userId}/`
  if (!path.startsWith(prefix)) return false
  const name = path.slice(prefix.length)
  return name.length > 0 && !name.includes('/') && name !== '.' && name !== '..'
}

/** Throw unless a check-in's photo can be published; returns what publishing needs. */
export function assertCheckinPhotoPublishable(checkin: CheckinPhotoRecord | undefined): {
  photoPath: string
  treeId: string
} {
  if (!checkin) throw new Error('Check-in not found')
  if (checkin.photoReview !== 'pending') throw new Error(`Photo is already ${checkin.photoReview ?? 'not offered'}`)
  if (typeof checkin.treeId !== 'string' || !checkin.treeId) throw new Error('Check-in has no tree id')
  if (!checkin.photoPath) throw new Error('Check-in has no photo')
  if (!isOwnUpload(checkin.photoPath, 'checkins', checkin.userId)) {
    throw new Error("Check-in photo is not in its own user's check-in folder")
  }
  return { photoPath: checkin.photoPath, treeId: checkin.treeId }
}

/**
 * The review queue's row for a check-in. Every field is typed here rather than
 * passed through, because the page renders them into markup.
 */
export function checkinPhotoListItem(
  id: string,
  data: Record<string, unknown> & { at?: { toDate(): Date } },
  photoUrl: (path: string) => string,
) {
  const photoPath = isOwnUpload(data.photoPath, 'checkins', data.userId) ? data.photoPath : null
  return {
    id,
    treeId: stringOrNull(data.treeId),
    city: stringOrNull(data.city),
    species: stringOrNull(data.species),
    treeLat: finiteOrNull(data.treeLat),
    treeLng: finiteOrNull(data.treeLng),
    distanceMeters: finiteOrNull(data.distanceMeters),
    userId: stringOrNull(data.userId),
    at: data.at?.toDate().toISOString() ?? null,
    photoUrl: photoPath ? photoUrl(photoPath) : null,
  }
}
