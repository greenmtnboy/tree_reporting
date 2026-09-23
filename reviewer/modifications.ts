import { isOwnUpload } from './checkinPhotos.ts'
import { assertCoordinatesAreInCity, CITY_CODES } from './submissionCity.ts'

/**
 * Tree modifications: reviewed proposals against a tree that is already on
 * the map, the way a submission is a reviewed proposal for a new one.
 *
 * The web client writes them to `treeModifications` as `pending`; approval
 * copies one into `publishedTreeModifications` and rewrites the public export
 * `community/tree_modifications.ndjson` plus its manifest. Like the approved
 * tree export, the pipeline reads the GCS object, never Firestore.
 *
 * - `missing`: the tree is not there (removed, a stump, or never was).
 * - `update`: a corrected position and/or species and/or DBH. A field the
 *   user did not change is null and means "keep what the source says".
 *
 * A tree can collect several approved modifications over time. The export
 * lists all of them in publish order, so a consumer resolves a tree by taking
 * the last non-null value per field, and a later `missing` wins over any
 * earlier `update`.
 */

export type ModificationKind = 'missing' | 'update'
export const MISSING_REASONS = ['removed', 'stump', 'never-existed', 'other'] as const
export type MissingReason = (typeof MISSING_REASONS)[number]

function isMissingReason(value: unknown): value is MissingReason {
  return typeof value === 'string' && (MISSING_REASONS as readonly string[]).includes(value)
}
export type ModificationStatus = 'pending' | 'published' | 'rejected'

export type PendingModification = {
  userId: string
  kind: ModificationKind
  treeId: string
  city: string
  treeLat: number
  treeLng: number
  userLat?: number | null
  userLng?: number | null
  distanceMeters?: number | null
  currentSpecies?: string | null
  currentDbhInches?: number | null
  missingReason?: string | null
  proposedLat?: number | null
  proposedLng?: number | null
  proposedSpecies?: string | null
  proposedDbhInches?: number | null
  notes?: string | null
  photoPath?: string | null
  status: ModificationStatus
}

export type ModificationExportRow = {
  modificationId: string
  treeId: string
  city: string
  kind: ModificationKind
  missingReason: string | null
  latitude: number | null
  longitude: number | null
  species: string | null
  dbhInches: number | null
  publishedAt: string | null
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * Throw unless a pending modification can be published as it stands.
 *
 * The rules only check shape, so this is where the content is held to what the
 * ingest could apply: a known city, a proposed position that is a coordinate
 * pair inside that city, and an `update` that actually changes something.
 */
export function assertModificationPublishable(modification: PendingModification): void {
  if (modification.status !== 'pending') throw new Error(`Modification is already ${modification.status}`)
  if (!CITY_CODES.has(modification.city)) {
    throw new Error(`Modification city ${modification.city} is not a supported city code`)
  }
  if (!modification.treeId) throw new Error('Modification has no tree id')
  if (modification.kind === 'missing') {
    if (modification.missingReason != null && !isMissingReason(modification.missingReason)) {
      throw new Error(`Unknown missing reason ${String(modification.missingReason)}`)
    }
    return
  }
  if (modification.kind !== 'update') throw new Error(`Unknown modification kind ${String(modification.kind)}`)

  const lat = finiteOrNull(modification.proposedLat)
  const lng = finiteOrNull(modification.proposedLng)
  if ((lat == null) !== (lng == null)) throw new Error('Proposed position needs both latitude and longitude')
  if (lat != null && lng != null) assertCoordinatesAreInCity(modification.city, lat, lng)

  const dbh = finiteOrNull(modification.proposedDbhInches)
  if (dbh != null && (dbh <= 0 || dbh > 400)) throw new Error(`Proposed DBH ${dbh} in is out of range`)

  const species = modification.proposedSpecies?.trim() || null
  if (lat == null && dbh == null && species == null) throw new Error('Update proposes no change')
}

/** The public export row: the change itself, with no user, notes, or photo. */
export function modificationExportRow(
  modificationId: string,
  data: Record<string, unknown>,
  publishedAt: string | null,
): ModificationExportRow {
  const kind: ModificationKind = data.kind === 'missing' ? 'missing' : 'update'
  const isUpdate = kind === 'update'
  const species = typeof data.species === 'string' ? data.species.trim() || null : null
  return {
    modificationId,
    treeId: String(data.treeId ?? ''),
    city: String(data.city ?? '').toUpperCase(),
    kind,
    missingReason: kind === 'missing' && isMissingReason(data.missingReason) ? data.missingReason : null,
    latitude: isUpdate ? finiteOrNull(data.latitude) : null,
    longitude: isUpdate ? finiteOrNull(data.longitude) : null,
    species: isUpdate ? species : null,
    dbhInches: isUpdate ? finiteOrNull(data.dbhInches) : null,
    publishedAt,
  }
}

/** Per-city freshness, so a report in one city rebuilds that city alone. */
export function modificationManifest(rows: ModificationExportRow[]): {
  count: number
  latestPublishedAt: string | null
  latestPublishedAtByCity: Record<string, string>
} {
  let latestPublishedAt: string | null = null
  const latestPublishedAtByCity: Record<string, string> = {}
  for (const row of rows) {
    if (!row.publishedAt) continue
    if (!latestPublishedAt || row.publishedAt > latestPublishedAt) latestPublishedAt = row.publishedAt
    const previous = latestPublishedAtByCity[row.city]
    if (!previous || row.publishedAt > previous) latestPublishedAtByCity[row.city] = row.publishedAt
  }
  return { count: rows.length, latestPublishedAt, latestPublishedAtByCity }
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

/**
 * The review queue's row for a modification. Built field by field rather than
 * spread from the document: the id must be the document's own (a stored `id`
 * field would otherwise point Approve at another record), and the page
 * renders every value into markup.
 */
export function modificationListItem(
  id: string,
  data: Record<string, unknown> & { submittedAt?: { toDate(): Date } },
  photoUrl: (path: string) => string,
) {
  const photoPath = isOwnUpload(data.photoPath, 'modifications', data.userId) ? data.photoPath : null
  return {
    id,
    userId: stringOrNull(data.userId),
    kind: data.kind === 'missing' ? 'missing' : 'update',
    treeId: stringOrNull(data.treeId),
    city: stringOrNull(data.city),
    treeLat: finiteOrNull(data.treeLat),
    treeLng: finiteOrNull(data.treeLng),
    distanceMeters: finiteOrNull(data.distanceMeters),
    currentSpecies: stringOrNull(data.currentSpecies),
    currentDbhInches: finiteOrNull(data.currentDbhInches),
    missingReason: isMissingReason(data.missingReason) ? data.missingReason : null,
    proposedLat: finiteOrNull(data.proposedLat),
    proposedLng: finiteOrNull(data.proposedLng),
    proposedSpecies: stringOrNull(data.proposedSpecies),
    proposedDbhInches: finiteOrNull(data.proposedDbhInches),
    notes: stringOrNull(data.notes),
    status: stringOrNull(data.status),
    submittedAt: data.submittedAt?.toDate().toISOString() ?? null,
    photoUrl: photoPath ? photoUrl(photoPath) : null,
  }
}
