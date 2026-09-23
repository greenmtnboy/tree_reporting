import { ref } from 'vue'
import {
  collection,
  doc,
  getDoc,
  getDocs,
  increment,
  limit,
  orderBy,
  query,
  serverTimestamp,
  setDoc,
  writeBatch,
  Timestamp,
  where,
} from 'firebase/firestore'
import {
  getDownloadURL,
  ref as storageRef,
  uploadBytesResumable,
} from 'firebase/storage'
import { db, storage } from '../lib/firebase'
import {
  e2eCheckins,
  e2eEnabled,
  e2eModifications,
  e2ePhotoUrl,
  e2eSubmissions,
  e2eTreeCheckinStats,
  e2eTreePhotos,
} from '../lib/e2eFixtures'
import type { Firestore } from 'firebase/firestore'
import type { FirebaseStorage } from 'firebase/storage'
import { signInIfNeeded, useAuth } from './useAuth'

function requireFirebase(): { db: Firestore; storage: FirebaseStorage } {
  if (!db || !storage) {
    throw new Error('Firebase is not configured — contribution features are unavailable')
  }
  return { db, storage }
}

export type SubmissionStatus = 'pending' | 'published' | 'rejected'

export interface Submission {
  id: string
  userId: string
  city: string
  photoPath: string
  additionalPhotoPaths: string[]
  initialLat: number
  initialLng: number
  initialAccuracy: number | null
  lat: number
  lng: number
  refinedByUser: boolean
  species: string | null
  notes: string | null
  submittedAt: Date | null
  status: SubmissionStatus
}

export interface Checkin {
  id: string
  userId: string
  treeId: string
  lat: number
  lng: number
  city: string
  distanceMeters: number | null
  photoPath: string | null
  // Set when the user offered the photo as a public photo of the tree:
  // 'pending' until the reviewer publishes or rejects it.
  photoReview: PhotoReviewStatus | null
  at: Date | null
  // Tree facts snapshotted at check-in time (null on older check-ins).
  // Achievements read these because the tree's city parquet may not be
  // loaded when they are evaluated.
  species: string | null
  treeForm: string | null
  dbhInches: number | null
  plantYear: number | null
  speciesCityCount: number | null
}

export type PhotoReviewStatus = 'pending' | 'published' | 'rejected'

export interface CheckinInput {
  treeId: string
  treeLat: number
  treeLng: number
  userLat: number
  userLng: number
  distanceMeters: number
  city: string
  photoBlob?: Blob
  // Offer the photo as a public photo of this tree, through the review queue.
  submitPhotoForTree?: boolean
  species?: string | null
  treeForm?: string | null
  dbhInches?: number | null
  plantYear?: number | null
  speciesCityCount?: number | null
  onProgress?: (fraction: number) => void
}

export interface SubmitInput {
  photoBlob: Blob
  additionalPhotoBlobs?: Blob[]
  city: string
  initialLat: number
  initialLng: number
  initialAccuracy: number | null
  lat: number
  lng: number
  refinedByUser: boolean
  species?: string
  notes?: string
  onProgress?: (fraction: number) => void
}

function uploadOne(
  bucket: FirebaseStorage,
  path: string,
  blob: Blob,
  onProgress?: (fraction: number) => void,
): Promise<void> {
  const contentType = blob.type || 'image/jpeg'
  const sref = storageRef(bucket, path)
  const task = uploadBytesResumable(sref, blob, { contentType })
  return new Promise<void>((resolve, reject) => {
    task.on(
      'state_changed',
      (snap) => {
        if (onProgress && snap.totalBytes > 0) {
          onProgress(snap.bytesTransferred / snap.totalBytes)
        }
      },
      (err) => reject(err),
      () => resolve(),
    )
  })
}

function extFor(blob: Blob): string {
  const contentType = blob.type || 'image/jpeg'
  return contentType.split('/')[1] ?? 'jpg'
}

export async function submitPhoto(input: SubmitInput): Promise<string> {
  const { db: firestore, storage: bucket } = requireFirebase()
  const user = await signInIfNeeded()
  const submissionRef = doc(collection(firestore, 'submissions'))
  const submissionId = submissionRef.id

  const allBlobs: Blob[] = [input.photoBlob, ...(input.additionalPhotoBlobs ?? [])]
  const paths = allBlobs.map((blob, i) => {
    const suffix = i === 0 ? '' : `-${i}`
    return `submissions/${user.uid}/${submissionId}${suffix}.${extFor(blob)}`
  })

  const fractions = allBlobs.map(() => 0)
  const reportProgress = () => {
    if (!input.onProgress) return
    const total = fractions.reduce((sum, f) => sum + f, 0) / fractions.length
    input.onProgress(total)
  }

  await Promise.all(
    allBlobs.map((blob, i) =>
      uploadOne(bucket, paths[i], blob, (f) => {
        fractions[i] = f
        reportProgress()
      }),
    ),
  )

  const docData = {
    userId: user.uid,
    city: input.city,
    photoPath: paths[0],
    additionalPhotoPaths: paths.slice(1),
    initialLat: input.initialLat,
    initialLng: input.initialLng,
    initialAccuracy: input.initialAccuracy,
    lat: input.lat,
    lng: input.lng,
    refinedByUser: input.refinedByUser,
    species: input.species?.trim() || null,
    notes: input.notes?.trim() || null,
    submittedAt: serverTimestamp(),
    status: 'pending' as SubmissionStatus,
  }
  // The submission and the user's rate-limit marker must be committed together.
  // Firestore rules validate both documents with getAfter(), so bypassing this
  // client-side code cannot bypass the 30-second limit.
  const batch = writeBatch(firestore)
  batch.set(submissionRef, docData)
  batch.set(doc(firestore, 'submissionRateLimits', user.uid), {
    userId: user.uid,
    lastSubmissionId: submissionId,
    submittedAt: serverTimestamp(),
  })
  await batch.commit()
  return submissionId
}

function mapSubmissionDoc(id: string, data: Record<string, unknown>): Submission {
  const submittedAt = data.submittedAt
  return {
    id,
    userId: String(data.userId ?? ''),
    city: String(data.city ?? ''),
    photoPath: String(data.photoPath ?? ''),
    additionalPhotoPaths: Array.isArray(data.additionalPhotoPaths)
      ? (data.additionalPhotoPaths as unknown[]).map((p) => String(p))
      : [],
    initialLat: Number(data.initialLat ?? 0),
    initialLng: Number(data.initialLng ?? 0),
    initialAccuracy:
      data.initialAccuracy === null || data.initialAccuracy === undefined
        ? null
        : Number(data.initialAccuracy),
    lat: Number(data.lat ?? 0),
    lng: Number(data.lng ?? 0),
    refinedByUser: Boolean(data.refinedByUser),
    species: (data.species as string | null) ?? null,
    notes: (data.notes as string | null) ?? null,
    submittedAt: submittedAt instanceof Timestamp ? submittedAt.toDate() : null,
    status: (data.status as SubmissionStatus) ?? 'pending',
  }
}

function mapCheckinDoc(id: string, data: Record<string, unknown>): Checkin {
  const at = data.at
  return {
    id,
    userId: String(data.userId ?? ''),
    treeId: String(data.treeId ?? ''),
    lat: Number(data.lat ?? 0),
    lng: Number(data.lng ?? 0),
    city: String(data.city ?? ''),
    distanceMeters:
      data.distanceMeters === null || data.distanceMeters === undefined
        ? null
        : Number(data.distanceMeters),
    photoPath: (data.photoPath as string | null) ?? null,
    photoReview: (data.photoReview as PhotoReviewStatus | null) ?? null,
    at: at instanceof Timestamp ? at.toDate() : null,
    species: (data.species as string | null) ?? null,
    treeForm: (data.treeForm as string | null) ?? null,
    dbhInches: data.dbhInches == null ? null : Number(data.dbhInches),
    plantYear: data.plantYear == null ? null : Number(data.plantYear),
    speciesCityCount: data.speciesCityCount == null ? null : Number(data.speciesCityCount),
  }
}

export async function recordCheckin(input: CheckinInput): Promise<string> {
  const { db: firestore, storage: bucket } = requireFirebase()
  const user = await signInIfNeeded()

  let photoPath: string | null = null
  if (input.photoBlob) {
    const checkinPhotoId =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const contentType = input.photoBlob.type || 'image/jpeg'
    const ext = contentType.split('/')[1] ?? 'jpg'
    photoPath = `checkins/${user.uid}/${checkinPhotoId}.${ext}`
    const sref = storageRef(bucket, photoPath)
    const uploadTask = uploadBytesResumable(sref, input.photoBlob, { contentType })
    await new Promise<void>((resolve, reject) => {
      uploadTask.on(
        'state_changed',
        (snap) => {
          if (input.onProgress && snap.totalBytes > 0) {
            input.onProgress(snap.bytesTransferred / snap.totalBytes)
          }
        },
        (err) => reject(err),
        () => resolve(),
      )
    })
  }

  const docData = {
    userId: user.uid,
    treeId: input.treeId,
    lat: input.userLat,
    lng: input.userLng,
    treeLat: input.treeLat,
    treeLng: input.treeLng,
    city: input.city,
    distanceMeters: input.distanceMeters,
    photoPath,
    // Only a photo the user explicitly offered ever reaches the review queue;
    // every other check-in photo stays in the user's private folder.
    ...(photoPath && input.submitPhotoForTree ? { photoReview: 'pending' as PhotoReviewStatus } : {}),
    species: input.species ?? null,
    treeForm: input.treeForm ?? null,
    dbhInches: input.dbhInches ?? null,
    plantYear: input.plantYear ?? null,
    speciesCityCount: input.speciesCityCount ?? null,
    at: serverTimestamp(),
  }
  const checkinRef = doc(collection(firestore, 'checkins'))
  // The check-in and the tree's public counter are committed together; the
  // rules only accept a counter bump that names a check-in this batch creates.
  const batch = writeBatch(firestore)
  batch.set(checkinRef, docData)
  batch.set(
    doc(firestore, 'treeCheckinStats', treeStatsKey(input.treeId)),
    {
      treeId: input.treeId,
      count: increment(1),
      lastCheckinId: checkinRef.id,
      lastCheckinAt: serverTimestamp(),
    },
    { merge: true },
  )
  try {
    await batch.commit()
  } catch (err) {
    // Until the rules that admit treeCheckinStats are deployed, the counter
    // write denies the whole batch. The check-in itself must never depend on
    // the counter, so record it alone; the count just misses this one.
    if (!isPermissionDenied(err)) throw err
    console.warn('[checkin] counter write refused; recording the check-in without it', err)
    await setDoc(checkinRef, docData)
  }
  return checkinRef.id
}

function isPermissionDenied(err: unknown): boolean {
  return (err as { code?: unknown } | null)?.code === 'permission-denied'
}

export interface TreeCheckinStats {
  count: number
  lastCheckinAt: Date | null
}

/**
 * Firestore document ids cannot contain `/`, and some source ids might. The
 * rules recompute this same escape from the stored `treeId`, so a counter
 * document can only ever sit under its own tree's key.
 */
export function treeStatsKey(treeId: string): string {
  return treeId.replace(/%/g, '%25').replace(/\//g, '%2F')
}

/**
 * The public, per-tree check-in counter shown on the tree card. Readable
 * without signing in; a tree nobody has checked in to has no document and
 * reads as zero.
 */
export async function getTreeCheckinStats(treeId: string): Promise<TreeCheckinStats> {
  if (e2eEnabled) return e2eTreeCheckinStats(treeId)
  const { db: firestore } = requireFirebase()
  const snap = await getDoc(doc(firestore, 'treeCheckinStats', treeStatsKey(treeId)))
  if (!snap.exists()) return { count: 0, lastCheckinAt: null }
  const data = snap.data()
  return {
    count: Number(data.count ?? 0),
    lastCheckinAt: data.lastCheckinAt instanceof Timestamp ? data.lastCheckinAt.toDate() : null,
  }
}

export interface TreePhotos {
  // Newest first; the reviewer keeps a bounded list.
  photoUrls: string[]
  count: number
}

/**
 * Reviewed visitor photos of a tree, published from check-ins. Public like the
 * check-in counter; written only by the reviewer. A tree with none has no
 * document.
 */
export async function getTreePhotos(treeId: string): Promise<TreePhotos> {
  if (e2eEnabled) return e2eTreePhotos(treeId)
  const { db: firestore } = requireFirebase()
  const snap = await getDoc(doc(firestore, 'treePhotos', treeStatsKey(treeId)))
  if (!snap.exists()) return { photoUrls: [], count: 0 }
  const data = snap.data()
  const photoUrls = Array.isArray(data.photoUrls) ? (data.photoUrls as unknown[]).map(String) : []
  return { photoUrls, count: Number(data.count ?? photoUrls.length) }
}

// --- Tree modifications -------------------------------------------------
//
// A modification is a reviewed proposal against an existing tree, the way a
// submission is a reviewed proposal for a new one: it lands `pending`, and
// only the local reviewer can publish it. `missing` says the tree is not
// there any more; `update` proposes a corrected position and/or properties,
// leaving any field it does not set as the tree already has it.

export type ModificationKind = 'missing' | 'update'
export type MissingReason = 'removed' | 'stump' | 'never-existed' | 'other'

export interface TreeModification {
  id: string
  userId: string
  kind: ModificationKind
  treeId: string
  city: string
  treeLat: number
  treeLng: number
  distanceMeters: number | null
  missingReason: MissingReason | null
  proposedLat: number | null
  proposedLng: number | null
  proposedSpecies: string | null
  proposedDbhInches: number | null
  notes: string | null
  photoPath: string | null
  submittedAt: Date | null
  status: SubmissionStatus
}

export interface ModificationInput {
  kind: ModificationKind
  treeId: string
  city: string
  treeLat: number
  treeLng: number
  userLat: number
  userLng: number
  distanceMeters: number
  // What the tree looked like when the user opened it, so a reviewer sees
  // the proposal against the values it replaces.
  currentSpecies?: string | null
  currentDbhInches?: number | null
  missingReason?: MissingReason
  proposedLat?: number | null
  proposedLng?: number | null
  proposedSpecies?: string | null
  proposedDbhInches?: number | null
  notes?: string | null
  photoBlob?: Blob
  onProgress?: (fraction: number) => void
}

export async function submitTreeModification(input: ModificationInput): Promise<string> {
  const { db: firestore, storage: bucket } = requireFirebase()
  const user = await signInIfNeeded()
  const modificationRef = doc(collection(firestore, 'treeModifications'))
  const modificationId = modificationRef.id

  let photoPath: string | null = null
  if (input.photoBlob) {
    photoPath = `modifications/${user.uid}/${modificationId}.${extFor(input.photoBlob)}`
    await uploadOne(bucket, photoPath, input.photoBlob, input.onProgress)
  }

  const isUpdate = input.kind === 'update'
  const docData = {
    userId: user.uid,
    kind: input.kind,
    treeId: input.treeId,
    city: input.city,
    treeLat: input.treeLat,
    treeLng: input.treeLng,
    userLat: input.userLat,
    userLng: input.userLng,
    distanceMeters: input.distanceMeters,
    currentSpecies: input.currentSpecies ?? null,
    currentDbhInches: input.currentDbhInches ?? null,
    missingReason: isUpdate ? null : (input.missingReason ?? 'other'),
    proposedLat: isUpdate ? (input.proposedLat ?? null) : null,
    proposedLng: isUpdate ? (input.proposedLng ?? null) : null,
    proposedSpecies: isUpdate ? (input.proposedSpecies?.trim() || null) : null,
    proposedDbhInches: isUpdate ? (input.proposedDbhInches ?? null) : null,
    notes: input.notes?.trim() || null,
    photoPath,
    submittedAt: serverTimestamp(),
    status: 'pending' as SubmissionStatus,
  }
  // Same shape as submitPhoto: the rate-limit marker is validated against
  // this document with getAfter(), so it cannot be skipped client-side.
  const batch = writeBatch(firestore)
  batch.set(modificationRef, docData)
  batch.set(doc(firestore, 'modificationRateLimits', user.uid), {
    userId: user.uid,
    lastModificationId: modificationId,
    submittedAt: serverTimestamp(),
  })
  // A denial is also what the 30-second rate limit looks like, and what every
  // report gets before the treeModifications rules are deployed.
  const denied = await batch.commit().then(
    () => false,
    (err: unknown) => {
      if (isPermissionDenied(err)) return true
      throw err
    },
  )
  if (denied) throw new Error("Couldn't send the report — wait a minute and try again.")
  return modificationId
}

function numberOrNull(value: unknown): number | null {
  return value === null || value === undefined ? null : Number(value)
}

function mapModificationDoc(id: string, data: Record<string, unknown>): TreeModification {
  const submittedAt = data.submittedAt
  return {
    id,
    userId: String(data.userId ?? ''),
    kind: data.kind === 'missing' ? 'missing' : 'update',
    treeId: String(data.treeId ?? ''),
    city: String(data.city ?? ''),
    treeLat: Number(data.treeLat ?? 0),
    treeLng: Number(data.treeLng ?? 0),
    distanceMeters: numberOrNull(data.distanceMeters),
    missingReason: (data.missingReason as MissingReason | null) ?? null,
    proposedLat: numberOrNull(data.proposedLat),
    proposedLng: numberOrNull(data.proposedLng),
    proposedSpecies: (data.proposedSpecies as string | null) ?? null,
    proposedDbhInches: numberOrNull(data.proposedDbhInches),
    notes: (data.notes as string | null) ?? null,
    photoPath: (data.photoPath as string | null) ?? null,
    submittedAt: submittedAt instanceof Timestamp ? submittedAt.toDate() : null,
    status: (data.status as SubmissionStatus) ?? 'pending',
  }
}

export async function listMyModifications(maxResults = 50): Promise<TreeModification[]> {
  const seeded = e2eModifications()
  if (seeded) return seeded.slice(0, maxResults)

  const { db: firestore } = requireFirebase()
  const user = await signInIfNeeded()
  const q = query(
    collection(firestore, 'treeModifications'),
    where('userId', '==', user.uid),
    orderBy('submittedAt', 'desc'),
    limit(maxResults),
  )
  const snap = await getDocs(q)
  return snap.docs.map((d) => mapModificationDoc(d.id, d.data()))
}

export async function listMySubmissions(maxResults = 50): Promise<Submission[]> {
  // Playwright fixtures stand in for Firestore; no-op in a normal build.
  const seeded = e2eSubmissions()
  if (seeded) return seeded.slice(0, maxResults)

  const { db: firestore } = requireFirebase()
  const user = await signInIfNeeded()
  const q = query(
    collection(firestore, 'submissions'),
    where('userId', '==', user.uid),
    orderBy('submittedAt', 'desc'),
    limit(maxResults),
  )
  const snap = await getDocs(q)
  return snap.docs.map((d) => mapSubmissionDoc(d.id, d.data()))
}

export async function listMyCheckins(maxResults = 50): Promise<Checkin[]> {
  const seeded = e2eCheckins()
  if (seeded) return seeded.slice(0, maxResults)

  const { db: firestore } = requireFirebase()
  const user = await signInIfNeeded()
  const q = query(
    collection(firestore, 'checkins'),
    where('userId', '==', user.uid),
    orderBy('at', 'desc'),
    limit(maxResults),
  )
  const snap = await getDocs(q)
  return snap.docs.map((d) => mapCheckinDoc(d.id, d.data()))
}

export async function getSubmissionPhotoUrl(photoPath: string): Promise<string> {
  const seeded = e2ePhotoUrl(photoPath)
  if (seeded) return seeded

  const { storage: bucket } = requireFirebase()
  return getDownloadURL(storageRef(bucket, photoPath))
}

export function useMyContributions() {
  const submissions = ref<Submission[]>([])
  const checkins = ref<Checkin[]>([])
  const modifications = ref<TreeModification[]>([])
  const loading = ref(false)
  const error = ref<Error | null>(null)
  const { user } = useAuth()

  async function refresh() {
    loading.value = true
    error.value = null
    try {
      const [subs, chks, mods] = await Promise.all([
        listMySubmissions(),
        listMyCheckins(),
        // Reports are the newest collection; a failure here (e.g. its index
        // still building) must not blank the photos and check-ins above it.
        listMyModifications().catch((e: unknown) => {
          console.warn('[contributions] could not load tree reports', e)
          return [] as TreeModification[]
        }),
      ])
      submissions.value = subs
      checkins.value = chks
      modifications.value = mods
    } catch (e) {
      error.value = e as Error
    } finally {
      loading.value = false
    }
  }

  return { submissions, checkins, modifications, loading, error, refresh, user }
}
