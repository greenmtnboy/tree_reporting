/**
 * The slice of `@duckdb/duckdb-wasm` the tile worker uses, backed by the same
 * wasm engine under Node: the package's blocking Node build, which runs the
 * bundle in this thread instead of a Web Worker. Tests substitute this module
 * for the package (`vi.mock('@duckdb/duckdb-wasm', ...)`), so the worker runs
 * unmodified against the engine the browser runs.
 *
 * Why wasm and not native DuckDB: the two plan some queries differently. A
 * WHERE on `ST_AsMVTGeom` that native DuckDB applies after the tile range is
 * pushed into the scan by the wasm build, which then runs the geometry on
 * every tree in the city (#144). This build reproduces that; native does not.
 *
 * Spatial is fetched from extensions.duckdb.org on first LOAD (about 4 MB):
 * the Node runtime reads extensions through a synchronous XHR shim, and a
 * LOAD from a local file, a registered buffer or a local repository hangs.
 */
import * as blocking from '@duckdb/duckdb-wasm/blocking'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

const require = createRequire(import.meta.url)
const DIST = dirname(require.resolve('@duckdb/duckdb-wasm/dist/duckdb-eh.wasm'))

type BlockingDb = Awaited<ReturnType<typeof blocking.createDuckDB>>
type BlockingConnection = ReturnType<BlockingDb['connect']>

/** Boot one in-process DuckDB-WASM (eh bundle). Half a second, no spatial yet. */
export async function createNodeDuckDB(): Promise<BlockingDb> {
  const db = await blocking.createDuckDB(
    { mvp: { mainModule: join(DIST, 'duckdb-mvp.wasm'), mainWorker: '' }, eh: { mainModule: join(DIST, 'duckdb-eh.wasm'), mainWorker: '' } },
    new blocking.VoidLogger(),
    blocking.NODE_RUNTIME,
  )
  await db.instantiate()
  return db
}

/** Every statement the worker ran, in order, for tests that check its SQL. */
export const executedSql: string[] = []

export class AsyncDuckDBConnection {
  constructor(private readonly conn: BlockingConnection) {}

  async query(sql: string) {
    executedSql.push(sql)
    return this.conn.query(sql)
  }
}

export class AsyncDuckDB {
  private db: BlockingDb | null = null

  constructor(_logger?: unknown, _worker?: unknown) {}

  async instantiate(): Promise<void> {
    this.db = await createNodeDuckDB()
  }

  async connect(): Promise<AsyncDuckDBConnection> {
    if (!this.db) throw new Error('instantiate() first')
    return new AsyncDuckDBConnection(this.db.connect())
  }
}

export class ConsoleLogger {
  log(): void {}
}

export async function selectBundle(bundles: Record<string, { mainModule: string; mainWorker?: string | null }>) {
  const bundle = bundles.eh ?? bundles.mvp
  return { mainModule: bundle.mainModule, mainWorker: bundle.mainWorker ?? null, pthreadWorker: null }
}
