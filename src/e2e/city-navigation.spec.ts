import { test, expect, type Page } from '@playwright/test'

// City name → city code, matching cityConfig.json
const CITY_CODE: Record<string, string> = {
  'San Francisco': 'USSFO',
  'New York City': 'USNYC',
  'Boston':        'USBOS',
  'Paris':         'FRPAR',
  'Burlington':    'USBTV',
}

/**
 * Clicks the city button and waits until the map container's
 * `data-trees-loaded-for` attribute equals the destination city code.
 *
 * This attribute is set in TreeMap.vue's sourcedata handler the moment the
 * 'trees' source finishes loading tiles for the current city. In the broken
 * state (watcher exits early, no tiles generated) the attribute stays on the
 * previous city and this function times out.
 */
async function assertCitySwitch(page: Page, cityName: string, timeoutMs: number): Promise<void> {
  const cityCode = CITY_CODE[cityName]
  if (!cityCode) throw new Error(`Unknown city name "${cityName}" — add it to CITY_CODE`)

  await page.locator('.city-btn').filter({ hasText: cityName }).click()

  // Wait for city button to become active (setSelectedCity fired).
  await expect(
    page.locator('.city-btn').filter({ hasText: cityName }),
  ).toHaveClass(/active/, { timeout: timeoutMs })

  // Wait for tiles to finish loading for this city.
  // data-trees-loaded-for is set by the sourcedata handler once per city load.
  await page.waitForFunction(
    (code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code,
    cityCode,
    { timeout: timeoutMs },
  )
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
    await assertCitySwitch(page, 'San Francisco', SWITCH_TIMEOUT)
    await assertCitySwitch(page, 'Burlington', SWITCH_TIMEOUT)
  })

  test('switching cities twice renders trees for each destination', async ({ page }) => {
    test.setTimeout(240_000)
    await assertCitySwitch(page, 'San Francisco', SWITCH_TIMEOUT)
    await assertCitySwitch(page, 'Boston', SWITCH_TIMEOUT)
    await assertCitySwitch(page, 'Burlington', SWITCH_TIMEOUT)
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
    await assertCitySwitch(page, 'San Francisco', SWITCH_TIMEOUT)
    await assertCitySwitch(page, 'Burlington', SWITCH_TIMEOUT)
  })

  test('switching cities twice renders trees for each destination', async ({ page }) => {
    test.setTimeout(150_000)
    await assertCitySwitch(page, 'San Francisco', SWITCH_TIMEOUT)
    await assertCitySwitch(page, 'Boston', SWITCH_TIMEOUT)
    await assertCitySwitch(page, 'Burlington', SWITCH_TIMEOUT)
  })
})
