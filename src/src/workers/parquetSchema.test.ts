import { describe, test, expect } from 'vitest'
import { DuckDBInstance } from '@duckdb/node-api'
import { REMOTE_TREES_PARQUET_URL, REMOTE_SPECIES_PARQUET_URL, cityTreeParquetUrl } from './parquetUrls'
import { formatDataSource } from '../data/dataSources'
import { CITY_CONFIG } from '../composables/useMapData'
import { SPECIES_SENTINELS } from '../data/species'
import { ALL_MODEL_SOURCES } from '../trilogyModels'

async function withDuckDB<T>(fn: (conn: Awaited<ReturnType<DuckDBInstance['connect']>>) => Promise<T>): Promise<T> {
  const instance = await DuckDBInstance.create(':memory:')
  const conn = await instance.connect()
  try {
    return await fn(conn)
  } finally {
    conn.closeSync()
  }
}

describe('parquet schema', () => {
  test('full_tree_info loads and has required columns including city', async () => {
    await withDuckDB(async (conn) => {
      await conn.run(`CREATE TABLE trees AS SELECT * FROM read_parquet('${REMOTE_TREES_PARQUET_URL}')`)

      const colResult = await conn.runAndReadAll(`SELECT column_name FROM information_schema.columns WHERE table_name = 'trees'`)
      const cols = colResult.getRowObjects().map((r) => r.column_name as string)
      const required = [
        'tree_id', 'city', 'species', 'latitude', 'longitude', 'diameter_at_breast_height',
        // The worker selects these by name; a parquet without them fails to load
        // at all rather than degrading, so assert the contract here.
        'data_source', 'submission_photo_url',
      ]
      for (const col of required) {
        expect(cols, `missing column: ${col}`).toContain(col)
      }

      const countResult = await conn.runAndReadAll(`SELECT city, COUNT(*) AS n FROM trees GROUP BY city ORDER BY city`)
      const counts = countResult.getRowObjects()
      expect(counts.length, 'expected at least one city in data').toBeGreaterThan(0)
      const cities = counts.map((r) => r.city as string)
      expect(cities).toContain('USSFO')
      expect(cities).toContain('USNYC')

      // Every row must be attributable to a source, and every source must be one
      // the frontend's picklist knows about.
      const sourceResult = await conn.runAndReadAll(
        `SELECT DISTINCT data_source FROM trees WHERE data_source IS NOT NULL`,
      )
      const sources = sourceResult.getRowObjects().map((r) => r.data_source as string)
      expect(sources.length, 'expected at least one data_source value').toBeGreaterThan(0)
      const nullSources = await conn.runAndReadAll(`SELECT COUNT(*) AS n FROM trees WHERE data_source IS NULL`)
      expect(Number(nullSources.getRowObjects()[0].n), 'every tree must carry a data_source').toBe(0)
      for (const source of sources) {
        expect(formatDataSource(source), `unlabelled data_source: ${source}`).toBeTruthy()
      }
    })
  }, 60_000)

  // Absorbed duplicates are pruned at the source: every city's published
  // target carries `where tree_id = cluster_id`, so a rebuilt parquet has one
  // row per tree and no `is_duplicate` column at all.
  //
  // The presence of that column is therefore the tell that a city has NOT been
  // rebuilt past the prune, and its rows still include the ones its clusters
  // absorbed. Nothing breaks while that is true -- the worker probes for the
  // column and keeps filtering when it is there -- so this is a staleness gate,
  // not a crash gate: it says "this city still needs its forced refresh".
  //
  // A parquet that does not exist at all is the one carve-out, for the same
  // reason as ever: a brand-new city has nothing on GCS until its first
  // credentialed build, and hard-failing the 404 turns every city-addition PR
  // red for its whole lifetime. For a 404 the gate moves to the model instead.
  test.each(Object.keys(CITY_CONFIG))('%s tree parquet is pruned, not flagged', async (city) => {
    const url = cityTreeParquetUrl(city)
    expect(url, `no per-city parquet url for ${city}`).toBeTruthy()
    if (!url) return
    const head = await fetch(url, { method: 'HEAD' })
    if (head.status === 404) {
      console.warn(
        `${city}'s tree parquet is not on GCS yet -- run the Trilogy refresh before ` +
          'deploying this city; asserting its model prunes instead',
      )
      // The prune is the shared derivation in data/raw/tree_dedup.preql; a city
      // takes part by importing it and gating its published target.
      const model = ALL_MODEL_SOURCES.find(
        (m) => m.alias.startsWith(`${city.toLowerCase()}.`) && m.alias.endsWith('_tree_info'),
      )
      const prunes =
        !!model &&
        model.contents.includes('import ..tree_dedup;') &&
        model.contents.split(/\r?\n/).includes('where tree_id = cluster_id') &&
        !model.contents.includes('is_duplicate')
      expect(
        prunes,
        `${city} has no parquet on GCS yet and its tree model does not prune ` +
          'absorbed rows, so its first build would publish duplicates',
      ).toBe(true)
      return
    }
    await withDuckDB(async (conn) => {
      const result = await conn.runAndReadAll(
        `SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('${url}'))`,
      )
      const cols = result.getRowObjects().map((r) => r.column_name as string)
      expect(
        cols,
        `${city}'s parquet still carries is_duplicate, so it predates the prune ` +
          `and still holds the rows its clusters absorbed -- force a rebuild: ` +
          `trilogy refresh raw/<city>/<city>_tree_info.preql -f <city>_tree_info`,
      ).not.toContain('is_duplicate')
    })
  }, 60_000)

  // A sentinel is not a taxon, but `species` is the join key — so with no row
  // here every enrichment column comes back NULL for the ~190k trees whose
  // source did not identify them, including `species` itself once a query reads
  // any enrichment field. The row exists to give that join something to land
  // on, and it is authored (data/raw/_ingest_shared.py SENTINEL_ENRICHMENT,
  // mirroring src/src/data/species.ts) rather than generated: one *generated*
  // row for "Unknown" labelled 189,139 trees across every city as Orania
  // timikae, and purge_non_taxa() drops whatever the parquet holds before the
  // authored rows go back.
  //
  // A sentinel added in a branch cannot have a row until the enrichment job has
  // run from main -- the daily tick runs main's code and would purge a row a
  // branch wrote (see EXTENDING.md, "The scheduled refresh runs main"). Such a
  // sentinel is listed here while its PR is open, and removed once the next
  // enrichment run has published the row; every other sentinel is gated hard.
  const PENDING_SENTINELS = new Set(['Dead'])
  test('tree_enrichment holds one authored row per species sentinel', async () => {
    await withDuckDB(async (conn) => {
      const literals = SPECIES_SENTINELS.map((s) => `'${s.species.replace(/'/g, "''")}'`).join(', ')
      const result = await conn.runAndReadAll(
        `SELECT species, common_names[1] AS label, tree_form, genus, photo_url
         FROM read_parquet('${REMOTE_SPECIES_PARQUET_URL}') WHERE species IN (${literals})`,
      )
      const rows = result.getRowObjects()
      const published = rows.map((r) => r.species as string)
      for (const pending of PENDING_SENTINELS) {
        if (!published.includes(pending)) {
          console.warn(`${pending} has no enrichment row yet; the next enrichment run from main writes it`)
        }
      }
      expect(
        published.sort(),
        'the enrichment table is missing a sentinel row -- run the enrichment ' +
          'pipeline, or every unidentified tree resolves to a null species',
      ).toEqual(
        SPECIES_SENTINELS.map((s) => s.species)
          .filter((s) => !PENDING_SENTINELS.has(s) || published.includes(s))
          .sort(),
      )

      for (const row of rows) {
        const sentinel = SPECIES_SENTINELS.find((s) => s.species === row.species)!
        expect(row.label, `${row.species} label`).toBe(sentinel.label)
        expect(row.tree_form, `${row.species} tree_form`).toBe(sentinel.treeForm)
        // The Orania row had both. A sentinel names itself and its growth form,
        // and claims nothing else.
        expect(row.genus, `${row.species} must claim no genus`).toBeNull()
        expect(row.photo_url, `${row.species} must carry no photo`).toBeNull()
      }
    })
  }, 60_000)

  test('tree_enrichment loads and has required columns', async () => {
    await withDuckDB(async (conn) => {
      await conn.run(`CREATE TABLE enrichment AS SELECT * FROM read_parquet('${REMOTE_SPECIES_PARQUET_URL}')`)

      const colResult = await conn.runAndReadAll(`SELECT column_name FROM information_schema.columns WHERE table_name = 'enrichment'`)
      const cols = colResult.getRowObjects().map((r) => r.column_name as string)
      expect(cols, 'missing column: species').toContain('species')
      expect(
        cols.includes('native_ecoregions') || cols.includes('native_status'),
        'missing nativeness column (expected native_ecoregions or legacy native_status)',
      ).toBe(true)
      expect(
        cols.includes('tree_form') || cols.includes('tree_category'),
        'missing tree-form column (expected tree_form or legacy tree_category)',
      ).toBe(true)

      const countResult = await conn.runAndReadAll(`SELECT COUNT(*) AS n FROM enrichment`)
      const n = Number(countResult.getRowObjects()[0].n)
      expect(n, 'enrichment table should have rows').toBeGreaterThan(0)
    })
  }, 60_000)
})
