import { ref, computed } from 'vue'
import type { ChatMessage as LibChatMessage } from '@trilogy-data/trilogy-studio-components/llm'
import type { ChatMessage, ToolCallRecord } from '../types'
import {
  buildCustomTrilogyPrompt,
  runToolLoop,
  RETURN_TO_USER_TOOL,
} from '@trilogy-data/trilogy-studio-components/llm'
import type {
  LLMAdapter,
  MessagePersistence,
  ToolExecutorFactory,
  ExecutionStateUpdater,
} from '@trilogy-data/trilogy-studio-components/llm'
import { useDuckDB } from './useDuckDB'
import { useFlyTo } from './useFlyTo'
import { useLandmarkData } from './useLandmarkData'
import { useMapData, CITY_CONFIG } from './useMapData'
import type { CityCode } from './useMapData'
import { ALL_MODEL_SOURCES } from '../trilogyModels'
import { useTrilogyRuntime } from './useTrilogyRuntime'
import { router } from '../router'
import {
  PROVIDER_DEFAULT_MODELS as CHAT_PROVIDER_DEFAULT_MODELS,
  resolveAppScreen as resolveRouteScreen,
  toolsForScreen as selectToolsForScreen,
} from './chatToolConfig'
import {
  createSafeToolExecutor as wrapSafeToolExecutor,
  safeJsonStringify as safeJsonStringifyHelper,
  toJsonSafeRows as toJsonSafeRowsHelper,
} from './chatToolExecution'
import { useSummaryFilters, type SummaryFilterField } from './useSummaryFilters'
import { useSummaryDashboardExecution } from './useSummaryDashboardExecution'
import {
  SUMMARY_CHARTS,
  SUMMARY_DASHBOARD_IMPORTS,
  SUMMARY_KPI_CHARTS,
  getSummaryBaseFilters,
  readSummaryRouteCity,
} from './summaryDashboardConfig'

const API_KEY_STORAGE = 'sf_trees_api_key'
const API_TYPE_STORAGE = 'sf_trees_provider_type'
const MAX_LOOPS = 10
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
      'Search the landmarks dataset for the active city by name (fuzzy match). Returns the landmark name, latitude, and longitude. Use this when the user mentions a place name to find its coordinates.',
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

type AppScreen = 'map' | 'summary' | 'info'

const SUMMARY_TOOLS = [
  TOOLS[0],
  {
    name: 'set_summary_filters',
    description:
      'Update the analytics page cross-filters. Use this on the summary page to focus the charts by tree_category, species, or native_status. You can replace all filters, replace only specific fields, append values, or clear filters.',
    input_schema: {
      type: 'object',
      properties: {
        operation: {
          type: 'string',
          enum: ['replace_all', 'replace_fields', 'append', 'clear'],
          description: 'How to apply the analytics filters.',
        },
        filters: {
          type: 'array',
          description: 'Analytics filters to apply.',
          items: {
            type: 'object',
            properties: {
              field: {
                type: 'string',
                enum: ['tree_category', 'species', 'native_status'],
                description: 'Which analytics dimension to filter.',
              },
              values: {
                type: 'array',
                items: { type: 'string' },
                description: 'One or more exact values for that field.',
              },
            },
            required: ['field'],
          },
        },
      },
      required: ['operation'],
    },
  },
  {
    name: 'inspect_summary_dashboard',
    description:
      'Inspect the active analytics dashboard. Returns the current chart queries, active filters, generated SQL, and a sampled result set for the requested summary cards.',
    input_schema: {
      type: 'object',
      properties: {
        chart_ids: {
          type: 'array',
          description: 'Optional list of summary chart ids to inspect. If omitted, all summary charts are included.',
          items: { type: 'string' },
        },
        row_limit: {
          type: 'number',
          description: 'Maximum number of result rows to return per chart. Defaults to 10 and is capped at 25.',
        },
      },
    },
  },
  TOOLS[4],
  RETURN_TO_USER_TOOL,
]

function getActiveScreen(): AppScreen {
  const routeName = router.currentRoute.value.name
  return routeName === 'summary' || routeName === 'info' ? routeName : 'map'
}

function toolsForScreen(screen: AppScreen) {
  return screen === 'summary' ? SUMMARY_TOOLS : TOOLS
}

const SUMMARY_INSPECTABLE_CHARTS = [...SUMMARY_KPI_CHARTS, ...SUMMARY_CHARTS]

const _today = new Date().toLocaleDateString('en-US', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
})

const _cityNames = Object.values(CITY_CONFIG).map((c) => c.name).join(', ')

function toJsonSafeValue(value: unknown): unknown {
  if (typeof value === 'bigint') {
    const asNumber = Number(value)
    return Number.isSafeInteger(asNumber) ? asNumber : value.toString()
  }

  if (value instanceof Date) {
    return value.toISOString()
  }

  if (value !== null && value !== undefined && (value as { isLuxonDateTime?: boolean }).isLuxonDateTime === true) {
    return (value as { toISO: () => string | null }).toISO() ?? null
  }

  if (Array.isArray(value)) {
    return value.map((item) => toJsonSafeValue(item))
  }

  if (typeof value === 'object' && value !== null) {
    return Object.fromEntries(
      Object.entries(value).map(([key, nestedValue]) => [key, toJsonSafeValue(nestedValue)]),
    )
  }

  return value
}

function toJsonSafeRows(rows: readonly Readonly<Record<string, unknown>>[]) {
  return rows.map((row) => toJsonSafeValue(row) as Record<string, unknown>)
}

function safeJsonStringify(value: unknown): string {
  return JSON.stringify(toJsonSafeValue(value))
}

function describeToolError(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  if (typeof error === 'string') {
    return error
  }

  try {
    return safeJsonStringify(error)
  } catch {
    return String(error)
  }
}

function normalizeToolResult<T extends { message?: unknown; error?: unknown }>(result: T): T {
  return {
    ...result,
    message:
      result.message == null
        ? undefined
        : typeof result.message === 'string'
          ? result.message
          : safeJsonStringify(result.message),
    error:
      result.error == null
        ? undefined
        : typeof result.error === 'string'
          ? result.error
          : safeJsonStringify(result.error),
  }
}

function buildSystemPromptForCity(city: CityCode, userLoc?: { lat: number; lng: number } | null): string {
  const cityName = CITY_CONFIG[city].name
  const userLocStr = userLoc
    ? `\nUSER LOCATION: The user's precise device location is lat ${userLoc.lat.toFixed(5)}, lng ${userLoc.lng.toFixed(5)}. When asked about "trees near me" or nearby trees, use a bounding box: WHERE latitude BETWEEN ${(userLoc.lat - 0.009).toFixed(5)} AND ${(userLoc.lat + 0.009).toFixed(5)} AND longitude BETWEEN ${(userLoc.lng - 0.011).toFixed(5)} AND ${(userLoc.lng + 0.011).toFixed(5)} (≈ 1 km radius). Adjust the range based on context.`
    : ''
  return buildCustomTrilogyPrompt(
    ({ rulesInput, aggFunctions, functions, datatypes }) => `You are an assistant for the Urban Trees map application. You help users explore cities' urban forest datasets of 100k+ trees and visualize the results. A default map is loaded with coloring by tree category. Cities supported include ${_cityNames}.${userLocStr}

ACTIVE CITY: ${cityName} (city code: ${city}). All queries must filter with WHERE city = '${city}' unless the user explicitly asks about another city.

You have access to tools for querying the tree dataset, displaying query results on the map, navigating the map camera, and looking up landmarks for the active city.

When users ask about trees, write Trilogy SELECT queries using the available concepts. When they want to visualize results on the map, use publish_results with a query that returns tree_id values and optional color map. The website and map are dark themed, so color appropriately. When they mention locations, use lookup_landmark to find coordinates, then navigate there.

AVAILABLE CONCEPTS:
- tree_id (string) — unique identifier
- tree_name (string) — e.g. "Swamp Myrtle"
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
2. For publish_results, include tree_id in the SELECT. To color trees, also SELECT override_color (a hex string) computed inline: SELECT tree_id, case when diameter_at_breast_height >= 20 then '#FF69B4' else '#4169E1' end as override_color WHERE ... All color logic must live in Trilogy — do not use color_field or color_mapping. Provide color_labels to label the legend: {"#FF69B4": "DBH ≥ 20", "#4169E1": "DBH < 20"}.
3. If a query fails, explain the error and try a corrected version.
4. Always finish by calling return_to_user with your complete response. Never return a plain text reply — use return_to_user to signal you are done.

Be concise and helpful. When showing query results, format them nicely.

Today's date: ${_today}`,
  )
}

function buildSummarySystemPrompt(city: CityCode, summaryFilterState: string): string {
  const cityName = CITY_CONFIG[city].name
  return buildCustomTrilogyPrompt(
    ({ rulesInput, aggFunctions, functions, datatypes }) => `You are an assistant for the Urban Trees analytics page. You help users explore city tree datasets through summary charts and direct data questions. Cities supported include ${_cityNames}.

ACTIVE SCREEN: Analytics summary.
ACTIVE CITY: ${cityName} (city code: ${city}). All queries must filter with WHERE city = '${city}' unless the user explicitly asks about another city.
ACTIVE ANALYTICS FILTERS: ${summaryFilterState}.

You have access to tools for querying the tree dataset, inspecting the active analytics dashboard, and updating the analytics dashboard filters. Use set_summary_filters when the user wants the charts narrowed, focused, or cleared. Use inspect_summary_dashboard when you need to see the current summary chart queries or results. Available analytics filter fields are tree_category, species, and native_status.

AVAILABLE CONCEPTS:
- tree_id (string) - unique identifier
- tree_name (string) - e.g. "Swamp Myrtle"
- plant_date (date) - date planted; not known for all trees.
- species (string) - full species string like "Tristaniopsis laurina :: Swamp Myrtle"
- latitude (float) - geographic latitude
- longitude (float) - geographic longitude
- diameter_at_breast_height (float) - trunk diameter in inches

SPECIES-LEVEL ENRICHMENT CONCEPTS:
- common_names (string) - comma-separated common names for the species
- native_status (string) - native_bay_area | native_california | non_native | naturalized | unknown
- is_evergreen (bool)
- mature_height_ft (float)
- canopy_spread_ft (float)
- growth_rate (string) - slow | moderate | fast
- lifespan_years (string) - e.g. "50-100", "200+"
- drought_tolerance (string) - low | moderate | high
- bloom_season (string) - September to November | autumn and winter | late spring and summer | late spring or summer | late spring to autumn | spring | spring and summer | summer | winter | year-round
- wildlife_value (string) - low | moderate | high
- fire_risk (string) - low | moderate | high
- tree_category (string) - palm | broadleaf | spreading | coniferous | columnar | ornamental | default

TRILOGY SYNTAX RULES:
${rulesInput}

AGGREGATE FUNCTIONS: ${aggFunctions.join(', ')}

COMMON FUNCTIONS: ${functions.join(', ')}

VALID DATA TYPES: ${datatypes.join(', ')}

IMPORTANT GUIDELINES:
1. Use a reasonable LIMIT (e.g., 100-500) for exploratory run_query calls.
2. Use set_summary_filters for requests like "filter to native trees", "show broadleaf species only", or "clear the filters".
3. If a query fails, explain the error and try a corrected version.
4. Always finish by calling return_to_user with your complete response. Never return a plain text reply - use return_to_user to signal you are done.

Be concise and helpful. When showing query results, format them nicely.

Today's date: ${_today}`,
  )
}

function getActiveSummaryCity(selectedCity: CityCode): CityCode {
  return readSummaryRouteCity(router.currentRoute.value.query.city) ?? selectedCity
}

function getSummaryChartDefinitions(chartIds?: string[]) {
  if (!chartIds?.length) {
    return SUMMARY_INSPECTABLE_CHARTS
  }
  const requested = new Set(chartIds)
  return SUMMARY_INSPECTABLE_CHARTS.filter((chart) => requested.has(chart.id))
}

function serializeDashboardRows(result: { toJSON: () => unknown }, rowLimit: number) {
  const serialized = result.toJSON() as {
    data?: Array<Record<string, unknown>>
    headers?: Record<string, unknown>
  }
  const rows = Array.isArray(serialized.data) ? serialized.data.slice(0, rowLimit) : []
  return {
    columns: Object.keys(serialized.headers ?? {}),
    rows: toJsonSafeRowsHelper(rows),
    totalRows: Array.isArray(serialized.data) ? serialized.data.length : 0,
  }
}

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
  const { query: duckQuery, setCityContext } = useDuckDB()
  const { flyTo } = useFlyTo()
  const { landmarks } = useLandmarkData()
  const { selectedCity, setSelectedCity, userLocation, publishMapTreeIdFilterSql, clearMapTreeIdFilter, publishColorOverride } = useMapData()
  const { crossFilters, applyValuesForField, clearFields, summaryFilterPromptState } = useSummaryFilters()
  const { initialize: initializeSummaryDashboard, connectionId: summaryConnectionId, queryExecutionService: summaryQueryExecutionService } = useSummaryDashboardExecution()
  const trilogy = useTrilogyRuntime()

  /** If (lat, lng) is closer to a different city than the current one, switch to it. */
  function detectAndSwitchCity(lat: number, lng: number): void {
    const R = 6371
    const haversineKm = (lat1: number, lng1: number, lat2: number, lng2: number) => {
      const dLat = ((lat2 - lat1) * Math.PI) / 180
      const dLng = ((lng2 - lng1) * Math.PI) / 180
      const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2
      return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
    }
    let closest = selectedCity.value
    let minDist = Infinity
    for (const [code, cfg] of Object.entries(CITY_CONFIG) as [CityCode, (typeof CITY_CONFIG)[CityCode]][]) {
      const dist = haversineKm(lat, lng, cfg.center[1], cfg.center[0])
      if (dist < minDist) { minDist = dist; closest = code }
    }
    if (closest !== selectedCity.value) {
      setSelectedCity(closest)
      void setCityContext(closest)
    }
  }

  // Create or update the LLM connection based on current providerType/apiKey
  async function ensureConnection() {
    const existing = trilogy.llmConnectionStore.connections[LLM_CONNECTION]
    const model = CHAT_PROVIDER_DEFAULT_MODELS[providerType.value] || ''

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
      // Same type, refresh the key and normalize the default model for app-managed connections.
      existing.setApiKey(apiKey.value)
      if (model) {
        existing.setModel(model)
      }
    }
  }

  async function compilePreQL(query: string): Promise<string> {
    const response = await trilogy.resolver.resolve_query(
      query,
      'duckdb',
      'preql',
      ALL_MODEL_SOURCES,
      [{ name: 'tree_enrichment', alias: '' }],
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
            message: safeJsonStringifyHelper({
              columns,
              rows: toJsonSafeRowsHelper(truncated),
              totalRows: rows.length,
            }),
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
            // Switch city based on the first stop if needed
            detectAndSwitchCity(stops[0].lat, stops[0].lng)
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
            detectAndSwitchCity(latitude, longitude)
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
          return { success: true, message: safeJsonStringifyHelper(matches.slice(0, 5)) }
        }
        case 'set_summary_filters': {
          const { operation, filters = [] } = input as {
            operation: 'replace_all' | 'replace_fields' | 'append' | 'clear'
            filters?: Array<{ field: SummaryFilterField; values?: string[] }>
          }

          if (resolveRouteScreen(router.currentRoute.value) !== 'summary') {
            return {
              success: false,
              error: 'set_summary_filters is only available on the analytics summary page.',
            }
          }

          const validFilters = filters.filter(
            (filter): filter is { field: SummaryFilterField; values?: string[] } =>
              filter != null &&
              ['tree_category', 'species', 'native_status'].includes(filter.field),
          )

          if (operation === 'clear') {
            const fields = validFilters.map((filter) => filter.field)
            clearFields(fields.length ? fields : undefined)
            return {
              success: true,
              message: `Updated analytics filters. Active filters: ${summaryFilterPromptState.value}.`,
            }
          }

          if (!validFilters.length) {
            return {
              success: false,
              error:
                'set_summary_filters requires at least one filter with field tree_category, species, or native_status.',
            }
          }

          if (operation === 'replace_all') {
            clearFields()
          }

          for (const filter of validFilters) {
            applyValuesForField(
              filter.field,
              Array.isArray(filter.values) ? filter.values : [],
              operation === 'append' ? 'append' : 'replace',
            )
          }

          return {
            success: true,
            message: `Updated analytics filters. Active filters: ${summaryFilterPromptState.value}.`,
          }
        }
        case 'inspect_summary_dashboard': {
          if (resolveRouteScreen(router.currentRoute.value) !== 'summary') {
            return {
              success: false,
              error: 'inspect_summary_dashboard is only available on the analytics summary page.',
            }
          }

          const { chart_ids: chartIds, row_limit: rawRowLimit } = input as {
            chart_ids?: string[]
            row_limit?: number
          }
          const rowLimit = Math.max(1, Math.min(Number(rawRowLimit) || 10, 25))
          const charts = getSummaryChartDefinitions(chartIds)

          if (!charts.length) {
            return {
              success: false,
              error: 'No matching summary chart ids were found to inspect.',
            }
          }

          await initializeSummaryDashboard()
          const activeCity = getActiveSummaryCity(selectedCity.value)
          const baseFilters = getSummaryBaseFilters(activeCity)
          const queries = charts.map((chart) => ({
            label: chart.id,
            query: chart.query,
            extra_filters: crossFilters.getSqlFiltersFor(chart.id, baseFilters),
          }))

          const { resultPromise } = await summaryQueryExecutionService.executeQueriesBatch(
            summaryConnectionId,
            queries,
            'trilogy',
            SUMMARY_DASHBOARD_IMPORTS.map((imp) => ({ name: imp.name, alias: imp.alias })),
          )
          const batchResult = await resultPromise

          const payload = charts.map((chart, index) => {
            const filters = crossFilters.getSqlFiltersFor(chart.id, baseFilters)
            const result = batchResult.results[index]
            const resultData =
              result?.success && result.results
                ? serializeDashboardRows(result.results, rowLimit)
                : { columns: [], rows: [], totalRows: 0 }

            return {
              id: chart.id,
              title: chart.title,
              query: chart.query,
              filters,
              generatedSql: result?.generatedSql ?? '',
              success: Boolean(result?.success),
              error: result?.error,
              ...resultData,
            }
          })

          return {
            success: true,
            message: safeJsonStringifyHelper({
              city: activeCity,
              activeFilters: summaryFilterPromptState.value,
              charts: payload,
            }),
          }
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
      addArtifact: () => { },
      getMessages: () => llmHistory,
    }

    const llmAdapter: LLMAdapter = {
      generateCompletion: (connName, opts, msgs) =>
        trilogy.llmConnectionStore.generateCompletion(connName, opts, msgs as any),
    }

    const toolExecutorFactory: ToolExecutorFactory = {
      getToolExecutor: () => ({
        executeToolCall: async (name, input) => {
          return wrapSafeToolExecutor(async (toolName, toolInput) => {
            if (toolName === 'return_to_user') {
              return {
                success: true,
                message: toolInput.message as string,
                terminatesLoop: true,
              }
            }
            return executeTool(toolName, toolInput)
          })(name, input)
        },
      }),
    }

    const stateUpdater: ExecutionStateUpdater = {
      setActiveToolName: () => { },
      checkAborted: () => false,
    }

    try {
      await ensureConnection()
      const screen = resolveRouteScreen(router.currentRoute.value)
      await runToolLoop(
        userText,
        LLM_CONNECTION,
        llmAdapter,
        persistence,
        toolExecutorFactory,
        stateUpdater,
        {
          tools: selectToolsForScreen(screen),
          maxIterations: MAX_LOOPS,
          buildSystemPrompt: () =>
            screen === 'summary'
              ? buildSummarySystemPrompt(selectedCity.value, summaryFilterPromptState.value)
              : buildSystemPromptForCity(selectedCity.value, userLocation.value),
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
