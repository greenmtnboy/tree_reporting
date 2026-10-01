import { test, expect } from '@playwright/test'

test.describe('Desktop map entry', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  for (const path of ['/#/', '/#/?mode=unknown&city=unknown', '/#/?city=USBOS&mode=nearby']) {
    test(`opens full city exploration directly at ${path}`, async ({ page }) => {
      await page.addInitScript(() => {
        navigator.geolocation.getCurrentPosition = () => {
          document.documentElement.dataset.unexpectedLocationRequest = 'true'
        }
      })
      await page.goto(path)
      await expect(page.locator('.tree-map canvas')).toBeAttached({ timeout: 15_000 })
      await expect(page.locator('.tree-map')).not.toHaveClass(/tree-map--mobile/)
      await expect(page.locator('.map-entry')).toHaveCount(0)
      await expect(page.locator('.experience-switch')).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Explore City', exact: true })).toHaveCount(0)
      await expect(page.locator('html')).not.toHaveAttribute('data-unexpected-location-request', 'true')
    })
  }

  test('a mobile shared tree link opens its destination on desktop', async ({ page }) => {
    test.setTimeout(120_000)
    await page.goto('/#/?city=USSFO&mode=nearby&tree=sf-1')
    await expect(page.locator('.tree-card')).toContainText('sf-1', { timeout: 90_000 })
    await expect(page.locator('.map-entry')).toHaveCount(0)
    await expect(page.locator('.tree-map')).not.toHaveClass(/tree-map--mobile/)
  })
})

for (const [name, viewport, colorScheme] of [
  ['small phone', { width: 320, height: 568 }, 'light'],
  ['landscape phone', { width: 667, height: 375 }, 'light'],
  ['dark phone', { width: 390, height: 844 }, 'dark'],
] as const) {
  test.describe(`Map entry — ${name}`, () => {
    test.use({ viewport, colorScheme })
    test('both choices are visible and keyboard accessible', async ({ page }, testInfo) => {
      await page.goto('/#/')
      const nearMe = page.getByRole('button', { name: 'Near Me', exact: true })
      const explore = page.getByRole('button', { name: 'Explore City', exact: true })
      await expect(nearMe).toBeVisible()
      for (const button of [nearMe, explore]) {
        const box = (await button.boundingBox())!
        expect(box.y).toBeGreaterThanOrEqual(0)
        expect(box.y + box.height).toBeLessThan(viewport.height - 66)
        expect(box.height).toBeGreaterThanOrEqual(44)
      }
      await page.keyboard.press('Tab')
      await expect(nearMe).toBeFocused()
      await page.keyboard.press('Tab')
      await expect(explore).toBeFocused()
      await expect(page.locator('.tree-map')).toHaveCount(0)
      await page.screenshot({ path: testInfo.outputPath('entry.png') })
    })
  })
}

for (const [label, viewport] of [
  ['mobile', { width: 390, height: 844 }],
] as const) {
  test.describe(`Map experience — ${label}`, () => {
    test.use({ viewport })

    test('a city hint keeps the chooser open and exploration uses that city', async ({ page }) => {
      await page.goto('/#/?city=USBOS')
      await expect(page.locator('.map-entry')).toBeVisible()
      await expect(page.locator('.tree-map')).toHaveCount(0)
      await expect(page.locator('.experience-switch')).toHaveCount(0)
      await page.getByRole('button', { name: 'Explore City', exact: true }).click()
      await expect(page).toHaveURL(/city=USBOS&mode=explore/)
      await expect(page.getByTestId('city-select')).toHaveValue('USBOS')
      await expect(page.locator('.tree-map')).toBeVisible()
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Explore City', exact: true })).toHaveCount(0)
      await expect(page.getByRole('button', { name: /Find Me/ })).toBeVisible()
      await page.goBack()
      await expect(page.locator('.map-entry')).toBeVisible()
    })

    test('switching a default desktop visit to mobile does not choose exploration', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 })
      await page.goto('/#/')
      await expect(page).toHaveURL(/city=/)
      await page.setViewportSize(viewport)
      await expect(page.locator('.map-entry')).toBeVisible()
      await expect(page.locator('.tree-map')).toHaveCount(0)
      await expect(page.locator('.experience-switch')).toHaveCount(0)
    })

    test('entry offers two paths without starting the map or requesting location', async ({ page }, testInfo) => {
      const parquets: string[] = []
      page.on('request', request => { if (request.url().includes('.parquet')) parquets.push(request.url()) })
      await page.addInitScript(() => {
        navigator.geolocation.getCurrentPosition = () => { throw new Error('Entry must not request location') }
      })
      await page.goto('/#/')
      await expect(page.getByRole('heading', { name: 'Discover the trees around you.' })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Explore City', exact: true })).toBeVisible()
      await expect(page.locator('.tree-map')).toHaveCount(0)
      await expect(page.locator('.experience-switch')).toHaveCount(0)
      if (label === 'mobile') {
        await expect(page.getByTestId('mobile-action-chat')).toHaveCount(0)
        await expect(page.getByTestId('mobile-action-landmarks')).toHaveCount(0)
        await expect(page.getByTestId('mobile-action-submit')).toHaveCount(0)
        const actions = await page.locator('.entry-actions').boundingBox()
        expect(actions).not.toBeNull()
        expect(Math.abs(actions!.x + actions!.width / 2 - viewport.width / 2)).toBeLessThan(2)
        expect(Math.abs(actions!.y + actions!.height / 2 - viewport.height / 2)).toBeLessThan(100)
      }
      expect(parquets).toEqual([])
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await testInfo.attach('entry', { body: await page.screenshot(), contentType: 'image/png' })
      await page.getByRole('button', { name: 'Explore City', exact: true }).click()
      await expect(page).toHaveURL(/mode=explore/)
      await expect(page.locator('.tree-map')).toBeVisible()
      if (label === 'mobile') await expect(page.getByTestId('mobile-action-landmarks')).toBeVisible()
      await page.goBack()
      await expect(page.locator('.map-entry')).toBeVisible()
      await expect(page.locator('.tree-map')).toHaveCount(0)
    })

    test('unknown experience and city parameters keep the choice open', async ({ page }) => {
      await page.goto('/#/?mode=unknown&city=unknown')
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toBeVisible()
      await expect(page.locator('.tree-map')).toHaveCount(0)
    })

    test('a shared tree link overrides nearby mode without asking for location', async ({ page }) => {
      test.setTimeout(120_000)
      await page.addInitScript(() => {
        navigator.geolocation.getCurrentPosition = () => { throw new Error('Tree destination must not request location') }
      })
      await page.goto('/#/?city=USSFO&mode=nearby&tree=sf-1')
      await expect(page).toHaveURL(/mode=explore/)
      await expect(page.locator('.tree-card')).toContainText('sf-1', { timeout: 90_000 })
    })

    test('nearby resolves the city, survives reload and allows normal map navigation', async ({ page, context }, testInfo) => {
      test.setTimeout(180_000)
      await context.grantPermissions(['geolocation'])
      await context.setGeolocation({ latitude: 42.3601, longitude: -71.0589 })
      await page.goto('/#/?city=USSFO&mode=nearby')
      await expect(page).toHaveURL(/city=USBOS&mode=nearby/)
      await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USBOS', { timeout: 90_000 })
      const camera = await page.evaluate(() => {
        const map = (window as any).__treeMap
        return { center: map.getCenter(), zoom: map.getZoom() }
      })
      expect(camera.center.lat).toBeCloseTo(42.3601, 4)
      expect(camera.center.lng).toBeCloseTo(-71.0589, 4)
      expect(camera.zoom).toBe(17)
      await page.waitForFunction(() => (window as any).__treeMap.queryRenderedFeatures({ layers: ['trees-circle'] }).length > 0)
      const timing = await page.evaluate(() => performance.getEntriesByType('measure').filter(e => e.name.startsWith('trees:')).map(e => e.toJSON()))
      await testInfo.attach('nearby-load-timing', { body: JSON.stringify(timing, null, 2), contentType: 'application/json' })
      expect(timing.some(e => e.name === 'trees:nearby:load')).toBe(true)
      await page.reload()
      await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USBOS', { timeout: 90_000 })
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Explore City', exact: true })).toHaveCount(0)
      await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
      await expect.poll(() => page.evaluate(() => (window as any).__treeMap.getZoom())).toBeLessThan(17)
      await page.getByTestId('city-select').selectOption('USBOS')
      await expect(page).toHaveURL(/mode=explore/)
      await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USBOS', { timeout: 60_000 })
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toHaveCount(0)
      await expect(page.getByRole('button', { name: /Find Me/ })).toBeVisible()
    })

    test('denied location offers retry and exploration without loading an unrelated city', async ({ page }) => {
      await page.addInitScript(() => {
        navigator.geolocation.getCurrentPosition = (_success, error) => error?.({ code: 1, message: 'Denied' } as GeolocationPositionError)
      })
      await page.goto('/#/?mode=nearby')
      await expect(page.getByRole('alert')).toContainText('Location access is off')
      await expect(page.locator('.tree-map')).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Try location again' })).toBeEnabled()
      await page.getByRole('button', { name: 'Explore City', exact: true }).click()
      await expect(page).toHaveURL(/mode=explore/)
      await expect(page.locator('.tree-map')).toBeVisible()
    })

    test('leaving a pending location request ignores its late result', async ({ page }) => {
      await page.addInitScript(() => {
        navigator.geolocation.getCurrentPosition = success => { (window as any).__finishLocation = success }
      })
      await page.goto('/#/?mode=nearby')
      await expect(page.getByRole('status')).toContainText('Finding your location')
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toBeDisabled()
      await expect(page.locator('.tree-map')).toHaveCount(0)
      if (label === 'mobile') await expect(page.getByTestId('mobile-action-landmarks')).toHaveCount(0)
      await page.getByRole('button', { name: 'Explore City', exact: true }).click()
      await expect(page).toHaveURL(/mode=explore/)
      await page.evaluate(() => (window as any).__finishLocation({ coords: { latitude: 42.36, longitude: -71.06 }, timestamp: Date.now() }))
      await expect(page).toHaveURL(/mode=explore/)
    })

    test('outside coverage explains the limitation and allows exploration', async ({ page, context }) => {
      await context.grantPermissions(['geolocation'])
      await context.setGeolocation({ latitude: 0, longitude: 0 })
      await page.goto('/#/?mode=nearby')
      await expect(page.getByRole('alert')).toContainText('do not have a tree inventory near you')
      await expect(page.locator('.tree-map')).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Explore City', exact: true })).toBeEnabled()
    })

    test('an empty neighborhood explains that there are no mapped trees', async ({ page, context }) => {
      test.setTimeout(90_000)
      await context.grantPermissions(['geolocation'])
      // Offshore, within Boston's selection radius but outside its tree inventory.
      await context.setGeolocation({ latitude: 42.3601, longitude: -70.7 })
      await page.goto('/#/?mode=nearby')
      await expect(page.locator('.nearby-empty')).toContainText('No mapped trees', { timeout: 60_000 })
      await expect(page.getByTestId('city-select')).toBeEnabled()
      await expect(page.getByRole('button', { name: 'Zoom out', exact: true })).toBeEnabled()
      await expect(page.getByRole('button', { name: 'Explore City', exact: true })).toHaveCount(0)
    })

    test('a timeout can be retried successfully from the entry button', async ({ page }) => {
      test.setTimeout(90_000)
      await page.addInitScript(() => {
        let calls = 0
        navigator.geolocation.getCurrentPosition = (success, error) => {
          if (calls++ === 0) error?.({ code: 3, message: 'Timeout' } as GeolocationPositionError)
          else success({ coords: { latitude: 42.3601, longitude: -71.0589, accuracy: 10 }, timestamp: Date.now() } as GeolocationPosition)
        }
      })
      await page.goto('/#/')
      await page.getByRole('button', { name: 'Near Me', exact: true }).click()
      await expect(page.getByRole('alert')).toContainText('took too long')
      await page.getByRole('button', { name: 'Try location again' }).click()
      await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USBOS', { timeout: 60_000 })
    })
  })
}
