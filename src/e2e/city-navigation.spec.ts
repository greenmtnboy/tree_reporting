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
 * Selects the city from the dropdown and waits until the map container's
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

  const citySelect = page.getByLabel('Select city')
  await citySelect.selectOption(cityCode)

  // Wait for the dropdown value to reflect the selected city.
  await expect(citySelect).toHaveValue(cityCode, { timeout: timeoutMs })

  // Wait for tiles to finish loading for this city.
  // data-trees-loaded-for is set by the sourcedata handler once per city load.
  await page.waitForFunction(
    (code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code,
    cityCode,
    { timeout: timeoutMs },
  )

  // The chat input must be enabled after city data loads.  A past bug caused
  // the input to stay disabled when switching cities mid-animation.
  const chatInput = page.locator('.chat-input-area input')
  if (await chatInput.count() > 0) {
    await expect(chatInput).toBeEnabled({ timeout: 15_000 })
  }
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
    await page.goto('/#/?city=USSFO')
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

  test('switching city during initial animation unlocks the chat', async ({ page }) => {
    test.setTimeout(180_000)
    // Wait just long enough for the initial city to start loading (but not finish
    // the intro animation), then switch to a different city.
    const citySelect = page.getByLabel('Select city')
    await expect(citySelect).toBeVisible({ timeout: 30_000 })
    // Switch immediately — the initial city may still be in the intro animation.
    await assertCitySwitch(page, 'Burlington', SWITCH_TIMEOUT)
    // assertCitySwitch already checks that the chat input is enabled.
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
    await page.goto('/#/?city=USSFO')
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

  test('switching city during initial load unlocks the chat', async ({ page }) => {
    test.setTimeout(120_000)
    const citySelect = page.getByLabel('Select city')
    await expect(citySelect).toBeVisible({ timeout: 30_000 })
    await assertCitySwitch(page, 'Burlington', SWITCH_TIMEOUT)
  })
})
