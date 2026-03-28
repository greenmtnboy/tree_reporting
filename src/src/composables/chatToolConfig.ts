import { RETURN_TO_USER_TOOL } from '@trilogy-data/trilogy-studio-components/llm'

export type AppScreen = 'map' | 'summary' | 'info'

export type RouteLike = {
  name?: unknown
  path?: string | null
}

export const PROVIDER_DEFAULT_MODELS: Record<string, string> = {
  anthropic: 'claude-sonnet-4-6',
  openai: 'gpt-5.2',
  google: 'google/gemini-3-flash-preview',
  openrouter: 'google/gemini-3-flash-preview',
  demo: 'google/gemini-3-flash-preview',
}

export const MAP_CHAT_TOOLS = [
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
      `Takes a Trilogy SELECT query that returns tree_id values for the trees to display on the map. Compiles and executes the query, persists those IDs as the active map filter, and applies DB-side filtering across map tiles. Use this after the user asks to show/highlight a subset of trees. To color trees, also SELECT an override_color column in the query (a hex color string computed with a CASE/IF expression — all coloring logic must be expressed in Trilogy so it is materialized before reaching the map). Provide color_labels to add a legend. Example: SELECT tree_id, case when diameter_at_breast_height >= 20 then '#FF69B4' else '#4169E1' end as override_color WHERE ...`,
    input_schema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description:
            'Trilogy SELECT returning tree_id. Optionally include override_color (hex string) for per-tree coloring — compute it inline with a CASE/IF expression.',
        },
        color_labels: {
          type: 'array',
          description:
            'List of {color, label} pairs mapping each hex color used in override_color to a legend label shown on the map. Example: [{"color": "#FF69B4", "label": "DBH ≥ 20\\""}, {"color": "#4169E1", "label": "DBH < 20\\""}]',
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

export const SUMMARY_CHAT_TOOLS = [
  MAP_CHAT_TOOLS[0],
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
          description:
            'Optional list of summary chart ids to inspect. If omitted, all summary charts are included.',
          items: { type: 'string' },
        },
        row_limit: {
          type: 'number',
          description:
            'Maximum number of result rows to return per chart. Defaults to 10 and is capped at 25.',
        },
      },
    },
  },
  MAP_CHAT_TOOLS[4],
  RETURN_TO_USER_TOOL,
]

export function resolveAppScreen(route: RouteLike): AppScreen {
  const name = route.name
  const path = route.path ?? ''

  if (name === 'summary' || path.startsWith('/summary')) return 'summary'
  if (name === 'info' || path.startsWith('/info')) return 'info'
  return 'map'
}

export function toolsForScreen(screen: AppScreen) {
  return screen === 'summary' ? SUMMARY_CHAT_TOOLS : MAP_CHAT_TOOLS
}
