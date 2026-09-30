/** @vitest-environment happy-dom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import CheckinDialog from './CheckinDialog.vue'

const mocks = vi.hoisted(() => ({
  locate: vi.fn(), submit: vi.fn(), checkin: vi.fn(),
}))
vi.mock('../lib/geo', () => ({ getCurrentPosition: mocks.locate }))
vi.mock('../composables/useSubmissions', () => ({
  submitTreeModification: mocks.submit, recordCheckin: mocks.checkin,
}))
vi.mock('../composables/useDuckDB', () => ({ useDuckDB: () => ({ query: vi.fn() }) }))
vi.mock('../composables/useMapData', () => ({
  closestCityTo: () => 'USSFO',
  haversineKm: (a: number, b: number, c: number, d: number) => Math.hypot(a - c, b - d) * 111,
}))
vi.mock('./SubmitLocationPicker.vue', () => ({
  default: defineComponent({
    emits: ['update'],
    setup(_, { emit }) {
      return () => h('button', {
        'data-testid': 'move-pin',
        onClick: () => emit('update', { lat: 37.776, lng: -122.420 }),
      }, 'Move pin')
    },
  }),
}))

let app: App
let host: HTMLDivElement
const root = document.body
async function mount(remoteCorrection: boolean) {
  host = document.createElement('div')
  document.body.append(host)
  app = createApp(CheckinDialog, {
    treeId: 'community-long-tree-id', treeLat: 37.775, treeLng: -122.419,
    species: 'Quercus laurifolia', dbhInches: 35.6, remoteCorrection,
  })
  app.mount(host)
  await nextTick()
}
async function settle() {
  await Promise.resolve()
  await nextTick()
}
async function input(selector: string, value: string) {
  const el = root.querySelector<HTMLInputElement>(selector)!
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}
const submitButton = () => root.querySelector<HTMLButtonElement>('[data-testid="checkin-submit"]')!

beforeEach(() => { vi.clearAllMocks(); mocks.submit.mockResolvedValue('report-1') })
afterEach(() => { app?.unmount(); host?.remove() })

describe('desktop corrections', () => {
  it('opens without GPS, accepts location/species/diameter changes, and sends a report only', async () => {
    await mount(true)
    expect(mocks.locate).not.toHaveBeenCalled()
    expect(root.querySelector('[role="tablist"]')).toBeNull()
    expect(submitButton().disabled).toBe(true)
    root.querySelector<HTMLButtonElement>('[data-testid="move-pin"]')!.click()
    await input('input[type="text"]', '  Acer rubrum  ')
    await input('input[type="number"]', '20')
    submitButton().click()
    await settle()
    expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'update', treeId: 'community-long-tree-id',
      userLat: null, userLng: null, distanceMeters: null,
      proposedLat: 37.776, proposedLng: -122.420,
      proposedSpecies: 'Acer rubrum', proposedDbhInches: 20,
    }))
    expect(mocks.checkin).not.toHaveBeenCalled()
    expect(root.textContent).toContain('Thanks — report sent.')
  })

  it('rejects unchanged and invalid values, and allows retry after a failed submission', async () => {
    await mount(true)
    await input('input[type="text"]', 'Quercus laurifolia')
    await input('input[type="number"]', '35.6')
    expect(submitButton().disabled).toBe(true)
    await input('input[type="text"]', 'Acer rubrum')
    await input('input[type="number"]', '401')
    expect(submitButton().disabled).toBe(true)
    await input('input[type="number"]', '')
    mocks.submit.mockRejectedValueOnce(new Error('Offline'))
    submitButton().click()
    await settle()
    expect(root.textContent).toContain('Offline')
    Array.from(root.querySelectorAll('button')).find(b => b.textContent === 'Retry')!.click()
    await settle()
    expect(mocks.submit).toHaveBeenCalledTimes(2)
    expect(root.textContent).toContain('Thanks — report sent.')
  })

  it('still gates mobile visits on geolocation', async () => {
    mocks.locate.mockResolvedValue({ coords: { latitude: 0, longitude: 0 } })
    await mount(false)
    await settle()
    expect(mocks.locate).toHaveBeenCalledOnce()
    expect(root.textContent).toContain("You're too far from this tree.")
    expect(root.querySelector('[data-testid="checkin-submit"]')).toBeNull()
  })
})
