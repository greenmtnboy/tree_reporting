import { ref } from 'vue'
import maplibregl from 'maplibre-gl'
import DuckDBPipelineWorker from '../workers/duckdbPipeline.worker?worker'

type PrefetchStatus = 'executed' | 'deduped' | 'skipped'
type TileRangeParams = { minX: number; maxX: number; minY: number; maxY: number }

type WorkerMethodMap = {
  ensureInit: { params: Record<string, never>; result: { ready: boolean; initError: string | null } }
  setTileQuery: { params: { sql: string | null }; result: void }
  setPublishedTreeIdFilterSql: { params: { sql: string | null }; result: void }
  setColorOverrideSql: { params: { sql: string | null }; result: void }
  setCityContext: { params: { city: string }; result: void }
  setViewportZoom: { params: { zoom: number }; result: void }
  setViewportCenter: { params: { lng: number; lat: number }; result: void }
  setVisibleTileRange: { params: { z: number; minX: number; maxX: number; minY: number; maxY: number }; result: void }
  prefetchVisibleDetailTilesAtZoom: { params: { z: number; range?: TileRangeParams }; result: PrefetchStatus }
  prewarmLodCaches: { params: Record<string, never>; result: void }
  setAutoTileFetchEnabled: { params: { enabled: boolean }; result: void }
  invalidateTileCaches: { params: Record<string, never>; result: void }
  query: { params: { sql: string }; result: { columns: string[]; rows: Record<string, unknown>[] } }
  getTile: { params: { z: number; x: number; y: number }; result: { tileBuffer: ArrayBuffer } }
}

type WorkerRequest<K extends keyof WorkerMethodMap> = {
  type: 'request'
  requestId: number
  method: K
  params: WorkerMethodMap[K]['params']
}


const ready = ref(false)
const initError = ref<string | null>(null)
const workerDistinctColors = ref<string[]>([])
const workerColorLabelMap = ref<Record<string, string>>({})

let worker: Worker | null = null
let nextRequestId = 1
let initPromise: Promise<void> | null = null
let protocolRegistered = false

const pending = new Map<number, {
  resolve: (value: unknown) => void
  reject: (error: unknown) => void
}>()

function getWorker(): Worker {
  if (worker) return worker

  worker = new DuckDBPipelineWorker({ name: 'duckdb-pipeline-worker' })
  worker.onmessage = (event: MessageEvent) => {
    const msg = event.data
    if (!msg) return

    // Handle non-RPC push messages from the worker
    if (msg.type === 'colorMapUpdate') {
      workerDistinctColors.value = msg.distinctColors ?? []
      workerColorLabelMap.value = msg.colorLabelMap ?? {}
      return
    }

    if (msg.type !== 'response') return

    const waiter = pending.get(msg.requestId)
    if (!waiter) return
    pending.delete(msg.requestId)

    if (msg.ok) waiter.resolve(msg.result)
    else waiter.reject(new Error(msg.error || 'Worker request failed'))
  }

  worker.onerror = (event) => {
    const message = event.message || 'DuckDB worker crashed'
    initError.value = message
    for (const waiter of pending.values()) {
      waiter.reject(new Error(message))
    }
    pending.clear()
  }

  return worker
}

function rpc<K extends keyof WorkerMethodMap>(
  method: K,
  params: WorkerMethodMap[K]['params'],
): Promise<WorkerMethodMap[K]['result']> {
  const w = getWorker()
  const requestId = nextRequestId++

  return new Promise<WorkerMethodMap[K]['result']>((resolve, reject) => {
    pending.set(requestId, {
      resolve: (value) => resolve(value as WorkerMethodMap[K]['result']),
      reject,
    })

    const message: WorkerRequest<K> = {
      type: 'request',
      requestId,
      method,
      params,
    }
    w.postMessage(message)
  })
}

function parseDuckdbTileUrl(url: string): { z: number; x: number; y: number } | null {
  const m = url.match(/^duckdb:\/\/trees\/(\d+)\/(\d+)\/(\d+)\.pbf(?:\?.*)?$/)
  if (!m) return null
  return { z: Number(m[1]), x: Number(m[2]), y: Number(m[3]) }
}

function fireAndForget<K extends Exclude<keyof WorkerMethodMap,
  'ensureInit' | 'prefetchVisibleDetailTilesAtZoom' | 'prewarmLodCaches' | 'query' | 'getTile'>>(
    method: K,
    params: WorkerMethodMap[K]['params'],
  ): void {
  void rpc(method, params).catch((e) => {
    console.warn(`[DuckDB RPC] ${String(method)} failed`, e)
  })
}

async function ensureInit() {
  if (ready.value) return
  if (!initPromise) {
    initPromise = rpc('ensureInit', {})
      .then((state) => {
        ready.value = !!state.ready
        initError.value = state.initError
      })
      .catch((e) => {
        ready.value = false
        initError.value = (e as Error).message
        throw e
      })
  }
  await initPromise
}

async function setTileQuery(sql: string | null): Promise<void> {
  await ensureInit()
  await rpc('setTileQuery', { sql })
}

async function setPublishedTreeIdFilterSql(sql: string | null): Promise<void> {
  await ensureInit()
  await rpc('setPublishedTreeIdFilterSql', { sql })
}

async function setColorOverrideSql(sql: string | null): Promise<void> {
  await ensureInit()
  await rpc('setColorOverrideSql', { sql })
}

async function setCityContext(city: string): Promise<void> {
  await ensureInit()
  await rpc('setCityContext', { city })
}

function setViewportZoom(zoom: number) {
  fireAndForget('setViewportZoom', { zoom })
}

function setViewportCenter(lng: number, lat: number) {
  fireAndForget('setViewportCenter', { lng, lat })
}

function setVisibleTileRange(z: number, minX: number, maxX: number, minY: number, maxY: number) {
  fireAndForget('setVisibleTileRange', { z, minX, maxX, minY, maxY })
}

async function prefetchVisibleDetailTilesAtZoom(z: number, range?: TileRangeParams): Promise<PrefetchStatus> {
  await ensureInit()
  return rpc('prefetchVisibleDetailTilesAtZoom', { z, range })
}

async function prewarmLodCaches(): Promise<void> {
  await ensureInit()
  await rpc('prewarmLodCaches', {})
}

async function ensureTileProtocolRegistered() {
  if (protocolRegistered) return
  await ensureInit()

  maplibregl.addProtocol('duckdb', async (params) => {
    const parsed = parseDuckdbTileUrl(params.url)
    if (!parsed) return { data: new Uint8Array() }

    const result = await rpc('getTile', parsed)
    return { data: new Uint8Array(result.tileBuffer) }
  })

  protocolRegistered = true
}

function setAutoTileFetchEnabled(enabled: boolean) {
  fireAndForget('setAutoTileFetchEnabled', { enabled })
}

async function invalidateTileCaches(): Promise<void> {
  await ensureInit()
  await rpc('invalidateTileCaches', {})
}

async function query(sql: string): Promise<{ columns: string[]; rows: Record<string, unknown>[] }> {
  await ensureInit()
  return rpc('query', { sql })
}

if (!initPromise) {
  initPromise = ensureInit().catch((e) => {
    initError.value = (e as Error).message
    console.error('DuckDB worker init failed:', e)
  })
}

export function useDuckDB() {
  return {
    ready,
    initError,
    query,
    ensureInit,
    ensureTileProtocolRegistered,
    setTileQuery,
    setPublishedTreeIdFilterSql,
    setColorOverrideSql,
    setCityContext,
    setViewportZoom,
    setViewportCenter,
    setVisibleTileRange,
    prefetchVisibleDetailTilesAtZoom,
    prewarmLodCaches,
    setAutoTileFetchEnabled,
    invalidateTileCaches,
    workerDistinctColors,
    workerColorLabelMap,
  }
}
