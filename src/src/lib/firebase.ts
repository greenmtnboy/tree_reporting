import { initializeApp, type FirebaseApp } from 'firebase/app'
import { getAuth, type Auth } from 'firebase/auth'
import { getFirestore, type Firestore } from 'firebase/firestore'
import { getStorage, type FirebaseStorage } from 'firebase/storage'

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
}

let _app: FirebaseApp | null = null
let _auth: Auth | null = null
let _db: Firestore | null = null
let _storage: FirebaseStorage | null = null

if (firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.appId) {
  try {
    _app = initializeApp(firebaseConfig)
    _auth = getAuth(_app)
    _db = getFirestore(_app)
    _storage = getStorage(_app)
  } catch (err) {
    console.warn('[firebase] initialization failed — auth and contribution features disabled', err)
    _app = null
    _auth = null
    _db = null
    _storage = null
  }
} else {
  console.warn('[firebase] VITE_FIREBASE_* env vars missing — auth and contribution features disabled')
}

export const firebaseAvailable = _auth !== null && _db !== null && _storage !== null
export const app: FirebaseApp | null = _app
export const auth: Auth | null = _auth
export const db: Firestore | null = _db
export const storage: FirebaseStorage | null = _storage
