import { test, expect, type Page } from '@playwright/test'

// ── MapLibre helpers ──────────────────────────────────────────────────────────
// These run inside page.evaluate() so they cannot close over Node-side values.

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

// ── Shared test logic ─────────────────────────────────────────────────────────

interface NavTestOptions {
  /** Milliseconds to allow for a single city switch (animation + DuckDB load + tile gen). */
  switchTimeout: number
}

/**
 * Switches to `cityName` and asserts that:
 * 1. The city button becomes active (setSelectedCity fired).
 * 2. The loading overlay reappears (watcher ran, defaultQueryLoading set to true)
 *    — this is the regression canary: in the broken state the watcher exits early
 *    and the overlay never reappears.
 * 3. The loading overlay disappears (trees source loaded).
 * 4. querySourceFeatures returns >0 features (tiles contain real tree data).
 */
async function assertCitySwitch(page: Page, cityName: string, opts: NavTestOptions): Promise<void> {
  const { switchTimeout } = opts

  await page.locator('.city-btn').filter({ hasText: cityName }).click()

  await expect(
    page.locator('.city-btn').filter({ hasText: cityName }),
  ).toHaveClass(/active/, { timeout: switchTimeout })

  await expect(page.locator('.map-loading')).toBeVisible({ timeout: switchTimeout })
  await expect(page.locator('.map-loading')).toBeHidden({ timeout: switchTimeout })

  await waitForMapIdle(page)

  const treeCount = await getLoadedTreeCount(page)
  expect(treeCount, `Expected trees for ${cityName}`).toBeGreaterThan(0)
}

// ── Desktop ───────────────────────────────────────────────────────────────────

test.describe('City navigation — desktop', () => {
  // Globe swoop: ~7 s zoom-out + ~12 s arc = ~19 s animation alone.
  const SWITCH_TIMEOUT = 90_000

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.addInitScript(() => {
      localStorage.setItem('sf_trees_welcome_dismissed', '1')
    })
    await page.goto('/')
  })

  test('switching to a new city loads trees for that city', async ({ page }) => {
    test.setTimeout(120_000)
    await expect(page.locator('.map-loading')).toBeHidden({ timeout: 60_000 })
    await assertCitySwitch(page, 'Burlington', { switchTimeout: SWITCH_TIMEOUT })
  })

  test('switching cities twice renders trees for each destination', async ({ page }) => {
    test.setTimeout(240_000)
    await expect(page.locator('.map-loading')).toBeHidden({ timeout: 60_000 })
    await assertCitySwitch(page, 'Boston', { switchTimeout: SWITCH_TIMEOUT })
    await assertCitySwitch(page, 'Burlington', { switchTimeout: SWITCH_TIMEOUT })
  })
})

// ── Mobile ────────────────────────────────────────────────────────────────────

test.describe('City navigation — mobile', () => {
  // Mobile uses a plain 5 s flyTo instead of the globe swoop.
  const SWITCH_TIMEOUT = 60_000

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.addInitScript(() => {
      localStorage.setItem('sf_trees_welcome_dismissed', '1')
    })
    await page.goto('/')
  })

  test('switching to a new city loads trees for that city', async ({ page }) => {
    test.setTimeout(90_000)
    await expect(page.locator('.map-loading')).toBeHidden({ timeout: 30_000 })
    await assertCitySwitch(page, 'Burlington', { switchTimeout: SWITCH_TIMEOUT })
  })

  test('switching cities twice renders trees for each destination', async ({ page }) => {
    test.setTimeout(150_000)
    await expect(page.locator('.map-loading')).toBeHidden({ timeout: 30_000 })
    await assertCitySwitch(page, 'Boston', { switchTimeout: SWITCH_TIMEOUT })
    await assertCitySwitch(page, 'Burlington', { switchTimeout: SWITCH_TIMEOUT })
  })
})
