import { test, expect } from '@playwright/test'

/**
 * The desktop intro must actually fly: it starts close in (INTRO_START_ZOOM,
 * 18.5) and sweeps out to the city view (INTRO_END_ZOOM, 13.5), north-up.
 *
 * Before moving, the intro waits until trees are drawn at the start pose. That
 * gate has failed silently before: when the trees-icon symbol layer became a
 * custom WebGL layer, queryRenderedFeatures stopped seeing it, every readiness
 * wait timed out, and the intro logged map:intro-gate:blocked-motion and left
 * the camera parked at z18.5 with no error in the UI.
 */

const CITY = 'USBOS'
const INTRO_START_ZOOM = 18.5
const INTRO_END_ZOOM = 13.5
// From tiles loaded to landing: the 10 s sweep plus a few seconds of gating.
// A blocked gate alone burns ~18 s of timeouts and then never moves.
const INTRO_LANDING_BUDGET_MS = 30_000

declare global {
  interface Window {
    __treeMap?: import('maplibre-gl').Map
  }
}

test('desktop intro zooms out to the city view within a bounded time', async ({ page }) => {
  test.setTimeout(150_000)
  const blockedGate: string[] = []
  page.on('console', (msg) => {
    if (msg.text().includes('map:intro-gate:blocked-motion')) blockedGate.push(msg.text())
  })

  await page.setViewportSize({ width: 1280, height: 800 })
  await page.addInitScript(() => {
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
  })
  await page.goto(`/#/?city=${CITY}&mode=explore`)

  await page.waitForFunction(
    (code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code,
    CITY,
    { timeout: 90_000 },
  )
  const startZoom = await page.evaluate(() => window.__treeMap!.getZoom())
  expect(startZoom).toBeGreaterThan(INTRO_END_ZOOM + 1)

  await page.waitForFunction(
    (endZoom) => {
      const map = window.__treeMap
      return !!map && map.getZoom() <= endZoom + 0.05
    },
    INTRO_END_ZOOM,
    { timeout: INTRO_LANDING_BUDGET_MS },
  )

  // The intro hands back control at the city view, north-up.
  await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 15_000 })
  const end = await page.evaluate(() => ({ zoom: window.__treeMap!.getZoom(), bearing: window.__treeMap!.getBearing() }))
  expect(end.zoom).toBeCloseTo(INTRO_END_ZOOM, 1)
  expect(Math.abs(end.bearing)).toBeLessThan(0.5)
  expect(startZoom).toBeLessThanOrEqual(INTRO_START_ZOOM + 0.01)
  expect(blockedGate).toEqual([])
})
