// Multi-city tree data and enrichment (combined dataset in new bucket)
const REMOTE_TREES_BASE_URL = 'https://storage.googleapis.com/trilogy_public_models/duckdb/trees'
const TREE_DATA_VERSION = 2
export const REMOTE_TREES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/full_tree_info_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_SPECIES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/tree_enrichment_v${TREE_DATA_VERSION}.parquet`
export const REMOTE_ECOREGION_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/ecoregion_info_v${TREE_DATA_VERSION}.parquet`

/** Per-city optimised parquet (e.g. ussfo_tree_info.parquet). Returns null if city code is unknown. */
export function cityTreeParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^[a-z]{2}[a-z]{3}$/)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_info_v${TREE_DATA_VERSION}.parquet`
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
