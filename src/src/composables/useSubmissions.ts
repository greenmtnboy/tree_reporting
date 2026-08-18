import { ref } from 'vue'
import {
  collection,
  doc,
  getDocs,
  limit,
  orderBy,
  query,
  serverTimestamp,
  writeBatch,
  Timestamp,
  where,
  addDoc,
} from 'firebase/firestore'
import {
  getDownloadURL,
  ref as storageRef,
  uploadBytesResumable,
} from 'firebase/storage'
import { db, storage } from '../lib/firebase'
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

export interface CheckinInput {
  treeId: string
  treeLat: number
  treeLng: number
  userLat: number
  userLng: number
  distanceMeters: number
  city: string
  photoBlob?: Blob
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
    species: input.species ?? null,
    treeForm: input.treeForm ?? null,
    dbhInches: input.dbhInches ?? null,
    plantYear: input.plantYear ?? null,
    speciesCityCount: input.speciesCityCount ?? null,
    at: serverTimestamp(),
  }
  const docRef = await addDoc(collection(firestore, 'checkins'), docData)
  return docRef.id
}

export async function listMySubmissions(maxResults = 50): Promise<Submission[]> {
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
  const { storage: bucket } = requireFirebase()
  return getDownloadURL(storageRef(bucket, photoPath))
}

export function useMyContributions() {
  const submissions = ref<Submission[]>([])
  const checkins = ref<Checkin[]>([])
  const loading = ref(false)
  const error = ref<Error | null>(null)
  const { user } = useAuth()

  async function refresh() {
    loading.value = true
    error.value = null
    try {
      const [subs, chks] = await Promise.all([listMySubmissions(), listMyCheckins()])
      submissions.value = subs
      checkins.value = chks
    } catch (e) {
      error.value = e as Error
    } finally {
      loading.value = false
    }
  }

  return { submissions, checkins, loading, error, refresh, user }
}
