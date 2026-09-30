import { computed, ref, shallowRef, triggerRef } from 'vue'
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
import { e2eEnabled, e2eFixtures, e2eUser, e2eSwitchAccount } from '../lib/e2eFixtures'

const GOOGLE_PROVIDER_ID = 'google.com'

// Firebase owns and mutates User instances. Do not deep-proxy SDK objects:
// same-UID account linking can mutate the raw instance without Vue observing it.
const user = shallowRef<User | null>(null)
const authReady = ref(false)
const authError = ref<Error | null>(null)
const redirectingToGoogle = ref(false)
let signInPromise: Promise<User> | null = null

function publishUser(nextUser: User | null) {
  if (user.value === nextUser) triggerRef(user)
  else user.value = nextUser
}

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
      "That Google account is already linked to another profile. Nothing was merged; you are still using this anonymous account. Switching may make this account's contributions inaccessible. Use Log in as account to reopen the existing profile after reviewing the warning.",
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
    publishUser(nextUser)
    authStateSettled = true
    redirectingToGoogle.value = false
    markReadyIfDone()
  })

  try {
    await setPersistence(auth, browserLocalPersistence)
    const result = await getRedirectResult(auth)
    if (result) publishUser(result.user)
  } catch (err) {
    authError.value = normalizeGoogleSignInError(err, auth.currentUser)
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
    .then((cred) => {
      publishUser(cred.user)
      return cred.user
    })
    .catch((err: Error) => {
      authError.value = err
      signInPromise = null
      throw err
    })

  return signInPromise
}

export async function signInWithGoogle(mode: 'link' | 'switch' = 'link'): Promise<User | null> {
  if (!auth) {
    const err = new Error('Firebase auth is not configured')
    authError.value = err
    throw err
  }

  await ensureAuthReady()
  authError.value = null

  const currentUser = auth.currentUser ?? user.value
  const shouldLink = mode === 'link' && currentUser?.isAnonymous
  const provider = createGoogleProvider()

  try {
    const fixture = e2eEnabled ? e2eFixtures() : null
    if (shouldLink && fixture?.googleLinkError) throw { code: fixture.googleLinkError }
    const seededUser = e2eEnabled && mode === 'switch' ? e2eSwitchAccount() : null
    // Signing in replaces the session only on success. Do not sign out first:
    // a cancelled popup must leave the guest and their contributions accessible.
    const result = seededUser ? { user: seededUser } :
      shouldLink
        ? await linkWithPopup(currentUser, provider)
        : await signInWithPopup(auth, provider)
    signInPromise = null
    // Linking retains the UID, so onAuthStateChanged need not fire. Explicitly
    // publish the completed credential, including same-object SDK mutations.
    publishUser(result.user)
    return result.user
  } catch (err) {
    if (shouldUseRedirectFallback(err)) {
      redirectingToGoogle.value = true
      try {
        if (shouldLink) {
          await linkWithRedirect(currentUser, provider)
        } else {
          await signInWithRedirect(auth, provider)
        }
        return null
      } catch (redirectError) {
        redirectingToGoogle.value = false
        const normalizedError = normalizeGoogleSignInError(redirectError, shouldLink ? currentUser : null)
        authError.value = normalizedError
        throw normalizedError
      }
    }

    const normalizedError = normalizeGoogleSignInError(err, shouldLink ? currentUser : null)
    authError.value = normalizedError
    throw normalizedError
  }
}

export async function signOut(): Promise<void> {
  if (e2eEnabled && e2eFixtures()) {
    user.value = null
    authError.value = null
    signInPromise = null
    return
  }
  if (!auth) return
  await firebaseSignOut(auth)
  authError.value = null
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
