/**
 * Runs the real tile worker (`../duckdbPipeline.worker.ts`) in this thread
 * and talks to it through its own message protocol, as the main thread does.
 *
 * The test file must mock the browser-only modules before calling this:
 *
 *   vi.mock('@duckdb/duckdb-wasm', () => import('./wasm/nodeDuckDB'))
 *   vi.mock('../duckdbAssetUrls', () => ({ DUCKDB_ASSET_URLS: { mvp: {}, eh: {} } }))
 *   vi.mock('./parquetUrls', async () => (await import('./wasm/fixtures')).fixtureParquetUrls)
 *
 * The worker keeps its state in module scope, so one test file is one worker.
 */
import { vi } from 'vitest'

type Response = { type: 'response'; requestId: number; ok: boolean; result?: unknown; error?: string }

export type WorkerHarness = {
  call<T = unknown>(method: string, params?: unknown): Promise<T>
  /** Every `[Perf]` line the worker logged, oldest first. */
  perf: { label: string; detail: Record<string, unknown> }[]
}

export async function startWorker(): Promise<WorkerHarness> {
  const pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>()
  const ctx: { onmessage: ((event: { data: unknown }) => void) | null; postMessage: (m: unknown) => void } = {
    onmessage: null,
    postMessage: (message) => {
      const msg = message as Response
      if (msg?.type !== 'response') return
      const entry = pending.get(msg.requestId)
      if (!entry) return
      pending.delete(msg.requestId)
      if (msg.ok) entry.resolve(msg.result)
      else entry.reject(new Error(msg.error ?? 'worker error'))
    },
  }
  vi.stubGlobal('self', ctx)
  // The worker constructs the bundle's Web Worker; the Node bundle runs inline.
  vi.stubGlobal('Worker', class { terminate() {} })

  const perf: WorkerHarness['perf'] = []
  const info = console.info.bind(console)
  vi.spyOn(console, 'info').mockImplementation((...args: unknown[]) => {
    if (typeof args[0] === 'string' && args[0].startsWith('[Perf]')) {
      perf.push({ label: args[0], detail: (args[1] ?? {}) as Record<string, unknown> })
      return
    }
    info(...args)
  })

  await import('../duckdbPipeline.worker')
  if (!ctx.onmessage) throw new Error('the worker did not install its message handler')

  let nextId = 1
  return {
    perf,
    call<T>(method: string, params: unknown = {}) {
      const requestId = nextId++
      return new Promise<T>((resolve, reject) => {
        pending.set(requestId, { resolve: resolve as (v: unknown) => void, reject })
        ctx.onmessage!({ data: { type: 'request', requestId, method, params } })
      })
    },
  }
}
