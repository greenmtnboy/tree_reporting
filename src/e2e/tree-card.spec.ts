import { test, expect, type Page } from '@playwright/test'

/**
 * Clicking a tree must open the three-pane tree card.
 *
 * This path has failed silently before: showTreeCard() runs a SQL query against
 * the worker's `trees_fast` table and swallows any error into a console.error,
 * so a single missing column (the `data_source` / `submission_photo_url` pair
 * dropped from the trees_fast projection) turned every tree click into a no-op
 * with nothing visible in the UI. These tests click a real rendered tree and
 * assert both that the card appears and that the query logged no error.
 */

const CITY = 'USSFO'

// The intro zoom-out lands at z13.5, where features are LOD aggregates carrying
// the 'unkwn' id sentinel and are deliberately not clickable. Individual trees
// only exist at higher zooms.
const TREE_ZOOM = 17

type Hit = { x: number; y: number; id: string }

declare global {
  interface Window {
    __treeMap?: import('maplibre-gl').Map
  }
}

async function openMap(page: Page, mobile: boolean): Promise<void> {
  await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 })
  await page.addInitScript(() => {
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
  })
  await page.goto(`/#/?city=${CITY}`)

  // Tiles for the city have been generated...
  await page.waitForFunction(
    (code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code,
    CITY,
    { timeout: 90_000 },
  )
  // ...and the lifecycle reached 'ready', which on desktop means the 10 s intro
  // animation is over and the camera is no longer being driven.
  await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 90_000 })
}

/**
 * Finds a pixel that MapLibre's own hit-test resolves to a clickable tree.
 *
 * Candidates come from a viewport-wide queryRenderedFeatures, but each one is
 * then re-queried as a single point — the same call the click handler makes —
 * so the returned coordinate is guaranteed to produce a feature on click
 * regardless of icon anchoring or offset.
 */
async function findClickableTree(page: Page, mobile: boolean): Promise<Hit> {
  const handle = await page.waitForFunction(
    ({ zoom, isMobile }) => {
      const map = window.__treeMap
      if (!map || map.isMoving() || map.isEasing()) return null

      if (Math.abs(map.getZoom() - zoom) > 0.01) {
        map.jumpTo({ zoom, pitch: 0, bearing: 0 })
        return null
      }

      const layerIds = (isMobile ? ['trees-circle'] : ['trees-icon', 'trees-circle'])
        .filter((id) => map.getLayer(id))
      if (!layerIds.length) return null

      const container = map.getContainer().getBoundingClientRect()
      // Stay clear of the overlay chrome (legend, compass, buttons) hugging the
      // container edges, so the click lands on the canvas and not a control.
      const margin = 100
      const usable = (p: { x: number; y: number }) =>
        p.x > margin && p.y > margin &&
        p.x < container.width - margin && p.y < container.height - margin

      const isTree = (f: GeoJSON.Feature) => {
        const id = f.properties?.id
        return typeof id === 'string' && id.length > 0 && id !== 'unkwn'
      }

      for (const feature of map.queryRenderedFeatures(undefined, { layers: layerIds })) {
        if (!isTree(feature as unknown as GeoJSON.Feature)) continue
        const geometry = feature.geometry
        if (geometry.type !== 'Point') continue
        const point = map.project(geometry.coordinates as [number, number])
        if (!usable(point)) continue

        // Verify with the exact single-point query the click handler performs.
        const atPoint = map.queryRenderedFeatures([point.x, point.y], { layers: layerIds })
        const hit = atPoint.find((f) => isTree(f as unknown as GeoJSON.Feature))
        if (!hit) continue

        return {
          x: container.left + point.x,
          y: container.top + point.y,
          id: String(hit.properties?.id),
        }
      }
      return null
    },
    { zoom: TREE_ZOOM, isMobile: mobile },
    { timeout: 60_000, polling: 500 },
  )
  return (await handle.jsonValue()) as Hit
}

for (const mobile of [false, true]) {
  test.describe(`Tree card — ${mobile ? 'mobile' : 'desktop'}`, () => {
    test('clicking a tree opens the card with data for that tree', async ({ page }) => {
      test.setTimeout(180_000)

      const queryErrors: string[] = []
      page.on('console', (msg) => {
        if (msg.type() === 'error' && msg.text().includes('Tree Card Query Error')) {
          queryErrors.push(msg.text())
        }
      })

      await openMap(page, mobile)
      const tree = await findClickableTree(page, mobile)

      await page.mouse.click(tree.x, tree.y)

      const card = page.locator('.tree-card')
      await expect(card, 'tree card did not open after clicking a tree').toBeVisible({ timeout: 15_000 })

      // The card is populated from the SQL result, not from the tile feature, so
      // matching the id proves the query ran and resolved the clicked tree.
      await expect(card.locator('.tree-card-title')).not.toBeEmpty()
      await expect(card).toContainText(tree.id)

      // Every tree row carries a data_source; the card renders it as a "Source"
      // row. This is the column whose absence broke the card.
      await expect(card).toContainText('Source')

      // The card is anchored above the clicked tree, so it has to be clamped to
      // stay on screen — a card whose header sits above y=0 looks like nothing
      // opened at all.
      const box = await card.boundingBox()
      const viewport = page.viewportSize()!
      expect(box, 'tree card has no layout box').not.toBeNull()
      expect(box!.x, 'tree card overflows the left edge').toBeGreaterThanOrEqual(0)
      expect(box!.y, 'tree card overflows the top edge').toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width, 'tree card overflows the right edge').toBeLessThanOrEqual(viewport.width)

      expect(queryErrors, `tree card query failed: ${queryErrors.join('\n')}`).toEqual([])
    })

    test('the tree card closes again', async ({ page }) => {
      test.setTimeout(180_000)

      await openMap(page, mobile)
      const tree = await findClickableTree(page, mobile)

      await page.mouse.click(tree.x, tree.y)
      const card = page.locator('.tree-card')
      await expect(card).toBeVisible({ timeout: 15_000 })

      await card.locator('.tree-card-close').click()
      await expect(card).toHaveCount(0)
    })
  })
}
