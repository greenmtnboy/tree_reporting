import { test, expect, type BrowserContext, type Page } from '@playwright/test'

declare global {
  interface Window {
    __treesLoadedForHistory?: string[]
  }
}

async function installStartupProbe(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
    window.__treesLoadedForHistory = []

    const originalSetAttribute = Element.prototype.setAttribute
    Element.prototype.setAttribute = function patchedSetAttribute(name: string, value: string) {
      if (
        name === 'data-trees-loaded-for'
        && this instanceof HTMLElement
        && this.classList.contains('tree-map')
      ) {
        window.__treesLoadedForHistory?.push(value)
      }
      return originalSetAttribute.call(this, name, value)
    }
  })
}

async function waitForInitialHydratedCity(page: Page, expectedCity: string) {
  await page.waitForFunction(
    (city) => window.__treesLoadedForHistory?.includes(city),
    expectedCity,
    { timeout: 90_000 },
  )

  await expect(page.getByTestId('city-select')).toHaveValue(expectedCity, { timeout: 30_000 })

  return page.evaluate(() => window.__treesLoadedForHistory ?? [])
}

async function setSharedLocation(context: BrowserContext, latitude: number, longitude: number) {
  await context.grantPermissions(['geolocation'])
  await context.setGeolocation({ latitude, longitude })
}

test.describe('Startup city resolution', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await installStartupProbe(page)
  })

  test('uses the route city immediately even when shared location points elsewhere', async ({ page, context }) => {
    test.setTimeout(120_000)
    await setSharedLocation(context, 42.3601, -71.0589)

    await page.goto('/#/?city=USSFO')

    const history = await waitForInitialHydratedCity(page, 'USSFO')

    expect(history[0]).toBe('USSFO')
    expect(history).not.toContain('USBOS')
    await expect(page).toHaveURL(/city=USSFO/)
  })

  test('waits for shared location when no route city is specified and hydrates that city first', async ({ page, context }) => {
    test.setTimeout(120_000)
    await setSharedLocation(context, 42.3601, -71.0589)

    await page.goto('/#/')

    const history = await waitForInitialHydratedCity(page, 'USBOS')

    expect(history[0]).toBe('USBOS')
    expect(history).not.toContain('USSFO')
    await expect(page).toHaveURL(/city=USBOS/)
  })

  test('falls back to the default city when there is no route city and no shared location', async ({ page, context }) => {
    test.setTimeout(120_000)
    await context.clearPermissions()

    await page.goto('/#/')

    const history = await waitForInitialHydratedCity(page, 'USSFO')

    expect(history[0]).toBe('USSFO')
    await expect(page).toHaveURL(/city=USSFO/)
  })
})