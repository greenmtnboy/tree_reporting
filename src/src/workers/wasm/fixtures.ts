/**
 * Local stand-ins for the published files the worker reads. `fixtureParquetUrls`
 * replaces `../parquetUrls` (`vi.mock`), pointing every URL into one temporary
 * directory; a file a test does not write is simply missing, which the worker
 * already tolerates for everything but the city's trees (species enrichment,
 * ecoregions and landmarks each load in their own try).
 */
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createNodeDuckDB } from './nodeDuckDB'

export const FIXTURE_DIR = mkdtempSync(join(tmpdir(), 'tree-worker-wasm-')).replace(/\\/g, '/')

const lower = (city: string) => city.toLowerCase()

/** Same exports as `../parquetUrls`, all under FIXTURE_DIR. */
export const fixtureParquetUrls = {
  REMOTE_TREES_BASE_URL: FIXTURE_DIR,
  TREE_DATA_VERSION: 2,
  REMOTE_TREES_PARQUET_URL: `${FIXTURE_DIR}/full_tree_info_v2.parquet`,
  REMOTE_SPECIES_PARQUET_URL: `${FIXTURE_DIR}/tree_enrichment_v2.parquet`,
  REMOTE_PREDICTIONS_PARQUET_URL: `${FIXTURE_DIR}/tree_predictions_v2.parquet`,
  REMOTE_ECOREGION_PARQUET_URL: `${FIXTURE_DIR}/ecoregion_info_v2.parquet`,
  REMOTE_ECOREGION_BROWSER_PARQUET_URL: `${FIXTURE_DIR}/ecoregion_browser_v2.parquet`,
  REMOTE_LANDMARKS_PARQUET_URL: `${FIXTURE_DIR}/full_landmark_info_v2.parquet`,
  cityTreeParquetUrl: (city: string) => `${FIXTURE_DIR}/${lower(city)}_tree_info_v2.parquet`,
  cityBrowserParquetUrl: (city: string) => `${FIXTURE_DIR}/${lower(city)}_tree_browser_v2.parquet`,
  cityPredictionsParquetUrl: (city: string) => `${FIXTURE_DIR}/${lower(city)}_tree_predictions_v2.parquet`,
  cityLandmarkParquetUrl: (city: string) => `${FIXTURE_DIR}/${lower(city)}_landmark_info_v2.parquet`,
}

export type CityFixture = {
  city: string
  rows: number
  /** Box the trees are scattered over: [west, south, east, north]. */
  bbox: [number, number, number, number]
}

/**
 * A city's browser projection (data/raw/tree_browser_city.preql's columns)
 * with `rows` trees scattered uniformly over `bbox`, the same every run.
 */
export async function writeCityProjection({ city, rows, bbox }: CityFixture): Promise<string> {
  const [west, south, east, north] = bbox
  const path = fixtureParquetUrls.cityBrowserParquetUrl(city)
  const db = await createNodeDuckDB()
  const conn = db.connect()
  conn.query('SELECT setseed(0.42)')
  conn.query(`COPY (
    SELECT
      '${lower(city)}-' || i AS tree_id,
      '${city}' AS city,
      'FIXTURE' AS data_source,
      CAST(NULL AS VARCHAR) AS tree_name,
      ['Red maple', 'Pin oak', 'Honeylocust', 'London plane'][1 + i % 4] AS display_name,
      ['broadleaf', 'spreading', 'columnar', 'default'][1 + i % 4] AS tree_form,
      CAST(NULL AS DATE) AS plant_date,
      ['Acer rubrum', 'Quercus palustris', 'Gleditsia triacanthos', 'Platanus x acerifolia'][1 + i % 4] AS species,
      CAST(NULL AS VARCHAR) AS cultivar,
      CAST(NULL AS DOUBLE) AS crown_width_m,
      CAST(4 + i % 9 AS DOUBLE) AS predicted_crown_width_m,
      ${south} + random() * ${north - south} AS latitude,
      ${west} + random() * ${east - west} AS longitude,
      CAST(5 + i % 30 AS DOUBLE) AS diameter_at_breast_height,
      CAST(NULL AS VARCHAR) AS submission_photo_url
    FROM range(${rows}) t(i)
  ) TO '${path}' (FORMAT parquet)`)
  conn.close()
  return path
}
