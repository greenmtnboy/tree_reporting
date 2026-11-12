/**
 * Local stand-ins for the published files the worker reads. `fixtureParquetUrls`
 * replaces `../parquetUrls` (`vi.mock`), pointing every URL into one temporary
 * directory; a file a test does not write is simply missing, which the worker
 * already tolerates for everything but the city's trees (species enrichment,
 * ecoregions and landmarks each load in their own try).
 */
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createNodeDuckDB } from './nodeDuckDB'
import { WORLD_BLOCK_COLUMNS, VOCAB_KINDS } from '../cityTables'
import { BLOCK_ZOOM } from '../worldBlocks'

export const FIXTURE_DIR = mkdtempSync(join(tmpdir(), 'tree-worker-wasm-')).replace(/\\/g, '/')

const lower = (city: string) => city.toLowerCase()

const WORLD_DIR = `${FIXTURE_DIR}/world/v1`

/** The worker's own error for a world file that is not there (parquetUrls.ts). */
export class WorldFileError extends Error {
  constructor(readonly file: string, readonly status: number) {
    super(`World file ${file}: HTTP ${status}`)
  }
}

/** Every world file the worker has fetched, oldest first, for tests that count them. */
export const fetchedWorldFiles: string[] = []

/** Same exports as `../parquetUrls`, all under FIXTURE_DIR. */
export const fixtureParquetUrls = {
  REMOTE_TREES_BASE_URL: FIXTURE_DIR,
  TREE_DATA_VERSION: 2,
  REMOTE_TREES_PARQUET_URL: `${FIXTURE_DIR}/full_tree_info_v2.parquet`,
  REMOTE_SPECIES_PARQUET_URL: `${FIXTURE_DIR}/tree_enrichment_v2.parquet`,
  REMOTE_ECOREGION_PARQUET_URL: `${FIXTURE_DIR}/ecoregion_info_v2.parquet`,
  REMOTE_ECOREGION_BROWSER_PARQUET_URL: `${FIXTURE_DIR}/ecoregion_browser_v2.parquet`,
  REMOTE_LANDMARKS_PARQUET_URL: `${FIXTURE_DIR}/full_landmark_info_v2.parquet`,
  WORLD_BASE_URL: WORLD_DIR,
  WORLD_MANIFEST_URL: `${WORLD_DIR}/manifest.json`,
  worldFileUrl: (name: string) => `${WORLD_DIR}/${name}`,
  WorldFileError,
  // The start's small files, whole, as the worker fetches them beside the engine.
  fetchPublishedFile: async (url: string): Promise<Uint8Array | null> => existsSync(url) ? new Uint8Array(readFileSync(url)) : null,
  fetchWorldManifest: async (): Promise<unknown> => JSON.parse(readFileSync(`${WORLD_DIR}/manifest.json`, 'utf-8')),
  fetchWorldFile: async (name: string): Promise<Uint8Array> => {
    const path = `${WORLD_DIR}/${name}`
    if (!existsSync(path)) throw new WorldFileError(name, 404)
    fetchedWorldFiles.push(name)
    return new Uint8Array(readFileSync(path))
  },
  cityTreeParquetUrl: (city: string) => `${FIXTURE_DIR}/${lower(city)}_tree_info_v2.parquet`,
  cityBrowserParquetUrl: (city: string) => `${FIXTURE_DIR}/${lower(city)}_tree_browser_v2.parquet`,
  cityLandmarkParquetUrl: (city: string) => `${FIXTURE_DIR}/${lower(city)}_landmark_info_v2.parquet`,
}

export type CityFixture = {
  city: string
  rows: number
  /** Box the trees are scattered over: [west, south, east, north]. */
  bbox: [number, number, number, number]
  /** The four species the trees cycle through; a second city can bring its own. */
  species?: [string, string, string, string]
}

/**
 * A city's browser projection (data/raw/tree_browser_city.preql's columns)
 * with `rows` trees scattered uniformly over `bbox`, the same every run.
 */
export async function writeCityProjection({ city, rows, bbox, species }: CityFixture): Promise<string> {
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
      [${(species ?? ['Acer rubrum', 'Quercus palustris', 'Gleditsia triacanthos', 'Platanus x acerifolia']).map((s) => `'${s}'`).join(', ')}][1 + i % 4] AS species,
      CAST(NULL AS VARCHAR) AS cultivar,
      CAST(NULL AS DOUBLE) AS crown_width_m,
      CAST(4 + i % 9 AS DOUBLE) AS predicted_crown_width_m,
      CAST(CASE WHEN i % 5 = 0 THEN 8 + i % 15 END AS FLOAT) AS height_m,
      CAST(6 + i % 12 AS FLOAT) AS predicted_height_m,
      ${south} + random() * ${north - south} AS latitude,
      ${west} + random() * ${east - west} AS longitude,
      CAST(5 + i % 30 AS DOUBLE) AS diameter_at_breast_height,
      CAST(NULL AS VARCHAR) AS submission_photo_url
    FROM range(${rows}) t(i)
  ) TO '${path}' (FORMAT parquet)`)
  conn.close()
  return path
}

/**
 * The world's block files, vocabulary and manifest (data/world/publish_blocks.py's
 * output) cut from the projections of `cities`, which must already be written.
 * The same layout the pipeline publishes: one file per z12 block holding every
 * city's trees in it, rows in cell order, and a manifest that names them.
 * `build` stands in for the content hash, so two worlds in one test differ.
 */
export async function writeWorld(cities: string[], build = '0'): Promise<{ blocks: number; trees: number }> {
  mkdirSync(`${WORLD_DIR}/blocks`, { recursive: true })
  const db = await createNodeDuckDB()
  const conn = db.connect()
  const n = 2 ** 20
  const lat = 'LEAST(GREATEST(latitude, -85.05112878), 85.05112878)'
  const sources = cities.map((city) => `'${fixtureParquetUrls.cityBrowserParquetUrl(city)}'`).join(', ')
  conn.query(`CREATE TABLE world AS
    SELECT ${WORLD_BLOCK_COLUMNS.join(', ')},
      CAST(floor(((longitude + 180.0) / 360.0) * ${n}) AS INTEGER) AS xt,
      CAST(floor((1.0 - ln(tan(radians(${lat})) + 1.0 / cos(radians(${lat}))) / pi()) / 2.0 * ${n}) AS INTEGER) AS yt
    FROM read_parquet([${sources}], union_by_name = true)
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL`)
  const shift = 20 - BLOCK_ZOOM
  const block = `((xt >> ${shift}) * ${2 ** BLOCK_ZOOM} + (yt >> ${shift}))`
  const hash = (key: number) => `${build}${key.toString(16)}`.padStart(16, '0').slice(-16)
  const order = cities.map((city, i) => `WHEN '${city}' THEN ${i}`).join(' ')
  const perBlock = new Map<number, { rows: number; cities: number[] }>()
  for (const row of conn.query(`SELECT ${block} AS block, CASE city ${order} END AS city, count(*) AS n FROM world GROUP BY 1, 2 ORDER BY 1, 2`).toArray()) {
    const entry = perBlock.get(Number(row.block)) ?? { rows: 0, cities: [] }
    entry.rows += Number(row.n)
    entry.cities.push(Number(row.city), Number(row.n))
    perBlock.set(Number(row.block), entry)
  }
  for (const key of perBlock.keys()) {
    conn.query(`COPY (SELECT ${WORLD_BLOCK_COLUMNS.join(', ')} FROM world WHERE ${block} = ${key} ORDER BY (yt >> 6), (xt >> 6), tree_id)
      TO '${WORLD_DIR}/blocks/${key}-${hash(key)}.parquet' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 10240)`)
  }
  const vocab = `vocab-${hash(0)}.parquet`
  conn.query(`COPY (${Object.values(VOCAB_KINDS).map((column) =>
    `SELECT '${column}' AS kind, ${column} AS value FROM world WHERE ${column} IS NOT NULL GROUP BY 2`).join(' UNION ALL ')}
    ) TO '${WORLD_DIR}/${vocab}' (FORMAT parquet, COMPRESSION zstd)`)
  const perCity = conn.query(`SELECT city, count(*) AS n, count(DISTINCT ${block}) AS blocks FROM world GROUP BY 1`).toArray()
  const trees = [...perBlock.values()].reduce((sum, b) => sum + b.rows, 0)
  conn.close()
  writeFileSync(`${WORLD_DIR}/manifest.json`, JSON.stringify({
    format: 1,
    generated_at: `2026-11-09T06:00:0${build}+00:00`,
    block_zoom: BLOCK_ZOOM,
    columns: WORLD_BLOCK_COLUMNS,
    vocab,
    trees,
    cities: cities.map((city) => {
      const row = perCity.find((r) => String(r.city) === city)
      return { code: city, name: city, center: [0, 0], trees: Number(row?.n ?? 0), blocks: Number(row?.blocks ?? 0), generation: 1 }
    }),
    blocks: [...perBlock.entries()].map(([key, b]) => [key, b.rows, hash(key), b.cities]),
  }))
  return { blocks: perBlock.size, trees }
}
