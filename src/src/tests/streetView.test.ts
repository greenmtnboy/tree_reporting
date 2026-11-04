// @vitest-environment happy-dom
import { createApp, nextTick } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { streetViewEmbedUrl, streetViewExternalUrl } from '../lib/streetView'
import StreetViewDialog from '../components/StreetViewDialog.vue'

const state = vi.hoisted(() => ({ listeners: {} as Record<string, () => void>, camera: { lat: (): number => 42.002, lng: (): number => -71.001 }, heading: 90, status: 'OK', rotation: vi.fn(), bounds: vi.fn(), dispose: vi.fn(), map: vi.fn(), markerPosition: vi.fn(), removeMap: vi.fn(), removeMarker: vi.fn(), unbind: vi.fn() }))
vi.mock('maplibre-gl', () => ({ default: {
  Map: class { constructor(options: unknown) { state.map(options) } fitBounds = state.bounds; resize() {} remove = state.removeMap },
  Marker: class { setLngLat(position: unknown) { state.markerPosition(position); return this } setRotation(value: number) { state.rotation(value); return this } addTo() { return this } remove = state.removeMarker },
} }))
vi.mock('../lib/streetViewRuntime', () => ({ loadStreetView: vi.fn(async () => ({
  StreetViewPanorama: class {
    getPosition() { return state.camera }
    getPov() { return { heading: state.heading, pitch: 0 } }
    getStatus() { return state.status }
    setVisible = state.dispose
    addListener(event: string, cb: () => void) { state.listeners[event] = cb; return { remove() {} } }
  },
  event: { trigger: vi.fn(), clearInstanceListeners: vi.fn() },
})) }))
vi.mock('../composables/mapTheme', () => ({ basemapStyleUrl: () => 'https://basemaps.cartocdn.com/test/style.json', bindMapTheme: () => state.unbind }))
vi.mock('../composables/useTheme', () => ({ useTheme: () => ({ resolvedTheme: { value: 'light' } }) }))
const position = { lat: 42.001, lng: -71 }
let unmount: (() => void) | undefined
const closed = vi.fn()
async function mount(key = 'test-key') {
  vi.stubEnv('VITE_GOOGLE_MAPS_API_KEY', key)
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')) }
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(StreetViewDialog, { position, onClose: closed })
  app.mount(host)
  unmount = () => { app.unmount(); host.remove() }
  await nextTick()
}
afterEach(() => { unmount?.(); unmount = undefined; vi.clearAllMocks(); vi.unstubAllEnvs() })

it('requests nearby outdoor imagery exclusively through Embed, safely encoding the key', () => {
  const url = new URL(streetViewEmbedUrl(position, 'test&key=other')!)
  expect(url.origin + url.pathname).toBe('https://www.google.com/maps/embed/v1/streetview')
  expect(Object.fromEntries(url.searchParams)).toEqual({ key: 'test&key=other', location: '42.001,-71', radius: '100', source: 'outdoor' })
})
it.each([undefined, '', '  '])('does not create an embed URL with missing key %s', key => {
  expect(streetViewEmbedUrl(position, key)).toBeNull()
})
it('keeps the external fallback key-free', () => {
  expect(streetViewExternalUrl(position)).toBe('https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=42.001,-71')
})
it('tracks the actual camera position and heading, independently of the tree', async () => {
  await mount()
  expect(state.markerPosition).toHaveBeenCalledWith([-71, 42.001])
  expect(state.markerPosition).toHaveBeenCalledWith([-71.001, 42.002])
  expect(state.rotation).toHaveBeenCalledWith(90)
  state.heading = 180
  state.listeners.pov_changed!()
  expect(state.rotation).toHaveBeenLastCalledWith(180)
  state.camera = { lat: () => 42.003, lng: () => -71.002 }
  state.listeners.position_changed!()
  expect(state.markerPosition).toHaveBeenLastCalledWith([-71.002, 42.003])
  state.status = 'ZERO_RESULTS'
  state.listeners.status_changed!()
  await nextTick()
  expect(document.querySelector('[role=status]')?.textContent).toContain('unavailable')
  expect(state.removeMarker).toHaveBeenCalled()
  state.status = 'OK'
})
it('expands the entire workspace and restores it', async () => {
  await mount()
  const button = document.querySelector<HTMLButtonElement>('button[aria-pressed]')!
  button.click()
  await nextTick()
  expect(document.querySelector('dialog')?.classList.contains('expanded')).toBe(true)
  button.click()
  await nextTick()
  expect(document.querySelector('dialog')?.classList.contains('expanded')).toBe(false)
})
it('shows missing configuration and the external fallback without creating a map', async () => {
  await mount('')
  expect(document.querySelector('[role=status]')?.textContent).toContain('not configured')
  expect(document.querySelector('iframe')).toBeNull()
  expect(state.map).toHaveBeenCalled()
  expect(document.querySelector('dialog a')?.getAttribute('href')).toBe(streetViewExternalUrl(position))
})
it('closes from the button and releases the inset, iframe, and focus on unmount', async () => {
  const opener = document.createElement('button')
  document.body.append(opener)
  opener.focus()
  await mount()
  document.querySelector<HTMLButtonElement>('button[aria-label="Close Street View"]')!.click()
  expect(closed).toHaveBeenCalledOnce()
  unmount!(); unmount = undefined
  expect(state.unbind).toHaveBeenCalledOnce()
  expect(state.removeMap).toHaveBeenCalledOnce()
  expect(state.removeMarker).toHaveBeenCalledTimes(2)
  expect(document.querySelector('iframe')).toBeNull()
  expect(document.activeElement).toBe(opener)
  opener.remove()
})
