/** @vitest-environment happy-dom */
import { createApp, nextTick } from 'vue'
import { expect, it, vi } from 'vitest'
import { type Firestore, deleteDoc, getDocFromServer, setDoc } from 'firebase/firestore'
import ContributorCredit from './ContributorCredit.vue'

vi.mock('firebase/firestore', () => ({
  doc: (_db: unknown, collection: string, uid: string) => collection + '/' + uid,
  getDocFromServer: vi.fn(), setDoc: vi.fn(), deleteDoc: vi.fn(),
}))

it('starts anonymous, saves the chosen public name, and deletes consent on opt-out', async () => {
  vi.mocked(getDocFromServer).mockResolvedValue({ data: () => undefined } as Awaited<ReturnType<typeof getDocFromServer>>)
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ContributorCredit, { uid: 'guest', firestore: {} as Firestore })
  app.mount(host)
  try {
    await vi.waitFor(() => expect(host.querySelector('fieldset')?.disabled).toBe(false))
    const toggle = host.querySelector<HTMLInputElement>('input[type=checkbox]')!
    expect(toggle.checked).toBe(false)
    expect(setDoc).not.toHaveBeenCalled()
    toggle.checked = true
    toggle.dispatchEvent(new Event('change'))
    await nextTick()
    const input = host.querySelector<HTMLInputElement>('input[maxlength]')!
    input.value = ' Juniper '
    input.dispatchEvent(new Event('input'))
    host.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(host.textContent).toContain('Saved.'))
    expect(setDoc).toHaveBeenCalledWith('contributorPreferences/guest', { shareName: true, publicName: 'Juniper' })
    toggle.checked = false
    toggle.dispatchEvent(new Event('change'))
    await nextTick()
    host.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(deleteDoc).toHaveBeenCalledWith('contributorPreferences/guest'))
  } finally {
    app.unmount()
    host.remove()
  }
})
