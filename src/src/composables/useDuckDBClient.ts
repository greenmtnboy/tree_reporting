import { ref } from 'vue'
import maplibregl from 'maplibre-gl'
import DuckDBPipelineWorker from '../workers/duckdbPipeline.worker?worker'
import type { TreeRenderView } from '../lib/treeCrowns'
import type { CrownBatch, CrownBatchOptions } from '../lib/crownBatch'
import type { WindowPolicy } from '../workers/cityWindow'
import type { PointChunkBatch, PointChunkColors, PointChunkDirectory, PointKeys } from '../workers/cityPoints'
import type { WorldDirectory } from '../workers/worldBlocks'

type PrefetchStatus = 'executed' | 'deduped' | 'skipped'
type TileRangeParams = { minX: number; maxX: number; minY: number; maxY: number }

type WorkerMethodMap = {
  getCrownBatch: { params: { camera: TreeRenderView; options: CrownBatchOptions }; result: CrownBatch }
  getPointKeys: { params: Record<string, never>; result: PointKeys | null }
  getPointChunkDirectory: { params: Record<string, never>; result: PointChunkDirectory | null }
  getPointChunks: { params: { rowsKey: string; keys: number[] }; result: PointChunkBatch | null }
  getPointChunkColors: { params: { rowsKey: string; keys: number[] }; result: PointChunkColors | null }
  getWorldDirectory: { params: Record<string, never>; result: WorldDirectory | null }
  ensureInit: { params: { city?: string; window?: WindowPolicy | null }; result: { ready: boolean; initError: string | null } }
  setTileQuery: { params: { sql: string | null }; result: void }
  setPublishedTreeIdFilterSql: { params: { sql: string | null }; result: void }
  setColorOverrideSql: { params: { sql: string | null }; result: void }
  setSpeciesShadeSql: { params: { sql: string | null }; result: void }
  setDbhColors: { params: { enabled: boolean }; result: void }
  setCityContext: { params: { city: string; window?: WindowPolicy | null }; result: void }
  setViewportZoom: { params: { zoom: number }; result: void }
  setViewportCenter: { params: { lng: number; lat: number }; result: void }
  ensureWindowAt: { params: { lng: number; lat: number }; result: void }
  prefetchWindowAt: { params: { lng: number; lat: number }; result: void }
  setVisibleTileRange: { params: { z: number; minX: number; maxX: number; minY: number; maxY: number }; result: void }
  prefetchVisibleTilesAtZoom: { params: { z: number; range?: TileRangeParams }; result: PrefetchStatus }
  prewarmLodCaches: { params: Record<string, never>; result: void }
  setAutoTileFetchEnabled: { params: { enabled: boolean }; result: void }
  invalidateTileCaches: { params: Record<string, never>; result: void }
  query: { params: { sql: string; maxRows?: number; at?: { lng: number; lat: number } }; result: { columns: string[]; rows: Record<string, unknown>[]; totalRows: number } }
  getTile: { params: { z: number; x: number; y: number }; result: { tileBuffer: ArrayBuffer; transient?: boolean } }
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
const dbPopulatedMs = ref<number | null>(null)
const dbTreeCount = ref<number | null>(null)

let worker: Worker | null = null
let nextRequestId = 1
let initPromise: Promise<void> | null = null
let protocolRegistered = false
/** The phone's window (useMapWindow.ts), sent with every bookmark; null is the desktop's, which the worker sizes itself. */
let windowPolicy: WindowPolicy | null = null

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

    if (msg.type === 'cityContextReady') {
      dbPopulatedMs.value = msg.loadMs as number
      dbTreeCount.value = msg.treeCount as number
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
  'ensureInit' | 'prefetchVisibleTilesAtZoom' | 'prewarmLodCaches' | 'query' | 'getTile'>>(
    method: K,
    params: WorkerMethodMap[K]['params'],
  ): void {
  void rpc(method, params).catch((e) => {
    console.warn(`[DuckDB RPC] ${String(method)} failed`, e)
  })
}

async function ensureInit(city?: string) {
  if (ready.value) return
  if (!initPromise) {
    initPromise = rpc('ensureInit', { city, window: windowPolicy })
      .then((state) => {
        // Do NOT set ready.value here — city data isn't loaded yet.
        // setCityContext sets ready.value = true once city tables exist.
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

async function setSpeciesShadeSql(sql: string | null): Promise<void> {
  await ensureInit()
  await rpc('setSpeciesShadeSql', { sql })
}

/** Colour by trunk diameter: a rule over the row, so it holds for trees paged in later. */
async function setDbhColors(enabled: boolean): Promise<void> {
  await ensureInit()
  await rpc('setDbhColors', { enabled })
}

/** The world's bookmarks and blocks, once the worker has read the manifest. */
let worldDirectory: Promise<WorldDirectory | null> | null = null
function getWorldDirectory(): Promise<WorldDirectory | null> {
  worldDirectory ??= ensureInit().then(() => rpc('getWorldDirectory', {})).catch((e) => {
    worldDirectory = null
    throw e
  })
  return worldDirectory
}

/**
 * Make `city` the bookmark in context: its landmarks and its city-wide reads.
 * No trees move; the worker holds whatever is near the camera.
 */
async function setCityContext(city: string): Promise<void> {
  await ensureInit(city)
  await rpc('setCityContext', { city, window: windowPolicy })
  // The world's tables and the bookmark's landmarks are up — signal the rest of the app.
  ready.value = true
}

/** Load the cell under a point, before a read about one tree there. */
async function ensureWindowAt(lng: number, lat: number): Promise<void> {
  await ensureInit()
  await rpc('ensureWindowAt', { lng, lat })
}

/**
 * Start fetching the trees under a point the map is about to open on (Near
 * Me's fix), while the engine that will read them is still downloading.
 */
function prefetchWindowAt(lng: number, lat: number): void {
  fireAndForget('prefetchWindowAt', { lng, lat })
}

/** Set before the first city load; the map's layout decides it. */
function setWindowPolicy(policy: WindowPolicy | null): void {
  windowPolicy = policy
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

async function prefetchVisibleTilesAtZoom(z: number, range?: TileRangeParams): Promise<PrefetchStatus> {
  await ensureInit()
  return rpc('prefetchVisibleTilesAtZoom', { z, range })
}

async function prewarmLodCaches(): Promise<void> {
  await ensureInit()
  await rpc('prewarmLodCaches', {})
}

/**
 * How long MapLibre may cache a tile that was answered while fetching was paused.
 * This expiry is the only thing that refreshes those empties after the intro:
 * MapLibre re-requests an expired tile in place, keeping every other tile on
 * screen, where a URL-nonce bump would clear the whole source first. Kept
 * short so stragglers fill in right after landing; each re-poll while still
 * paused is a cache lookup in the worker, not a query.
 */
const TRANSIENT_TILE_TTL_MS = 1500

async function ensureTileProtocolRegistered(initialCity?: string) {
  if (protocolRegistered) return
  await ensureInit(initialCity)

  maplibregl.addProtocol('duckdb', async (params) => {
    const parsed = parseDuckdbTileUrl(params.url)
    if (!parsed) return { data: new Uint8Array() }

    const result = await rpc('getTile', parsed)
    const data = new Uint8Array(result.tileBuffer)
    if (result.transient) {
      // Empty response produced while auto tile fetch was paused (intro/swoop).
      // Give it a short expiry so MapLibre re-requests it instead of caching
      // the empty as if the tile genuinely had no trees.
      return { data, expires: new Date(Date.now() + TRANSIENT_TILE_TTL_MS) }
    }
    return { data }
  })

  protocolRegistered = true
}

/** Start DuckDB init early (e.g. before the map is created) with a known city. */
function preWarmForCity(city: string) {
  void ensureInit(city)
}

function setAutoTileFetchEnabled(enabled: boolean) {
  fireAndForget('setAutoTileFetchEnabled', { enabled })
}

async function invalidateTileCaches(): Promise<void> {
  await ensureInit()
  await rpc('invalidateTileCaches', {})
}

/**
 * Run `sql` in the map's worker; `maxRows` keeps that many rows and counts
 * the rest. `at` is for a read about one tree: the worker loads the cell
 * under that point and reads TREES_AT_TOKEN in the SQL as its table.
 */
async function query(sql: string, options: { maxRows?: number; at?: { lng: number; lat: number } } = {}): Promise<{ columns: string[]; rows: Record<string, unknown>[]; totalRows: number }> {
  await ensureInit()
  return rpc('query', { sql, maxRows: options.maxRows, at: options.at })
}

export function useDuckDB() {
  return {
    getCrownBatch: (camera: TreeRenderView, options: CrownBatchOptions) => rpc('getCrownBatch', { camera, options }),
    getPointKeys: () => rpc('getPointKeys', {}),
    getPointChunkDirectory: () => rpc('getPointChunkDirectory', {}),
    getPointChunks: (rowsKey: string, keys: number[]) => rpc('getPointChunks', { rowsKey, keys }),
    getPointChunkColors: (rowsKey: string, keys: number[]) => rpc('getPointChunkColors', { rowsKey, keys }),
    ready,
    initError,
    query,
    ensureInit,
    preWarmForCity,
    ensureTileProtocolRegistered,
    setTileQuery,
    setPublishedTreeIdFilterSql,
    setColorOverrideSql,
    setSpeciesShadeSql,
    setDbhColors,
    getWorldDirectory,
    setCityContext,
    setWindowPolicy,
    ensureWindowAt,
    prefetchWindowAt,
    setViewportZoom,
    setViewportCenter,
    setVisibleTileRange,
    prefetchVisibleTilesAtZoom,
    prewarmLodCaches,
    setAutoTileFetchEnabled,
    invalidateTileCaches,
    workerDistinctColors,
    workerColorLabelMap,
    dbPopulatedMs,
    dbTreeCount,
  }
}
