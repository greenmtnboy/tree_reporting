import { test, expect } from '@playwright/test'

async function openMobileMenu(page: import('@playwright/test').Page) {
  await page.getByLabel('Open navigation menu').click()
  await expect(page.locator('.mobile-nav-menu')).toBeVisible()
}

test.describe('Mobile layout', () => {
  test.beforeEach(async ({ page }) => {
    // Use a mobile viewport (below the 768px breakpoint)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.addInitScript(() => {
      localStorage.setItem('sf_trees_welcome_dismissed', '1')
    })
    await page.goto('/#/?city=USSFO')
  })

  test('renders the map and mobile navigation controls', async ({ page }) => {
    await expect(page.locator('.mobile-map-container')).toBeVisible()
    await expect(page.locator('.tree-map canvas')).toBeAttached({ timeout: 15_000 })
    await expect(page.locator('.mobile-bottom-bar')).toBeVisible()
    await expect(page.getByLabel('Open navigation menu')).toBeVisible()

    await expect(page.locator('.mobile-bottom-bar')).toContainText('Landmarks')
    await expect(page.locator('.mobile-bottom-bar')).toContainText('Chat')
  })

  test('desktop sidebar is not rendered on mobile', async ({ page }) => {
    await expect(page.locator('.sidebar')).not.toBeVisible()
  })

  test('landmarks overlay opens and shows search', async ({ page }) => {
    await page.locator('.mobile-action-btn').filter({ hasText: 'Landmarks' }).click()

    const overlay = page.locator('.mobile-overlay')
    await expect(overlay).toBeVisible()
    await expect(page.locator('.mobile-search-input')).toBeVisible()

    // Landmarks should load
    const firstItem = page.locator('.mobile-landmark-item').first()
    await expect(firstItem).toBeVisible({ timeout: 10_000 })
  })

  test('landmarks overlay filters results', async ({ page }) => {
    await page.locator('.mobile-action-btn').filter({ hasText: 'Landmarks' }).click()

    const items = page.locator('.mobile-landmark-item')
    await expect(items.first()).toBeVisible({ timeout: 10_000 })
    const countBefore = await items.count()
    expect(countBefore).toBeGreaterThan(1)

    // Use a common letter rather than a location-specific name to avoid
    // coupling the test to the exact landmark dataset.
    await page.locator('.mobile-search-input').fill('a')
    await expect(items.first()).toBeVisible({ timeout: 5_000 })
    const countAfter = await items.count()
    expect(countAfter).toBeLessThan(countBefore)
    expect(countAfter).toBeGreaterThan(0)
  })

  test('clicking a landmark closes overlay and moves map', async ({ page }) => {
    await page.locator('.mobile-action-btn').filter({ hasText: 'Landmarks' }).click()

    const firstItem = page.locator('.mobile-landmark-item').first()
    await expect(firstItem).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.tree-map canvas')).toBeAttached({ timeout: 15_000 })

    const centerBefore = await page.evaluate(() => {
      const canvas = document.querySelector('.tree-map canvas')
      if (!canvas) return null
      const mapInstance =
        (canvas as any).__maplibre_map ??
        Object.values(canvas as any).find(
          (v: any) => v && typeof v.getCenter === 'function',
        )
      if (mapInstance) {
        const c = mapInstance.getCenter()
        return { lng: c.lng, lat: c.lat }
      }
      return null
    })

    await firstItem.click()

    // Overlay should close after selecting a landmark
    await expect(page.locator('.mobile-overlay')).not.toBeVisible({ timeout: 5_000 })

    await page.waitForTimeout(1500)

    const centerAfter = await page.evaluate(() => {
      const canvas = document.querySelector('.tree-map canvas')
      if (!canvas) return null
      const mapInstance =
        (canvas as any).__maplibre_map ??
        Object.values(canvas as any).find(
          (v: any) => v && typeof v.getCenter === 'function',
        )
      if (mapInstance) {
        const c = mapInstance.getCenter()
        return { lng: c.lng, lat: c.lat }
      }
      return null
    })

    if (centerBefore && centerAfter) {
      const moved =
        Math.abs(centerBefore.lng - centerAfter.lng) > 0.0001 ||
        Math.abs(centerBefore.lat - centerAfter.lat) > 0.0001
      expect(moved).toBe(true)
    }
  })

  test('overlay closes via close button', async ({ page }) => {
    await page.locator('.mobile-action-btn').filter({ hasText: 'Landmarks' }).click()
    await expect(page.locator('.mobile-overlay')).toBeVisible()

    await page.locator('.mobile-overlay-close').click()
    await expect(page.locator('.mobile-overlay')).not.toBeVisible({ timeout: 3_000 })
  })

  test('navigation menu switches to analytics, species, and info screens', async ({ page }) => {
    await openMobileMenu(page)
    await page.locator('.mobile-nav-item').filter({ hasText: 'Analytics' }).click()

    await expect(page.locator('.mobile-route-screen')).toBeVisible()
    await expect(page.locator('.mobile-route-header')).toContainText('City Summary')
    await expect(page.locator('.summary-page h1')).toContainText('City Summary')
    await expect(page.locator('.mobile-bottom-bar')).toContainText('Chat')

    await openMobileMenu(page)
    await page.locator('.mobile-nav-item').filter({ hasText: 'Species' }).click()

    await expect(page.locator('.mobile-route-header')).toContainText('Species Explorer')
    await expect(page.locator('.species-page h1')).toContainText('Tree Species Explorer')
    await expect(page.locator('.mobile-bottom-bar')).toContainText('Chat')

    await openMobileMenu(page)
    await page.locator('.mobile-nav-item').filter({ hasText: 'Info' }).click()

    await expect(page.locator('.mobile-route-header')).toContainText('Project Info')
    await expect(page.locator('.info-page h1')).toContainText('About Urban Trees')
  })

  test('analytics screen opens chat overlay', async ({ page }) => {
    await openMobileMenu(page)
    await page.locator('.mobile-nav-item').filter({ hasText: 'Analytics' }).click()

    await page.getByLabel('Open analytics chat').click()
    await expect(page.locator('.mobile-chat-overlay')).toBeVisible()
    await expect(page.locator('.mobile-chat-overlay')).toContainText('Analytics Assistant')
  })
})
