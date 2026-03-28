import { describe, expect, it } from 'vitest'
import { runToolLoop } from '@trilogy-data/trilogy-studio-components/llm'
import {
  PROVIDER_DEFAULT_MODELS,
  resolveAppScreen,
  toolsForScreen,
} from '../composables/chatToolConfig'
import { createSafeToolExecutor } from '../composables/chatToolExecution'

describe('chat loop regression coverage', () => {
  it('keeps Anthropic on Sonnet by default', () => {
    expect(PROVIDER_DEFAULT_MODELS.anthropic).toBe('claude-sonnet-4-6')
  })

  it('resolves summary screen from path even if route name is unset', () => {
    expect(resolveAppScreen({ path: '/summary', name: undefined })).toBe('summary')
  })

  it('uses summary-only tools on the analytics page', () => {
    const toolNames = toolsForScreen('summary').map((tool) => tool.name)
    expect(toolNames).toContain('run_query')
    expect(toolNames).toContain('set_summary_filters')
    expect(toolNames).toContain('inspect_summary_dashboard')
    expect(toolNames).not.toContain('publish_results')
    expect(toolNames).not.toContain('navigate')
    expect(toolNames).not.toContain('lookup_landmark')
  })

  it('calls the LLM again after a thrown tool error and includes the error as a tool result', async () => {
    const persistedMessages: any[] = []
    const seenHistories: any[][] = []
    let completionCalls = 0

    const result = await runToolLoop(
      'Show me Boston trees',
      'test-connection',
      {
        async generateCompletion(_connectionName, _options, messages) {
          seenHistories.push(messages.map((message) => ({ ...message })))

          if (completionCalls === 0) {
            completionCalls += 1
            return {
              text: 'Running a query.',
              usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
              toolCalls: [
                {
                  id: 'tool_fail_1',
                  name: 'run_query',
                  input: { query: 'SELECT tree_id WHERE local.city = \'USMA-BOS\'' },
                },
              ],
            }
          }

          if (completionCalls === 1) {
            const toolResultsMessage = messages.find(
              (message) =>
                message.role === 'user' &&
                Array.isArray(message.toolResults) &&
                message.toolResults.some((toolResult: any) => toolResult.toolName === 'run_query'),
            ) as { toolResults: Array<{ result: string }> } | undefined

            expect(toolResultsMessage).toBeDefined()
            expect(toolResultsMessage?.toolResults[0].result).toContain('Tool "run_query" failed:')
            expect(toolResultsMessage?.toolResults[0].result).toContain('USMA-BOS')

            completionCalls += 1
            return {
              text: 'Done.',
              usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
              toolCalls: [
                {
                  id: 'tool_fail_2',
                  name: 'return_to_user',
                  input: {
                    message: 'That query failed because the city code is invalid.',
                  },
                },
              ],
            }
          }

          throw new Error('LLM should only be called twice in this scenario.')
        },
      },
      {
        addMessage(message) {
          persistedMessages.push(message)
        },
        addArtifact() {},
        getMessages() {
          return persistedMessages
        },
      },
      {
        getToolExecutor: () => ({
          executeToolCall: async (name) => {
            if (name === 'run_query') {
              throw new Error(
                "Value 'USMA-BOS' is not a valid member of enum enum<'USSFO', 'USNYC', 'USBOS', 'FRPAR', 'USBTV'> for 'local.city'",
              )
            }

            if (name === 'return_to_user') {
              return {
                success: true,
                message: 'That query failed because the city code is invalid.',
                terminatesLoop: true,
              }
            }

            return {
              success: false,
              error: `Unexpected tool ${name}`,
            }
          },
        }),
      },
      {
        setActiveToolName() {},
        checkAborted() {
          return false
        },
      },
      {
        tools: toolsForScreen('summary'),
        buildSystemPrompt: () => 'Test summary prompt',
        maxIterations: 4,
      },
    )

    expect(result.terminated).toBe(true)
    expect(result.finalMessage).toBe('That query failed because the city code is invalid.')
    expect(completionCalls).toBe(2)
    expect(seenHistories).toHaveLength(2)
  })

  it('calls the LLM again after a tool result and sends toolResults in history', async () => {
    const persistedMessages: any[] = []
    const seenTools: string[][] = []
    const seenHistories: any[][] = []
    let completionCalls = 0

    const result = await runToolLoop(
      'Filter the analytics to native trees',
      'test-connection',
      {
        async generateCompletion(_connectionName, options, messages) {
          seenTools.push((options.tools ?? []).map((tool) => tool.name))
          seenHistories.push(messages.map((msg) => ({ ...msg })))

          if (completionCalls === 0) {
            completionCalls += 1
            return {
              text: 'Updating filters.',
              usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
              toolCalls: [
                {
                  id: 'tool_1',
                  name: 'set_summary_filters',
                  input: { operation: 'clear' },
                },
              ],
            }
          }

          if (completionCalls === 1) {
            completionCalls += 1
            return {
              text: 'Done.',
              usage: { promptTokens: 1, completionTokens: 1, totalTokens: 2 },
              toolCalls: [
                {
                  id: 'tool_2',
                  name: 'return_to_user',
                  input: { message: 'Cleared the analytics filters.' },
                },
              ],
            }
          }

          throw new Error('LLM should only be called twice in this scenario.')
        },
      },
      {
        addMessage(message) {
          persistedMessages.push(message)
        },
        addArtifact() {},
        getMessages() {
          return persistedMessages
        },
      },
      {
        getToolExecutor: () => ({
          executeToolCall: createSafeToolExecutor(async (name, input) => {
            if (name === 'set_summary_filters') {
              return {
                success: true,
                message: `Updated analytics filters with ${input.operation}.`,
              }
            }
            if (name === 'return_to_user') {
              return {
                success: true,
                message: input.message as string,
                terminatesLoop: true,
              }
            }
            return {
              success: false,
              error: `Unexpected tool ${name}`,
            }
          }),
        }),
      },
      {
        setActiveToolName() {},
        checkAborted() {
          return false
        },
      },
      {
        tools: toolsForScreen('summary'),
        buildSystemPrompt: () => 'Test summary prompt',
        maxIterations: 4,
      },
    )

    expect(result.terminated).toBe(true)
    expect(result.finalMessage).toBe('Cleared the analytics filters.')
    expect(completionCalls).toBe(2)
    expect(seenTools).toHaveLength(2)
    expect(seenTools[0]).toEqual(seenTools[1])
    expect(seenTools[0]).toContain('set_summary_filters')
    expect(seenTools[0]).not.toContain('publish_results')

    const secondHistory = seenHistories[1]
    expect(
      secondHistory.some(
        (message) =>
          message.role === 'user' &&
          Array.isArray(message.toolResults) &&
          message.toolResults.some((result: any) => result.toolName === 'set_summary_filters'),
      ),
    ).toBe(true)
  })
})
