import { computed, ref } from 'vue'
import {
  GoogleAuthProvider,
  browserLocalPersistence,
  getRedirectResult,
  linkWithPopup,
  linkWithRedirect,
  onAuthStateChanged,
  setPersistence,
  signInAnonymously,
  signInWithPopup,
  signInWithRedirect,
  signOut as firebaseSignOut,
  type User,
} from 'firebase/auth'
import { auth, firebaseAvailable } from '../lib/firebase'
import { e2eEnabled, e2eFixtures, e2eUser } from '../lib/e2eFixtures'

const GOOGLE_PROVIDER_ID = 'google.com'

const user = ref<User | null>(null)
const authReady = ref(false)
const authError = ref<Error | null>(null)
const redirectingToGoogle = ref(false)
let signInPromise: Promise<User> | null = null

function createGoogleProvider() {
  const provider = new GoogleAuthProvider()
  provider.setCustomParameters({ prompt: 'select_account' })
  return provider
}

function shouldUseRedirectFallback(err: unknown): boolean {
  const code = typeof err === 'object' && err !== null && 'code' in err ? String(err.code) : ''
  return code === 'auth/popup-blocked' || code === 'auth/operation-not-supported-in-this-environment'
}

function normalizeGoogleSignInError(err: unknown, currentUser: User | null | undefined): Error {
  const code = typeof err === 'object' && err !== null && 'code' in err ? String(err.code) : ''
  if (
    currentUser?.isAnonymous &&
    (code === 'auth/credential-already-in-use' ||
      code === 'auth/email-already-in-use' ||
      code === 'auth/account-exists-with-different-credential')
  ) {
    return new Error(
      'That Google account is already linked to another profile. Sign out first, then use Continue with Google to reopen the existing account.',
    )
  }
  return err as Error
}

async function initializeAuthState(): Promise<void> {
  // Playwright seeds a session before app code runs; no-op in a normal build.
  const seededUser = e2eUser()
  if (seededUser !== undefined) {
    user.value = seededUser
    authReady.value = true
    return
  }

  if (typeof window === 'undefined' || !auth) {
    authReady.value = true
    return
  }

  let authStateSettled = false
  let redirectSettled = false

  const markReadyIfDone = () => {
    if (authStateSettled && redirectSettled) {
      authReady.value = true
    }
  }

  onAuthStateChanged(auth, (nextUser) => {
    user.value = nextUser
    authStateSettled = true
    redirectingToGoogle.value = false
    markReadyIfDone()
  })

  try {
    await setPersistence(auth, browserLocalPersistence)
    await getRedirectResult(auth)
  } catch (err) {
    authError.value = err as Error
  } finally {
    redirectSettled = true
    markReadyIfDone()
  }
}

const authInitPromise = initializeAuthState()

async function ensureAuthReady(): Promise<void> {
  await authInitPromise
}

export async function signInIfNeeded(): Promise<User> {
  if (!auth) {
    const err = new Error('Firebase auth is not configured')
    authError.value = err
    throw err
  }

  await ensureAuthReady()

  if (user.value) return user.value
  if (signInPromise) return signInPromise

  authError.value = null
  signInPromise = signInAnonymously(auth)
    .then((cred) => cred.user)
    .catch((err: Error) => {
      authError.value = err
      signInPromise = null
      throw err
    })

  return signInPromise
}

export async function signInWithGoogle(): Promise<User | null> {
  if (!auth) {
    const err = new Error('Firebase auth is not configured')
    authError.value = err
    throw err
  }

  await ensureAuthReady()
  authError.value = null

  const currentUser = auth.currentUser ?? user.value
  const provider = createGoogleProvider()

  try {
    const result =
      currentUser?.isAnonymous
        ? await linkWithPopup(currentUser, provider)
        : await signInWithPopup(auth, provider)
    signInPromise = null
    return result.user
  } catch (err) {
    if (shouldUseRedirectFallback(err)) {
      redirectingToGoogle.value = true
      if (currentUser?.isAnonymous) {
        await linkWithRedirect(currentUser, provider)
      } else {
        await signInWithRedirect(auth, provider)
      }
      return null
    }

    const normalizedError = normalizeGoogleSignInError(err, currentUser)
    authError.value = normalizedError
    throw normalizedError
  }
}

export async function signOut(): Promise<void> {
  if (e2eEnabled && e2eFixtures()) {
    user.value = null
    signInPromise = null
    return
  }
  if (!auth) return
  await firebaseSignOut(auth)
  signInPromise = null
  redirectingToGoogle.value = false
}

export function useAuth() {
  const providerIds = computed(() => user.value?.providerData.map((entry) => entry.providerId) ?? [])

  return {
    user,
    uid: computed(() => user.value?.uid ?? null),
    isAnonymous: computed(() => user.value?.isAnonymous ?? false),
    isGoogleLinked: computed(() => providerIds.value.includes(GOOGLE_PROVIDER_ID)),
    authReady,
    authError,
    firebaseAvailable,
    redirectingToGoogle,
    signInIfNeeded,
    signInWithGoogle,
    signOut,
  }
}
