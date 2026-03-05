import { ref, computed } from 'vue'
import type { ChatMessage as LibChatMessage } from '@trilogy-data/trilogy-studio-components'
import type { ChatMessage, ToolCallRecord } from '../types'
import {
  useTrilogyCore,
  buildCustomTrilogyPrompt,
  runToolLoop,
  RETURN_TO_USER_TOOL,
} from '@trilogy-data/trilogy-studio-components'
import type {
  LLMAdapter,
  MessagePersistence,
  ToolExecutorFactory,
  ExecutionStateUpdater,
} from '@trilogy-data/trilogy-studio-components'
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
  openai: 'gpt-5.2',
  google: 'google/gemini-3-flash-preview',
  openrouter: 'google/gemini-3-flash-preview',
  demo: 'google/gemini-3-flash-preview',
}

const TREES_MODEL_SOURCE = { alias: 'trees', contents: TREES_MODEL }

const TOOLS = [
  {
    name: 'run_query',
    description:
      'Execute a Trilogy against the trees dataset. Write a SELECT statement using the available concepts — no FROM clause needed; Trilogy resolves the source automatically. Returns up to 100 JSON rows and total count. Use this to filter, aggregate, or explore the dataset. Limit results to 500 rows max unless the user specifically needs more.',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'The Trilogy SELECT statement to execute' },
      },
      required: ['query'],
    },
  },
  {
    name: 'publish_results',
    description:
      'Takes a Trilogy SELECT query that returns tree_id values for the trees to display on the map. Compiles and executes the query, persists those IDs as the active map filter, and applies DB-side filtering across map tiles. Use this after the user asks to show/highlight a subset of trees. To color trees, also SELECT an override_color column in the query (a hex color string computed with a CASE/IF expression — all coloring logic must be expressed in Trilogy so it is materialized before reaching the map). Provide color_labels to add a legend. Example: SELECT tree_id, case when diameter_at_breast_height >= 20 then \'#FF69B4\' else \'#4169E1\' end as override_color WHERE ...',
    input_schema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Trilogy SELECT returning tree_id. Optionally include override_color (hex string) for per-tree coloring — compute it inline with a CASE/IF expression.',
        },
        color_labels: {
          type: 'array',
          description: 'List of {color, label} pairs mapping each hex color used in override_color to a legend label shown on the map. Example: [{"color": "#FF69B4", "label": "DBH ≥ 20\\""}, {"color": "#4169E1", "label": "DBH < 20\\""}]',
          items: {
            type: 'object',
            properties: {
              color: { type: 'string', description: 'Hex color string, e.g. "#FF69B4"' },
              label: { type: 'string', description: 'Legend label for this color' },
            },
            required: ['color', 'label'],
          },
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
  {
    name: 'send_user_message',
    description:
      'Send a message to the user immediately, while continuing to work. Use this to share partial results, progress updates, or context before your final answer. After calling this, keep using other tools as needed, then call return_to_user when fully done.',
    input_schema: {
      type: 'object',
      properties: {
        message: { type: 'string', description: 'The message to display to the user right now' },
      },
      required: ['message'],
    },
  },
  RETURN_TO_USER_TOOL,
]

const _today = new Date().toLocaleDateString('en-US', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
})

const SYSTEM_PROMPT = buildCustomTrilogyPrompt(
  ({ rulesInput, aggFunctions, functions, datatypes }) => `You are an assistant for the SF Trees map application. You help users explore San Francisco's urban forest dataset of 100k+ trees and visualize the results. A default map is loaded with coloring by tree category, zoomed out to see SF from the oakland side.

You have access to tools for querying the tree dataset, displaying query results on the map, navigating the map camera, and looking up SF landmarks.

When users ask about trees, write Trilogy/PreQL SELECT queries using the available concepts. When they want to visualize results on the map, use publish_results with a query that returns tree_id values and optional color map. The website and map are dark themed, so color appropriately. When they mention locations, use lookup_landmark to find coordinates, then navigate there.

AVAILABLE CONCEPTS:
- tree_id (int) — unique identifier
- common_name (string) — e.g. "Swamp Myrtle"
- site_info (string) — planting site info (e.g. "Sidewalk: Curb side")
- plant_date (date) — date planted; not known for all trees.
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
2. For publish_results, include tree_id in the SELECT. To color trees, also SELECT override_color (a hex string) computed inline: SELECT tree_id, case when diameter_at_breast_height >= 20 then '#FF69B4' else '#4169E1' end as override_color WHERE ... All color logic must live in Trilogy — do not use color_field or color_mapping. Provide color_labels to label the legend: {"#FF69B4": "DBH ≥ 20\"", "#4169E1": "DBH < 20\""}.
3. If a query fails, explain the error and try a corrected version.
4. Always finish by calling return_to_user with your complete response. Never return a plain text reply — use return_to_user to signal you are done.

Be concise and helpful. When showing query results, format them nicely.

Today's date: ${_today}`,
)

// Module-level state (singleton)
const messages = ref<ChatMessage[]>([])
// LLM history in the lib's ChatMessage format — persists across sendMessage calls for multi-turn context
const llmHistory: LibChatMessage[] = []
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

// Convert a lib message's executedToolCalls to the app's ToolCallRecord[] for UI display.
// Filters out return_to_user since its message surfaces as the assistant's content instead.
function toToolCallRecords(
  executedToolCalls?: LibChatMessage['executedToolCalls'],
): ToolCallRecord[] | undefined {
  const display = executedToolCalls?.filter(
    (tc) => tc.name !== 'return_to_user' && tc.name !== 'send_user_message',
  )
  if (!display?.length) return undefined
  return display.map((tc) => ({
    id: tc.id,
    name: tc.name,
    input: tc.input as Record<string, unknown>,
    result: tc.result?.message || tc.result?.error || '',
    isError: !(tc.result?.success ?? true),
  }))
}

export function useChat() {
  const { query: duckQuery } = useDuckDB()
  const { flyTo } = useFlyTo()
  const { landmarks } = useLandmarkData()
  const { publishMapTreeIdFilterSql, clearMapTreeIdFilter, publishColorOverride } = useMapData()
  const trilogy = useTrilogyCore()

  // Ensure the Trilogy resolver points at the production service
  if (
    !trilogy.userSettingsStore.settings.trilogyResolver ||
    trilogy.userSettingsStore.settings.trilogyResolver.includes('localhost')
  ) {
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
      [{ name: TREES_MODEL_SOURCE.alias, alias: '' }],
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
    llmHistory.length = 0
    cancelPendingNavigationTimers()
  }

  async function executeTool(
    name: string,
    input: Record<string, any>,
  ): Promise<{ success: boolean; message?: string; error?: string }> {
    try {
      switch (name) {
        case 'run_query': {
          const { query } = input as { query: string }
          const sql = await compilePreQL(query)
          const { columns, rows } = await duckQuery(sql)
          const truncated = rows.slice(0, 100)
          return {
            success: true,
            message: JSON.stringify({ columns, rows: truncated, totalRows: rows.length }),
          }
        }
        case 'publish_results': {
          const { query, color_labels: rawColorLabels } = input as {
            query: string
            color_labels?: Array<{ color: string; label: string }>
          }

          // --- color_labels validation & debug ---
          console.log('[publish_results] color_labels received:', rawColorLabels)
          // Normalize to Record<string, string> with canonical #RRGGBB keys.
          // We match loosely on the last 6 hex chars so minor agent formatting quirks (e.g. missing #) still work.
          const HEX_TAIL = /([0-9a-fA-F]{6})$/i
          let normalizedColorLabels: Record<string, string> | null = null
          if (rawColorLabels !== undefined) {
            if (!Array.isArray(rawColorLabels)) {
              console.warn('[publish_results] color_labels is not an array:', rawColorLabels)
              return {
                success: false,
                error: 'color_labels must be an array of {color, label} objects, e.g. [{"color": "#FF69B4", "label": "Big trees"}].',
              }
            }
            const malformed: string[] = []
            const built: Record<string, string> = {}
            for (const entry of rawColorLabels) {
              const m = typeof entry.color === 'string' ? entry.color.match(HEX_TAIL) : null
              const label = typeof entry.label === 'string' ? entry.label.trim() : ''
              if (!m) {
                malformed.push(`"${entry.color}" has no valid 6-digit hex tail`)
              } else if (!label) {
                malformed.push(`entry for "${entry.color}" has an empty label`)
              } else {
                built['#' + m[1].toUpperCase()] = label
              }
            }
            if (malformed.length > 0) {
              console.warn('[publish_results] color_labels malformed entries:', malformed)
              return {
                success: false,
                error: `color_labels has malformed entries — legend will not render correctly. Issues: ${malformed.join('; ')}. Each entry must be {color: "#RRGGBB", label: "some text"}.`,
              }
            }
            normalizedColorLabels = built
            console.log(
              '[publish_results] color_labels normalized:',
              Object.entries(built).map(([k, v]) => `${k} → "${v}"`).join(', '),
            )
          } else {
            console.log('[publish_results] no color_labels provided')
          }
          // --- end validation ---

          const sql = await compilePreQL(query)

          // Check if the query returns an override_color column
          const { columns } = await duckQuery(`SELECT * FROM (${sql}) AS __probe LIMIT 0`)
          const hasColor = columns.includes('override_color')

          if (hasColor && !normalizedColorLabels) {
            console.warn(
              '[publish_results] override_color column present but color_labels was not provided — legend will be empty',
            )
          }

          const wrappedSql = `
SELECT tree_id
FROM (
${sql}
) AS __publish_ids
WHERE tree_id IS NOT NULL
`
          const { rows } = await duckQuery(
            `SELECT COUNT(*) AS cnt FROM (${wrappedSql}) AS __count_ids`,
          )
          const count = Number(rows[0]?.cnt ?? 0)

          if (!Number.isFinite(count) || count <= 0) {
            clearMapTreeIdFilter()
            return {
              success: true,
              message: 'Publish query returned no tree_ids. Cleared the active map filter.',
            }
          }

          if (hasColor) {
            const colorOverrideSql = `
SELECT tree_id, CAST(override_color AS VARCHAR) AS override_color
FROM (
${sql}
) AS __color_src
WHERE tree_id IS NOT NULL AND override_color IS NOT NULL
`
            // Execute the full color SQL before publishing so any runtime errors (e.g. invalid datetime
            // casts) are caught here and returned to the agent as a tool failure to refine.
            await duckQuery(colorOverrideSql)
            console.log('[publish_results] publishing color override, labels:', normalizedColorLabels)
            publishColorOverride(colorOverrideSql, normalizedColorLabels)
          } else {
            publishColorOverride(null, null)
          }

          publishMapTreeIdFilterSql(wrappedSql)
          const colorNote = hasColor ? ' with per-tree coloring' : ''
          const legendNote =
            hasColor && !normalizedColorLabels
              ? ' Warning: no color_labels provided so the legend will be empty.'
              : ''
          return {
            success: true,
            message: `Published ${count} tree_ids to the map filter${colorNote}.${legendNote}`,
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
              success: true,
              message: `Touring ${stops.length} locations (6s between each stop).`,
            }
          }
          if (latitude != null && longitude != null) {
            flyTo({ lat: latitude, lng: longitude, zoom: zoom ?? 16 })
            return {
              success: true,
              message: `Navigating to [${latitude.toFixed(4)}, ${longitude.toFixed(4)}]`,
            }
          }
          return {
            success: false,
            error: 'Must provide either latitude/longitude or a locations array.',
          }
        }
        case 'lookup_landmark': {
          const { name } = input as { name: string }
          const q = name.toLowerCase()
          const matches = landmarks.value.filter((l) => l.name.toLowerCase().includes(q))
          if (matches.length === 0)
            return { success: true, message: 'No landmarks found matching that name.' }
          return { success: true, message: JSON.stringify(matches.slice(0, 5)) }
        }
        case 'send_user_message': {
          const { message } = input as { message: string }
          const intermediateMsg: ChatMessage = { role: 'assistant', content: message }
          const loadingIdx = messages.value.findIndex((m) => m.isLoading)
          if (loadingIdx !== -1) {
            messages.value.splice(loadingIdx, 0, intermediateMsg)
          } else {
            messages.value.push(intermediateMsg)
          }
          return { success: true, message: 'Message delivered to user.' }
        }
        default:
          return { success: false, error: `Unknown tool: ${name}` }
      }
    } catch (e) {
      const err = e as Error
      console.error('[Tool Error]', { tool: name, input, message: err.message, stack: err.stack })
      return { success: false, error: err.message }
    }
  }

  async function sendMessage(userText: string) {
    // Add to UI and to LLM history (runToolLoop slices off the last entry as the current prompt)
    messages.value.push({ role: 'user', content: userText })
    llmHistory.push({ role: 'user', content: userText })

    // Loading placeholder — replaced in addMessage by the first real assistant response
    const loadingMsg: ChatMessage = { role: 'assistant', content: '', isLoading: true }
    messages.value.push(loadingMsg)
    isLoading.value = true

    const persistence: MessagePersistence = {
      addMessage: (msg) => {
        llmHistory.push(msg)
        if (msg.hidden) return

        // When return_to_user fires, its message is the user-visible answer
        const returnToUser = msg.executedToolCalls?.find((tc) => tc.name === 'return_to_user')
        const appMsg: ChatMessage = {
          role: msg.role as 'user' | 'assistant',
          content: returnToUser?.result?.message ?? msg.content,
          toolCalls: toToolCallRecords(msg.executedToolCalls),
        }

        if (msg.role === 'assistant') {
          const idx = messages.value.indexOf(loadingMsg)
          if (returnToUser) {
            // Final message — replace the loading placeholder
            if (idx !== -1) {
              messages.value.splice(idx, 1, appMsg)
            } else {
              messages.value.push(appMsg)
            }
            return
          }
          // Intermediate message (tool calls) — insert before the spinner so it stays visible
          if (idx !== -1) {
            messages.value.splice(idx, 0, appMsg)
          } else {
            messages.value.push(appMsg)
          }
          return
        }
        messages.value.push(appMsg)
      },
      addArtifact: () => {},
      getMessages: () => llmHistory,
    }

    const llmAdapter: LLMAdapter = {
      generateCompletion: (connName, opts, msgs) =>
        trilogy.llmConnectionStore.generateCompletion(connName, opts, msgs as any),
    }

    const toolExecutorFactory: ToolExecutorFactory = {
      getToolExecutor: () => ({
        executeToolCall: async (name, input) => {
          if (name === 'return_to_user') {
            return { success: true, message: input.message as string, terminatesLoop: true }
          }
          return executeTool(name, input)
        },
      }),
    }

    const stateUpdater: ExecutionStateUpdater = {
      setActiveToolName: () => {},
      checkAborted: () => false,
    }

    try {
      await ensureConnection()
      await runToolLoop(
        userText,
        LLM_CONNECTION,
        llmAdapter,
        persistence,
        toolExecutorFactory,
        stateUpdater,
        {
          tools: TOOLS,
          maxIterations: MAX_LOOPS,
          buildSystemPrompt: () => SYSTEM_PROMPT,
        },
      )
    } catch (e) {
      messages.value.push({ role: 'assistant', content: `Error: ${(e as Error).message}` })
    } finally {
      // Remove the loading placeholder if the loop never produced an assistant message
      const idx = messages.value.indexOf(loadingMsg)
      if (idx !== -1) messages.value.splice(idx, 1)
      isLoading.value = false
    }
  }

  function clearMessages() {
    messages.value = []
    llmHistory.length = 0
    cancelPendingNavigationTimers()
  }

  return {
    messages,
    isLoading,
    providerType: computed(() => providerType.value),
    apiKey: computed(() => apiKey.value),
    isConfigured: computed(() =>
      providerType.value === 'demo' ? !!providerType.value : !!apiKey.value,
    ),
    setConnection,
    deleteConnection,
    sendMessage,
    clearMessages,
  }
}
