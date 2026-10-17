import { test, expect } from '@playwright/test'

for (const [name, viewport] of [
  ['desktop', { width: 1280, height: 800 }],
  ['mobile', { width: 390, height: 844 }],
] as const) {
  test(`species URL scopes the map and responsive legend on ${name}`, async ({ page }) => {
    test.setTimeout(180_000)
    await page.setViewportSize(viewport)
    await page.addInitScript(() => localStorage.setItem('sf_trees_welcome_dismissed', '1'))
    // Omitting mode exercises the generic filter link's mobile entry bypass too.
    await page.goto('/#/?city=USSFO&filter.species=Platanus+x+hispanica')
    await expect(page.locator('.map-entry')).toHaveCount(0)
    const legend = page.getByRole('region', { name: 'Map legend' })
    await expect(legend).toContainText('Platanus x hispanica')
    await expect(page.getByLabel('Color trees by')).toHaveValue('dbh')
    await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 120_000 })
    await expect(page.locator('.map-error')).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => {
      const map = window.__treeMap
      return map?.getSource('trees') ? map.querySourceFeatures('trees', { sourceLayer: 'trees' }).length : 0
    }), { timeout: 60_000 }).toBeGreaterThan(0)
    await expect(legend).toContainText('Unknown trunk diameter')
    const box = (await legend.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width)
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height)
    await page.screenshot({ path: `test-results/species-filter-${name}.png` })

    await page.getByLabel('Color trees by').selectOption('tree_form')
    await expect(page).toHaveURL(/colorBy=tree_form/)
    await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 60_000 })
    await expect(legend).not.toContainText('Unknown trunk diameter')
    await page.getByRole('button', { name: 'Show all trees' }).click()
    await expect(page).not.toHaveURL(/filter.species/)
    await expect(legend).not.toContainText('Filtered trees')
    await page.goBack()
    await expect(legend).toContainText('Platanus x hispanica')
    await expect(page.getByLabel('Color trees by')).toHaveValue('tree_form')
    await page.goBack()
    await expect(page.getByLabel('Color trees by')).toHaveValue('dbh')
    await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 60_000 })

    // A nonexistent species must remove every tree, including low-zoom aggregates.
    await page.evaluate(() => {
      window.location.hash = '/?city=USSFO&mode=explore&filter.species=No+such+species'
    })
    await expect(legend).toContainText('No such species')
    await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 60_000 })
    await expect.poll(() => page.evaluate(() => {
      const map = window.__treeMap
      if (!map || !map.getSource('trees') || !map.isSourceLoaded('trees')) return -1
      return map.querySourceFeatures('trees', { sourceLayer: 'trees' }).length
    }), { timeout: 60_000 }).toBe(0)
    await expect(page.getByRole('status').filter({ hasText: 'No matching trees' })).toBeVisible()
  })
}
