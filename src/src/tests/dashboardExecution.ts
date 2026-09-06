import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { applySqlParameters } from '../composables/sqlParameters'
import { isSpeciesSentinel } from '../data/species'
import {
  FIXTURE_ECOREGIONS,
  FIXTURE_SPECIES,
  FIXTURE_TREES,
  fixtureFieldValue,
  type FixtureTree,
} from './dashboardFixtures'
import { cityContext, type DashboardQueryCase, type QueryState } from './dashboardQueryCatalog'

/**
 * Runs a compiled dashboard query against the fixtures.
 *
 * The resolver hands back SQL that reads the published Parquets over HTTPS. To
 * execute it locally the reads are repointed at seeded tables — the table is
 * created from the real Parquet's schema (`LIMIT 0`, which reads only the
 * footer) so a column the query selects can never be missing, then filled with
 * the fixture rows.
 *
 * This is the half that compiling cannot do: it proves the SQL parses and binds
 * in DuckDB, and that the filters actually restrict the rows rather than
 * evaluating against a fanned-out join.
 */

const PARQUET_READ = /read_parquet\('([^']+)'\)/g

type SeedKind = 'trees' | 'species' | 'ecoregions' | 'empty'

type Seed = { kind: SeedKind; city?: string }

/**
 * Which fixture rows belong in the table standing in for this Parquet.
 *
 * A per-city Parquet gets only that city's trees. This is not tidiness: each
 * city's datasource asserts `complete where city = 'X'`, so the planner is
 * entitled to leave the city predicate out of the SQL entirely. Seeding another
 * city's rows into it would make a correct plan look like a dropped filter.
 */
function seedFor(url: string): Seed {
  const name = url.split('/').pop() ?? url
  if (name.includes('tree_enrichment')) return { kind: 'species' }
  if (name.includes('ecoregion_info')) return { kind: 'ecoregions' }
  if (name.includes('full_tree_info')) return { kind: 'trees' }
  const perCity = /^([a-z]{5})_tree_info/.exec(name)
  if (perCity) return { kind: 'trees', city: perCity[1].toUpperCase() }
  if (name.includes('tree_info')) return { kind: 'trees' }
  return { kind: 'empty' }
}

function tableNameFor(url: string): string {
  const name = (url.split('/').pop() ?? url).replace(/\.parquet$/, '')
  return `seed_${name.replace(/[^A-Za-z0-9_]/g, '_')}`
}

function sqlLiteral(value: unknown): string {
  if (value == null) return 'NULL'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE'
  if (Array.isArray(value)) return `[${value.map(sqlLiteral).join(', ')}]`
  return `'${String(value).replace(/'/g, "''")}'`
}

function rowsForSeed(seed: Seed): Array<Record<string, unknown>> {
  switch (seed.kind) {
    case 'trees':
      // Every published row is its own cluster's survivor -- the city targets
      // carry `where tree_id = cluster_id`, so the column is a tautology in the
      // parquet and the fixtures have to agree. Seeding it null instead makes
      // the gate `NULL = NULL`, which is never true, and every query in the
      // suite returns zero rows rather than a wrong number.
      return FIXTURE_TREES
        .filter((tree) => !seed.city || tree.city === seed.city)
        .map((tree) => ({ ...tree, cluster_id: tree.tree_id }))
    case 'species':
      return FIXTURE_SPECIES as unknown as Array<Record<string, unknown>>
    case 'ecoregions':
      return FIXTURE_ECOREGIONS as unknown as Array<Record<string, unknown>>
    case 'empty':
      return []
  }
}

export class FixtureDatabase {
  private constructor(
    private readonly instance: DuckDBInstance,
    private readonly connection: DuckDBConnection,
    // Keyed by URL and holding the in-flight promise, not the finished name:
    // the workers seed concurrently, and two of them racing to CREATE the same
    // table is a catalog error, not a duplicate row.
    private readonly seeded: Map<string, Promise<string>>,
  ) {}

  static async create(): Promise<FixtureDatabase> {
    const instance = await DuckDBInstance.create(':memory:')
    const connection = await instance.connect()
    return new FixtureDatabase(instance, connection, new Map())
  }

  /** Create (once) a seeded table mirroring the schema of the Parquet at `url`. */
  private ensureSeeded(url: string): Promise<string> {
    const existing = this.seeded.get(url)
    if (existing) return existing

    const pending = this.seed(url)
    this.seeded.set(url, pending)
    return pending
  }

  private async seed(url: string): Promise<string> {
    const table = tableNameFor(url)
    await this.connection.run(
      `CREATE TABLE ${table} AS SELECT * FROM read_parquet('${url}') LIMIT 0`,
    )
    const described = await this.connection.runAndReadAll(`DESCRIBE ${table}`)
    const columns = new Set(described.getRowObjects().map((row) => String(row.column_name)))

    for (const row of rowsForSeed(seedFor(url))) {
      const present = Object.keys(row).filter((column) => columns.has(column))
      if (present.length === 0) continue
      await this.connection.run(
        `INSERT INTO ${table} (${present.join(', ')}) VALUES (${present
          .map((column) => sqlLiteral(row[column]))
          .join(', ')})`,
      )
    }

    return table
  }

  /** Repoint every remote Parquet read at its seeded table and run the query. */
  async run(sql: string, parameters: Record<string, unknown>): Promise<Array<Record<string, unknown>>> {
    const urls = [...sql.matchAll(PARQUET_READ)].map((match) => match[1])
    let rewritten = sql
    for (const url of new Set(urls)) {
      const table = await this.ensureSeeded(url)
      rewritten = rewritten.split(`read_parquet('${url}')`).join(table)
    }

    const bound = applySqlParameters(rewritten, normalizeParameters(parameters))
    const result = await this.connection.runAndReadAll(bound)
    return result.getRowObjects() as Array<Record<string, unknown>>
  }

  close() {
    this.connection.closeSync()
    void this.instance
  }
}

/** Cross-filter bind maps arrive keyed with the leading colon; strip it. */
function normalizeParameters(parameters: Record<string, unknown>): Record<string, unknown> {
  const normalized: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(parameters)) {
    normalized[key.startsWith(':') ? key.slice(1) : key] = value
  }
  return normalized
}

/** The first token of a scientific name, matching `split(species, ' ')[1]`. */
function genusOf(species: string): string {
  return species.split(' ')[0] ?? species
}

/**
 * The fixture trees a page in this state should be counting. This is the
 * reference the executed results are checked against — the same question the
 * dashboard asks, answered without the planner.
 */
export function expectedTrees(state: QueryState): FixtureTree[] {
  const context = cityContext(state.city)
  return FIXTURE_TREES.filter((tree) => {
    if (state.city && tree.city !== state.city) return false
    if (state.genus && genusOf(tree.species) !== state.genus) return false
    // A species carrying an apostrophe is filtered with LIKE clauses rather than
    // equality (see getSpeciesSqlFilter); the fixtures hold no such species, so
    // either form matches nothing but the exact binomial.
    if (state.species && tree.species !== state.species) return false
    for (const selection of state.crossFilters) {
      if (fixtureFieldValue(tree, selection.field, context) !== selection.value) return false
    }
    return true
  })
}

function numeric(value: unknown): number | null {
  if (typeof value === 'number') return value
  if (typeof value === 'bigint') return Number(value)
  return null
}

/**
 * Check an executed result against the fixtures. Returns a message when the
 * numbers are wrong, null when they are right.
 *
 * Three things are checked, and between them they cover what compiling cannot:
 * a query that returns more trees than exist has fanned out; a query that
 * returns the unfiltered count under a filter has lost the filter; and the dot
 * map's row count is the one place where the rows themselves, not an aggregate,
 * have to be right.
 */
export function checkResult(
  queryCase: DashboardQueryCase,
  rows: Array<Record<string, unknown>>,
): string | null {
  const expected = expectedTrees(queryCase.state)
  const id = queryCase.id.replace(/ \[.*\]$/, '')

  if (id === 'summary:total-trees' || id === 'species:sp-total-trees') {
    const actual = numeric(rows[0]?.total_trees) ?? 0
    if (actual !== expected.length) {
      return `total_trees = ${actual}, fixtures say ${expected.length}`
    }
  }

  if (id === 'summary:unique-species') {
    const distinct = new Set(
      expected.map((tree) => tree.species).filter((species) => !isSpeciesSentinel(species)),
    )
    const actual = numeric(rows[0]?.unique_species) ?? 0
    if (actual !== distinct.size) {
      return `unique_species = ${actual}, fixtures say ${distinct.size}`
    }
  }

  if (id === 'summary:tree-dot-map' || id === 'species:tree-dot-map') {
    const coordinates = new Set(expected.map((tree) => `${tree.latitude},${tree.longitude}`))
    if (rows.length !== coordinates.size) {
      return `dot map returned ${rows.length} points, fixtures say ${coordinates.size}`
    }
  }

  for (const row of rows) {
    const count = numeric(row.tree_count)
    if (count != null && count > expected.length) {
      return `tree_count = ${count} exceeds the ${expected.length} tree(s) the fixtures hold — the join fanned out`
    }
  }

  return null
}
