import { describe, expect, it } from 'vitest'
import { applySqlParameters } from '../composables/sqlParameters'

describe('applySqlParameters', () => {
  it('substitutes resolver-returned named parameters into SQL literals', () => {
    const sql = `
SELECT
  tree_id,
  :override_color AS override_color
FROM trees
WHERE city = :active_city
`

    expect(applySqlParameters(sql, {
      override_color: '#DAA520',
      active_city: 'USBOS',
    })).toContain(`'#DAA520' AS override_color`)
    expect(applySqlParameters(sql, {
      override_color: '#DAA520',
      active_city: 'USBOS',
    })).toContain(`WHERE city = 'USBOS'`)
  })

  it('does not mistake type casts for named parameters', () => {
    const sql = 'SELECT 1::INTEGER AS value, :label AS label'

    expect(applySqlParameters(sql, { label: 'ok' })).toBe(
      "SELECT 1::INTEGER AS value, 'ok' AS label",
    )
  })
})
