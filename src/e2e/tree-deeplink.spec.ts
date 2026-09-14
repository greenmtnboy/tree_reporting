import { test, expect } from '@playwright/test'

/**
 * `#/?city=USSFO&tree=sf-1` must open that tree's card without a click, and
 * the address bar must track the selection: opening a tree writes `?tree=`,
 * closing the card removes it. This is the link people share (and the one
 * used to reproduce a bad row from the map), so it is asserted end to end
 * against the real parquet.
 */

const CITY = 'USSFO'
// The lowest tree_id in San Francisco's inventory: a Monterey Pine. A stable
// choice as long as the SF ingest keeps its source ids.
const TREE_ID = 'sf-1'
// Matched case-insensitively: the card shows the enrichment common name.
const TREE_NAME = /monterey pine/i

for (const [label, viewport] of [
  ['desktop', { width: 1280, height: 800 }],
  ['mobile', { width: 390, height: 844 }],
] as const) {
  test.describe(`Tree deep link — ${label}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await page.addInitScript(() => {
        localStorage.setItem('sf_trees_welcome_dismissed', '1')
      })
    })

    test('opens the linked tree and clears ?tree= when the card closes', async ({ page }) => {
      test.setTimeout(180_000)
      await page.goto(`/#/?city=${CITY}&tree=${TREE_ID}`)

      // The card waits for the city load and, on desktop, the intro animation,
      // then a 1.5 s flight to the tree.
      const card = page.locator('.tree-card')
      await expect(card).toBeVisible({ timeout: 120_000 })
      await expect(card).toContainText(TREE_ID)
      await expect(card).toContainText(TREE_NAME)
      expect(page.url()).toContain(`tree=${TREE_ID}`)

      await card.locator('.tree-card-close').click()
      await expect(card).toHaveCount(0)
      await expect.poll(() => page.url()).not.toContain('tree=')
      // The city survives the close.
      expect(page.url()).toContain(`city=${CITY}`)
    })

    test('drops an unknown ?tree= instead of leaving it in the URL', async ({ page }) => {
      test.setTimeout(180_000)
      await page.goto(`/#/?city=${CITY}&tree=sf-does-not-exist`)
      await expect.poll(() => page.url(), { timeout: 120_000 }).not.toContain('tree=')
      await expect(page.locator('.tree-card')).toHaveCount(0)
    })
  })
}
