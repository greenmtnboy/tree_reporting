import {
  QueryExecutionService,
  type ExecutionConnection,
  type ExecutionConnectionProvider,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import {
  DuckDBConnection,
  configureDuckDBAssets,
} from '@trilogy-data/trilogy-studio-components/connections'
import { ref } from 'vue'
import { useTrilogyRuntime } from './useTrilogyRuntime'
import { ALL_MODEL_SOURCES } from '../trilogyModels'
import { DUCKDB_ASSET_URLS } from '../duckdbAssetUrls'

const CONNECTION_ID = 'summary-duckdb'

const ready = ref(false)
const initError = ref<string | null>(null)

let connection: DuckDBConnection | null = null
let initPromise: Promise<void> | null = null

async function ensureInit(): Promise<void> {
  if (ready.value && connection?.connected) {
    return
  }

  if (!initPromise) {
    initPromise = (async () => {
      configureDuckDBAssets(DUCKDB_ASSET_URLS)
      if (!connection) {
        connection = new DuckDBConnection(CONNECTION_ID)
      }

      await connection.reset()
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

async function executeSql(
  sql: string,
  parameters?: Record<string, unknown> | null,
) {
  await ensureInit()
  if (!connection) {
    throw new Error('Summary DuckDB connection unavailable')
  }

  return connection.query(sql, parameters ?? null)
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
        isConnected: () => ready.value && connection?.connected === true,
        executeSql,
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
