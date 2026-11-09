import { test, expect, type Page } from '@playwright/test'

// The desktop draws every tree from memory in one custom layer
// (composables/treeDotsLayer.ts) instead of vector tiles: a colour change
// recolours it in place, and its circles are what a click picks.

type DotsLayer = {
  count: number
  loaded: boolean
  visibleTrees(max?: number): { x: number; y: number; rowid: number }[]
}

async function openDesktopMap(page: Page) {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.addInitScript(() => localStorage.setItem('sf_trees_welcome_dismissed', '1'))
  await page.goto('/#/?city=USSFO&mode=explore')
  await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USSFO', { timeout: 90_000 })
  await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 90_000 })
}

test('a colour change recolours the resident trees in place', async ({ page }) => {
  test.setTimeout(180_000)
  await openDesktopMap(page)
  const resident = await page.evaluate(() => {
    const map = window.__treeMap!
    const layer = (map.getLayer('trees-dots') as unknown as { implementation?: DotsLayer } | undefined)?.implementation
    ;(window as unknown as { __dots?: unknown }).__dots = layer
    return { count: layer?.count ?? 0, tileSource: !!map.getSource('trees') }
  })
  expect(resident.count).toBeGreaterThan(0)
  expect(resident.tileSource).toBe(false)

  // Watch every frame from the change: the drawn set must never empty.
  await page.evaluate(() => {
    const w = window as unknown as { __minDrawn: number; __dots: DotsLayer }
    w.__minDrawn = Infinity
    const tick = () => { w.__minDrawn = Math.min(w.__minDrawn, w.__dots.count); requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
  })
  const recolored = page.waitForEvent('console', { predicate: (m) => m.text().includes('map:dots:recolor'), timeout: 60_000 })
  await page.getByLabel('Color trees by').selectOption('tree_form')
  await expect(page).toHaveURL(/colorBy=tree_form/)
  await recolored
  const after = await page.evaluate(() => {
    const w = window as unknown as { __minDrawn: number; __dots: DotsLayer }
    const layer = (window.__treeMap!.getLayer('trees-dots') as unknown as { implementation?: DotsLayer }).implementation
    return { sameLayer: layer === w.__dots, minDrawn: w.__minDrawn, count: layer?.count ?? 0 }
  })
  expect(after.sameLayer).toBe(true)
  expect(after.minDrawn).toBe(resident.count)
  expect(after.count).toBe(resident.count)
  await expect(page.getByRole('region', { name: 'Map legend' })).toContainText('Broadleaf')
})

test('clicking a resident circle opens that tree’s card', async ({ page }) => {
  test.setTimeout(180_000)
  await openDesktopMap(page)
  const handle = await page.waitForFunction(() => {
    const map = window.__treeMap!
    if (map.isMoving()) return null
    // Dots are clickable from z12.8 (CIRCLE_PICK_MIN_ZOOM) until the sprites take over (z13.4 to z14).
    if (Math.abs(map.getZoom() - 13) > 0.01) { map.jumpTo({ zoom: 13, pitch: 0, bearing: 0 }); return null }
    const layer = (map.getLayer('trees-dots') as unknown as { implementation?: DotsLayer }).implementation!
    const box = map.getContainer().getBoundingClientRect()
    for (const t of layer.visibleTrees(500)) {
      if (t.x < 120 || t.y < 120 || t.x > box.width - 160 || t.y > box.height - 260) continue
      if (document.elementFromPoint(box.left + t.x, box.top + t.y) !== map.getCanvas()) continue
      return { x: box.left + t.x, y: box.top + t.y }
    }
    return null
  }, undefined, { timeout: 60_000, polling: 500 })
  const target = (await handle.jsonValue())!
  await page.mouse.click(target.x, target.y)
  await expect(page.locator('.tree-card')).toBeVisible({ timeout: 15_000 })
  await expect(page).toHaveURL(/tree=/)

  // The debug panel fixes the sprite cap, which otherwise adjusts itself, and hands it back.
  await page.locator('.cache-refresh-btn[aria-pressed]').click()
  const readout = page.locator('.sprite-debug')
  await expect(readout).toContainText('auto', { timeout: 15_000 })
  await page.getByLabel('Maximum tree sprites').selectOption('16384')
  await expect(readout).toContainText('/ 16,384 fixed')
  await page.getByLabel('Maximum tree sprites').selectOption('auto')
  await expect(readout).toContainText('auto')
})
