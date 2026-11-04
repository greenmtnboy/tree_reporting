import { test, expect, type Page } from '@playwright/test'

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

type ZoomRange = { min: number; current: number; steps: number }

/** Count the camera's zoom changes from the moment the map exists. */
async function recordZoomRange(page: Page): Promise<void> {
  await page.waitForFunction(() => (window as unknown as { __treeMap?: unknown }).__treeMap, null, { timeout: 60_000 })
  await page.evaluate(() => {
    const map = (window as unknown as { __treeMap: { getZoom(): number; on(type: string, fn: () => void): void } }).__treeMap
    const range = { min: map.getZoom(), current: map.getZoom(), steps: 0, last: map.getZoom() }
    ;(window as unknown as { __zoomRange: typeof range }).__zoomRange = range
    map.on('zoom', () => {
      const z = map.getZoom()
      if (Math.abs(z - range.last) > 0.01) range.steps += 1
      range.last = z
      range.current = z
      range.min = Math.min(range.min, z)
    })
  })
}

async function zoomRange(page: Page): Promise<ZoomRange> {
  return page.evaluate(() => {
    const { min, current, steps } = (window as unknown as { __zoomRange: ZoomRange }).__zoomRange
    return { min, current, steps }
  })
}

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
      await recordZoomRange(page)

      // The card waits for the city load, then the camera jumps to the tree:
      // no intro fly-out on desktop and no flight on either layout.
      const card = page.locator('.tree-card')
      await expect(card).toBeVisible({ timeout: 120_000 })
      await expect(card).toContainText(TREE_ID)
      await expect(card).toContainText(TREE_NAME)
      expect(page.url()).toContain(`tree=${TREE_ID}`)
      const zoom = await zoomRange(page)
      expect(zoom.current).toBeCloseTo(17, 1)
      expect(zoom.min, 'the camera never zoomed out on its way to the tree').toBeGreaterThanOrEqual(13)
      expect(zoom.steps, 'one jump, not an animated flight').toBeLessThan(5)

      await card.locator('.tree-card-close').click()
      await expect(card).toHaveCount(0)
      await expect.poll(() => page.url()).not.toContain('tree=')
      // The city survives the close.
      expect(page.url()).toContain(`city=${CITY}`)
      await expect(page.locator('.tree-map')).toBeVisible()
      await expect(page.locator('.map-entry')).toHaveCount(0)
    })

    test('drops an unknown ?tree= instead of leaving it in the URL', async ({ page }) => {
      test.setTimeout(180_000)
      await page.goto(`/#/?city=${CITY}&tree=sf-does-not-exist`)
      await expect.poll(() => page.url(), { timeout: 120_000 }).not.toContain('tree=')
      await expect(page.locator('.tree-card')).toHaveCount(0)
      await expect(page.locator('.tree-map')).toBeVisible()
      await expect(page.locator('.map-entry')).toHaveCount(0)
    })
  })
}
