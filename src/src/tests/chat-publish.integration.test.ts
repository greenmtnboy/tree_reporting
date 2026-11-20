import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  duckQuery: vi.fn(),
  publishMapTreeIdFilterSql: vi.fn(),
  publishMapTreeRule: vi.fn(),
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
      publishMapTreeRule: mocks.publishMapTreeRule,
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

  /** One turn in which the model calls `name` with `input`, then returns. */
  async function callTool(name: string, input: Record<string, unknown>) {
    let completionCalls = 0
    mocks.generateCompletion.mockImplementation(async () => {
      completionCalls += 1
      return completionCalls === 1
        ? {
            text: 'Working.',
            usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
            toolCalls: [{ id: 'tool_1', name, input }],
          }
        : {
            text: 'Done.',
            usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
            toolCalls: [{ id: 'return_1', name: 'return_to_user', input: { message: 'Finished.' } }],
          }
    })
    const { useChat } = await import('../composables/useChat')
    const chat = useChat()
    await chat.sendMessage('Do it')
    // What the model was shown after the tool ran: its result is in there.
    return { chat, afterTool: JSON.stringify(mocks.generateCompletion.mock.calls[1] ?? []) }
  }

  const HONEY_LOCUST_SQL = `SELECT
  "map_trees"."tree_id" as "tree_id",
  :override_color AS override_color
FROM "trees" as "map_trees"
WHERE "map_trees"."species" LIKE 'Gleditsia triacanthos%'`

  it('publishes a per-tree query as a rule over the map\'s own rows, with its parameters applied', async () => {
    mocks.resolveQuery.mockResolvedValue({
      data: { generated_sql: `${HONEY_LOCUST_SQL};`, parameters: { override_color: '#DAA520' }, error: null },
    })
    mocks.duckQuery.mockImplementation(async (sql: string) => {
      if (sql.includes('__probe')) return { columns: ['tree_id', 'override_color'], rows: [] }
      if (sql.includes('__rule_count')) return { columns: ['cnt', 'colored', 'held'], rows: [{ cnt: 12, colored: 12, held: 4000 }] }
      throw new Error(`Unexpected SQL: ${sql}`)
    })

    const { chat, afterTool } = await callTool('publish_results', {
      query: `WHERE species LIKE 'Gleditsia triacanthos%'
SELECT
  tree_id,
  :override_color AS override_color;`,
      color_labels: [{ label: 'Honey Locust (Gleditsia triacanthos)', color: '#DAA520' }],
    })

    // The rule compiles against the map's `trees`, not a city's parquet:
    // that is what lets the worker ask it of each cell it loads. That the
    // import list resolves is `dashboard-pushdown.test.ts`, against the
    // live service; this pins the call site to it.
    const { MAP_RULE_IMPORTS } = await import('../composables/chatModelImports')
    expect(mocks.resolveQuery).toHaveBeenCalledTimes(1)
    const [, , , , imports] = mocks.resolveQuery.mock.calls[0] as unknown[]
    expect(imports).toEqual(MAP_RULE_IMPORTS)

    // A probe for the columns and one count over the trees held now, which
    // also evaluates every colour. No rows come back to this thread.
    expect(mocks.duckQuery).toHaveBeenCalledTimes(2)
    for (const [sql] of mocks.duckQuery.mock.calls as Array<[string]>) {
      expect(sql).not.toContain(':override_color')
      expect(sql).not.toContain(';')
    }

    expect(mocks.publishMapTreeRule).toHaveBeenCalledWith(
      expect.stringContaining("'#DAA520' AS override_color"),
      true,
      { '#DAA520': 'Honey Locust (Gleditsia triacanthos)' },
    )
    expect(mocks.publishMapTreeIdFilterSql).not.toHaveBeenCalled()
    expect(mocks.publishColorOverride).not.toHaveBeenCalled()
    expect(mocks.clearMapTreeIdFilter).not.toHaveBeenCalled()
    expect(afterTool).toContain('12 of the 4000 trees loaded around the camera match')
    expect(chat.messages.value.some((message) => message.content === 'Finished.')).toBe(true)
  })

  it('keeps a rule that matches nothing here: the next city may have matches', async () => {
    mocks.resolveQuery.mockResolvedValue({
      data: { generated_sql: `SELECT "map_trees"."tree_id" as "tree_id" FROM "trees" as "map_trees" WHERE "map_trees"."species" = 'Adansonia digitata'`, parameters: null, error: null },
    })
    mocks.duckQuery.mockImplementation(async (sql: string) => sql.includes('__probe')
      ? { columns: ['tree_id'], rows: [] }
      : { columns: ['cnt', 'held'], rows: [{ cnt: 0, held: 4000 }] })

    const { afterTool } = await callTool('publish_results', { query: "select tree_id where species = 'Adansonia digitata';" })

    expect(mocks.publishMapTreeRule).toHaveBeenCalledWith(expect.stringContaining('Adansonia'), false, null)
    expect(mocks.clearMapTreeIdFilter).not.toHaveBeenCalled()
    expect(afterTool).toContain('None of the trees loaded here match')
  })

  it('names what a rule may use when it asks for a column the tiles do not carry', async () => {
    mocks.resolveQuery.mockResolvedValue({
      data: { generated_sql: '', parameters: null, error: 'Discovery error: {tree_id}; {tree_name}' },
    })

    const { afterTool } = await callTool('publish_results', { query: 'select tree_id where tree_name is not null;' })

    expect(mocks.duckQuery).not.toHaveBeenCalled()
    expect(mocks.publishMapTreeRule).not.toHaveBeenCalled()
    expect(afterTool).toContain('Discovery error')
    expect(afterTool).toContain('diameter_at_breast_height')
  })

  it('runs a ranked query once, against the published files, when it says where', async () => {
    mocks.resolveQuery.mockResolvedValue({
      data: { generated_sql: `SELECT tree_id FROM read_parquet('https://example.test/usbos.parquet') ORDER BY dbh DESC LIMIT 50`, parameters: null, error: null },
    })
    mocks.duckQuery.mockImplementation(async (sql: string) => sql.includes('__probe')
      ? { columns: ['tree_id'], rows: [] }
      : { columns: ['cnt'], rows: [{ cnt: 50 }] })

    const { afterTool } = await callTool('publish_results', {
      query: "select tree_id, --diameter_at_breast_height where city = 'USBOS' order by diameter_at_breast_height desc limit 50;",
    })

    const { MAP_CHAT_IMPORTS } = await import('../composables/chatModelImports')
    const [, , , , imports] = mocks.resolveQuery.mock.calls[0] as unknown[]
    expect(imports).toEqual(MAP_CHAT_IMPORTS)
    expect(mocks.publishMapTreeIdFilterSql).toHaveBeenCalledWith(expect.stringContaining('usbos.parquet'))
    expect(mocks.publishColorOverride).toHaveBeenCalledWith(null, null)
    expect(mocks.publishMapTreeRule).not.toHaveBeenCalled()
    expect(afterTool).toContain('Published 50 tree_ids')
  })

  it('refuses a ranked query with no bounds, and says how to bound it', async () => {
    const { afterTool } = await callTool('publish_results', {
      query: 'select tree_id, --diameter_at_breast_height order by diameter_at_breast_height desc limit 50;',
    })

    expect(mocks.resolveQuery).not.toHaveBeenCalled()
    expect(mocks.duckQuery).not.toHaveBeenCalled()
    expect(mocks.publishMapTreeIdFilterSql).not.toHaveBeenCalled()
    expect(mocks.publishMapTreeRule).not.toHaveBeenCalled()
    expect(afterTool).toContain('publish_results was not run: it has a LIMIT')
    expect(afterTool).toContain('lookup_landmark')
  })

  it('refuses an unbounded run_query on the map, and runs a bounded one', async () => {
    const unbounded = await callTool('run_query', { query: 'select species, count(tree_id) as n order by n desc limit 10;' })
    expect(mocks.resolveQuery).not.toHaveBeenCalled()
    expect(mocks.duckQuery).not.toHaveBeenCalled()
    expect(unbounded.afterTool).toContain('run_query was not run')

    vi.resetModules()
    mocks.generateCompletion.mockReset()
    mocks.resolveQuery.mockResolvedValue({ data: { generated_sql: 'SELECT 1 AS n', parameters: null, error: null } })
    mocks.duckQuery.mockResolvedValue({ columns: ['n'], rows: [{ n: 1 }], totalRows: 1 })
    const bounded = await callTool('run_query', {
      query: 'select count(tree_id) as n where latitude between 42.3 and 42.4 and longitude between -71.1 and -71.0;',
    })
    expect(mocks.resolveQuery).toHaveBeenCalledTimes(1)
    expect(bounded.afterTool).toContain('totalRows')
  })

  it('clears whatever was published', async () => {
    const { afterTool } = await callTool('clear_published_results', {})
    expect(mocks.clearMapTreeIdFilter).toHaveBeenCalledTimes(1)
    expect(afterTool).toContain('The map shows every tree')
  })
})
