// @vitest-environment happy-dom
import { createApp, nextTick } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { headingToTree } from '../lib/streetView'
import StreetViewDialog from '../components/StreetViewDialog.vue'
const state = vi.hoisted(() => ({ load: vi.fn(), request: vi.fn(), panorama: vi.fn(), callbacks: {} as Record<string, () => void>, removed: vi.fn(), heading: 90, setIcon: vi.fn(), fit: vi.fn(), visible: vi.fn() }))
vi.mock('../lib/streetView', async (original) => ({ ...await original<typeof import('../lib/streetView')>(), loadStreetView: state.load }))
let unmount: (() => void) | undefined
async function mount() {
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(StreetViewDialog, { position: { lat: 42.001, lng: -71 } })
  app.mount(host)
  unmount = () => { app.unmount(); host.remove() }
  for (let i = 0; i < 8; i++) await nextTick()
}
afterEach(() => { unmount?.(); vi.clearAllMocks(); vi.unstubAllGlobals() })
function fakeMaps() {
  return {
    StreetViewService: class { getPanorama = state.request },
    StreetViewPreference: { NEAREST: 'nearest' }, StreetViewSource: { OUTDOOR: 'outdoor' },
    SymbolPath: { CIRCLE: 'circle', FORWARD_CLOSED_ARROW: 'arrow' },
    StreetViewPanorama: class {
      constructor(...args: unknown[]) { state.panorama(...args) }
      getPosition() { return { toJSON: () => ({ lat: 42, lng: -71 }) } }
      getPov() { return { heading: state.heading } }
      setPov() {}
      setVisible = state.visible
      addListener(name: string, callback: () => void) { state.callbacks[name] = callback; return { remove: state.removed } }
    },
    Map: class { fitBounds = state.fit },
    Marker: class { setPosition() {} setIcon = state.setIcon; setMap() {} },
    Polyline: class { setPath() {} setMap() {} },
    LatLngBounds: class { extend() { return this } },
  }
}
it.each([[1, 0, 0], [0, 1, 90], [-1, 0, 180], [0, -1, 270]])('faces a tree at %s,%s', (lat, lng, expected) => {
  expect(headingToTree({ lat: 0, lng: 0 }, { lat, lng })).toBeCloseTo(expected)
})
it('handles the antimeridian', () => {
  expect(headingToTree({ lat: 0, lng: 179 }, { lat: 0, lng: -179 })).toBeCloseTo(90)
})
it('shows configuration failures with an external fallback', async () => {
  state.load.mockRejectedValue(new Error('Street View is not configured yet.'))
  await mount()
  expect(document.querySelector('[role=status]')?.textContent).toContain('not configured')
  expect(document.querySelector('dialog a')?.getAttribute('href')).toContain('42.001,-71')
})
it('handles unavailable imagery', async () => {
  state.load.mockResolvedValue(fakeMaps())
  state.request.mockResolvedValue({ data: {} })
  await mount()
  expect(document.querySelector('[role=status]')?.textContent).toContain('No outdoor Street View')
  expect(state.panorama).not.toHaveBeenCalled()
})
it('uses the nearest outdoor panorama and synchronizes the camera', async () => {
  const maps = fakeMaps()
  vi.stubGlobal('google', { maps })
  state.load.mockResolvedValue(maps)
  state.request.mockResolvedValue({ data: { location: { pano: 'closest', latLng: { toJSON: () => ({ lat: 42, lng: -71 }) } } } })
  await mount()
  expect(state.request).toHaveBeenCalledWith(expect.objectContaining({ preference: 'nearest', sources: ['outdoor'], radius: 100 }))
  expect(state.panorama.mock.calls[0][1]).toMatchObject({ pano: 'closest', pov: { heading: 0, pitch: 0 } })
  state.heading = 215
  state.callbacks.pov_changed()
  expect(state.setIcon).toHaveBeenLastCalledWith(expect.objectContaining({ rotation: 215 }))
  state.callbacks.position_changed()
  expect(state.fit).toHaveBeenCalledTimes(2)
  unmount!(); unmount = undefined
  expect(state.removed).toHaveBeenCalledTimes(2)
  expect(state.visible).toHaveBeenCalledWith(false)
})
it('does not create a viewer if closed during loading', async () => {
  let resolve!: (value: unknown) => void
  state.load.mockReturnValue(new Promise(r => { resolve = r }))
  await mount()
  unmount!(); unmount = undefined
  resolve(fakeMaps())
  await nextTick()
  expect(state.request).not.toHaveBeenCalled()
})
