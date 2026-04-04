import type { TreeForm } from './types'

export const CATEGORY_COLORS: Record<TreeForm, string> = {
  broadleaf:   '#4CAF50',
  conifer:     '#1B5E20',
  palm:        '#F9A825',
  columnar:    '#1565C0',
  spreading:   '#66BB6A',
  weeping:     '#26A69A',
  ornamental:  '#EC407A',
  multi_trunk: '#8D6E63',
  default:     '#81C784',
}

/** SQL CASE expression mapping lower(tree_form) to a hex color literal. */
export function treeFormColorSql(columnExpr: string, alias: string): string {
  const whenClauses = (Object.entries(CATEGORY_COLORS) as [TreeForm, string][])
    .map(([form, hex]) => `when '${form}' then '${hex}'`)
    .join('\n')
  return `case lower(${columnExpr})\n${whenClauses}\nelse '${CATEGORY_COLORS.default}'\nend::string::hex as ${alias}`
}
