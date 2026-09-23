import { test, expect } from '@playwright/test'

/**
 * "Find Me" must work before the first city has finished loading. On a phone
 * the initial load is the slowest moment of the session, and a user who opens
 * the app standing at a tree wants to be taken there, not to wait for a city
 * they may not even be in.
 */

// Near the San Francisco Ferry Building.
const SF = { latitude: 37.7955, longitude: -122.3937 }

for (const mobile of [true, false]) {
  test.describe(`Find Me during initial load — ${mobile ? 'mobile' : 'desktop'}`, () => {
    test('is enabled while the map is still loading', async ({ page }) => {
      await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 })
      await page.addInitScript(() => {
        localStorage.setItem('sf_trees_welcome_dismissed', '1')
      })
      await page.goto('/#/?city=USSFO')

      await expect(page.locator('.map-loading')).toBeVisible({ timeout: 30_000 })
      const button = page.locator(mobile ? '.locate-btn' : '.locate-btn-desktop')
      await expect(button).toBeVisible()
      await expect(button).toBeEnabled()
    })
  })
}

test('a mid-load press on mobile moves to the user\'s city and lands on them', async ({ page, context }) => {
  test.setTimeout(180_000)
  await context.grantPermissions(['geolocation'])
  await context.setGeolocation(SF)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => {
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
  })
  // Opened on Boston; the user is in San Francisco.
  await page.goto('/#/?city=USBOS')

  await expect(page.locator('.map-loading')).toBeVisible({ timeout: 30_000 })
  await page.locator('.locate-btn').click()

  await expect(page).toHaveURL(/city=USSFO/, { timeout: 30_000 })
  await page.waitForFunction(
    () => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === 'USSFO',
    undefined,
    { timeout: 120_000 },
  )
  await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 60_000 })

  // The deferred camera move runs once the map is ready and lands at street zoom.
  await page.waitForFunction(
    ({ lat, lng }) => {
      const map = (window as unknown as { __treeMap?: import('maplibre-gl').Map }).__treeMap
      if (!map || map.isMoving()) return false
      const c = map.getCenter()
      return Math.abs(c.lat - lat) < 0.001 && Math.abs(c.lng - lng) < 0.001 && map.getZoom() > 16
    },
    { lat: SF.latitude, lng: SF.longitude },
    { timeout: 60_000 },
  )
})
