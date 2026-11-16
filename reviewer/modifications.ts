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
 * - `update`: a corrected position and/or species and/or DBH, and/or a
 *   notable tree's name, blurb and source. A field the user did not change is
 *   null and means "keep what the source says".
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
  proposedTreeName?: string | null
  proposedSignificance?: string | null
  proposedSignificanceSource?: string | null
  // Fields the reporter says are wrong with no better value: blank them.
  clearFields?: unknown
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
  // A notable tree's name, why it is notable, and where that is from
  // (docs/NAMED_TREES_AND_CROWN_POSITION.md); data/raw/tree_modification_info.py
  // reads them.
  treeName: string | null
  significance: string | null
  significanceSource: string | null
  // Fields to publish blank, by name. A cleared field carries no value.
  clearFields: ClearableField[]
  publishedAt: string | null
}

// Fields a report can clear (src/src/composables/useSubmissions.ts, and the
// Firestore rules, keep the same list).
export const CLEARABLE_FIELDS = ['dbhInches'] as const
// Fields the reviewer can also clear: a notable tree's text. A public report
// can propose a name but never remove one, so the rules do not list these.
export const REVIEWER_CLEARABLE_FIELDS = [...CLEARABLE_FIELDS, 'treeName', 'significance', 'significanceSource'] as const
export type ClearableField = (typeof REVIEWER_CLEARABLE_FIELDS)[number]

/** The clear names a client document may carry. */
export function clearableFields(value: unknown): ClearableField[] {
  if (!Array.isArray(value)) return []
  return CLEARABLE_FIELDS.filter(field => value.includes(field))
}

/** The clear names a published (reviewer-written) document may carry. */
export function publishedClearFields(value: unknown): ClearableField[] {
  if (!Array.isArray(value)) return []
  return REVIEWER_CLEARABLE_FIELDS.filter(field => value.includes(field))
}

// The longest name, blurb and source a report can publish; the client form,
// the Firestore rules and the ingest hold the same limits.
export const NOTABLE_TEXT_LIMITS = { treeName: 60, significance: 500, significanceSource: 300 } as const
export type NotableTextField = keyof typeof NOTABLE_TEXT_LIMITS
export const NOTABLE_TEXT_FIELDS = Object.keys(NOTABLE_TEXT_LIMITS) as NotableTextField[]

/** Free text, whitespace collapsed; null when nothing is left. Never cut: the caller checks the limit. */
export function cleanText(value: unknown): string | null {
  if (typeof value !== 'string') return null
  return value.split(/\s+/).filter(Boolean).join(' ') || null
}

/** Throw unless `text` fits its field's limit. */
function assertTextFits(field: NotableTextField, text: string | null): void {
  if (text != null && text.length > NOTABLE_TEXT_LIMITS[field]) {
    throw new Error(`${field} is ${text.length} characters; the limit is ${NOTABLE_TEXT_LIMITS[field]}`)
  }
}

/** A source is a link or a register's name; a link must be http(s). */
function assertSourceIsSafe(source: string | null): void {
  if (source == null || !/^[a-z][a-z0-9+.-]*:/i.test(source)) return
  if (!/^https?:\/\//i.test(source)) throw new Error('A source link must be http or https')
}

/**
 * The notable text a reviewer approves: the report's proposal, or the
 * reviewer's edit of it when one is given (an edit to blank drops that
 * field from the change, it does not clear the tree's).
 */
export function approvedNotableText(
  modification: Pick<PendingModification, 'proposedTreeName' | 'proposedSignificance' | 'proposedSignificanceSource'>,
  edits: Partial<Record<NotableTextField, unknown>> = {},
): Record<NotableTextField, string | null> {
  const proposed: Record<NotableTextField, unknown> = {
    treeName: modification.proposedTreeName,
    significance: modification.proposedSignificance,
    significanceSource: modification.proposedSignificanceSource,
  }
  const text = {} as Record<NotableTextField, string | null>
  for (const field of NOTABLE_TEXT_FIELDS) {
    text[field] = cleanText(field in edits ? edits[field] : proposed[field])
    assertTextFits(field, text[field])
  }
  assertSourceIsSafe(text.significanceSource)
  if (text.significanceSource != null && text.treeName == null && text.significance == null) {
    throw new Error('A source needs a name or a blurb to attribute')
  }
  return text
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * A reviewer-authored notable change with no report behind it: name a tree,
 * say why it matters, or clear what it shows. Returns the published
 * document's fields (without the timestamps).
 */
export function authoredNotableModification(body: unknown): {
  treeId: string
  city: string
  kind: 'update'
  treeName: string | null
  significance: string | null
  significanceSource: string | null
  clearFields: ClearableField[]
} {
  const input = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  const treeId = cleanText(input.treeId)
  const city = cleanText(input.city)?.toUpperCase() ?? ''
  if (!treeId) throw new Error('A tree id is required')
  if (!CITY_CODES.has(city)) throw new Error(`${city || 'A blank city'} is not a supported city code`)
  const clear = publishedClearFields(input.clearFields).filter(field => field !== 'dbhInches')
  const edits: Partial<Record<NotableTextField, unknown>> = {}
  for (const field of NOTABLE_TEXT_FIELDS) edits[field] = clear.includes(field) ? null : input[field]
  const text = approvedNotableText({}, edits)
  if (clear.length === 0 && NOTABLE_TEXT_FIELDS.every(field => text[field] == null)) {
    throw new Error('Give a name, a blurb, or a field to clear')
  }
  return { treeId, city, kind: 'update', ...text, clearFields: clear }
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
  const cleared = clearableFields(modification.clearFields)
  if (cleared.includes('dbhInches') && dbh != null) throw new Error('Proposed DBH cannot be both a value and cleared')

  const species = modification.proposedSpecies?.trim() || null
  const text = approvedNotableText(modification)
  const named = text.treeName != null || text.significance != null
  if (lat == null && dbh == null && !cleared.length && species == null && !named) throw new Error('Update proposes no change')
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
  const cleared = isUpdate ? publishedClearFields(data.clearFields) : []
  // A cleared field carries no value; otherwise the approved text, capped
  // again so a hand-edited document cannot publish more than the limit.
  const text = (field: NotableTextField) =>
    isUpdate && !cleared.includes(field) ? cleanText(data[field])?.slice(0, NOTABLE_TEXT_LIMITS[field]) ?? null : null
  return {
    modificationId,
    treeId: String(data.treeId ?? ''),
    city: String(data.city ?? '').toUpperCase(),
    kind,
    missingReason: kind === 'missing' && isMissingReason(data.missingReason) ? data.missingReason : null,
    latitude: isUpdate ? finiteOrNull(data.latitude) : null,
    longitude: isUpdate ? finiteOrNull(data.longitude) : null,
    species: isUpdate ? species : null,
    dbhInches: isUpdate && !cleared.includes('dbhInches') ? finiteOrNull(data.dbhInches) : null,
    treeName: text('treeName'),
    significance: text('significance'),
    significanceSource: text('significanceSource'),
    clearFields: cleared,
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
    proposedTreeName: stringOrNull(data.proposedTreeName),
    proposedSignificance: stringOrNull(data.proposedSignificance),
    proposedSignificanceSource: stringOrNull(data.proposedSignificanceSource),
    clearFields: clearableFields(data.clearFields),
    notes: stringOrNull(data.notes),
    status: stringOrNull(data.status),
    submittedAt: data.submittedAt?.toDate().toISOString() ?? null,
    photoUrl: photoPath ? photoUrl(photoPath) : null,
  }
}
