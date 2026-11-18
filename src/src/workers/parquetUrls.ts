// Multi-city tree data and enrichment (combined dataset in new bucket).
// VITE_TREES_BASE_URL points the worker at a local copy of the published
// files, for measuring a load against a file that is not published yet
// (docs/MOBILE_LOAD_BUDGET.md); the dashboards and chat keep the bucket.
export const REMOTE_TREES_BASE_URL: string =
  import.meta.env?.VITE_TREES_BASE_URL || 'https://storage.googleapis.com/trilogy_public_models/duckdb/trees'
export const TREE_DATA_VERSION = 2
export const REMOTE_TREES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/full_tree_info_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_SPECIES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/tree_enrichment_v${TREE_DATA_VERSION}.parquet`
/** Whether a genus drops its leaves where it stands, per 1-degree cell (data/raw/foliage_defaults.preql; lib/foliageSeason). */
export const REMOTE_FOLIAGE_DEFAULTS_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/foliage_defaults_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_ECOREGION_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/ecoregion_info_v${TREE_DATA_VERSION}.parquet`
/** The ecoregion dimension as the worker reads it: id, biome, realm (data/raw/ecoregion_browser.preql). */
export const REMOTE_ECOREGION_BROWSER_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/ecoregion_browser_v${TREE_DATA_VERSION}.parquet`

/**
 * The world's trees as geo blocks (worldBlocks.ts, data/world/publish_blocks.py):
 * the manifest, and the directory its content-addressed block and vocabulary
 * files sit in. This is what the map draws from; the per-city files below
 * serve the reads that are about one city (a species count, a deep link).
 */
export const WORLD_BASE_URL: string = import.meta.env?.VITE_WORLD_BASE_URL || `${REMOTE_TREES_BASE_URL}/world/v1`
export const WORLD_MANIFEST_URL = `${WORLD_BASE_URL}/manifest.json`

/** A file under the world's base URL, by the name the manifest gives it. */
export function worldFileUrl(name: string): string {
  return `${WORLD_BASE_URL}/${name}`
}

/**
 * The manifest, never from the HTTP cache: it is the one mutable object, and
 * a stale copy can name blocks that have since been deleted.
 */
export async function fetchWorldManifest(): Promise<unknown> {
  const response = await fetch(WORLD_MANIFEST_URL, { cache: 'no-cache' })
  if (!response.ok) throw new Error(`World manifest: HTTP ${response.status}`)
  return response.json()
}

/**
 * A block or vocabulary file, whole. They are immutable, so the browser's
 * cache answers a revisit, and fetched here rather than by DuckDB so a view's
 * blocks download side by side instead of one range request at a time.
 */
export async function fetchWorldFile(name: string): Promise<Uint8Array> {
  const response = await fetch(worldFileUrl(name))
  if (!response.ok) throw new WorldFileError(name, response.status)
  return new Uint8Array(await response.arrayBuffer())
}

/**
 * Part of a block file: bytes `[start, end)`, or its last `suffix` bytes.
 * The answer says where the bytes start and how long the whole file is (the
 * bucket exposes Content-Range). A server that ignores the range answers with
 * the whole file, which is reported as such.
 */
export async function fetchWorldRange(
  name: string, range: { start: number; end: number } | { suffix: number },
): Promise<{ bytes: Uint8Array; start: number; total: number }> {
  const header = 'suffix' in range ? `bytes=-${range.suffix}` : `bytes=${range.start}-${range.end - 1}`
  const response = await fetch(worldFileUrl(name), { headers: { Range: header } })
  if (!response.ok) throw new WorldFileError(name, response.status)
  const bytes = new Uint8Array(await response.arrayBuffer())
  if (response.status !== 206) return { bytes, start: 0, total: bytes.length }
  const match = /^bytes (\d+)-\d+\/(\d+)$/.exec(response.headers.get('Content-Range') ?? '')
  if (!match) throw new Error(`World file ${name}: a partial answer with no readable Content-Range`)
  return { bytes, start: Number(match[1]), total: Number(match[2]) }
}

/**
 * A small published file, whole, or null when it cannot be fetched. For the
 * files the worker's start reads in full anyway: asked for beside the engine's
 * download instead of after it, and in one request where DuckDB's range reads
 * of the same file are a dozen in a row.
 */
export async function fetchPublishedFile(url: string): Promise<Uint8Array | null> {
  try {
    const response = await fetch(url)
    return response.ok ? new Uint8Array(await response.arrayBuffer()) : null
  } catch {
    return null
  }
}

/**
 * The DuckDB release the bundled duckdb-wasm is built from: the directory of
 * extensions.duckdb.org it asks for its parquet reader in. The engine only
 * asks once it is running and meets its first parquet file, so the worker
 * asks for the same URL earlier, beside the engine's own download, and the
 * engine's request is then answered from the browser's cache. A wrong version
 * here is a megabyte fetched for nothing and no error, so
 * duckdbPipeline.wasm.test.ts pins it to the engine's own answer.
 */
export const DUCKDB_EXTENSION_VERSION = 'v1.4.3'

/** The parquet extension the engine at `mainModule` will load, or null when it should not be asked for ahead. */
export function parquetExtensionUrl(mainModule: string): string | null {
  const platform = /duckdb-mvp/.test(mainModule) ? 'wasm_mvp' : 'wasm_eh'
  return `https://extensions.duckdb.org/${DUCKDB_EXTENSION_VERSION}/${platform}/parquet.duckdb_extension.wasm`
}

export class WorldFileError extends Error {
  constructor(readonly file: string, readonly status: number) {
    super(`World file ${file}: HTTP ${status}`)
  }
}

/** Per-city optimised parquet (e.g. ussfo_tree_info.parquet). Returns null if city code is unknown. */
export function cityTreeParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^[a-z]{2}[a-z]{3}$/)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_info_v${TREE_DATA_VERSION}.parquet`
}

/**
 * The city's browser projection (data/raw/city_derived.preql): every
 * tree of one city in the rows and columns the worker keeps. The map's trees
 * come from the world's blocks, which are cut from these; this file is what
 * a bookmark's city-wide reads use (`trees_published`). Returns null if city
 * code is unknown.
 */
export function cityBrowserParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^[a-z]{2}[a-z]{3}$/)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_browser_v${TREE_DATA_VERSION}.parquet`
}

// Multi-city landmark data
const REMOTE_LANDMARKS_BASE_URL = 'https://storage.googleapis.com/trilogy_public_models/duckdb/landmarks'
const LANDMARK_DATA_VERSION = 2
export const REMOTE_LANDMARKS_PARQUET_URL = `${REMOTE_LANDMARKS_BASE_URL}/full_landmark_info_v${LANDMARK_DATA_VERSION}.parquet`

/** Per-city optimised landmark parquet (e.g. ussfo_landmark_info.parquet). Returns null if city code is unknown. */
export function cityLandmarkParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^[a-z]{2}[a-z]{3}$/)) return null
  return `${REMOTE_LANDMARKS_BASE_URL}/${lower}_landmark_info_v${LANDMARK_DATA_VERSION}.parquet`
}
