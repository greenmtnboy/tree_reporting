import { expect, test, type Page } from '@playwright/test'

/** One tree's crown at the centre of the view, 240 px across, read back once both atlases are up. */
async function drawCentred(page: Page, trees: { id: string; offset: number; category: string; deciduous?: boolean }[]) {
  return page.evaluate(async (trees) => {
    const { map, crowns, layer } = (window as any).crownTest
    map.setPaintProperty('ground', 'background-opacity', 0)
    map.jumpTo({ zoom: 20, pitch: 0, bearing: 0 })
    layer.viewAngle = true
    const center = map.getCenter()
    const metresPerPixel = 40075016.68557849 * Math.cos(center.lat * Math.PI / 180) / (512 * 2 ** 20)
    const degreesPerPixel = 360 / (512 * 2 ** 20)
    crowns.splice(0, crowns.length, ...trees.map(t => ({
      id: t.id, lng: center.lng + t.offset * degreesPerPixel, lat: center.lat,
      width: 240 * metresPerPixel, measured: true, color: '#66584c', category: t.category, deciduous: t.deciduous,
    })))
    layer.invalidate()
    await new Promise(resolve => setTimeout(resolve, 300))
    // The leafless atlases are built at idle after the first bare tree arrives.
    while (layer.inFlight || layer.timer || !layer.count || !layer.crownsReady || (layer.drawn.bare > 0 && !layer.bareReady)) {
      await new Promise(resolve => setTimeout(resolve, 50))
    }
    await new Promise<void>(resolve => { map.once('render', () => resolve()); map.triggerRepaint() })
    const source = map.getCanvas()
    return trees.map(t => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 240
      const context = canvas.getContext('2d')!
      context.drawImage(source, (source.width - 240) / 2 + t.offset, (source.height - 240) / 2, 240, 240, 0, 0, 240, 240)
      const pixels = context.getImageData(0, 0, 240, 240).data
      let ink = 0, transparent = 0
      for (let i = 3; i < pixels.length; i += 4) {
        if (pixels[i] > 32) ink++
        if (pixels[i] === 0) transparent++
      }
      return { ink, transparent }
    })
  }, trees)
}

for (const mobile of [false, true]) {
  test('leafless ' + (mobile ? 'phone' : 'desktop') + ' leaves the gaps between branches transparent', async ({ page }) => {
    await page.goto('/renderer-tests/crowns.html?appearance=leafless')
    await page.waitForFunction(() => (window as any).crownTest?.layer?.crownsReady)
    if (mobile) await page.locator('#mobile').click()
    const [bare] = await drawCentred(page, [{ id: 'bare', offset: 0, category: 'multi_trunk', deciduous: true }])
    // Sample inside the crown footprint: branch gaps must be truly clear, not a faint wash.
    expect(bare.ink).toBeGreaterThan(1000)
    expect(bare.transparent).toBeGreaterThan(240 * 240 * .65)
  })

  test('in winter only the deciduous tree is bare (' + (mobile ? 'phone' : 'desktop') + ')', async ({ page }) => {
    await page.goto('/renderer-tests/crowns.html?date=2027-01-15')
    await page.waitForFunction(() => (window as any).crownTest?.layer?.crownsReady)
    if (mobile) await page.locator('#mobile').click()
    const [evergreen, deciduous] = await drawCentred(page, [
      { id: 'evergreen', offset: -260, category: 'broadleaf' },
      { id: 'deciduous', offset: 260, category: 'broadleaf', deciduous: true },
    ])
    // The leafy crown is filled (desktop) or washed and outlined (phone): far fewer clear pixels
    // than the bare branches beside it, drawn in the same batch.
    expect(deciduous.ink).toBeGreaterThan(1000)
    expect(deciduous.transparent).toBeGreaterThan(240 * 240 * .65)
    expect(evergreen.transparent).toBeLessThan(deciduous.transparent * 0.75)
  })
}

test('a deciduous tree out of winter keeps its leaves', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html?date=2027-07-15')
  await page.waitForFunction(() => (window as any).crownTest?.layer?.crownsReady)
  await drawCentred(page, [{ id: 'summer', offset: 0, category: 'broadleaf', deciduous: true }])
  expect(await page.evaluate(() => (window as any).crownTest.layer.drawn.bare)).toBe(0)
})
