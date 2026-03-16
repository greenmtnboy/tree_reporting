import { test, expect } from '@playwright/test'

test.describe('Mobile layout', () => {
  test.beforeEach(async ({ page }) => {
    // Use a mobile viewport (below the 768px breakpoint)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.addInitScript(() => {
      localStorage.setItem('sf_trees_welcome_dismissed', '1')
    })
    await page.goto('/')
  })

  test('renders the map and bottom navigation bar', async ({ page }) => {
    await expect(page.locator('.mobile-map-container')).toBeVisible()
    await expect(page.locator('.tree-map canvas')).toBeAttached({ timeout: 15_000 })
    await expect(page.locator('.mobile-bottom-bar')).toBeVisible()
    // All three nav buttons should be present
    const navBtns = page.locator('.mobile-bar-btn')
    await expect(navBtns).toHaveCount(3)
  })

  test('desktop sidebar is not rendered on mobile', async ({ page }) => {
    await expect(page.locator('.sidebar')).not.toBeVisible()
  })

  test('landmarks overlay opens and shows search', async ({ page }) => {
    // Tap the first nav button (landmarks/search)
    await page.locator('.mobile-bar-btn').first().click()

    const overlay = page.locator('.mobile-overlay')
    await expect(overlay).toBeVisible()
    await expect(page.locator('.mobile-search-input')).toBeVisible()

    // Landmarks should load
    const firstItem = page.locator('.mobile-landmark-item').first()
    await expect(firstItem).toBeVisible({ timeout: 10_000 })
  })

  test('landmark search filters results', async ({ page }) => {
    await page.locator('.mobile-bar-btn').first().click()

    const items = page.locator('.mobile-landmark-item')
    await expect(items.first()).toBeVisible({ timeout: 10_000 })
    const countBefore = await items.count()
    expect(countBefore).toBeGreaterThan(1)

    await page.locator('.mobile-search-input').fill('Golden Gate')
    const filtered = page.locator('.mobile-landmark-item')
    await expect(filtered.first()).toBeVisible()
    const countAfter = await filtered.count()
    expect(countAfter).toBeLessThan(countBefore)
    expect(countAfter).toBeGreaterThan(0)
  })

  test('clicking a landmark closes overlay and moves map', async ({ page }) => {
    await page.locator('.mobile-bar-btn').first().click()

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
    await page.locator('.mobile-bar-btn').first().click()
    await expect(page.locator('.mobile-overlay')).toBeVisible()

    await page.locator('.mobile-overlay-close').click()
    await expect(page.locator('.mobile-overlay')).not.toBeVisible({ timeout: 3_000 })
  })
})
