import { ref, computed } from 'vue'
import {
  onAuthStateChanged,
  signInAnonymously,
  signOut as firebaseSignOut,
  type User,
} from 'firebase/auth'
import { auth, firebaseAvailable } from '../lib/firebase'

const user = ref<User | null>(null)
const authReady = ref(false)
const authError = ref<Error | null>(null)
let signInPromise: Promise<User> | null = null

if (typeof window !== 'undefined' && auth) {
  onAuthStateChanged(auth, (u) => {
    user.value = u
    authReady.value = true
  })
} else {
  // Firebase not configured — mark ready immediately so UI doesn't wait.
  authReady.value = true
}

export async function signInIfNeeded(): Promise<User> {
  if (!auth) {
    const err = new Error('Firebase auth is not configured')
    authError.value = err
    throw err
  }
  if (user.value) return user.value
  if (signInPromise) return signInPromise
  const authClient = auth
  signInPromise = signInAnonymously(authClient)
    .then((cred) => cred.user)
    .catch((err: Error) => {
      authError.value = err
      signInPromise = null
      throw err
    })
  return signInPromise
}

export async function signOut(): Promise<void> {
  if (!auth) return
  await firebaseSignOut(auth)
  signInPromise = null
}

export function useAuth() {
  return {
    user,
    uid: computed(() => user.value?.uid ?? null),
    isAnonymous: computed(() => user.value?.isAnonymous ?? false),
    authReady,
    authError,
    firebaseAvailable,
    signInIfNeeded,
    signOut,
  }
}
