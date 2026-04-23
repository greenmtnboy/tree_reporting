import { ref } from 'vue'
import {
  addDoc,
  collection,
  getDocs,
  limit,
  orderBy,
  query,
  serverTimestamp,
  Timestamp,
  where,
} from 'firebase/firestore'
import {
  getDownloadURL,
  ref as storageRef,
  uploadBytesResumable,
} from 'firebase/storage'
import { db, storage } from '../lib/firebase'
import { signInIfNeeded, useAuth } from './useAuth'

export type SubmissionStatus = 'pending' | 'published' | 'rejected'

export interface Submission {
  id: string
  userId: string
  city: string
  photoPath: string
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
  onProgress?: (fraction: number) => void
}

export interface SubmitInput {
  photoBlob: Blob
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

export async function submitPhoto(input: SubmitInput): Promise<string> {
  const user = await signInIfNeeded()
  const submissionId =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`

  const contentType = input.photoBlob.type || 'image/jpeg'
  const ext = contentType.split('/')[1] ?? 'jpg'
  const photoPath = `submissions/${user.uid}/${submissionId}.${ext}`

  const sref = storageRef(storage, photoPath)
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

  const docData = {
    userId: user.uid,
    city: input.city,
    photoPath,
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
  const docRef = await addDoc(collection(db, 'submissions'), docData)
  return docRef.id
}

function mapSubmissionDoc(id: string, data: Record<string, unknown>): Submission {
  const submittedAt = data.submittedAt
  return {
    id,
    userId: String(data.userId ?? ''),
    city: String(data.city ?? ''),
    photoPath: String(data.photoPath ?? ''),
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
  }
}

export async function recordCheckin(input: CheckinInput): Promise<string> {
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
    const sref = storageRef(storage, photoPath)
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
    at: serverTimestamp(),
  }
  const docRef = await addDoc(collection(db, 'checkins'), docData)
  return docRef.id
}

export async function listMySubmissions(maxResults = 50): Promise<Submission[]> {
  const user = await signInIfNeeded()
  const q = query(
    collection(db, 'submissions'),
    where('userId', '==', user.uid),
    orderBy('submittedAt', 'desc'),
    limit(maxResults),
  )
  const snap = await getDocs(q)
  return snap.docs.map((d) => mapSubmissionDoc(d.id, d.data()))
}

export async function listMyCheckins(maxResults = 50): Promise<Checkin[]> {
  const user = await signInIfNeeded()
  const q = query(
    collection(db, 'checkins'),
    where('userId', '==', user.uid),
    orderBy('at', 'desc'),
    limit(maxResults),
  )
  const snap = await getDocs(q)
  return snap.docs.map((d) => mapCheckinDoc(d.id, d.data()))
}

export async function getSubmissionPhotoUrl(photoPath: string): Promise<string> {
  return getDownloadURL(storageRef(storage, photoPath))
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
