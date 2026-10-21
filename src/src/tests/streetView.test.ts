// @vitest-environment happy-dom
import { createApp, nextTick } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { streetViewEmbedUrl, streetViewExternalUrl } from '../lib/streetView'
import StreetViewDialog from '../components/StreetViewDialog.vue'

const state = vi.hoisted(() => ({ map: vi.fn(), markerPosition: vi.fn(), removeMap: vi.fn(), removeMarker: vi.fn(), unbind: vi.fn() }))
vi.mock('maplibre-gl', () => ({ default: {
  Map: class { constructor(options: unknown) { state.map(options) } remove = state.removeMap },
  Marker: class { setLngLat(position: unknown) { state.markerPosition(position); return this } addTo() { return this } remove = state.removeMarker },
} }))
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
it('renders an accessible iframe and a separate tree-location map without loading the Google JS SDK', async () => {
  await mount()
  const frame = document.querySelector('iframe')!
  expect(frame.title).toBe('Google Street View near this tree')
  expect(frame.getAttribute('referrerpolicy')).toBe('strict-origin-when-cross-origin')
  expect(frame.hasAttribute('allowfullscreen')).toBe(true)
  expect(new URL(frame.src).pathname).toBe('/maps/embed/v1/streetview')
  expect(document.querySelector('script[src*="maps.googleapis.com"]')).toBeNull()
  expect(document.querySelector('dialog')?.textContent).toContain('does not track Street View')
  expect(document.querySelector('dialog')?.textContent).not.toContain('Face tree')
  expect(state.map).toHaveBeenCalledWith(expect.objectContaining({ center: [-71, 42.001], interactive: false }))
  expect(state.markerPosition).toHaveBeenCalledWith([-71, 42.001])
})
it('shows missing configuration and the external fallback without creating a map', async () => {
  await mount('')
  expect(document.querySelector('[role=status]')?.textContent).toContain('not configured')
  expect(document.querySelector('iframe')).toBeNull()
  expect(state.map).not.toHaveBeenCalled()
  expect(document.querySelector('dialog a')?.getAttribute('href')).toBe(streetViewExternalUrl(position))
})
it('closes from the button and releases the inset, iframe, and focus on unmount', async () => {
  const opener = document.createElement('button')
  document.body.append(opener)
  opener.focus()
  await mount()
  document.querySelector<HTMLButtonElement>('dialog header button')!.click()
  expect(closed).toHaveBeenCalledOnce()
  unmount!(); unmount = undefined
  expect(state.unbind).toHaveBeenCalledOnce()
  expect(state.removeMap).toHaveBeenCalledOnce()
  expect(state.removeMarker).toHaveBeenCalledOnce()
  expect(document.querySelector('iframe')).toBeNull()
  expect(document.activeElement).toBe(opener)
  opener.remove()
})
