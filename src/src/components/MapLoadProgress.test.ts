/** @vitest-environment happy-dom */
import { afterEach, describe, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import MapLoadProgress from './MapLoadProgress.vue'
import { NEARBY_LOAD_STEPS } from '../lib/loadProgress'

let app: App
let host: HTMLDivElement

async function mount(current: number) {
  host = document.createElement('div')
  document.body.append(host)
  app = createApp(MapLoadProgress, { steps: NEARBY_LOAD_STEPS, current })
  app.mount(host)
  await nextTick()
}
const items = () => [...host.querySelectorAll<HTMLLIElement>('li')]

afterEach(() => {
  app?.unmount()
  host?.remove()
})

describe('MapLoadProgress', () => {
  it('lists every step with its playful title over the plain meaning in italics', async () => {
    await mount(2)
    expect(items()).toHaveLength(NEARBY_LOAD_STEPS.length)
    for (const [i, li] of items().entries()) {
      expect(li.querySelector('.map-load-progress-title')!.textContent).toContain(NEARBY_LOAD_STEPS[i].title)
      const detail = li.querySelector('.map-load-progress-detail')!
      expect(detail.tagName).toBe('EM')
      expect(detail.textContent).toBe(NEARBY_LOAD_STEPS[i].detail)
    }
  })

  it('ticks the steps before the current one, marks the current one, and leaves the rest', async () => {
    await mount(3)
    const state = items().map((li) => li.classList.contains('done') ? 'done' : li.classList.contains('active') ? 'active' : 'todo')
    expect(state).toEqual(['done', 'done', 'done', 'active', 'todo', 'todo'])
    expect(items().filter((li) => li.querySelector('svg'))).toHaveLength(3)
    expect(items()[3].getAttribute('aria-current')).toBe('step')
    expect(items().filter((li) => li.hasAttribute('aria-current'))).toHaveLength(1)
    expect(items()[3].querySelector('.map-load-progress-title')!.textContent).toBe(`${NEARBY_LOAD_STEPS[3].title}…`)
    expect(host.querySelector('ol')!.getAttribute('aria-label')).toBe(`Step 4 of ${NEARBY_LOAD_STEPS.length}`)
  })

  it('shows the twirling leaf', async () => {
    await mount(1)
    expect(host.querySelector('.leaf-spinner')).not.toBeNull()
  })
})
