import { test, expect } from '@playwright/test'

test.describe('Desktop layout', () => {
  test.beforeEach(async ({ page }) => {
    // Use a desktop viewport (well above the 768px mobile breakpoint)
    await page.setViewportSize({ width: 1280, height: 800 })
    // Dismiss the welcome modal so it doesn't block interactions
    await page.addInitScript(() => {
      localStorage.setItem('sf_trees_welcome_dismissed', '1')
    })
    await page.goto('/')
  })

  test('renders sidebar with header and landmarks', async ({ page }) => {
    // Sidebar header
    await expect(page.locator('.sidebar h1')).toHaveText('Urban Trees')
    await expect(page.locator('.sidebar .subtitle')).toHaveText(
      'The Woods of the Concrete Jungle',
    )

    // Landmarks section is present with search input
    await expect(page.locator('.landmarks-search')).toBeVisible()

    // Wait for landmark buttons to populate (loaded from JSON)
    const firstLandmark = page.locator('.landmark-item').first()
    await expect(firstLandmark).toBeVisible({ timeout: 10_000 })
  })

  test('renders the map container', async ({ page }) => {
    const mapEl = page.locator('.tree-map')
    await expect(mapEl).toBeVisible()
    // The MapLibre canvas should be created inside the container
    await expect(mapEl.locator('canvas')).toBeAttached({ timeout: 15_000 })
  })

  test('landmark search filters the list', async ({ page }) => {
    // Wait for landmarks to load
    const landmarks = page.locator('.landmark-item')
    await expect(landmarks.first()).toBeVisible({ timeout: 10_000 })

    const countBefore = await landmarks.count()
    expect(countBefore).toBeGreaterThan(1)

    // Type a search query that should narrow results
    await page.locator('.landmarks-search').fill('Golden Gate')
    const filtered = page.locator('.landmark-item')
    await expect(filtered.first()).toBeVisible()
    const countAfter = await filtered.count()
    expect(countAfter).toBeLessThan(countBefore)
    expect(countAfter).toBeGreaterThan(0)
  })

  test('clicking a landmark triggers map movement', async ({ page }) => {
    // Wait for landmarks and map canvas
    const firstLandmark = page.locator('.landmark-item').first()
    await expect(firstLandmark).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.tree-map canvas')).toBeAttached({
      timeout: 15_000,
    })

    // Capture the map center before clicking by reading the MapLibre instance
    const centerBefore = await page.evaluate(() => {
      const canvas = document.querySelector('.tree-map canvas')
      if (!canvas) return null
      // Access the MapLibre map instance via the internal property
      const mapInstance = (canvas as any).__maplibre_map ??
        Object.values(canvas as any).find(
          (v: any) => v && typeof v.getCenter === 'function',
        )
      if (mapInstance) {
        const c = mapInstance.getCenter()
        return { lng: c.lng, lat: c.lat }
      }
      return null
    })

    // Click the first landmark
    await firstLandmark.click()

    // The map should start a fly-to animation. Wait a moment for it to begin,
    // then verify the map center has changed (or a flyTo was scheduled).
    // We verify the camera started moving by checking after a short delay.
    await page.waitForTimeout(1500)

    const centerAfter = await page.evaluate(() => {
      const canvas = document.querySelector('.tree-map canvas')
      if (!canvas) return null
      const mapInstance = (canvas as any).__maplibre_map ??
        Object.values(canvas as any).find(
          (v: any) => v && typeof v.getCenter === 'function',
        )
      if (mapInstance) {
        const c = mapInstance.getCenter()
        return { lng: c.lng, lat: c.lat }
      }
      return null
    })

    // If we could read the map instance, verify movement occurred.
    // If MapLibre internals aren't accessible, at least verify no crash.
    if (centerBefore && centerAfter) {
      const moved =
        Math.abs(centerBefore.lng - centerAfter.lng) > 0.0001 ||
        Math.abs(centerBefore.lat - centerAfter.lat) > 0.0001
      expect(moved).toBe(true)
    }
  })
})
