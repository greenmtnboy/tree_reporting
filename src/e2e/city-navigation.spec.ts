import { test, expect, type Page } from '@playwright/test'

/** Wait until the MapLibre map fires 'idle' (no pending tile loads or animations). */
async function waitForMapIdle(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    const canvas = document.querySelector('.tree-map canvas') as HTMLCanvasElement
    if (!canvas) throw new Error('Map canvas not found')
    const map = (canvas as any).__maplibre_map ??
      Object.values(canvas).find((v: any) => v && typeof v.getCenter === 'function')
    if (!map) throw new Error('MapLibre instance not found on canvas — internal property may have changed')
    if (map.loaded() && !map.isMoving()) { resolve(); return }
    map.once('idle', resolve)
  }))
}

/** Returns the number of features currently loaded in the 'trees' source. */
async function getLoadedTreeCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const canvas = document.querySelector('.tree-map canvas') as HTMLCanvasElement
    if (!canvas) throw new Error('Map canvas not found')
    const map = (canvas as any).__maplibre_map ??
      Object.values(canvas).find((v: any) => v && typeof v.getCenter === 'function')
    if (!map) throw new Error('MapLibre instance not found on canvas — internal property may have changed')
    return map.querySourceFeatures('trees', { sourceLayer: 'trees' }).length
  })
}

test.describe('City navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.addInitScript(() => {
      localStorage.setItem('sf_trees_welcome_dismissed', '1')
    })
    await page.goto('/')
  })

  test('switching to a new city loads trees for that city', async ({ page }) => {
    // The globe swoop takes ~19 s; allow extra time for DuckDB parquet download and tile generation.
    test.setTimeout(120_000)

    // ── Wait for initial SF load ──────────────────────────────────────────────
    // The loading overlay appears on mount and hides once the first tiles load.
    await expect(page.locator('.map-loading')).toBeHidden({ timeout: 60_000 })

    // ── Switch to Burlington ──────────────────────────────────────────────────
    await page.locator('.city-btn').filter({ hasText: 'Burlington' }).click()

    // The Burlington button should get the 'active' class once setSelectedCity fires.
    await expect(
      page.locator('.city-btn').filter({ hasText: 'Burlington' }),
    ).toHaveClass(/active/, { timeout: 60_000 })

    // The watcher must set defaultQueryLoading=true, which makes the loading
    // overlay reappear.  In the broken state the watcher exits early and this
    // never happens — so this assertion is the regression canary.
    await expect(page.locator('.map-loading')).toBeVisible({ timeout: 60_000 })

    // Then it must disappear once the trees source finishes loading.
    await expect(page.locator('.map-loading')).toBeHidden({ timeout: 60_000 })

    // ── Verify trees are actually in the source ───────────────────────────────
    await waitForMapIdle(page)

    const treeCount = await getLoadedTreeCount(page)
    expect(treeCount).toBeGreaterThan(0)
  })

  test('switching cities twice renders trees for each destination', async ({ page }) => {
    // Two globe swoops back-to-back: budget ~3 minutes.
    test.setTimeout(180_000)

    // Wait for initial SF load.
    await expect(page.locator('.map-loading')).toBeHidden({ timeout: 60_000 })

    for (const city of ['Boston', 'Burlington']) {
      await page.locator('.city-btn').filter({ hasText: city }).click()

      await expect(
        page.locator('.city-btn').filter({ hasText: city }),
      ).toHaveClass(/active/, { timeout: 60_000 })

      // Loading overlay must reappear (watcher ran) then disappear (tiles loaded).
      await expect(page.locator('.map-loading')).toBeVisible({ timeout: 60_000 })
      await expect(page.locator('.map-loading')).toBeHidden({ timeout: 60_000 })

      await waitForMapIdle(page)

      const treeCount = await getLoadedTreeCount(page)
      expect(treeCount, `Expected trees for ${city}`).toBeGreaterThan(0)
    }
  })
})
