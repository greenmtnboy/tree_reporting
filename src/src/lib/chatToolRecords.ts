import type { ChatMessage as LibChatMessage } from '@trilogy-data/trilogy-studio-components/llm'
import type { ChatMessage, ToolCallRecord } from '../types'

/**
 * The library records a tool call's outcome in two places. `executedToolCalls`
 * on the assistant message carries `success`, `error` and a short `message`
 * for the UI. The full result text the model was sent -- query rows, the error
 * with its context -- only exists on the hidden user message that follows, as
 * `toolResults`. The UI never shows hidden messages, so the text is joined
 * back onto the call record by id when that message arrives.
 */

const HIDDEN_TOOLS = new Set(['return_to_user', 'send_user_message'])

/** Convert a lib message's executedToolCalls to the app's ToolCallRecord[] for UI display. */
export function toToolCallRecords(
  executedToolCalls?: LibChatMessage['executedToolCalls'],
): ToolCallRecord[] | undefined {
  const display = executedToolCalls?.filter((tc) => !HIDDEN_TOOLS.has(tc.name))
  if (!display?.length) return undefined
  return display.map((tc) => ({
    id: tc.id,
    name: tc.name,
    input: tc.input as Record<string, unknown>,
    result: tc.result?.message || tc.result?.error || '',
    isError: !(tc.result?.success ?? true),
  }))
}

/**
 * Attach each result's text to the call record with that id, searching from
 * the newest message back since the call is always the one just made.
 * Returns how many records were updated.
 */
export function attachToolOutputs(
  messages: ChatMessage[],
  toolResults: LibChatMessage['toolResults'],
): number {
  if (!toolResults?.length) return 0
  let attached = 0
  for (const result of toolResults) {
    if (!result.toolCallId) continue
    for (let i = messages.length - 1; i >= 0; i--) {
      const record = messages[i].toolCalls?.find((tc) => tc.id === result.toolCallId)
      if (record) {
        record.output = result.result
        attached += 1
        break
      }
    }
  }
  return attached
}
