import { RETURN_TO_USER_TOOL } from '@trilogy-data/trilogy-studio-components/llm'

export type AppScreen = 'map' | 'summary' | 'species' | 'info'

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
      'Execute a Trilogy against the trees dataset. Write a SELECT statement using the available concepts — no FROM clause needed; Trilogy resolves the source automatically. Returns up to 100 JSON rows and total count. Use this to filter, aggregate, or explore the dataset. Limit results to 500 rows max unless the user specifically needs more. On the map the query must be bounded, or it is rejected: filter by city (WHERE city = \'USSFO\') or by a latitude and longitude range around a place.',
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
      `Show a subset of trees on the map, optionally recoloured. Takes a Trilogy SELECT returning tree_id. Write it as a per-tree rule: a WHERE over one tree's own attributes and its species' (SELECT tree_id WHERE genus = 'Quercus'). The map applies a rule to every tile of trees it loads, so it keeps working as the user pans or flies to another city; do not add a city filter unless the user wants only that city. A query that ranks, limits or aggregates (ORDER BY, LIMIT, count, rank) cannot be applied per tile: it is run once instead and must be bounded by a city filter or a latitude and longitude range, or it is rejected. To color trees, also SELECT an override_color column (a hex color string computed with a CASE/IF expression — all coloring logic must be expressed in Trilogy). Provide color_labels to add a legend. Example: SELECT tree_id, case when diameter_at_breast_height >= 20 then '#FF69B4' else '#4169E1' end as override_color WHERE is_evergreen = true`,
    input_schema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description:
            'Trilogy SELECT returning tree_id: a per-tree rule (no ORDER BY, LIMIT or aggregates), or a bounded query. Optionally include override_color (hex string) for per-tree coloring — compute it inline with a CASE/IF expression.',
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
    name: 'clear_published_results',
    description:
      'Remove whatever publish_results put on the map (the filter and any colours) so every tree shows again. Use it when the user asks to reset or show all trees.',
    input_schema: {
      type: 'object',
      properties: {},
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

const toolNamed = (name: string) => MAP_CHAT_TOOLS.find((tool) => tool.name === name)!
const RUN_QUERY_TOOL = toolNamed('run_query')
const SEND_USER_MESSAGE_TOOL = toolNamed('send_user_message')

export const SUMMARY_CHAT_TOOLS = [
  RUN_QUERY_TOOL,
  {
    name: 'set_summary_filters',
    description:
      'Update the analytics page cross-filters. Use this on the summary page to focus the charts by tree_form, species, native_locality_bucket, hardiness_fit_bucket, water_resilience_bucket, sun_exposure_label, lifespan_bucket, growth_rate, wildlife_value, or fire_risk. You can replace all filters, replace only specific fields, append values, or clear filters.',
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
                enum: [
                  'tree_form',
                  'species',
                  'native_locality_bucket',
                  'hardiness_fit_bucket',
                  'water_resilience_bucket',
                  'sun_exposure_label',
                  'lifespan_bucket',
                  'growth_rate',
                  'wildlife_value',
                  'fire_risk',
                ],
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
  SEND_USER_MESSAGE_TOOL,
  RETURN_TO_USER_TOOL,
]

export const SPECIES_CHAT_TOOLS = [
  RUN_QUERY_TOOL,
  {
    name: 'set_species_filters',
    description:
      'Update the species explorer selectors. Use this on the species page to set or clear the active genus and species filters. A species value may be provided without a genus; the genus will be derived automatically from the first token.',
    input_schema: {
      type: 'object',
      properties: {
        operation: {
          type: 'string',
          enum: ['replace', 'clear_species', 'clear_all'],
          description: 'How to update the species explorer filters.',
        },
        genus: {
          type: 'string',
          description: 'Exact genus value to select, such as "Acer".',
        },
        species: {
          type: 'string',
          description: 'Exact species value to select, such as "Acer rubrum".',
        },
      },
      required: ['operation'],
    },
  },
  {
    name: 'inspect_species_view',
    description:
      'Inspect the current species explorer state. Returns the active city, genus, species, and the scoped Trilogy base filters currently applied to the page.',
    input_schema: {
      type: 'object',
      properties: {},
    },
  },
  SEND_USER_MESSAGE_TOOL,
  RETURN_TO_USER_TOOL,
]

export function resolveAppScreen(route: RouteLike): AppScreen {
  const name = route.name
  const path = route.path ?? ''

  if (name === 'summary' || path.startsWith('/summary')) return 'summary'
  if (name === 'species' || path.startsWith('/species')) return 'species'
  if (name === 'info' || path.startsWith('/info')) return 'info'
  return 'map'
}

export function toolsForScreen(screen: AppScreen) {
  if (screen === 'summary') return SUMMARY_CHAT_TOOLS
  if (screen === 'species') return SPECIES_CHAT_TOOLS
  return MAP_CHAT_TOOLS
}
