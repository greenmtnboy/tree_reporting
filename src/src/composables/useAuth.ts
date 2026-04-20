import { ref, computed } from 'vue'
import {
  onAuthStateChanged,
  signInAnonymously,
  signOut as firebaseSignOut,
  type User,
} from 'firebase/auth'
import { auth } from '../lib/firebase'

const user = ref<User | null>(null)
const authReady = ref(false)
const authError = ref<Error | null>(null)
let signInPromise: Promise<User> | null = null

if (typeof window !== 'undefined') {
  onAuthStateChanged(auth, (u) => {
    user.value = u
    authReady.value = true
  })
}

export async function signInIfNeeded(): Promise<User> {
  if (user.value) return user.value
  if (signInPromise) return signInPromise
  signInPromise = signInAnonymously(auth)
    .then((cred) => cred.user)
    .catch((err: Error) => {
      authError.value = err
      signInPromise = null
      throw err
    })
  return signInPromise
}

export async function signOut(): Promise<void> {
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
    signInIfNeeded,
    signOut,
  }
}
