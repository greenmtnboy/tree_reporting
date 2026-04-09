export type SqlParameterMap = Record<string, unknown>

const NAMED_SQL_PARAMETER = /(?<!:):([a-zA-Z_]\w*)/g

function serializeSqlParameter(value: unknown): string {
  if (value == null) {
    return 'NULL'
  }
  if (typeof value === 'string') {
    return `'${value.replace(/'/g, "''")}'`
  }
  if (typeof value === 'number' || typeof value === 'bigint') {
    return String(value)
  }
  if (typeof value === 'boolean') {
    return value ? 'TRUE' : 'FALSE'
  }
  if (value instanceof Date) {
    return `'${value.toISOString().replace(/'/g, "''")}'`
  }
  if (Array.isArray(value)) {
    return `(${value.map((entry) => serializeSqlParameter(entry)).join(', ')})`
  }
  return `'${JSON.stringify(value).replace(/'/g, "''")}'`
}

export function applySqlParameters(
  sql: string,
  parameters?: SqlParameterMap | null,
): string {
  if (!parameters || Object.keys(parameters).length === 0) {
    return sql
  }

  return sql.replace(NAMED_SQL_PARAMETER, (token, name: string) => {
    if (!(name in parameters)) {
      return token
    }
    return serializeSqlParameter(parameters[name])
  })
}
