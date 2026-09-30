/** @vitest-environment happy-dom */
import { beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  auth: { currentUser: null as null | { uid: string; isAnonymous: boolean; providerData: { providerId: string }[]; email?: string } },
  popup: vi.fn(), redirectResult: vi.fn(), redirect: vi.fn(),
}))
vi.mock('../lib/firebase', () => ({ auth: state.auth, firebaseAvailable: true }))
vi.mock('../lib/e2eFixtures', () => ({ e2eEnabled: false, e2eUser: () => undefined }))
vi.mock('./useSubmissions', async () => {
  const { ref } = await import('vue')
  return { useMyContributions: () => ({
    submissions: ref([]), checkins: ref([]), loading: ref(false), error: ref(null), refresh: vi.fn(),
  }) }
})
vi.mock('firebase/auth', () => ({
  GoogleAuthProvider: class { setCustomParameters() {} },
  browserLocalPersistence: {},
  setPersistence: async () => {},
  onAuthStateChanged: (_auth: unknown, callback: (user: unknown) => void) => { callback(state.auth.currentUser) },
  getRedirectResult: state.redirectResult,
  linkWithPopup: state.popup,
  signInWithPopup: state.popup,
  linkWithRedirect: state.redirect, signInWithRedirect: state.redirect,
  signInAnonymously: vi.fn(), signOut: vi.fn(),
}))

beforeEach(() => {
  vi.resetModules()
  state.auth.currentUser = { uid: 'same-account', isAnonymous: true, providerData: [] }
  state.popup.mockReset()
  state.redirect.mockReset()
  state.redirectResult.mockReset().mockResolvedValue(null)
})

function upgradeSameObject() {
  const linked = state.auth.currentUser!
  linked.isAnonymous = false
  linked.providerData = [{ providerId: 'google.com' }]
  linked.email = 'linked@example.com'
  return { user: linked }
}

it('updates cached profile/provider state after same-object, same-UID popup linking', async () => {
  const { useAuth, signInWithGoogle } = await import('./useAuth')
  const auth = useAuth()
  expect(auth.isAnonymous.value).toBe(true)
  expect(auth.isGoogleLinked.value).toBe(false)
  state.popup.mockImplementation(async () => upgradeSameObject())
  await signInWithGoogle()
  expect(auth.uid.value).toBe('same-account')
  expect(auth.isAnonymous.value).toBe(false)
  expect(auth.isGoogleLinked.value).toBe(true)
  expect(auth.user.value?.email).toBe('linked@example.com')
})

it('publishes a completed redirect link even without another auth-state callback', async () => {
  let finish!: (credential: unknown) => void
  state.redirectResult.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { useAuth } = await import('./useAuth')
  const auth = useAuth()
  expect(auth.isAnonymous.value).toBe(true)
  expect(auth.isGoogleLinked.value).toBe(false)
  finish(upgradeSameObject())
  await vi.waitFor(() => expect(auth.authReady.value).toBe(true))
  expect(auth.isAnonymous.value).toBe(false)
  expect(auth.isGoogleLinked.value).toBe(true)
})

it('keeps the anonymous account and explains a Google credential already linked elsewhere', async () => {
  const { useAuth, signInWithGoogle } = await import('./useAuth')
  const auth = useAuth()
  state.popup.mockRejectedValue({ code: 'auth/credential-already-in-use' })
  await expect(signInWithGoogle()).rejects.toThrow('already linked to another profile')
  expect(auth.isAnonymous.value).toBe(true)
  expect(auth.uid.value).toBe('same-account')
  expect(auth.authError.value?.message).toContain('Sign out first')
  expect(auth.authError.value?.message).toContain('Nothing was merged')
  expect(auth.authError.value?.message).toContain('contributions inaccessible')
})

it('the Profile link button changes the rendered account status without a reload', async () => {
  const { createApp, defineComponent, h } = await import('vue')
  const { default: ProfileView } = await import('../views/ProfileView.vue')
  state.popup.mockImplementation(async () => upgradeSameObject())
  const host = document.createElement('div')
  const app = createApp(ProfileView)
  app.component('RouterLink', defineComponent({ setup(_, { slots }) { return () => h('a', slots.default?.()) } }))
  app.mount(host)
  try {
    await vi.waitFor(() => expect(host.textContent).toContain('Signed in anonymously.'))
    const button = Array.from(host.querySelectorAll('button')).find(b => b.textContent?.trim() === 'Link Google account')!
    button.click()
    await vi.waitFor(() => expect(host.textContent).toContain('Signed in with Google.'))
    expect(host.textContent).not.toContain('Signed in anonymously.')
    expect(host.textContent).not.toContain('Link Google account')
    expect(host.textContent).toContain('same-account')
    expect(host.textContent).toContain('linked@example.com')
  } finally {
    app.unmount()
  }
})

it('shows a redirect-fallback failure as a focused Profile alert instead of swallowing it', async () => {
  const { createApp, defineComponent, h } = await import('vue')
  const { default: ProfileView } = await import('../views/ProfileView.vue')
  state.popup.mockRejectedValue({ code: 'auth/popup-blocked' })
  state.redirect.mockRejectedValue({ code: 'auth/credential-already-in-use' })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ProfileView)
  app.component('RouterLink', defineComponent({ setup(_, { slots }) { return () => h('a', slots.default?.()) } }))
  app.mount(host)
  try {
    await vi.waitFor(() => expect(host.textContent).toContain('Signed in anonymously.'))
    Array.from(host.querySelectorAll('button')).find(b => b.textContent?.trim() === 'Link Google account')!.click()
    await vi.waitFor(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain('Nothing was merged'))
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Account linking failed')
    expect(document.activeElement).toBe(host.querySelector('[role="alert"]'))
    expect(host.textContent).toContain('Signed in anonymously.')
    expect(host.textContent).not.toContain('Redirecting to Google...')
  } finally {
    app.unmount()
    host.remove()
  }
})
