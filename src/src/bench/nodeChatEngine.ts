/**
 * The pieces of the browser the chat loop needs, rebuilt for node.
 *
 * The benchmark drives the real `useChat` composable -- its system prompts,
 * its tool schemas, its `executeTool` with every validation branch -- and
 * only replaces what cannot run outside a browser:
 *
 * - the DuckDB worker, with `@duckdb/node-api` reading the same published
 *   parquets over HTTPS that the resolver's SQL names;
 * - the summary page's DuckDB-WASM connection, with a node
 *   `ExecutionConnectionProvider` handed to the library's *real*
 *   `QueryExecutionService`, so a summary or species `run_query` compiles
 *   through the same batch path the page uses;
 * - the pinia-backed `useTrilogyCore`, with the library's real
 *   `TrilogyResolver` pointed at the hosted service and a connection store
 *   holding the real `DemoProvider` -- the default public token every
 *   visitor gets -- or an OpenRouter key when `OPENROUTER_API_KEY` is set;
 * - the landmark table, loaded from the city's landmark parquet.
 *
 * Everything here is stateless across rounds except the DuckDB instance and
 * the resolver's compile cache, which are the expensive parts and carry no
 * conversation.
 */
import { setDefaultResultOrder } from 'node:dns'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { QueryExecutionService, TrilogyResolver } from '@trilogy-data/trilogy-studio-components/stores'
import {
  DemoProvider,
  OpenRouterProvider,
  type LLMMessage,
  type LLMProvider,
  type LLMRequestOptions,
  type LLMResponse,
} from '@trilogy-data/trilogy-studio-components/llm'
import { applySqlParameters } from '../composables/sqlParameters'
import { normalizeValue } from '../workers/normalizeValue'
import { cityLandmarkParquetUrl } from '../workers/parquetUrls'
import type { Landmark } from '../types'

// Node tries IPv6 before IPv4 by default; prefer IPv4 so an unroutable IPv6
// path cannot stall every fetch the benchmark makes. Equivalent to running
// under NODE_OPTIONS=--dns-result-order=ipv4first.
setDefaultResultOrder('ipv4first')

export const RESOLVER_URL = process.env.TRILOGY_RESOLVER_URL ?? 'https://trilogy-service.fly.dev'

export type QueryRows = { columns: string[]; rows: Record<string, unknown>[] }

/** A node DuckDB standing in for the worker's `query` and the page's `executeSql`. */
export class NodeTreeDatabase {
  private constructor(
    private readonly instance: DuckDBInstance,
    private readonly connection: DuckDBConnection,
  ) {}

  static async create(): Promise<NodeTreeDatabase> {
    const instance = await DuckDBInstance.create(':memory:')
    const connection = await instance.connect()
    return new NodeTreeDatabase(instance, connection)
  }

  /** The worker's `runQuery`: raw SQL in, JSON-safe rows out. */
  async query(sql: string): Promise<QueryRows> {
    const result = await this.connection.runAndReadAll(sql)
    const columns = result.columnNames()
    const rows = result.getRowObjectsJS().map((row) => {
      const out: Record<string, unknown> = {}
      for (const column of columns) out[column] = normalizeValue(row[column])
      return out
    })
    return { columns, rows }
  }

  /**
   * The library connection's `executeSql`: bind the resolver's parameters and
   * return a `Results`-shaped object (a headers Map plus rows, with the
   * `toJSON` the chat's row serializer reads).
   */
  async executeSql(sql: string, parameters?: Record<string, unknown> | null) {
    const bound = applySqlParameters(sql, stripParameterColons(parameters ?? {}))
    const result = await this.connection.runAndReadAll(bound)
    const columns = result.columnNames()
    const types = result.columnTypes()
    const headers = new Map(
      columns.map((name, index) => [name, { name, type: columnTypeOf(types[index]?.toString() ?? '') }]),
    )
    const data = result.getRowObjectsJS().map((row) => {
      const out: Record<string, unknown> = {}
      for (const column of columns) out[column] = normalizeValue(row[column])
      return out
    })
    return {
      headers,
      data,
      toJSON: () => ({ headers: Object.fromEntries(headers), data }),
    }
  }

  /** The worker's `landmarks` table for one city, shaped as `useLandmarkData` shapes it. */
  async loadLandmarks(city: string): Promise<Landmark[]> {
    const url = cityLandmarkParquetUrl(city)
    if (!url) return []
    const { rows } = await this.query(
      `SELECT landmark_id, name, latitude, longitude FROM read_parquet('${url}') WHERE city = '${city}' ORDER BY name`,
    )
    return rows
      .filter((row) => row.name)
      .map((row) => ({
        id: String(row.landmark_id ?? ''),
        name: String(row.name).trim(),
        lng: row.longitude as number,
        lat: row.latitude as number,
      }))
  }

  close() {
    this.connection.closeSync()
    void this.instance
  }
}

/** Cross-filter bind maps arrive keyed with the leading colon; strip it. */
function stripParameterColons(parameters: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(parameters)) {
    out[key.startsWith(':') ? key.slice(1) : key] = value
  }
  return out
}

function columnTypeOf(duckType: string): string {
  const type = duckType.toUpperCase()
  if (type.startsWith('DECIMAL')) return 'numeric'
  if (type === 'VARCHAR') return 'string'
  if (type === 'BIGINT' || type === 'INTEGER' || type === 'HUGEINT' || type === 'SMALLINT') return 'int'
  if (type === 'DOUBLE' || type === 'FLOAT') return 'float'
  if (type === 'BOOLEAN') return 'bool'
  if (type === 'DATE') return 'date'
  if (type.startsWith('TIMESTAMP')) return 'datetime'
  if (type.endsWith('[]') || type.startsWith('LIST')) return 'array'
  if (type.startsWith('STRUCT')) return 'struct'
  return 'unknown'
}

/** The library's resolver, pointed at the hosted service the app uses. */
export function createResolver(): TrilogyResolver {
  return new TrilogyResolver({ settings: { trilogyResolver: RESOLVER_URL } } as never)
}

/**
 * `useSummaryDashboardExecution` for node: the real `QueryExecutionService`
 * over the node database, with the dashboard context source swapped per city
 * exactly as the page does.
 */
export function createSummaryExecution(
  resolver: TrilogyResolver,
  db: NodeTreeDatabase,
  modelSources: Array<{ alias: string; contents: string }>,
  buildContextSource: (city: string | null) => { alias: string; contents: string },
  initialCity: string | null,
) {
  const connectionId = 'summary-duckdb'
  let contextSource = buildContextSource(initialCity)

  const provider = {
    getConnection: (id: string) =>
      id === connectionId
        ? {
            name: connectionId,
            queryType: 'duckdb',
            isConnected: () => true,
            executeSql: (sql: string, parameters?: Record<string, unknown> | null) =>
              db.executeSql(sql, parameters),
          }
        : null,
    ensureConnected: async () => {},
    getConnectionSources: (id: string) => (id === connectionId ? [...modelSources, contextSource] : []),
  }

  return {
    initialize: async () => {},
    connectionId,
    queryExecutionService: new QueryExecutionService(resolver, provider as never, false),
    setDashboardContext(city: string | null) {
      contextSource = buildContextSource(city)
    },
  }
}

export type LlmCallRecord = {
  startedAt: number
  durationMs: number
  historyLength: number
  systemPromptChars: number
  /** The full system prompt; the driver keeps it on a round's first call only. */
  systemPrompt?: string
  toolNames: string[]
  response?: LLMResponse
  error?: string
}

/**
 * The slice of the library's LLM connection store that `useChat` touches,
 * holding real providers. `demo` is the token every visitor gets; set
 * `OPENROUTER_API_KEY` to run the same model on your own key when the demo
 * budget for this IP runs out, and `CHAT_BENCH_MODEL` to try another model.
 */
export function createLlmConnectionStore(onCall: (record: LlmCallRecord) => void) {
  const connections: Record<string, LLMProvider> = {}
  const modelOverride = process.env.CHAT_BENCH_MODEL

  return {
    connections,
    async newConnection(
      name: string,
      type: string,
      options: { apiKey: string; model: string; saveCredential: boolean },
    ) {
      const model = modelOverride || options.model
      const provider =
        type === 'demo'
          ? new DemoProvider(name, model)
          : type === 'openrouter'
            ? new OpenRouterProvider(name, options.apiKey, model, false)
            : null
      if (!provider) throw new Error(`chat benchmark: unsupported provider type "${type}"`)
      connections[name] = provider
      // Mints the demo token (or validates the key) and loads the model list;
      // reset does not touch the chosen model, but pin it in case that changes.
      await provider.reset()
      if (provider.model !== model) provider.setModel(model)
    },
    async generateCompletion(name: string, options: LLMRequestOptions, messages: LLMMessage[]) {
      const provider = connections[name]
      if (!provider) throw new Error(`LLM connection with name "${name}" not found.`)
      const startedAt = Date.now()
      const base = {
        startedAt,
        historyLength: messages.length,
        systemPromptChars: options.systemPrompt?.length ?? 0,
        systemPrompt: options.systemPrompt,
        toolNames: (options.tools ?? []).map((tool) => tool.name),
      }
      try {
        const response = await provider.generateCompletion(options, messages)
        onCall({ ...base, durationMs: Date.now() - startedAt, response })
        return response
      } catch (error) {
        onCall({ ...base, durationMs: Date.now() - startedAt, error: (error as Error).message })
        throw error
      }
    },
  }
}
