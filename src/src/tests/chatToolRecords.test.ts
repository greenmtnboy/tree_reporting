import { describe, expect, it } from 'vitest'
import { attachToolOutputs, toToolCallRecords } from '../lib/chatToolRecords'
import type { ChatMessage } from '../types'

describe('toToolCallRecords', () => {
  it('keeps the visible tools with their short outcome and error flag', () => {
    const records = toToolCallRecords([
      { id: 'a', name: 'run_query', input: { query: 'select 1' }, result: { success: true, message: '3 rows' } },
      { id: 'b', name: 'run_query', input: { query: 'select x' }, result: { success: false, error: 'boom' } },
      { id: 'c', name: 'return_to_user', input: { message: 'done' }, result: { success: true } },
    ])
    expect(records).toEqual([
      { id: 'a', name: 'run_query', input: { query: 'select 1' }, result: '3 rows', isError: false },
      { id: 'b', name: 'run_query', input: { query: 'select x' }, result: 'boom', isError: true },
    ])
  })

  it('returns undefined when nothing is left to show', () => {
    expect(toToolCallRecords(undefined)).toBeUndefined()
    expect(toToolCallRecords([{ id: 'c', name: 'return_to_user', input: {} }])).toBeUndefined()
  })
})

describe('attachToolOutputs', () => {
  it('joins the full result text onto the matching call by id, newest message first', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: 'hi' },
      {
        role: 'assistant',
        content: '',
        toolCalls: [{ id: 'a', name: 'run_query', input: {}, result: 'failed', isError: true }],
      },
      {
        role: 'assistant',
        content: '',
        toolCalls: [{ id: 'b', name: 'run_query', input: {}, result: '3 rows' }],
      },
    ]
    const attached = attachToolOutputs(messages, [
      { toolCallId: 'b', toolName: 'run_query', result: 'tree_id,species\n1,Quercus\n2,Acer\n3,Pinus' },
      { toolCallId: 'zzz', toolName: 'run_query', result: 'orphan' },
    ])
    expect(attached).toBe(1)
    expect(messages[2].toolCalls?.[0].output).toBe('tree_id,species\n1,Quercus\n2,Acer\n3,Pinus')
    expect(messages[1].toolCalls?.[0].output).toBeUndefined()
  })

  it('is a no-op without results', () => {
    const messages: ChatMessage[] = [{ role: 'assistant', content: '', toolCalls: [{ id: 'a', name: 'x', input: {}, result: '' }] }]
    expect(attachToolOutputs(messages, undefined)).toBe(0)
    expect(attachToolOutputs(messages, [])).toBe(0)
  })
})
