/** @vitest-environment happy-dom */
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, ref, type App } from 'vue'
import { useMyContributions } from './useSubmissions'

const mocks = vi.hoisted(() => ({ read: vi.fn() }))
const user = ref<{ uid: string } | null>({ uid: 'alice' })
vi.mock('./useAuth', () => ({ useAuth: () => ({ user }), signInIfNeeded: async () => user.value }))
vi.mock('../lib/firebase', () => ({ db: {}, storage: {} }))
vi.mock('../lib/e2eFixtures', () => ({
  e2eEnabled: false, e2eSubmissions: () => null, e2eCheckins: () => null, e2eModifications: () => null,
}))
vi.mock('firebase/firestore', async importOriginal => ({
  ...await importOriginal<typeof import('firebase/firestore')>(),
  collection: (_db: unknown, name: string) => name,
  query: (name: string, ...constraints: unknown[]) => ({ name, constraints }),
  getDocsFromServer: mocks.read,
}))

let app: App
let host: HTMLElement
let contributions: ReturnType<typeof useMyContributions>
beforeEach(() => {
  user.value = { uid: 'alice' }
  mocks.read.mockReset().mockResolvedValue({ docs: [] })
  host = document.createElement('div')
  app = createApp({ setup() { contributions = useMyContributions(); return () => null } })
  app.mount(host)
})
afterEach(() => app.unmount())

it('updates check-ins even when the submissions lookup fails', async () => {
  mocks.read.mockImplementation(async ({ name }) => {
    if (name === 'submissions') throw new Error('Missing index')
    return { docs: name === 'checkins' ? [{ id: 'new-visit', data: () => ({ city: 'USBOS', treeId: 'boston-1' }) }] : [] }
  })
  await contributions.refresh()
  expect(contributions.checkins.value.map(c => c.id)).toEqual(['new-visit'])
  expect(contributions.error.value?.message).toContain('Submissions: Missing index')
})

it('requires a server read and preserves known data on failure', async () => {
  mocks.read.mockResolvedValueOnce({ docs: [] })
    .mockResolvedValueOnce({ docs: [{ id: 'old-visit', data: () => ({ treeId: 'boston-1', city: 'USBOS' }) }] })
  await contributions.refresh()
  mocks.read.mockRejectedValue(new Error('Offline'))
  await contributions.refresh()
  expect(contributions.checkins.value.map(c => c.id)).toEqual(['old-visit'])
  expect(contributions.error.value?.message).toContain('Check-ins: Offline')
})

it('ignores a response belonging to a previous account', async () => {
  let finish!: (value: unknown) => void
  mocks.read.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const pending = contributions.refresh()
  await Promise.resolve()
  await Promise.resolve()
  user.value = { uid: 'bob' }
  finish({ docs: [{ id: 'alice-private-visit', data: () => ({ city: 'USBOS' }) }] })
  await pending
  expect(contributions.checkins.value).toEqual([])
})
