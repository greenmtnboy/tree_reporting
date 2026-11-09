import { test, expect, type Page } from '@playwright/test'

// The map draws the trees under the camera, whichever inventory published
// them (docs/SEAMLESS_WORLD.md): it reads the world's block files as the
// camera reaches them, and a city is a bookmark. So a map opened on Boston
// can be flown to Burlington, Vermont, or to San Francisco, with no city
// switch: the trees there load and draw, and the bookmark (the selector, the
// URL) follows the camera.

type DotsLayer = {
  count: number
  residentCount: number
  chunkCount: number
  loaded: boolean
  viewSettled: boolean
  visibleTrees(max?: number): { x: number; y: number; rowid: number }[]
}

const CENTERS: Record<string, [number, number]> = {
  USBOS: [-71.0589, 42.3601],
  USBTV: [-73.2121, 44.4759],
  USSFO: [-122.4194, 37.7749],
}

async function openMap(page: Page, city: string, viewport: { width: number; height: number }) {
  await page.setViewportSize(viewport)
  await page.addInitScript(() => localStorage.setItem('sf_trees_welcome_dismissed', '1'))
  await page.goto(`/#/?city=${city}&mode=explore`)
  await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', city, { timeout: 90_000 })
  await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 90_000 })
}

/** Move the camera as a person would, by flying: nothing is told which city this is. */
async function flyCamera(page: Page, [lng, lat]: [number, number], zoom: number) {
  await page.evaluate(([lng, lat, zoom]) => {
    window.__treeMap!.flyTo({ center: [lng, lat], zoom, pitch: 0, bearing: 0, duration: 3000, essential: true })
  }, [lng, lat, zoom])
  await page.waitForFunction(() => !window.__treeMap!.isMoving(), undefined, { timeout: 60_000 })
}

/** Trees the resident layer holds within about 20 km of a point. */
function residentNear(page: Page, [lng, lat]: [number, number]) {
  return page.evaluate(([lng, lat]) => {
    const map = window.__treeMap!
    const layer = (map.getLayer('trees-dots') as unknown as { implementation?: DotsLayer } | undefined)?.implementation
    if (!layer?.viewSettled) return -1
    const centre = map.getCenter()
    if (Math.abs(centre.lng - lng) > 0.25 || Math.abs(centre.lat - lat) > 0.25) return -1
    return layer.visibleTrees(500).length
  }, [lng, lat])
}

test('a desktop opened on Boston flies to Burlington and to San Francisco, and their trees load and draw', async ({ page }) => {
  test.setTimeout(300_000)
  await openMap(page, 'USBOS', { width: 1280, height: 800 })

  const boston = await page.evaluate(() => {
    const map = window.__treeMap!
    const layer = (map.getLayer('trees-dots') as unknown as { implementation?: DotsLayer }).implementation!
    ;(window as unknown as { __dots?: DotsLayer }).__dots = layer
    return { resident: layer.residentCount, world: layer.count, tiles: !!map.getSource('trees') }
  })
  expect(boston.resident).toBeGreaterThan(0)
  // The rows are the world's, not a city's: far more than is held.
  expect(boston.world).toBeGreaterThan(boston.resident)
  expect(boston.tiles).toBe(false)

  for (const city of ['USBTV', 'USSFO']) {
    await flyCamera(page, CENTERS[city], 13)
    // The bookmark follows the camera: the map's own marker, the URL and the selector.
    await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', city, { timeout: 60_000 })
    await expect(page).toHaveURL(new RegExp(`city=${city}`))
    await expect(page.getByTestId('city-select')).toHaveValue(city)
    // Its trees were fetched for this view and are drawn.
    await expect.poll(() => residentNear(page, CENTERS[city]), { timeout: 90_000 }).toBeGreaterThan(20)
    // No city was loaded: no overlay, the same layer, and Boston still held.
    await expect(page.locator('.map-loading')).toHaveCount(0)
    const after = await page.evaluate(() => {
      const w = window as unknown as { __dots: DotsLayer }
      const layer = (window.__treeMap!.getLayer('trees-dots') as unknown as { implementation?: DotsLayer }).implementation
      return { sameLayer: layer === w.__dots, resident: layer?.residentCount ?? 0 }
    })
    expect(after.sameLayer).toBe(true)
    expect(after.resident).toBeGreaterThan(boston.resident)
  }

  // A tree of the city flown to opens its card: it is read from its own block.
  const handle = await page.waitForFunction(() => {
    const map = window.__treeMap!
    if (map.isMoving()) return null
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
  await expect(page).toHaveURL(/city=USSFO/)

  // The address the camera wandered to is a link to where it is: it opens there, on that tree.
  await page.reload()
  await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USSFO', { timeout: 90_000 })
  await expect(page.getByTestId('city-select')).toHaveValue('USSFO')
  await expect(page.locator('.tree-card')).toBeVisible({ timeout: 60_000 })
})

test('a phone panned from Boston to Burlington draws Burlington\'s trees', async ({ page }) => {
  test.setTimeout(240_000)
  await openMap(page, 'USBOS', { width: 390, height: 844 })
  // A phone holds only the ground near the camera, in tiles; it cannot zoom out, so it travels at street scale.
  await page.evaluate(([lng, lat]) => window.__treeMap!.jumpTo({ center: [lng, lat], zoom: 15 }), CENTERS.USBTV)
  await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USBTV', { timeout: 60_000 })
  await expect(page).toHaveURL(/city=USBTV/)
  await expect.poll(() => page.evaluate(() => {
    const map = window.__treeMap!
    if (!map.getSource('trees')) return 0
    // One source layer per colour (workers/mvtLayers.ts).
    const sourceLayers = new Set(map.getStyle().layers.flatMap((l) => ('source-layer' in l && l.source === 'trees' && l['source-layer'] ? [l['source-layer']] : [])))
    return [...sourceLayers].reduce((n, sourceLayer) => n + map.querySourceFeatures('trees', { sourceLayer }).length, 0)
  }), { timeout: 90_000 }).toBeGreaterThan(20)
  await expect(page.locator('.map-loading')).toHaveCount(0)
})
