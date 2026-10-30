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
  await page.goto(`/#/?city=${CITY}&mode=explore`)

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
 * Finds a pixel that the active renderer resolves to a clickable tree.
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

      const layerIds = ['trees-circle']
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

      if (!isMobile) {
        const sprites = (map.getLayer('trees-icon') as unknown as { implementation?: {
          trees: readonly { lng: number; lat: number }[]
          pick(point: { x: number; y: number }): { id: string } | undefined
        } } | undefined)?.implementation
        for (const tree of sprites?.trees ?? []) {
          const point = map.project([tree.lng, tree.lat])
          // Sample just above the ground anchor, inside the rounded trunk.
          point.y -= 2
          if (!usable(point)) continue
          const hit = sprites!.pick(point)
          if (hit) return { x: container.left + point.x, y: container.top + point.y, id: hit.id }
        }
        return null
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

      if (!mobile) {
        // Stress the same layout with a community id like the original overflow.
        await card.locator('.tree-card-pane--tree .tc-value').first().evaluate(el => {
          el.textContent = 'community-LN351F0hhfzOVEL2v12345678901234567890'
        })
        const pane = card.locator('.tree-card-pane--tree')
        expect(await pane.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
        await expect(page.getByTestId('tree-checkin-count')).toHaveCount(0)
        await page.getByTestId('tree-report-link').click()
        const dialog = page.getByRole('dialog', { name: 'Suggest a correction' })
        await expect(dialog).toBeVisible()
        await expect(dialog.getByText('Finding your location…')).toHaveCount(0)
        await expect(dialog.getByRole('tablist')).toHaveCount(0)
        await expect(dialog.getByRole('button', { name: 'Send fix' })).toBeDisabled()
        await dialog.getByLabel('Species (scientific name)').fill('Acer rubrum')
        await expect(dialog.getByRole('button', { name: 'Send fix' })).toBeEnabled()
        await expect(dialog.locator('.location-picker__map canvas')).toBeVisible()
        expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
      }
    })

    test('the card shows check-ins and visitor photos, and a check-in button only on mobile', async ({ page }) => {
      test.setTimeout(180_000)

      // The clicked tree is not known in advance, so every id reads as 7.
      await page.addInitScript(() => {
        const photo =
          'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAoMBgDTD2qgAAAAASUVORK5CYII='
        window.__treeE2E = {
          treeCheckinCounts: new Proxy({}, { get: () => 7 }),
          treePhotos: new Proxy({}, { get: () => [photo, photo + '#2'] }),
        }
      })
      await openMap(page, mobile)
      const tree = await findClickableTree(page, mobile)
      await page.mouse.click(tree.x, tree.y)

      const count = page.getByTestId('tree-checkin-count')
      await expect(count).toBeVisible({ timeout: 15_000 })
      await expect(count).toContainText('7 check-ins')

      // Reviewed visitor photos replace the example species photo.
      const visitorPhoto = page.getByTestId('tree-visitor-photo')
      await expect(visitorPhoto).toBeVisible()
      await expect(visitorPhoto).toContainText('2 visitor photos')
      await expect(page.locator('.tree-card-pane--photos .tree-card-section-label').first()).toHaveText('Photo of this tree')

      // Checking in is mobile-only; desktop can suggest reviewed corrections.
      const checkin = page.locator('.tree-card-checkin')
      const report = page.getByTestId('tree-report-link')
      if (mobile) {
        await expect(checkin).toBeVisible()
        await expect(report).toBeVisible()
      } else {
        await expect(checkin).toHaveCount(0)
        await expect(report).toBeVisible()
        await expect(report).toHaveText('Correct or confirm info')
        await expect(page.locator('.tree-card-header-actions').getByTestId('tree-report-link')).toBeVisible()
      }
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

test('a check-in photo is only offered as a tree photo once one is attached', async ({ page, context }) => {
  test.setTimeout(180_000)

  await openMap(page, true)
  const tree = await findClickableTree(page, true)
  // Stand on the tree so the dialog lands on its ready step.
  const at = await page.evaluate(({ x, y }) => {
    const map = window.__treeMap!
    const box = map.getContainer().getBoundingClientRect()
    const p = map.unproject([x - box.left, y - box.top])
    return { latitude: p.lat, longitude: p.lng }
  }, tree)
  await context.grantPermissions(['geolocation'])
  await context.setGeolocation(at)

  await page.mouse.click(tree.x, tree.y)
  await page.locator('.tree-card-checkin').click()
  await expect(page.getByTestId('checkin-submit')).toBeVisible({ timeout: 20_000 })

  const share = page.getByTestId('checkin-share-photo')
  await expect(share).toHaveCount(0)

  await page.locator('.checkin-dialog .photo-input').setInputFiles({
    name: 'tree.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAoMBgDTD2qgAAAAASUVORK5CYII=',
      'base64',
    ),
  })
  await expect(share).toBeVisible()
  // Opt-in: nothing is offered for publication unless the user ticks it.
  await expect(share).not.toBeChecked()

  // Reports never offer their photo for publication.
  await page.getByTestId('checkin-mode-missing').click()
  await expect(share).toHaveCount(0)
})

test.describe('Tree taps — mobile', () => {
  test('a tap opens a full-screen sheet at once, before the tree data arrives', async ({ page }) => {
    test.setTimeout(180_000)
    await openMap(page, true)
    const tree = await findClickableTree(page, true)
    await page.evaluate(() => {
      const seen: string[] = []
      ;(window as unknown as { __cardsSeen: string[] }).__cardsSeen = seen
      new MutationObserver(() => {
        const card = document.querySelector('.tree-card')
        if (card && !seen.includes(card.className)) seen.push(card.className)
      }).observe(document.body, { childList: true, subtree: true })
    })

    await page.mouse.click(tree.x, tree.y)
    const card = page.locator('.tree-card')
    await expect(card).toContainText(tree.id, { timeout: 15_000 })

    // The first card on screen is the loading sheet: the tap is answered before
    // the worker's query returns, not after it.
    const seen = await page.evaluate(() => (window as unknown as { __cardsSeen: string[] }).__cardsSeen)
    expect(seen[0]).toContain('tree-card--loading')
    expect(seen[0]).toContain('tree-card--sheet')

    const viewport = page.viewportSize()!
    const box = (await card.boundingBox())!
    expect(box.x).toBe(0)
    expect(box.y).toBe(0)
    expect(box.width).toBe(viewport.width)
    expect(box.height).toBe(viewport.height)
    // Above the bottom bar and the city selector, with a thumb-sized close.
    expect(await page.evaluate(({ w, h }) => [[w / 2, 30], [w / 2, h - 30]]
      .every(([x, y]) => !!document.elementFromPoint(x, y)?.closest('.tree-card')), { w: viewport.width, h: viewport.height })).toBe(true)
    const close = (await card.locator('.tree-card-close').boundingBox())!
    expect(close.width).toBeGreaterThanOrEqual(44)
    expect(close.height).toBeGreaterThanOrEqual(44)
    await card.locator('.tree-card-close').click()
    await expect(card).toHaveCount(0)
  })

  test('a near miss still picks the tree, and a clear miss shows where the tap landed', async ({ page }) => {
    test.setTimeout(180_000)
    await openMap(page, true)
    await findClickableTree(page, true)
    // A tree with no other tree within reach, and a tap 14px off its center.
    // Chosen on a settled map, and only once the same tree has held for
    // 1.5 s: on a phone a tile drawn while its cells load is transient and
    // redrawn when they land, and a window load reads the projection over
    // the network, so a candidate found mid-load can be gone at the tap.
    const near = await page.waitForFunction(() => {
      const map = window.__treeMap!
      if (!map.loaded() || !map.areTilesLoaded()) return null
      const held = window as unknown as { __nearCandidate?: { id: string; x: number; y: number; since: number } }
      const box = map.getContainer().getBoundingClientRect()
      const trees = map.queryRenderedFeatures(undefined, { layers: ['trees-circle'] })
        .filter(f => f.geometry.type === 'Point' && f.properties?.id && f.properties.id !== 'unkwn')
        .map(f => ({ id: String(f.properties!.id), p: map.project((f.geometry as GeoJSON.Point).coordinates as [number, number]) }))
      for (const t of trees) {
        if (t.p.x < 120 || t.p.y < 160 || t.p.x > box.width - 120 || t.p.y > box.height - 220) continue
        const tap = { x: t.p.x + 14, y: t.p.y }
        // Not under the legend or another control floating over the map.
        if (document.elementFromPoint(box.left + tap.x, box.top + tap.y) !== map.getCanvas()) continue
        if (trees.some(o => o.id !== t.id && Math.hypot(o.p.x - tap.x, o.p.y - tap.y) < 30)) continue
        // The exact pixel misses: this tap only lands with the tolerance.
        if (map.queryRenderedFeatures([tap.x, tap.y], { layers: ['trees-circle'] }).length) continue
        const found = { id: t.id, x: box.left + tap.x, y: box.top + tap.y }
        const last = held.__nearCandidate
        if (!last || last.id !== found.id || Math.hypot(last.x - found.x, last.y - found.y) > 1) {
          held.__nearCandidate = { ...found, since: Date.now() }
          return null
        }
        return Date.now() - last.since >= 1_500 ? found : null
      }
      held.__nearCandidate = undefined
      return null
    }, undefined, { timeout: 60_000, polling: 500 })
    const target = (await near.jsonValue())!

    await page.evaluate(() => {
      const rings: string[] = []
      ;(window as unknown as { __rings: string[] }).__rings = rings
      new MutationObserver(records => {
        for (const r of records) for (const n of r.addedNodes) {
          if (n instanceof HTMLElement && n.classList.contains('map-tap-ring')) rings.push(n.className)
        }
      }).observe(window.__treeMap!.getContainer(), { childList: true })
    })
    const rings = () => page.evaluate(() => (window as unknown as { __rings: string[] }).__rings)

    await page.mouse.click(target.x, target.y)
    await expect(page.locator('.tree-card')).toContainText(target.id, { timeout: 15_000 })
    expect(await rings()).toEqual(['map-tap-ring map-tap-ring--hit'])
    await page.locator('.tree-card-close').click()

    const empty = await page.evaluate(() => {
      const map = window.__treeMap!
      const box = map.getContainer().getBoundingClientRect()
      for (let y = 200; y < box.height - 240; y += 20) {
        for (let x = 80; x < box.width - 80; x += 20) {
          if (document.elementFromPoint(box.left + x, box.top + y) !== map.getCanvas()) continue
          if (!map.queryRenderedFeatures([[x - 40, y - 40], [x + 40, y + 40]], { layers: ['trees-circle'] }).length) {
            return { x: box.left + x, y: box.top + y }
          }
        }
      }
      return null
    })
    test.skip(!empty, 'no empty patch of map in view')
    await page.mouse.click(empty!.x, empty!.y)
    await expect.poll(rings).toEqual(['map-tap-ring map-tap-ring--hit', 'map-tap-ring'])
    await expect(page.locator('.tree-card')).toHaveCount(0)
    // The ring is a transient acknowledgement, not a marker left on the map.
    await expect(page.locator('.map-tap-ring')).toHaveCount(0)
  })
})
