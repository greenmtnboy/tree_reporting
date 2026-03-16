// Multi-city tree data and enrichment (combined dataset in new bucket)
const REMOTE_TREES_BASE_URL = 'https://storage.googleapis.com/trilogy_public_models/duckdb/trees'
export const REMOTE_TREES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/full_tree_info.parquet`
export const REMOTE_SPECIES_PARQUET_URL = `${REMOTE_TREES_BASE_URL}/tree_enrichment.parquet`

/** Per-city optimised parquet (e.g. ussfo_tree_info.parquet). Returns null if city code is unknown. */
export function cityTreeParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^us[a-z]{3}$/)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_info.parquet`
}

// Multi-city landmark data
const REMOTE_LANDMARKS_BASE_URL = 'https://storage.googleapis.com/trilogy_public_models/duckdb/landmarks'
export const REMOTE_LANDMARKS_PARQUET_URL = `${REMOTE_LANDMARKS_BASE_URL}/full_landmark_info.parquet`

/** Per-city optimised landmark parquet (e.g. ussfo_landmark_info.parquet). Returns null if city code is unknown. */
export function cityLandmarkParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!lower.match(/^us[a-z]{3}$/)) return null
  return `${REMOTE_LANDMARKS_BASE_URL}/${lower}_landmark_info.parquet`
}
