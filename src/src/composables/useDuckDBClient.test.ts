import { describe, expect, it, vi } from 'vitest'

// A worker that answers every request and lets the test push messages.
const fake = vi.hoisted(() => ({
  instance: null as null | {
    onmessage: ((event: { data: unknown }) => void) | null
    posted: Array<{ requestId: number; method: string }>
    push: (data: unknown) => void
    answer: (method: string, result?: unknown) => void
  },
}))

vi.mock('maplibre-gl', () => ({ default: { addProtocol: vi.fn() } }))
vi.mock('../workers/duckdbPipeline.worker?worker', () => ({
  default: class {
    onmessage: ((event: { data: unknown }) => void) | null = null
    onerror: unknown = null
    posted: Array<{ requestId: number; method: string }> = []
    constructor() {
      fake.instance = this
    }
    postMessage(message: { requestId: number; method: string }) {
      this.posted.push(message)
    }
    push(data: unknown) {
      this.onmessage?.({ data })
    }
    answer(method: string, result?: unknown) {
      const request = this.posted.find((m) => m.method === method)!
      this.posted = this.posted.filter((m) => m !== request)
      this.push({ type: 'response', requestId: request.requestId, ok: true, result })
    }
  },
}))

const { useDuckDB } = await import('./useDuckDBClient')

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('useDuckDB load progress', () => {
  it('follows the init stages and block loads the worker pushes, and counts unanswered city contexts', async () => {
    const duck = useDuckDB()
    expect(duck.initStage.value).toBeNull()
    expect(duck.windowLoadsActive.value).toBe(0)
    expect(duck.cityContextPending.value).toBe(0)

    const context = duck.setCityContext('USSFO')
    expect(duck.cityContextPending.value).toBe(1)
    await flush()
    const worker = fake.instance!

    for (const stage of ['engine', 'guide', 'world', 'ready']) {
      worker.push({ type: 'initStage', stage })
      expect(duck.initStage.value).toBe(stage)
    }
    worker.answer('ensureInit', { ready: true, initError: null })
    await flush()
    expect(duck.cityContextPending.value).toBe(1)

    worker.push({ type: 'windowLoads', active: 2 })
    expect(duck.windowLoadsActive.value).toBe(2)
    worker.push({ type: 'windowLoads', active: 0 })
    expect(duck.windowLoadsActive.value).toBe(0)

    worker.answer('setCityContext')
    await context
    expect(duck.cityContextPending.value).toBe(0)
    expect(duck.ready.value).toBe(true)
  })

  it('stops counting a city context that failed', async () => {
    const duck = useDuckDB()
    const context = duck.setCityContext('USBOS')
    expect(duck.cityContextPending.value).toBe(1)
    await flush()
    const worker = fake.instance!
    const request = worker.posted.find((m) => m.method === 'setCityContext')!
    worker.push({ type: 'response', requestId: request.requestId, ok: false, error: 'boom' })
    await expect(context).rejects.toThrow('boom')
    expect(duck.cityContextPending.value).toBe(0)
  })
})
