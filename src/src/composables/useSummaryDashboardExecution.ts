import * as duckdb from '@duckdb/duckdb-wasm'
import {
  ColumnType,
  QueryExecutionService,
  Results,
  type ExecutionConnection,
  type ExecutionConnectionProvider,
  type ResultColumn,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import { ref } from 'vue'
import { useTrilogyRuntime } from './useTrilogyRuntime'
import { ALL_MODEL_SOURCES } from '../trilogyModels'
import duckdb_wasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import duckdb_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import duckdb_wasm_eh from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import duckdb_worker_eh from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'

const CONNECTION_ID = 'summary-duckdb'

const ready = ref(false)
const initError = ref<string | null>(null)

let db: duckdb.AsyncDuckDB | null = null
let conn: duckdb.AsyncDuckDBConnection | null = null
let initPromise: Promise<void> | null = null

function inferColumnType(value: unknown): ColumnType {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? ColumnType.INTEGER : ColumnType.FLOAT
  }
  if (typeof value === 'boolean') {
    return ColumnType.BOOLEAN
  }
  if (value instanceof Date) {
    return ColumnType.DATETIME
  }
  if (typeof value === 'string') {
    return ColumnType.STRING
  }
  return ColumnType.UNKNOWN
}

async function ensureInit(): Promise<void> {
  if (ready.value && conn) {
    return
  }

  if (!initPromise) {
    initPromise = (async () => {
      const bundles: duckdb.DuckDBBundles = {
        mvp: {
          mainModule: duckdb_wasm,
          mainWorker: duckdb_worker,
        },
        eh: {
          mainModule: duckdb_wasm_eh,
          mainWorker: duckdb_worker_eh,
        },
      }

      const bundle = await duckdb.selectBundle(bundles)
      const logger = new duckdb.ConsoleLogger()
      const worker = new Worker(bundle.mainWorker!)

      db = new duckdb.AsyncDuckDB(logger, worker)
      await db.instantiate(bundle.mainModule, bundle.pthreadWorker)
      conn = await db.connect()

      ready.value = true
      initError.value = null
    })().catch((error) => {
      ready.value = false
      initError.value = error instanceof Error ? error.message : String(error)
      initPromise = null
      throw error
    })
  }

  await initPromise
}

async function executeSql(sql: string): Promise<Results> {
  await ensureInit()
  if (!conn) {
    throw new Error('Summary DuckDB connection unavailable')
  }

  const result = await conn.query(sql)
  const rows = result.toArray().map((row) => row.toJSON() as Record<string, unknown>)
  const headers = new Map<string, ResultColumn>()

  for (const field of result.schema.fields) {
    const sampleValue = rows.find((row) => row[field.name] != null)?.[field.name]
    headers.set(field.name, {
      name: field.name,
      type: inferColumnType(sampleValue),
    })
  }

  return new Results(headers, rows)
}

export function useSummaryDashboardExecution() {
  const trilogy = useTrilogyRuntime()

  const provider: ExecutionConnectionProvider = {
    getConnection(connectionId: string): ExecutionConnection | null {
      if (connectionId !== CONNECTION_ID) {
        return null
      }

      return {
        name: CONNECTION_ID,
        queryType: 'duckdb',
        isConnected: () => ready.value && conn !== null,
        executeSql: (sql: string, parameters?: Record<string, any> | null) => {
          if (parameters && Object.keys(parameters).length > 0) {
            throw new Error('Parameterized dashboard queries are not supported in summary analytics')
          }
          return executeSql(sql)
        },
      }
    },
    async ensureConnected(connectionId: string): Promise<void> {
      if (connectionId !== CONNECTION_ID) {
        throw new Error(`Unknown connection ${connectionId}`)
      }
      await ensureInit()
    },
    getConnectionSources(connectionId: string) {
      if (connectionId !== CONNECTION_ID) {
        return []
      }
      return ALL_MODEL_SOURCES
    },
  }

  const queryExecutionService = new QueryExecutionService(trilogy.resolver, provider, false)

  return {
    initialize: ensureInit,
    ready,
    initError,
    connectionId: CONNECTION_ID,
    queryExecutionService,
  }
}
