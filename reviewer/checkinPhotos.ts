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
