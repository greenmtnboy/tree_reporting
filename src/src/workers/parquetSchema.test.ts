import { describe, test, expect } from 'vitest'
import { DuckDBInstance } from '@duckdb/node-api'
import { REMOTE_TREES_PARQUET_URL, REMOTE_SPECIES_PARQUET_URL, WORLD_MANIFEST_URL, cityTreeParquetUrl, worldFileUrl } from './parquetUrls'
import { VOCAB_KINDS, WORLD_BLOCK_COLUMNS } from './cityTables'
import { blockAt, blockFile, blockKey, parseWorldManifest } from './worldBlocks'
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
  // The rollup is ~300MB and this never needs a row of it. The column list and
  // the per-column null counts come from the parquet footer alone, and the
  // (city, data_source) pairs come from a two-column projection that DuckDB
  // range-reads -- about a second over httpfs, against a full download that
  // blew the 60s budget on a home connection. Do not `CREATE TABLE ... AS
  // SELECT *` here.
  test('full_tree_info has required columns, known cities, and labelled sources', async () => {
    await withDuckDB(async (conn) => {
      const colResult = await conn.runAndReadAll(
        `SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('${REMOTE_TREES_PARQUET_URL}'))`,
      )
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

      // Every row must be attributable to a source. Row-group statistics carry
      // the null count per column, so this is a footer read, not a scan.
      const nullResult = await conn.runAndReadAll(
        `SELECT COALESCE(SUM(stats_null_count), 0) AS nulls, COUNT(*) AS row_groups
         FROM parquet_metadata('${REMOTE_TREES_PARQUET_URL}')
         WHERE path_in_schema = 'data_source'`,
      )
      const [nullRow] = nullResult.getRowObjects()
      expect(Number(nullRow.row_groups), 'no row groups carry a data_source column').toBeGreaterThan(0)
      expect(Number(nullRow.nulls), 'every tree must carry a data_source').toBe(0)

      const pairResult = await conn.runAndReadAll(
        `SELECT city, data_source FROM read_parquet('${REMOTE_TREES_PARQUET_URL}') GROUP BY ALL ORDER BY ALL`,
      )
      const pairs = pairResult.getRowObjects()
      expect(pairs.length, 'expected at least one city in data').toBeGreaterThan(0)
      const cities = new Set(pairs.map((r) => r.city as string))
      expect(cities).toContain('USSFO')
      expect(cities).toContain('USNYC')

      // ...and every source must be one the frontend's picklist knows about.
      const sources = [...new Set(pairs.map((r) => r.data_source as string))]
      expect(sources.length, 'expected at least one data_source value').toBeGreaterThan(0)
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
      // takes part by importing it and gating its published target -- either
      // with its own `where tree_id = cluster_id` line or by extending
      // `published_tree_info`, the template that carries that gate for every
      // city scaffolded since it was written.
      const model = ALL_MODEL_SOURCES.find(
        (m) => m.alias.startsWith(`${city.toLowerCase()}.`) && m.alias.endsWith('_tree_info'),
      )
      const dedup = ALL_MODEL_SOURCES.find((m) => m.alias === 'tree_dedup')
      const templatePrunes =
        !!dedup &&
        dedup.contents.split(/\r?\n/).some((line) => line.startsWith('where tree_id = cluster_id'))
      const prunes =
        !!model &&
        model.contents.includes('import ..tree_dedup;') &&
        (model.contents.split(/\r?\n/).includes('where tree_id = cluster_id') ||
          (templatePrunes && model.contents.includes('extends published_tree_info'))) &&
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
  // on, and it is authored (data/raw/shared/ingest.py SENTINEL_ENRICHMENT,
  // mirroring src/src/data/species.ts) rather than generated: one *generated*
  // row for "Unknown" labelled 189,139 trees across every city as Orania
  // timikae, and purge_non_taxa() drops whatever the parquet holds before the
  // authored rows go back.
  //
  // A sentinel added in a branch cannot have a row until the enrichment job has
  // run from main -- the daily tick runs main's code and would purge a row a
  // branch wrote (see docs/SPECIES_ENRICHMENT.md, "How the job behaves"). Such a
  // sentinel is listed here while its PR is open, and removed once the next
  // enrichment run has published the row; every other sentinel is gated hard.
  const PENDING_SENTINELS = new Set(['Dead', 'Unknown deciduous', 'Unknown conifer', 'Unknown evergreen'])
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

  // What the map draws from (docs/SEAMLESS_WORLD.md). The manifest is the
  // commit point of the block job, so if it parses and one block and the
  // vocabulary read as the worker reads them, the published world is usable.
  // Only footers and one small block are read.
  test('the world manifest names blocks and a vocabulary the worker can read', async () => {
    const response = await fetch(WORLD_MANIFEST_URL)
    expect(response.ok, `world manifest: HTTP ${response.status}`).toBe(true)
    const manifest = parseWorldManifest(await response.json())
    expect(manifest.blocks.size).toBeGreaterThan(100)
    expect([...manifest.blocks.values()].reduce((n, b) => n + b.rows, 0)).toBe(manifest.trees)
    // Every bookmark the manifest lists is a city the app knows, and holds trees.
    for (const city of manifest.cities) {
      expect(CITY_CONFIG[city.code], `${city.code} is in the manifest but not in cityConfig.json`).toBeTruthy()
      expect(city.trees).toBeGreaterThan(0)
    }
    // Burlington's centre is a block of the world: the litmus city for a map opened elsewhere.
    const [lng, lat] = CITY_CONFIG.USBTV.center
    const at = blockAt(lng, lat)
    const block = manifest.blocks.get(blockKey(at.x, at.y))
    expect(block, 'no block under Burlington').toBeTruthy()
    await withDuckDB(async (conn) => {
      const url = worldFileUrl(blockFile(block!))
      const columns = (await conn.runAndReadAll(`SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('${url}'))`))
        .getRowObjects().map((r) => String(r.column_name))
      expect(columns).toEqual([...WORLD_BLOCK_COLUMNS])
      const rows = (await conn.runAndReadAll(`SELECT count(*) AS n, count(DISTINCT tree_id) AS ids FROM read_parquet('${url}')`)).getRowObjects()[0]
      expect([Number(rows.n), Number(rows.ids)]).toEqual([block!.rows, block!.rows])
      const kinds = (await conn.runAndReadAll(`SELECT DISTINCT kind FROM read_parquet('${worldFileUrl(manifest.vocab)}') ORDER BY 1`))
        .getRowObjects().map((r) => String(r.kind))
      expect(kinds).toEqual(Object.values(VOCAB_KINDS).sort())
    })
  }, 60_000)
})
