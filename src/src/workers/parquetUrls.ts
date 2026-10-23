// Multi-city tree data and enrichment (combined dataset in new bucket).
// VITE_TREES_BASE_URL points the worker at a local copy of the published
// files, for measuring a load against a file that is not published yet
// (docs/MOBILE_LOAD_BUDGET.md); the dashboards and chat keep the bucket.
export const REMOTE_TREES_BASE_URL: string =
  import.meta.env?.VITE_TREES_BASE_URL || 'https://storage.googleapis.com/trilogy_public_models/duckdb/trees'
export const TREE_DATA_VERSION = 2
export const REMOTE_TREES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/full_tree_info_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_SPECIES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/tree_enrichment_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_PREDICTIONS_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/tree_predictions_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_ECOREGION_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/ecoregion_info_v${TREE_DATA_VERSION}.parquet`
/** The ecoregion dimension as the worker reads it: id, biome, realm (data/raw/ecoregion_browser.preql). */
export const REMOTE_ECOREGION_BROWSER_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/ecoregion_browser_v${TREE_DATA_VERSION}.parquet`

/** Per-city optimised parquet (e.g. ussfo_tree_info.parquet). Returns null if city code is unknown. */
export function cityTreeParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^[a-z]{2}[a-z]{3}$/)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_info_v${TREE_DATA_VERSION}.parquet`
}

/**
 * The city's browser projection (data/raw/tree_browser_city.preql): the
 * rows and columns the worker keeps, display name, growth form and crown
 * prediction resolved by the pipeline. Returns null if city code is unknown.
 */
export function cityBrowserParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^[a-z]{2}[a-z]{3}$/)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_browser_v${TREE_DATA_VERSION}.parquet`
}

/**
 * Per-city crown-predictions slice. The job that wrote these is gone (the
 * browser projection carries the prediction); the files it left stay
 * published and feed the fallback load of a city whose projection is not
 * out yet. Returns null if city code is unknown.
 */
export function cityPredictionsParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^[a-z]{2}[a-z]{3}$/)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_predictions_v${TREE_DATA_VERSION}.parquet`
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
