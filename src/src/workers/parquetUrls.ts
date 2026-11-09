// Multi-city tree data and enrichment (combined dataset in new bucket).
// VITE_TREES_BASE_URL points the worker at a local copy of the published
// files, for measuring a load against a file that is not published yet
// (docs/MOBILE_LOAD_BUDGET.md); the dashboards and chat keep the bucket.
export const REMOTE_TREES_BASE_URL: string =
  import.meta.env?.VITE_TREES_BASE_URL || 'https://storage.googleapis.com/trilogy_public_models/duckdb/trees'
export const TREE_DATA_VERSION = 2
export const REMOTE_TREES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/full_tree_info_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_SPECIES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/tree_enrichment_v${TREE_DATA_VERSION}.parquet`
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
 * The city's browser projection (data/raw/tree_browser_city.preql): every
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
