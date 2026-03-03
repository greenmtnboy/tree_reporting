import { ref, computed } from 'vue'
import type { ChatMessage, ToolCallRecord } from '../types'
import { useTrilogyCore, buildCustomTrilogyPrompt } from '@trilogy-data/trilogy-studio-components'
import { useDuckDB } from './useDuckDB'
import { useFlyTo } from './useFlyTo'
import { useLandmarkData } from './useLandmarkData'
import { useMapData } from './useMapData'
import TREES_MODEL from '../../../data/raw/tree_info.preql?raw'

const API_KEY_STORAGE = 'sf_trees_api_key'
const API_TYPE_STORAGE = 'sf_trees_provider_type'
const MAX_LOOPS = 10
const TRILOGY_RESOLVER_URL = 'https://trilogy-service.fly.dev'
const LLM_CONNECTION = 'sf-trees'

// Default models per provider — used when first creating the connection so
// generateCompletion works immediately without waiting for reset() to finish.
const PROVIDER_DEFAULT_MODELS: Record<string, string> = {
  anthropic: 'claude-sonnet-4-6',
  openai: 'gpt-4o',
  google: 'gemini-2.0-flash-001',
  openrouter: 'openai/gpt-4o-mini',
  demo: 'deepseek/deepseek-v3.2',
}

const TREES_MODEL_SOURCE = { alias: 'trees', contents: TREES_MODEL }

// Matches the library's LLMMessage shape for multi-turn history
interface HistoryMsg {
  role: 'user' | 'assistant'
  content: string
  toolCalls?: Array<{ id: string; name: string; input: Record<string, unknown> }>
  toolResults?: Array<{ toolCallId: string; toolName: string; result: string }>
}

const _ALL_CATEGORIES = new Set(['palm', 'broadleaf', 'spreading', 'coniferous', 'columnar', 'ornamental', 'default'])

const TOOLS = [
  {
    name: 'run_query',
    description:
      'Execute a Trilogy/PreQL query against the trees dataset. Write a SELECT statement using the available concepts — no FROM clause needed; Trilogy resolves the source automatically. Returns JSON rows. Use this to filter, aggregate, or explore the dataset. Limit results to 500 rows max unless the user specifically needs more.',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'The Trilogy/PreQL SELECT statement to execute' },
      },
      required: ['query'],
    },
  },
  {
    name: 'publish_results',
    description:
      'Takes a Trilogy/PreQL SELECT query that returns tree_id values for the trees to display on the map. Compiles and executes the query, persists those IDs as the active map filter, and applies DB-side filtering across map tiles. Use this after the user asks to show/highlight a subset of trees. The query only needs tree_id.',
    input_schema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Trilogy/PreQL SELECT returning tree_id values (alias optional)',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'navigate',
    description:
      'Fly the map camera to one or more locations. For a single location, provide latitude and longitude. For a tour of multiple locations, provide a "locations" array — the camera will visit each in sequence with a brief pause between stops.',
    input_schema: {
      type: 'object',
      properties: {
        latitude: { type: 'number', description: 'Latitude for a single location' },
        longitude: { type: 'number', description: 'Longitude for a single location' },
        zoom: { type: 'number', description: 'Zoom level (default 16)' },
        locations: {
          type: 'array',
          description: 'Array of locations to tour in sequence',
          items: {
            type: 'object',
            properties: {
              latitude: { type: 'number' },
              longitude: { type: 'number' },
              zoom: { type: 'number' },
            },
            required: ['latitude', 'longitude'],
          },
        },
      },
    },
  },
  {
    name: 'lookup_landmark',
    description:
      'Search the SF landmarks dataset by name (fuzzy match). Returns the landmark name, latitude, and longitude. Use this when the user mentions a place name in SF to find its coordinates.',
    input_schema: {
      type: 'object',
      properties: {
        name: { type: 'string', description: 'Landmark name to search for (partial match)' },
      },
      required: ['name'],
    },
  },
]

const SYSTEM_PROMPT = buildCustomTrilogyPrompt(
  ({ rulesInput, aggFunctions, functions, datatypes }) => `You are an assistant for the SF Trees map application. You help users explore San Francisco's urban forest dataset of approximately 10,000 street trees.

You have access to tools for querying the tree dataset, displaying query results on the map, navigating the map camera, and looking up SF landmarks.

When users ask about trees, write Trilogy/PreQL SELECT queries using the available concepts. When they want to visualize results on the map, use publish_results with a query that returns tree_id values only. When they mention locations, use lookup_landmark to find coordinates, then navigate there.

AVAILABLE CONCEPTS:
- tree_id (int) — unique identifier
- common_name (string) — e.g. "Swamp Myrtle"
- site_info (string) — planting site info (e.g. "Sidewalk: Curb side")
- plant_date (string) — date planted, format "MM/DD/YYYY HH:MM:SS AM"
- species (string) — full species string like "Tristaniopsis laurina :: Swamp Myrtle"
- latitude (float) — geographic latitude
- longitude (float) — geographic longitude
- diameter_at_breast_height (float) — trunk diameter in inches

SPECIES-LEVEL ENRICHMENT CONCEPTS:
- common_names (string) — comma-separated common names for the species
- native_status (string) — native_bay_area | native_california | non_native | naturalized | unknown
- is_evergreen (bool)
- mature_height_ft (float)
- canopy_spread_ft (float)
- growth_rate (string) — slow | moderate | fast
- lifespan_years (string) — e.g. "50-100", "200+"
- drought_tolerance (string) — low | moderate | high
- bloom_season (string) — September to November | autumn and winter | late spring and summer | late spring or summer | late spring to autumn | spring | spring and summer | summer | winter | year-round
- wildlife_value (string) — low | moderate | high
- fire_risk (string) — low | moderate | high
- tree_category (string) — palm | broadleaf | spreading | coniferous | columnar | ornamental | default

TRILOGY SYNTAX RULES:
${rulesInput}

AGGREGATE FUNCTIONS: ${aggFunctions.join(', ')}

COMMON FUNCTIONS: ${functions.join(', ')}

VALID DATA TYPES: ${datatypes.join(', ')}

IMPORTANT GUIDELINES:
1. Use a reasonable LIMIT (e.g., 100–500) for exploratory run_query calls. For publish_results tree_id filters, do not add restrictive LIMIT unless the user explicitly asks for a capped subset.
2. For publish_results, return only tree_id (or alias that resolves to tree_id). Do not require latitude/longitude in publish queries.
3. If a query fails, explain the error and try a corrected version

Be concise and helpful. When showing query results, format them nicely.`,
)

// Module-level state (singleton)
const messages = ref<ChatMessage[]>([])
const isLoading = ref(false)
const providerType = ref(localStorage.getItem(API_TYPE_STORAGE) || '')
const apiKey = ref(localStorage.getItem(API_KEY_STORAGE) || '')
const pendingNavigationTimers: number[] = []

function cancelPendingNavigationTimers() {
  while (pendingNavigationTimers.length > 0) {
    const timerId = pendingNavigationTimers.pop()
    if (timerId != null) {
      window.clearTimeout(timerId)
    }
  }
}

export function useChat() {
  const { query: duckQuery } = useDuckDB()
  const { flyTo } = useFlyTo()
  const { landmarks } = useLandmarkData()
  const { publishMapTreeIdFilterSql, clearMapTreeIdFilter } = useMapData()
  const trilogy = useTrilogyCore()

  // Ensure the Trilogy resolver points at the production service
  if (!trilogy.userSettingsStore.settings.trilogyResolver ||
      trilogy.userSettingsStore.settings.trilogyResolver.includes('localhost')) {
    trilogy.userSettingsStore.updateSetting('trilogyResolver', TRILOGY_RESOLVER_URL)
  }

  // Create or update the LLM connection based on current providerType/apiKey
  async function ensureConnection() {
    const existing = trilogy.llmConnectionStore.connections[LLM_CONNECTION]
    const model = PROVIDER_DEFAULT_MODELS[providerType.value] || ''

    if (!existing) {
      await trilogy.llmConnectionStore.newConnection(LLM_CONNECTION, providerType.value, {
        apiKey: apiKey.value,
        model,
        saveCredential: false,
      })
    } else if (existing.type !== providerType.value) {
      // Provider type changed — remove old and create fresh
      delete trilogy.llmConnectionStore.connections[LLM_CONNECTION]
      await trilogy.llmConnectionStore.newConnection(LLM_CONNECTION, providerType.value, {
        apiKey: apiKey.value,
        model,
        saveCredential: false,
      })
    } else {
      // Same type, just refresh the key
      existing.setApiKey(apiKey.value)
    }
  }

  async function compilePreQL(query: string): Promise<string> {
    const response = await trilogy.resolver.resolve_query(
      query,
      'duckdb',
      'preql',
      [TREES_MODEL_SOURCE],
      [{name: TREES_MODEL_SOURCE.alias, alias: ''}]
    )
    if (response.data.error) {
      throw new Error(`Trilogy compile error: ${response.data.error}`)
    }
    return response.data.generated_sql
  }

  function setConnection(type: string, key: string) {
    providerType.value = type
    apiKey.value = key
    localStorage.setItem(API_TYPE_STORAGE, type)
    localStorage.setItem(API_KEY_STORAGE, key)
    // Remove existing connection so ensureConnection recreates it with new settings
    if (trilogy.llmConnectionStore.connections[LLM_CONNECTION]) {
      delete trilogy.llmConnectionStore.connections[LLM_CONNECTION]
    }
  }

  function deleteConnection() {
    providerType.value = ''
    apiKey.value = ''
    localStorage.removeItem(API_TYPE_STORAGE)
    localStorage.removeItem(API_KEY_STORAGE)
    if (trilogy.llmConnectionStore.connections[LLM_CONNECTION]) {
      delete trilogy.llmConnectionStore.connections[LLM_CONNECTION]
    }
    messages.value = []
    cancelPendingNavigationTimers()
  }

  // Convert UI ChatMessage[] to the library's LLMMessage-compatible history format
  function buildHistory(): HistoryMsg[] {
    const result: HistoryMsg[] = []
    for (const msg of messages.value) {
      if (msg.isLoading) continue
      if (msg.role === 'user') {
        result.push({ role: 'user', content: msg.content })
      } else {
        result.push({
          role: 'assistant',
          content: msg.content,
          ...(msg.toolCalls?.length && {
            toolCalls: msg.toolCalls.map((tc) => ({ id: tc.id, name: tc.name, input: tc.input })),
          }),
        })
        if (msg.toolCalls?.length) {
          result.push({
            role: 'user',
            content: '',
            toolResults: msg.toolCalls.map((tc) => ({
              toolCallId: tc.id,
              toolName: tc.name,
              result: tc.result,
            })),
          })
        }
      }
    }
    return result
  }

  async function executeTool(
    name: string,
    input: Record<string, unknown>,
  ): Promise<{ result: string; isError: boolean }> {
    try {
      switch (name) {
        case 'run_query': {
          const { query } = input as { query: string }
          const sql = await compilePreQL(query)
          const { columns, rows } = await duckQuery(sql)
          const truncated = rows.slice(0, 100)
          return {
            result: JSON.stringify({ columns, rows: truncated, totalRows: rows.length }),
            isError: false,
          }
        }
        case 'publish_results': {
          const { query } = input as { query: string }
          const sql = await compilePreQL(query)
          const wrappedSql = `
SELECT tree_id
FROM (
${sql}
) AS __publish_ids
WHERE tree_id IS NOT NULL
`

          const { rows } = await duckQuery(`SELECT COUNT(*) AS cnt FROM (${wrappedSql}) AS __count_ids`)
          const count = Number(rows[0]?.cnt ?? 0)

          if (!Number.isFinite(count) || count <= 0) {
            clearMapTreeIdFilter()
            return {
              result: 'Publish query returned no tree_ids. Cleared the active map filter.',
              isError: false,
            }
          }

          publishMapTreeIdFilterSql(wrappedSql)
          return {
            result: `Published ${count} tree_ids to the map filter.`,
            isError: false,
          }
        }
        case 'navigate': {
          cancelPendingNavigationTimers()

          const { latitude, longitude, zoom, locations } = input as {
            latitude?: number
            longitude?: number
            zoom?: number
            locations?: Array<{ latitude: number; longitude: number; zoom?: number }>
          }
          if (locations && locations.length > 0) {
            const stops = locations.map((l) => ({
              lat: l.latitude,
              lng: l.longitude,
              zoom: l.zoom ?? zoom ?? 16,
            }))
            flyTo(stops[0])
            for (let i = 1; i < stops.length; i++) {
              const stop = stops[i]
              const timerId = window.setTimeout(() => {
                flyTo(stop)
              }, i * 6000)
              pendingNavigationTimers.push(timerId)
            }
            return {
              result: `Touring ${stops.length} locations (3s between each stop).`,
              isError: false,
            }
          }
          if (latitude != null && longitude != null) {
            flyTo({ lat: latitude, lng: longitude, zoom: zoom ?? 16 })
            return { result: `Navigating to [${latitude.toFixed(4)}, ${longitude.toFixed(4)}]`, isError: false }
          }
          return { result: 'Must provide either latitude/longitude or a locations array.', isError: true }
        }
        case 'lookup_landmark': {
          const { name } = input as { name: string }
          const q = name.toLowerCase()
          const matches = landmarks.value.filter((l) => l.name.toLowerCase().includes(q))
          if (matches.length === 0) return { result: 'No landmarks found matching that name.', isError: false }
          return { result: JSON.stringify(matches.slice(0, 5)), isError: false }
        }
        default:
          return { result: `Unknown tool: ${name}`, isError: true }
      }
    } catch (e) {
      const err = e as Error
      console.error('[Tool Error]', {
        tool: name,
        input,
        message: err.message,
        stack: err.stack,
      })
      return { result: `Error: ${err.message}`, isError: true }
    }
  }

  async function sendMessage(userText: string) {
    messages.value.push({ role: 'user', content: userText })
    const assistantMsg: ChatMessage = { role: 'assistant', content: '', isLoading: true }
    messages.value.push(assistantMsg)
    isLoading.value = true

    try {
      await ensureConnection()
      // buildHistory() includes all settled turns, ending with the current user message
      let loopHistory = buildHistory()
      let loopCount = 0

      while (loopCount < MAX_LOOPS) {
        loopCount++
        // prompt is empty — full conversation lives in loopHistory
        const response = await trilogy.llmConnectionStore.generateCompletion(
          LLM_CONNECTION,
          { prompt: '', systemPrompt: SYSTEM_PROMPT, tools: TOOLS, maxTokens: 4096 },
          loopHistory as any,
        )

        if (!response.toolCalls?.length) {
          assistantMsg.content = response.text
          assistantMsg.isLoading = false
          break
        }

        // Execute tools
        const toolCalls: ToolCallRecord[] = []
        for (const tc of response.toolCalls) {
          const { result, isError } = await executeTool(tc.name, tc.input)
          if (isError) {
            console.error('[Tool Error Result]', {
              tool: tc.name,
              input: tc.input,
              result,
            })
          }
          toolCalls.push({ id: tc.id, name: tc.name, input: tc.input, result, isError })
        }

        // Update UI with intermediate state
        assistantMsg.content = response.text
        assistantMsg.toolCalls = [...(assistantMsg.toolCalls || []), ...toolCalls]

        // Extend history so the LLM sees its tool calls and results
        loopHistory = [
          ...loopHistory,
          { role: 'assistant', content: response.text, toolCalls: response.toolCalls },
          {
            role: 'user',
            content: '',
            toolResults: toolCalls.map((tc) => ({
              toolCallId: tc.id,
              toolName: tc.name,
              result: tc.result,
            })),
          },
        ]

        if (loopCount >= MAX_LOOPS) {
          assistantMsg.isLoading = false
        }
      }
    } catch (e) {
      assistantMsg.content = `Error: ${(e as Error).message}`
      assistantMsg.isLoading = false
    } finally {
      isLoading.value = false
      assistantMsg.isLoading = false
    }
  }

  function clearMessages() {
    messages.value = []
  }

  return {
    messages,
    isLoading,
    providerType: computed(() => providerType.value),
    apiKey: computed(() => apiKey.value),
    isConfigured: computed(() => providerType.value === 'demo' ? !!providerType.value : !!apiKey.value),
    setConnection,
    deleteConnection,
    sendMessage,
    clearMessages,
  }
}
