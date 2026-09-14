import { test, expect, type Page, type Route } from '@playwright/test'

/*
  The chat agent loop with a scripted model, and the tool inspector on top.

  The demo provider is an OpenRouter connection whose key comes from a token
  service, so three endpoints are mocked: the token mint, the model list, and
  chat completions. Everything else is real: DuckDB-wasm in the browser, the
  Trilogy resolver compiling each query, and the library's tool loop running
  the scripted calls.

  The inspector exists for one reason: a production loop where the model
  retried a failing query over and over, and nothing on screen said why. So
  the script here is exactly that shape -- a bad query, then a good one -- and
  the assertions are on what each pill reveals: the input the model sent and
  the full result text it was given back.
*/

const CITY = 'USSFO'

interface ToolCall {
  name: string
  input: Record<string, unknown>
}

function completion(toolCall: ToolCall, index: number) {
  return {
    id: `mock-${index}`,
    model: 'mock-model',
    choices: [
      {
        index: 0,
        finish_reason: 'tool_calls',
        message: {
          role: 'assistant',
          content: '',
          tool_calls: [
            {
              id: `call_${index}`,
              type: 'function',
              function: { name: toolCall.name, arguments: JSON.stringify(toolCall.input) },
            },
          ],
        },
      },
    ],
    usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
  }
}

/** Install the provider mocks. Returns the tool results each request carried, in order. */
async function scriptModel(page: Page, turns: ToolCall[]): Promise<string[][]> {
  const requests: string[][] = []
  await page.route('https://open-router-token-service.fly.dev/**', (route: Route) =>
    route.fulfill({ json: { api_key: 'sk-or-test' } }),
  )
  await page.route('https://openrouter.ai/api/v1/models', (route: Route) =>
    route.fulfill({ json: { data: [{ id: 'google/gemini-3-flash-preview', name: 'Mock Gemini' }] } }),
  )
  await page.route('https://openrouter.ai/api/v1/chat/completions', (route: Route) => {
    const body = route.request().postDataJSON() as { messages: { role: string; content: unknown }[] }
    requests.push(body.messages.filter((m) => m.role === 'tool').map((m) => String(m.content)))
    const index = requests.length - 1
    const turn = turns[index]
    if (!turn) {
      return route.fulfill({ status: 500, json: { error: { message: `no scripted turn ${index}` } } })
    }
    return route.fulfill({ json: completion(turn, index) })
  })
  return requests
}

async function openMapAndConnectDemo(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.addInitScript(() => {
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
  })
  await page.goto(`/#/?city=${CITY}`)
  await page.waitForFunction(
    (code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code,
    CITY,
    { timeout: 90_000 },
  )
  await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 90_000 })

  await page.locator('.chat-setup-select').selectOption('demo')
  await page.getByRole('button', { name: 'Start Chatting' }).click()
  const input = page.locator('.chat-input-area input')
  await expect(input).toBeEnabled({ timeout: 30_000 })
}

async function ask(page: Page, question: string): Promise<void> {
  const input = page.locator('.chat-input-area input')
  await input.fill(question)
  await page.keyboard.press('Enter')
}

test.describe('Chat tool inspector', () => {
  // City load, then DuckDB-wasm plus a resolver round trip per query.
  test.setTimeout(240_000)

  test('a failed call opens to its input and the error the model was sent', async ({ page }) => {
    const requests = await scriptModel(page, [
      { name: 'run_query', input: { query: 'select no_such_concept;' } },
      // Filtered to the city on purpose: an unfiltered query resolves to the
      // all-cities rollup, and the browser would download 300MB to answer it
      // (see AGENTS.md, "What the chat resolves against").
      // bloom_months is a LIST column: the worker must hand it over as a plain
      // array, not the Arrow vector's internals (see workers/normalizeValue.ts).
      { name: 'run_query', input: { query: `select species, bloom_months, count(tree_id) as n where city = '${CITY}' order by n desc limit 3;` } },
      { name: 'return_to_user', input: { message: 'Top three species, as requested.' } },
    ])
    await openMapAndConnectDemo(page)
    await ask(page, 'What are the top species?')

    await expect(page.locator('.chat-msg--assistant').last()).toContainText('Top three species', {
      timeout: 120_000,
    })
    await expect(page.locator('.chat-loading')).toHaveCount(0)

    // The model was told about the failure before it retried.
    expect(requests).toHaveLength(3)
    expect(requests[1][0]).toMatch(/no_such_concept|failed|error/i)

    // Two run_query pills: the first failed, the second did not.
    const pills = page.getByTestId('chat-tool-pill')
    await expect(pills).toHaveText([/run_query$/, /^run_query$/])
    const failed = page.locator('.chat-tool-pill--error')
    await expect(failed).toHaveCount(1)
    await expect(failed.locator('.chat-tool-pill-icon')).toHaveCount(1)

    // The failed pill: input as sent, result as the model read it.
    await failed.click()
    const inspector = page.getByTestId('tool-inspector')
    await expect(inspector).toBeVisible()
    await expect(inspector.locator('.tool-inspector-status')).toHaveText('failed')
    await expect(inspector.locator('.tool-inspector-call-name')).toHaveText('run_query')
    await expect(inspector.locator('.tool-inspector-pre').nth(0)).toContainText('no_such_concept')
    await expect(inspector.locator('.tool-inspector-section-label').nth(1)).toHaveText('Result sent to the model')
    await expect(inspector.locator('.tool-inspector-pre').nth(1)).toContainText(/no_such_concept|failed|error/i)

    await page.keyboard.press('Escape')
    await expect(inspector).toHaveCount(0)

    // The successful pill shows the rows the model saw.
    await pills.nth(1).click()
    await expect(inspector).toBeVisible()
    await expect(inspector.locator('.tool-inspector-status')).toHaveText('ok')
    await expect(inspector.locator('.tool-inspector-pre').nth(1)).toContainText('species')
    const resultText = await inspector.locator('.tool-inspector-pre').nth(1).innerText()
    expect(resultText).toMatch(/"bloom_months":(\[[\d,]*\]|null)/)
    expect(resultText).not.toContain('_offsets')

    // Clicking the backdrop (the overlay itself, away from the dialog) closes it.
    await inspector.click({ position: { x: 5, y: 5 } })
    await expect(inspector).toHaveCount(0)
  })
})
