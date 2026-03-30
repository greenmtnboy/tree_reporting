import {
  ColumnType,
  QueryExecutionService,
  Results,
  type ExecutionConnection,
  type ExecutionConnectionProvider,
  type ResultColumn,
} from '@trilogy-data/trilogy-studio-components/dashboard'
import { useDuckDB } from './useDuckDB'
import { useTrilogyRuntime } from './useTrilogyRuntime'
import { ALL_MODEL_SOURCES } from '../trilogyModels'

const CONNECTION_ID = 'worker-duckdb'

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

export function useWorkerDashboardExecution() {
  const trilogy = useTrilogyRuntime()
  const { ready, query: workerQuery, setCityContext } = useDuckDB()

  const provider: ExecutionConnectionProvider = {
    getConnection(connectionId: string): ExecutionConnection | null {
      if (connectionId !== CONNECTION_ID) {
        return null
      }

      return {
        name: CONNECTION_ID,
        queryType: 'duckdb',
        isConnected: () => ready.value,
        async executeSql(sql: string, parameters?: Record<string, any> | null) {
          if (parameters && Object.keys(parameters).length > 0) {
            throw new Error('Parameterized dashboard queries are not supported in the worker adapter')
          }

          const result = await workerQuery(sql)
          const headers = new Map<string, ResultColumn>()

          result.columns.forEach((columnName) => {
            const sampleValue = result.rows.find((row) => row[columnName] != null)?.[columnName]
            headers.set(columnName, {
              name: columnName,
              type: inferColumnType(sampleValue),
            })
          })

          return new Results(headers, result.rows)
        },
      }
    },
    async ensureConnected(connectionId: string) {
      if (connectionId !== CONNECTION_ID) {
        throw new Error(`Unknown connection ${connectionId}`)
      }
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
    connectionId: CONNECTION_ID,
    queryExecutionService,
    setCityContext,
  }
}
