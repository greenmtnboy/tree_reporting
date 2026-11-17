/** @vitest-environment happy-dom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import MapFetchIndicator from './MapFetchIndicator.vue'
import { FETCH_DETAIL, FETCH_PHRASES } from '../lib/loadProgress'

let app: App
let host: HTMLDivElement
const active = ref(false)
const mobile = ref(false)

function mount() {
  host = document.createElement('div')
  document.body.append(host)
  app = createApp({ render: () => h(MapFetchIndicator, { active: active.value, mobile: mobile.value }) })
  app.mount(host)
}
const indicator = () => host.querySelector<HTMLElement>('.map-fetching')
async function advance(ms: number) {
  vi.advanceTimersByTime(ms)
  await nextTick()
}

beforeEach(() => {
  vi.useFakeTimers()
  active.value = false
  mobile.value = false
})
afterEach(() => {
  app?.unmount()
  host?.remove()
  vi.useRealTimers()
})

describe('MapFetchIndicator', () => {
  it('shows nothing while no block is being fetched', async () => {
    mount()
    await advance(5_000)
    expect(indicator()).toBeNull()
  })

  it('waits a beat before showing, so a quick fetch never flashes it', async () => {
    mount()
    active.value = true
    await nextTick()
    await advance(200)
    expect(indicator()).toBeNull()
    active.value = false
    await nextTick()
    await advance(1_000)
    expect(indicator()).toBeNull()
  })

  it('shows a twirling leaf with a playful phrase over the plain meaning in italics', async () => {
    mount()
    active.value = true
    await nextTick()
    await advance(300)
    const el = indicator()!
    expect(el).not.toBeNull()
    expect(el.getAttribute('role')).toBe('status')
    expect(el.querySelector('.leaf-spinner')).not.toBeNull()
    expect(el.querySelector('.map-fetching-phrase')!.textContent).toBe(FETCH_PHRASES[0])
    const detail = el.querySelector('.map-fetching-detail')!
    expect(detail.tagName).toBe('EM')
    expect(detail.textContent).toBe(FETCH_DETAIL)
  })

  it('cycles the playful phrase while the fetch runs, and keeps the plain line', async () => {
    mount()
    active.value = true
    await nextTick()
    await advance(300)
    await advance(2_600)
    expect(indicator()!.querySelector('.map-fetching-phrase')!.textContent).toBe(FETCH_PHRASES[1])
    await advance(2_600 * (FETCH_PHRASES.length - 1))
    expect(indicator()!.querySelector('.map-fetching-phrase')!.textContent).toBe(FETCH_PHRASES[0])
    expect(indicator()!.querySelector('.map-fetching-detail')!.textContent).toBe(FETCH_DETAIL)
  })

  it('stays up long enough to read once shown, then goes', async () => {
    mount()
    active.value = true
    await nextTick()
    await advance(300)
    active.value = false
    await nextTick()
    await advance(300)
    expect(indicator()).not.toBeNull()
    await advance(500)
    // The leave transition keeps the node until it ends; the state is what matters.
    expect(indicator() === null || indicator()!.classList.contains('map-fetching-leave-active')).toBe(true)
  })

  it('a fetch resuming while it is still shown keeps it up', async () => {
    mount()
    active.value = true
    await nextTick()
    await advance(300)
    active.value = false
    await nextTick()
    await advance(100)
    active.value = true
    await nextTick()
    await advance(2_000)
    expect(indicator()).not.toBeNull()
    expect(indicator()!.classList.contains('map-fetching-leave-active')).toBe(false)
  })

  it('sits above the bottom navigation on a phone', async () => {
    mobile.value = true
    mount()
    active.value = true
    await nextTick()
    await advance(300)
    expect(indicator()!.classList.contains('map-fetching--mobile')).toBe(true)
  })
})
