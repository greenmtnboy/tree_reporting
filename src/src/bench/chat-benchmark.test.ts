/** @vitest-environment happy-dom */
/**
 * The agent chat benchmark: every suggested prompt, N rounds each, through the
 * real chat loop, against the live resolver and the demo model.
 *
 * What is real: `useChat` itself -- its system prompts, the tool schemas the
 * screen selects, `executeTool` with every validation branch, the library's
 * `runToolLoop`, `TrilogyResolver` and `QueryExecutionService`, and the
 * `DemoProvider` minting the same public token a visitor gets. What is stood
 * in for is only what needs a browser: the DuckDB worker (node DuckDB over
 * the same published parquets), the map and route state, and the landmark
 * table. See `nodeChatEngine.ts`.
 *
 * A round is one fresh conversation (the module registry is reset, so chat
 * history and the analytics filters start empty) sending one prompt. It is
 * scored, most severe first:
 *
 *   timeout          the loop did not finish inside CHAT_BENCH_ROUND_TIMEOUT_MS
 *   loop_error       the LLM call itself failed, or sendMessage threw
 *   no_final_answer  the loop ended without the agent calling return_to_user
 *   tool_error       a tool call returned an error the agent had to recover from
 *   clean            return_to_user reached with every tool call succeeding
 *
 * Only `clean` counts as passing: the suggestions are the prompts we tell
 * users to try, so a round where the model wrote a query the resolver
 * rejected and then fixed it still says the prompt is not reliably answered.
 * The report groups the error text by prompt and tool, which is what points
 * at a prompt or schema fix.
 *
 * Network, money and minutes: ~120 conversations of two to four model calls
 * each against a per-IP demo budget, and every compile goes to the one shared
 * resolver instance, so rounds run one at a time. Not part of `pnpm test`.
 *
 *   pnpm bench:chat                                   the full sweep
 *   CHAT_BENCH_ROUNDS=3 pnpm bench:chat               fewer rounds
 *   CHAT_BENCH_CASES=map-2,species pnpm bench:chat    ids or substrings
 *   OPENROUTER_API_KEY=... pnpm bench:chat            your key, same model
 *   CHAT_BENCH_MODEL=openai/gpt-5.2 pnpm bench:chat   a different model
 *   CHAT_BENCH_FAIL_ON_ERRORS=1 pnpm bench:chat       red when any round is not clean
 *
 * Results land in `src/bench-results/` (gitignored): a JSON file with every
 * round's tool calls and model responses, and a markdown summary.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// happy-dom has no Worker; the dashboard bundle constructs one at import time.
vi.hoisted(() => {
  const globals = globalThis as { Worker?: unknown }
  globals.Worker ??= class {
    postMessage() {}
    terminate() {}
    addEventListener() {}
    removeEventListener() {}
  }
})

/**
 * Shared across module resets. Every round re-imports `useChat` fresh (so its
 * module-level conversation state is empty) and the mock factories below run
 * again; what they hand out comes from here, so the database, resolver cache
 * and connection store survive while the chat does not.
 */
const state = vi.hoisted(() => ({
  db: null as null | { query: (sql: string) => Promise<unknown>; executeSql: unknown },
  resolver: null as unknown,
  llmStore: null as unknown,
  summaryExec: null as unknown,
  landmarks: [] as unknown[],
  selectedCity: 'USSFO',
  route: { path: '/', name: 'map', query: {} as Record<string, string> },
  llmCalls: [] as Array<Record<string, unknown>>,
  mapCalls: [] as Array<Record<string, unknown>>,
}))

vi.mock('../composables/useDuckDB', () => ({
  useDuckDB: () => ({
    query: (sql: string) => state.db!.query(sql),
  }),
}))

vi.mock('../composables/useLandmarkData', async () => {
  const { ref } = await import('vue')
  return {
    useLandmarkData: () => ({
      landmarks: ref(state.landmarks),
      loading: ref(false),
      error: ref(null),
    }),
  }
})

vi.mock('../composables/useMapData', async (importOriginal) => {
  const original = await importOriginal<typeof import('../composables/useMapData')>()
  const { ref } = await import('vue')
  return {
    ...original,
    useMapData: () => ({
      selectedCity: ref(state.selectedCity),
      userLocation: ref(null),
      publishMapTreeIdFilterSql: (sql: string) => state.mapCalls.push({ call: 'publishMapTreeIdFilterSql', sql }),
      clearMapTreeIdFilter: () => state.mapCalls.push({ call: 'clearMapTreeIdFilter' }),
      publishColorOverride: (sql: string | null, labels: unknown) =>
        state.mapCalls.push({ call: 'publishColorOverride', sql, labels }),
    }),
  }
})

vi.mock('../composables/useSummaryDashboardExecution', () => ({
  useSummaryDashboardExecution: () => state.summaryExec,
}))

vi.mock('../composables/useTrilogyRuntime', () => ({
  useTrilogyRuntime: () => ({
    resolver: state.resolver,
    llmConnectionStore: state.llmStore,
  }),
}))

vi.mock('../router', async () => {
  const { ref } = await import('vue')
  const currentRoute = ref({ ...state.route, query: { ...state.route.query } })
  return {
    router: {
      currentRoute,
      // The species tools change the selection by rewriting the route query
      // and then read it back, so replace has to land.
      replace: async (to: { query?: Record<string, string> }) => {
        currentRoute.value = { ...currentRoute.value, query: { ...(to.query ?? {}) } }
      },
    },
  }
})

import { ALL_MODEL_SOURCES } from '../trilogyModels'
import { buildDashboardContextSource } from '../composables/dashboardContextSource'
import { PROVIDER_DEFAULT_MODELS } from '../composables/chatToolConfig'
import { CHAT_BENCH_CASES, startingRoute, type ChatBenchCase } from './chatBenchCatalog'
import {
  NodeTreeDatabase,
  createLlmConnectionStore,
  createResolver,
  createSummaryExecution,
  type LlmCallRecord,
} from './nodeChatEngine'

const ROUNDS = Number(process.env.CHAT_BENCH_ROUNDS ?? 10)
const CITY = process.env.CHAT_BENCH_CITY ?? 'USSFO'
const ROUND_TIMEOUT_MS = Number(process.env.CHAT_BENCH_ROUND_TIMEOUT_MS ?? 180_000)
const FAIL_ON_ERRORS = process.env.CHAT_BENCH_FAIL_ON_ERRORS === '1'
const OUT_DIR = process.env.CHAT_BENCH_OUT_DIR ?? join(process.cwd(), 'bench-results')
const PROVIDER = process.env.OPENROUTER_API_KEY ? 'openrouter' : 'demo'
const MODEL = process.env.CHAT_BENCH_MODEL || PROVIDER_DEFAULT_MODELS[PROVIDER]

function selectedCases(): ChatBenchCase[] {
  const filter = (process.env.CHAT_BENCH_CASES ?? '')
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
  if (!filter.length) return CHAT_BENCH_CASES
  // Ids and id prefixes only. Matching prompt text as well looked convenient
  // and cost a demo-budget window: `species` also matched "...the top 5
  // species?" on the summary page and re-ran that instead.
  return CHAT_BENCH_CASES.filter((testCase) =>
    filter.some((part) => testCase.id === part || testCase.id.startsWith(part)),
  )
}

type Verdict =
  | 'clean'
  | 'tool_error'
  | 'no_final_answer'
  | 'loop_error'
  | 'timeout'
  /** The model call was refused for budget: the demo key's $0.50 is spent. */
  | 'budget_exhausted'
  /** Skipped because an earlier round exhausted the budget; not a measurement. */
  | 'not_run'

/**
 * The demo token is one $0.50 key per IP, minted with an hour's TTL; when it
 * is spent every model call answers 403 until a new key is issued. Once one
 * round sees that, the rest of the sweep would only record the same refusal,
 * so the remaining rounds are marked `not_run` and the run ends early.
 */
const BUDGET_EXHAUSTED = /Key limit exceeded|\(402\)|\(403\)/
let budgetExhausted = false

type ToolCallSummary = {
  name: string
  input: Record<string, unknown>
  isError: boolean
  result: string
}

type RoundResult = {
  caseId: string
  screen: string
  prompt: string
  round: number
  verdict: Verdict
  durationMs: number
  llmCalls: number
  toolCalls: ToolCallSummary[]
  errors: string[]
  finalMessage: string | null
  loopError: string | null
  systemPrompt: string | null
  toolNames: string[]
  transcript: LlmCallRecord[]
  mapCalls: Array<Record<string, unknown>>
  appLogs: string[]
}

const results: RoundResult[] = []

function installConnectionSettings() {
  localStorage.setItem('sf_trees_provider_type', PROVIDER)
  localStorage.setItem('sf_trees_api_key', process.env.OPENROUTER_API_KEY ?? '')
}

async function runRound(testCase: ChatBenchCase, round: number): Promise<RoundResult> {
  vi.resetModules()
  state.route = startingRoute(testCase.screen, CITY)
  state.selectedCity = CITY
  state.llmCalls = []
  state.mapCalls = []
  state.summaryExec = createSummaryExecution(
    state.resolver as never,
    state.db as never,
    ALL_MODEL_SOURCES,
    buildDashboardContextSource,
    CITY,
  )
  installConnectionSettings()

  const { useChat } = await import('../composables/useChat')
  // Set the route on the instance useChat sees rather than trusting the mock
  // factory to have re-read `state.route`: the first sweep ran every prompt
  // on the map screen because the factory's ref was created once and kept.
  const { router } = await import('../router')
  router.currentRoute.value = { ...state.route, query: { ...state.route.query } } as never
  const chat = useChat()

  // The chat logs every tool call and error to the console. That is the
  // round's evidence, not the sweep's progress, so it goes into the record.
  const appLogs: string[] = []
  const restoreConsole = captureConsole((level, text) => appLogs.push(`${level}: ${text}`))

  const startedAt = Date.now()
  let timedOut = false
  try {
    await Promise.race([
      chat.sendMessage(testCase.prompt),
      new Promise<void>((resolve) =>
        setTimeout(() => {
          timedOut = true
          resolve()
        }, ROUND_TIMEOUT_MS),
      ),
    ])
  } finally {
    restoreConsole()
  }
  const durationMs = Date.now() - startedAt

  const transcript = state.llmCalls as unknown as LlmCallRecord[]
  const systemPrompt = transcript[0]?.systemPrompt ?? null
  for (const call of transcript) delete call.systemPrompt
  const toolCalls: ToolCallSummary[] = chat.messages.value.flatMap((message) =>
    (message.toolCalls ?? []).map((record) => ({
      name: record.name,
      input: record.input,
      isError: Boolean(record.isError),
      result: record.output ?? record.result,
    })),
  )
  const errors = toolCalls.filter((call) => call.isError).map((call) => `${call.name}: ${call.result}`)

  const llmFailure = transcript.find((call) => call.error)?.error ?? null
  const thrown = chat.messages.value.find(
    (message) => message.role === 'assistant' && !message.toolCalls && message.content.startsWith('Error: '),
  )
  const loopError = llmFailure ?? thrown?.content ?? null

  const returned = transcript
    .flatMap((call) => call.response?.toolCalls ?? [])
    .find((call) => call.name === 'return_to_user')
  const finalMessage = returned ? String((returned.input as { message?: unknown }).message ?? '') : null

  const verdict: Verdict = timedOut
    ? 'timeout'
    : loopError && BUDGET_EXHAUSTED.test(loopError)
      ? 'budget_exhausted'
      : loopError
        ? 'loop_error'
        : !returned
          ? 'no_final_answer'
          : errors.length
            ? 'tool_error'
            : 'clean'
  if (verdict === 'budget_exhausted') budgetExhausted = true

  return {
    caseId: testCase.id,
    screen: testCase.screen,
    prompt: testCase.prompt,
    round,
    verdict,
    durationMs,
    llmCalls: transcript.length,
    toolCalls,
    errors,
    finalMessage,
    loopError,
    systemPrompt,
    toolNames: transcript[0]?.toolNames ?? [],
    transcript,
    mapCalls: state.mapCalls,
    appLogs,
  }
}

function notRun(testCase: ChatBenchCase, round: number): RoundResult {
  return {
    caseId: testCase.id,
    screen: testCase.screen,
    prompt: testCase.prompt,
    round,
    verdict: 'not_run',
    durationMs: 0,
    llmCalls: 0,
    toolCalls: [],
    errors: [],
    finalMessage: null,
    loopError: null,
    systemPrompt: null,
    toolNames: [],
    transcript: [],
    mapCalls: [],
    appLogs: [],
  }
}

type ConsoleLevel = 'log' | 'debug' | 'info' | 'warn' | 'error'

function captureConsole(sink: (level: ConsoleLevel, text: string) => void) {
  const levels: ConsoleLevel[] = ['log', 'debug', 'info', 'warn', 'error']
  const originals = Object.fromEntries(levels.map((level) => [level, console[level]])) as Record<
    ConsoleLevel,
    (...args: unknown[]) => void
  >
  for (const level of levels) {
    console[level] = (...args: unknown[]) => {
      sink(
        level,
        args
          .map((arg) => (typeof arg === 'string' ? arg : safeStringify(arg)))
          .join(' ')
          .slice(0, 2_000),
      )
    }
  }
  return () => {
    for (const level of levels) console[level] = originals[level]
  }
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, (_key, nested) => (typeof nested === 'bigint' ? nested.toString() : nested))
  } catch {
    return String(value)
  }
}

function seconds(ms: number) {
  return `${(ms / 1000).toFixed(1)}s`
}

function summarize(rounds: RoundResult[]) {
  const byCase = new Map<string, RoundResult[]>()
  for (const round of rounds) {
    byCase.set(round.caseId, [...(byCase.get(round.caseId) ?? []), round])
  }
  return [...byCase.entries()].map(([caseId, caseRounds]) => {
    const count = (verdict: Verdict) => caseRounds.filter((round) => round.verdict === verdict).length
    const errorCounts = new Map<string, number>()
    for (const round of caseRounds) {
      for (const error of round.errors) {
        const key = error.replace(/\s+/g, ' ').slice(0, 220)
        errorCounts.set(key, (errorCounts.get(key) ?? 0) + 1)
      }
      if (round.loopError) {
        const key = `loop: ${round.loopError.replace(/\s+/g, ' ').slice(0, 220)}`
        errorCounts.set(key, (errorCounts.get(key) ?? 0) + 1)
      }
      if (round.verdict === 'no_final_answer') {
        errorCounts.set('no return_to_user', (errorCounts.get('no return_to_user') ?? 0) + 1)
      }
      if (round.verdict === 'timeout') {
        errorCounts.set('timeout', (errorCounts.get('timeout') ?? 0) + 1)
      }
    }
    // A budget refusal and a skipped round say nothing about the prompt, so
    // the rate is over the rounds that actually reached the model.
    const measured = caseRounds.filter(
      (round) => round.verdict !== 'not_run' && round.verdict !== 'budget_exhausted',
    )
    return {
      caseId,
      screen: caseRounds[0].screen,
      prompt: caseRounds[0].prompt,
      rounds: caseRounds.length,
      measured: measured.length,
      clean: count('clean'),
      toolError: count('tool_error'),
      noFinalAnswer: count('no_final_answer'),
      loopError: count('loop_error'),
      timeout: count('timeout'),
      budgetExhausted: count('budget_exhausted'),
      notRun: count('not_run'),
      errorRatePct: measured.length
        ? Math.round((100 * (measured.length - count('clean'))) / measured.length)
        : null,
      avgDurationMs: measured.length
        ? Math.round(measured.reduce((sum, round) => sum + round.durationMs, 0) / measured.length)
        : 0,
      avgLlmCalls: measured.length
        ? Number((measured.reduce((sum, round) => sum + round.llmCalls, 0) / measured.length).toFixed(1))
        : 0,
      errors: [...errorCounts.entries()].sort((a, b) => b[1] - a[1]).map(([message, n]) => ({ message, n })),
    }
  })
}

function renderMarkdown(rounds: RoundResult[], startedAt: Date) {
  const summary = summarize(rounds)
  const measured = summary.reduce((sum, row) => sum + row.measured, 0)
  const clean = rounds.filter((round) => round.verdict === 'clean').length
  const unmeasured = rounds.length - measured
  const lines: string[] = []
  lines.push(`# Chat benchmark`)
  lines.push('')
  lines.push(`- run: ${startedAt.toISOString()}`)
  lines.push(`- provider: ${PROVIDER}, model: ${MODEL}, city: ${CITY}`)
  lines.push(
    `- rounds measured: ${measured} of ${rounds.length}, clean: ${clean} (${measured ? Math.round((100 * clean) / measured) : 0}%)` +
      (unmeasured ? ` -- ${unmeasured} rounds not measured: the demo key's budget was exhausted` : ''),
  )
  lines.push('')
  lines.push(
    '| case | prompt | measured | clean | tool err | no final | loop err | timeout | budget | not run | error rate | avg s | avg LLM calls |',
  )
  lines.push('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
  for (const row of summary) {
    lines.push(
      `| ${row.caseId} | ${row.prompt} | ${row.measured}/${row.rounds} | ${row.clean} | ${row.toolError} | ${row.noFinalAnswer} | ${row.loopError} | ${row.timeout} | ${row.budgetExhausted} | ${row.notRun} | ${row.errorRatePct == null ? '-' : `${row.errorRatePct}%`} | ${(row.avgDurationMs / 1000).toFixed(1)} | ${row.avgLlmCalls} |`,
    )
  }
  lines.push('')
  lines.push('## Errors by prompt')
  lines.push('')
  for (const row of summary) {
    if (!row.errors.length) continue
    lines.push(`### ${row.caseId}: ${row.prompt}`)
    lines.push('')
    for (const error of row.errors) {
      lines.push(`- (${error.n}) ${error.message}`)
    }
    lines.push('')
  }
  return lines.join('\n')
}

describe('agent chat benchmark', () => {
  const cases = selectedCases()
  const startedAt = new Date()

  beforeAll(async () => {
    state.db = (await NodeTreeDatabase.create()) as never
    state.resolver = createResolver()
    state.llmStore = createLlmConnectionStore((record) => {
      state.llmCalls.push(record as unknown as Record<string, unknown>)
    })
    state.landmarks = await (state.db as unknown as NodeTreeDatabase).loadLandmarks(CITY)
    console.log(
      `chat benchmark: ${cases.length} prompts x ${ROUNDS} rounds, provider=${PROVIDER} model=${MODEL} city=${CITY}, ${state.landmarks.length} landmarks loaded`,
    )
  }, 120_000)

  afterAll(() => {
    if (!results.length) return
    mkdirSync(OUT_DIR, { recursive: true })
    const stamp = startedAt.toISOString().replace(/[:.]/g, '-')
    const jsonPath = join(OUT_DIR, `chat-benchmark-${stamp}.json`)
    const mdPath = join(OUT_DIR, `chat-benchmark-${stamp}.md`)
    const markdown = renderMarkdown(results, startedAt)
    writeFileSync(
      jsonPath,
      JSON.stringify({ startedAt, provider: PROVIDER, model: MODEL, city: CITY, rounds: ROUNDS, summary: summarize(results), results }, null, 2),
    )
    writeFileSync(mdPath, markdown)
    writeFileSync(join(OUT_DIR, 'chat-benchmark-latest.md'), markdown)
    console.log(`\n${markdown}\n\nwrote ${jsonPath}\nwrote ${mdPath}`)
    ;(state.db as unknown as NodeTreeDatabase).close()
  })

  it.each(cases.map((testCase) => [testCase.id, testCase] as const))(
    '%s',
    async (_id, testCase) => {
      const caseRounds: RoundResult[] = []
      for (let round = 1; round <= ROUNDS; round += 1) {
        const result = budgetExhausted ? notRun(testCase, round) : await runRound(testCase, round)
        caseRounds.push(result)
        results.push(result)
        if (result.verdict === 'not_run') continue
        const tools = result.toolCalls.map((call) => `${call.name}${call.isError ? '!' : ''}`).join(',') || '-'
        console.log(
          `[${testCase.id} ${round}/${ROUNDS}] ${result.verdict.padEnd(15)} ${seconds(result.durationMs).padStart(6)} llm=${result.llmCalls} tools=${tools}`,
        )
        for (const error of result.errors) console.log(`    ! ${error.replace(/\s+/g, ' ').slice(0, 300)}`)
        if (result.loopError) console.log(`    ! loop: ${result.loopError.slice(0, 300)}`)
      }
      // The harness has to have produced a verdict for every round; whether
      // the verdicts are clean is the report's question unless asked to gate.
      expect(caseRounds).toHaveLength(ROUNDS)
      if (FAIL_ON_ERRORS) {
        const failed = caseRounds.filter((round) => round.verdict !== 'clean')
        expect(failed, `${failed.length}/${ROUNDS} rounds of "${testCase.prompt}" were not clean`).toHaveLength(0)
      }
    },
    ROUNDS * ROUND_TIMEOUT_MS + 60_000,
  )
})
