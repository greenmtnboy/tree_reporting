import { test, expect } from '@playwright/test'

for (const [label, viewport] of [
  ['mobile', { width: 390, height: 844 }],
  ['desktop', { width: 1280, height: 800 }],
] as const) {
  test.describe(`Map experience — ${label}`, () => {
    test.use({ viewport })

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
      expect(parquets).toEqual([])
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await testInfo.attach('entry', { body: await page.screenshot(), contentType: 'image/png' })
      await page.getByRole('button', { name: 'Explore City', exact: true }).click()
      await expect(page).toHaveURL(/mode=explore/)
      await expect(page.locator('.tree-map')).toBeVisible()
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

    test('nearby resolves the actual city, starts at street scale, survives reload and switches to explore', async ({ page, context }, testInfo) => {
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
      await page.getByRole('button', { name: 'Explore City', exact: true }).click()
      await expect(page).toHaveURL(/mode=explore/)
      await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USBOS', { timeout: 60_000 })
      await expect(page.getByRole('button', { name: 'Near Me', exact: true })).toBeVisible()
      await page.goBack()
      await expect(page).toHaveURL(/mode=nearby/)
      await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USBOS', { timeout: 60_000 })
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
