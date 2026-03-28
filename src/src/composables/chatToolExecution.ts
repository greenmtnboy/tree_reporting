export type SafeToolResult = {
  success: boolean
  message?: string
  error?: string
  terminatesLoop?: boolean
  awaitsUserInput?: boolean
  artifact?: any
}

function toJsonSafeValue(value: unknown): unknown {
  if (typeof value === 'bigint') {
    const asNumber = Number(value)
    return Number.isSafeInteger(asNumber) ? asNumber : value.toString()
  }

  if (value instanceof Date) {
    return value.toISOString()
  }

  if (
    value !== null &&
    value !== undefined &&
    (value as { isLuxonDateTime?: boolean }).isLuxonDateTime === true
  ) {
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

export function safeJsonStringify(value: unknown): string {
  return JSON.stringify(toJsonSafeValue(value))
}

export function toJsonSafeRows(rows: readonly Readonly<Record<string, unknown>>[]) {
  return rows.map((row) => toJsonSafeValue(row) as Record<string, unknown>)
}

export function describeToolError(error: unknown): string {
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

export function normalizeToolResult<T extends { message?: unknown; error?: unknown }>(
  result: T,
): T & SafeToolResult {
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
  } as T & SafeToolResult
}

export function createSafeToolExecutor(
  executeTool: (
    name: string,
    input: Record<string, any>,
  ) => Promise<{ success: boolean; message?: unknown; error?: unknown; terminatesLoop?: boolean; awaitsUserInput?: boolean; artifact?: any }>,
) {
  return async (name: string, input: Record<string, any>): Promise<SafeToolResult> => {
    try {
      return normalizeToolResult(await executeTool(name, input))
    } catch (error) {
      return {
        success: false,
        error: `Tool "${name}" failed: ${describeToolError(error)}`,
      }
    }
  }
}
