import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  duckQuery: vi.fn(),
  publishMapTreeIdFilterSql: vi.fn(),
  clearMapTreeIdFilter: vi.fn(),
  publishColorOverride: vi.fn(),
  resolveQuery: vi.fn(),
  generateCompletion: vi.fn(),
  newConnection: vi.fn(async (name: string, type: string) => {
    mocks.connections[name] = {
      type,
      setApiKey: vi.fn(),
      setModel: vi.fn(),
    }
  }),
  routerReplace: vi.fn(),
  connections: {} as Record<string, { type: string; setApiKey: ReturnType<typeof vi.fn>; setModel: ReturnType<typeof vi.fn> }>,
}))

vi.mock('../composables/useDuckDB', () => ({
  useDuckDB: () => ({
    query: mocks.duckQuery,
  }),
}))

vi.mock('../composables/useFlyTo', async () => {
  const { ref } = await import('vue')
  return {
    useFlyTo: () => ({
      target: ref(null),
      flyTo: vi.fn(),
      counter: () => 0,
    }),
  }
})

vi.mock('../composables/useLandmarkData', async () => {
  const { ref } = await import('vue')
  return {
    useLandmarkData: () => ({
      landmarks: ref([]),
      loading: ref(false),
      error: ref(null),
    }),
  }
})

vi.mock('../composables/useMapData', async () => {
  const { ref } = await import('vue')
  return {
    CITY_CONFIG: {
      USSFO: { name: 'San Francisco', center: [-122.4194, 37.7749] as [number, number] },
      USBOS: { name: 'Boston', center: [-71.0589, 42.3601] as [number, number] },
    },
    useMapData: () => ({
      selectedCity: ref('USBOS'),
      userLocation: ref(null),
      publishMapTreeIdFilterSql: mocks.publishMapTreeIdFilterSql,
      clearMapTreeIdFilter: mocks.clearMapTreeIdFilter,
      publishColorOverride: mocks.publishColorOverride,
    }),
  }
})

vi.mock('../composables/useSummaryFilters', async () => {
  const { ref, computed } = await import('vue')
  return {
    SUMMARY_FILTER_FIELDS: [],
    useSummaryFilters: () => ({
      crossFilters: {
        getSqlFiltersFor: vi.fn(() => []),
        getSqlParametersFor: vi.fn(() => ({})),
        version: ref(0),
      },
      applyValuesForField: vi.fn(),
      clearFields: vi.fn(),
      summaryFilterPromptState: computed(() => 'none'),
    }),
  }
})

vi.mock('../composables/useSummaryDashboardExecution', () => ({
  useSummaryDashboardExecution: () => ({
    initialize: vi.fn(async () => {}),
    connectionId: 'summary-duckdb',
    queryExecutionService: {},
    setDashboardContext: vi.fn(),
  }),
}))

vi.mock('../composables/useTrilogyRuntime', () => ({
  useTrilogyRuntime: () => ({
    resolver: {
      resolve_query: mocks.resolveQuery,
    },
    llmConnectionStore: {
      connections: mocks.connections,
      newConnection: mocks.newConnection,
      generateCompletion: mocks.generateCompletion,
    },
  }),
}))

vi.mock('../router', async () => {
  const { ref } = await import('vue')
  return {
    router: {
      currentRoute: ref({ path: '/', name: 'map', query: {} }),
      replace: mocks.routerReplace,
    },
  }
})

function installBrowserGlobals() {
  Object.defineProperty(globalThis, 'window', {
    value: globalThis,
    configurable: true,
  })

  const storage = new Map<string, string>([['sf_trees_provider_type', 'demo']])

  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        storage.set(key, value)
      },
      removeItem: (key: string) => {
        storage.delete(key)
      },
      clear: () => {
        storage.clear()
      },
    },
    configurable: true,
  })
}

describe('chat publish_results integration', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    installBrowserGlobals()
    Object.keys(mocks.connections).forEach((key) => delete mocks.connections[key])
  })

  it('applies resolver output parameters before probing and publishing map SQL', async () => {
    let completionCalls = 0

    mocks.generateCompletion.mockImplementation(async () => {
      if (completionCalls === 0) {
        completionCalls += 1
        return {
          text: 'Publishing highlighted trees.',
          usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
          toolCalls: [
            {
              id: 'publish_1',
              name: 'publish_results',
              input: {
                query: `WHERE city = 'USBOS' AND species LIKE 'Gleditsia triacanthos%'
SELECT
  tree_id,
  :override_color AS override_color;`,
                color_labels: [
                  {
                    label: 'Honey Locust (Gleditsia triacanthos)',
                    color: '#DAA520',
                  },
                ],
              },
            },
          ],
        }
      }

      return {
        text: 'Done.',
        usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
        toolCalls: [
          {
            id: 'return_1',
            name: 'return_to_user',
            input: { message: 'Published the highlighted trees.' },
          },
        ],
      }
    })

    mocks.resolveQuery.mockResolvedValue({
      data: {
        generated_sql: `SELECT
  tree_id,
  :override_color AS override_color
FROM trees
WHERE city = 'USBOS' AND species LIKE 'Gleditsia triacanthos%'`,
        parameters: {
          override_color: '#DAA520',
        },
        error: null,
      },
    })

    mocks.duckQuery.mockImplementation(async (sql: string) => {
      if (sql.includes('__probe')) {
        return { columns: ['tree_id', 'override_color'], rows: [] }
      }
      if (sql.includes('__count_ids')) {
        return { columns: ['cnt'], rows: [{ cnt: 1 }] }
      }
      if (sql.includes('__color_src')) {
        return {
          columns: ['tree_id', 'override_color'],
          rows: [{ tree_id: 'tree-1', override_color: '#DAA520' }],
        }
      }
      throw new Error(`Unexpected SQL: ${sql}`)
    })

    const { useChat } = await import('../composables/useChat')
    const chat = useChat()

    await chat.sendMessage('Highlight honey locusts in Boston')

    expect(mocks.resolveQuery).toHaveBeenCalledTimes(1)
    expect(mocks.duckQuery).toHaveBeenCalledTimes(3)

    for (const [sql] of mocks.duckQuery.mock.calls as Array<[string]>) {
      expect(sql).not.toContain(':override_color')
    }

    expect(mocks.publishColorOverride).toHaveBeenCalledWith(
      expect.stringContaining("'#DAA520' AS override_color"),
      { '#DAA520': 'Honey Locust (Gleditsia triacanthos)' },
    )
    expect(mocks.publishMapTreeIdFilterSql).toHaveBeenCalledWith(
      expect.stringContaining("'#DAA520' AS override_color"),
    )
    expect(mocks.clearMapTreeIdFilter).not.toHaveBeenCalled()
    expect(chat.messages.value.some((message) => message.content === 'Published the highlighted trees.')).toBe(true)
  })
})
