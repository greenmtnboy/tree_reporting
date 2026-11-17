import { test, expect, type Page } from '@playwright/test'

async function settle(page: Page) {
  await expect.poll(() => page.evaluate(() => {
    const layer = (window as any).crownTest?.layer
    // The overhead crowns' atlas is built at idle; until it lands they are plain discs.
    return layer && !layer.inFlight && !layer.timer && layer.count > 0 && layer.crownsReady
  })).toBe(true)
  await page.waitForFunction(() => {
    const layer = (window as any).crownTest.layer
    return !layer.simplified || performance.now() - layer.appearedAt > 350
  })
  await page.evaluate(async () => {
    const { map } = (window as any).crownTest
    await new Promise(resolve => { map.once('render', resolve); map.triggerRepaint() })
  })
}

test('tile completion events do not repeat independent sprite queries', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  const before = await page.evaluate(() => (window as any).crownTest.calls)
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => (window as any).crownTest.map.fire('sourcedata', { sourceId: 'trees', isSourceLoaded: true }))
    await page.waitForTimeout(350)
  }
  expect(await page.evaluate(() => (window as any).crownTest.calls)).toBe(before)
})

test('small pans reuse buffered coverage, including trees whose anchors just left the viewport', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.requests = []
    state.layer.load = async (view: any, options: any) => {
      state.requests.push(view)
      return state.batch(state.crowns, view, options)
    }
    state.layer.invalidate()
  })
  await settle(page)
  const buffered = await page.evaluate(() => {
    const { map, requests } = (window as any).crownTest
    const bounds = map.getBounds(), query = requests[0].bounds
    return query.west < bounds.getWest() && query.east > bounds.getEast()
      && query.south < bounds.getSouth() && query.north > bounds.getNorth()
  })
  expect(buffered).toBe(true)
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => (window as any).crownTest.map.panBy([10, 0], { duration: 0 }))
    await page.waitForTimeout(350)
  }
  expect(await page.evaluate(() => (window as any).crownTest.requests.length)).toBe(1)
})

test('a slow query for a departed viewport cannot replace the displayed batch', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.layer.load = (view: any, options: any) => new Promise(resolve => { state.finish = (list: any[]) => resolve(state.batch(list, view, options)) })
    state.layer.schedule()
  })
  // Move far enough to require a new coverage query.
  await page.evaluate(() => (window as any).crownTest.map.panBy([700, 0], { duration: 0 }))
  await page.waitForFunction(() => !!(window as any).crownTest.finish)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.map.panBy([2000, 0], { duration: 0 })
    state.finish([{ ...state.crowns[0], id: 'obsolete-viewport' }])
  })
  expect(await page.evaluate(() => (window as any).crownTest.layer.trees.map((t: any) => t.id))).not.toContain('obsolete-viewport')
})

test('crowns render at metre scale, respect zoom/distance, and release their resources', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  // The upright sprite; from overhead it is a crown disc (tested below).
  await page.evaluate(async () => {
    const { map, layer } = (window as any).crownTest
    layer.viewAngle = false
    await new Promise(resolve => { map.once('render', resolve); map.triggerRepaint() })
  })
  const sprite = await page.evaluate(() => {
    const { map, crowns, layer } = (window as any).crownTest
    const canvas = map.getCanvas()
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    const point = map.project([crowns[0].lng, crowns[0].lat])
    const ratio = canvas.width / canvas.clientWidth
    const expected = 12 / (40075016.68557849 * Math.cos(crowns[0].lat * Math.PI / 180) / (512 * 2 ** map.getZoom())) * ratio
    const cell = layer.sprites.cells.get('broadleaf')
    const height = expected / cell.crownFraction
    const x = Math.round(point.x * ratio), y = canvas.height - Math.round(point.y * ratio) + Math.round(height * (0.95 - 0.38))
    const pixels = new Uint8Array(160 * 4)
    gl.readPixels(x - 80, y, 160, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    const green = Array.from({ length: 160 }, (_, i) => i).filter(i => pixels[i * 4 + 1] - pixels[i * 4] > 20)
    return { width: Math.max(...green) - Math.min(...green), expected, error: gl.getError(), iconLayers: map.getLayersOrder().filter((id: string) => id === 'trees-icon').length,
      picked: layer.pick({ x: point.x, y: point.y - height / ratio * (0.95 - 0.38) })?.id }
  })
  expect(sprite.error).toBe(0)
  expect(sprite.iconLayers).toBe(1)
  expect(sprite.picked).toBe('measured')
  expect(Math.abs(sprite.width - sprite.expected)).toBeLessThan(5)
  await page.screenshot({ path: testInfo.outputPath('desktop.png') })

  await page.getByRole('button', { name: 'Mobile rings' }).click()
  await settle(page)
  // Read the rendered ring through its center. Exclude the faint interior;
  // its two green outline crossings must be the projected 12 m apart.
  const footprint = await page.evaluate(() => {
    const { map, crowns } = (window as any).crownTest
    const canvas = map.getCanvas()
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    const point = map.project([crowns[0].lng, crowns[0].lat])
    const ratio = canvas.width / canvas.clientWidth
    const x = Math.round(point.x * ratio), y = canvas.height - Math.round(point.y * ratio)
    const pixels = new Uint8Array(160 * 4)
    gl.readPixels(x - 80, y, 160, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    const green = Array.from({ length: 160 }, (_, i) => i).filter(i => pixels[i * 4 + 1] - pixels[i * 4] > 20)
    const expected = 12 / (40075016.68557849 * Math.cos(crowns[0].lat * Math.PI / 180) / (512 * 2 ** map.getZoom())) * ratio
    return { crossings: green.length, width: Math.max(...green) - Math.min(...green), expected, error: gl.getError() }
  })
  expect(footprint.error).toBe(0)
  expect(footprint.crossings).toBeGreaterThan(2)
  expect(Math.abs(footprint.width - footprint.expected)).toBeLessThan(5)
  await page.screenshot({ path: testInfo.outputPath('mobile-rings.png') })

  await page.getByRole('button', { name: 'Pitch 75°' }).click()
  await settle(page)
  await page.screenshot({ path: testInfo.outputPath('high-pitch.png') })
  await page.getByRole('button', { name: 'Distant camera' }).click()
  await expect.poll(() => page.evaluate(() => (window as any).crownTest.layer.count)).toBe(0)
  await page.getByRole('button', { name: 'Top down' }).click()
  await settle(page)
  const calls = await page.evaluate(() => (window as any).crownTest.calls)
  await page.getByRole('button', { name: 'Zoom out' }).click()
  await page.waitForTimeout(400)
  expect(await page.evaluate(() => (window as any).crownTest.calls)).toBe(calls)
  await page.getByRole('button', { name: 'Top down' }).click()
  await settle(page)
  await page.getByRole('button', { name: 'Filter all out' }).click()
  await expect.poll(() => page.evaluate(() => (window as any).crownTest.layer.count)).toBe(0)

  const removed = await page.evaluate(() => {
    const { map, layer } = (window as any).crownTest
    const gl = map.getCanvas().getContext('webgl2') ?? map.getCanvas().getContext('webgl')
    const buffer = layer.buffer, program = layer.program
    map.removeLayer(layer.id)
    return { buffer: gl.isBuffer(buffer), program: gl.isProgram(program), timer: !!layer.timer }
  })
  expect(removed).toEqual({ buffer: false, program: false, timer: false })
  expect(errors).toEqual([])
})

test('first sprites wait for complete sizing and cross the former handoff without changing renderer', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html?delay=1000')
  await page.waitForFunction(() => (window as any).crownTest?.layer.inFlight)
  expect(await page.evaluate(() => {
    const { map, layer } = (window as any).crownTest
    return { count: layer.count, icons: map.getLayersOrder().filter((id: string) => id.includes('icon')), legacy: !!map.getLayer('trees-crown') }
  })).toEqual({ count: 0, icons: ['trees-icon'], legacy: false })
  await settle(page)
  const frames = await page.evaluate(async () => {
    const { map, layer } = (window as any).crownTest
    const firstWidths = layer.trees.map((t: { width: number }) => t.width).sort((a: number, b: number) => a - b)
    const buffer = layer.buffer
    // Keep subsequent requests pending: camera movement alone must scale the
    // first complete batch continuously, with no response-triggered handoff.
    layer.load = () => new Promise(() => {})
    const widths: number[] = []
    for (const zoom of [15.98, 15.99, 16, 16.01, 16.02]) {
      map.jumpTo({ zoom })
      await new Promise(resolve => { map.once('render', resolve); map.triggerRepaint() })
      const canvas = map.getCanvas(), gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
      const center = map.project([layer.trees[0].lng, layer.trees[0].lat])
      const pixels = new Uint8Array(30 * 70 * 4)
      gl.readPixels(Math.round(center.x) - 15, canvas.height - Math.round(center.y), 30, 70, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
      const xs: number[] = []
      for (let i = 0; i < 30 * 70; i++) if (pixels[i * 4 + 1] - pixels[i * 4] > 20) xs.push(i % 30)
      widths.push(Math.max(...xs) - Math.min(...xs))
    }
    return { firstWidths, widths, sameBuffer: buffer === layer.buffer, sameLayer: map.getLayer('trees-icon').implementation === layer }
  })
  expect(frames.firstWidths).toEqual([6, 12, 20])
  expect(frames.sameBuffer).toBe(true)
  expect(frames.sameLayer).toBe(true)
  expect(frames.widths.every(Number.isFinite)).toBe(true)
  expect(Math.max(...frames.widths) - Math.min(...frames.widths)).toBeLessThanOrEqual(2)
})

test('enlarged sprite edges stay sharp and all materials share one draw and two textures', async ({ page }, testInfo) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  await page.evaluate(() => {
    const { map, crowns, layer } = (window as any).crownTest
    layer.viewAngle = false
    map.jumpTo({ center: [crowns[0].lng, crowns[0].lat + 0.000075], zoom: 21 })
  })
  await settle(page)
  const edge = await page.evaluate(() => {
    const { map, layer, crowns } = (window as any).crownTest
    const canvas = map.getCanvas(), gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    const point = map.project([crowns[0].lng, crowns[0].lat])
    const expected = 12 / (40075016.68557849 * Math.cos(crowns[0].lat * Math.PI / 180) / (512 * 2 ** 21))
    const size = expected / layer.sprites.cells.get('broadleaf').crownFraction
    const row = new Uint8Array(600 * 4)
    gl.readPixels(Math.round(point.x) - 300, canvas.height - Math.round(point.y - size * 0.57), 600, 1, gl.RGBA, gl.UNSIGNED_BYTE, row)
    const covered = Array.from({ length: 600 }, (_, i) => i).filter(i => row[i * 4] < 165)
    // Only the edge should contain intermediate coverage, not a several-pixel
    // enlarged raster fringe. The tree spans roughly 400 screen pixels here.
    const partial = Array.from({ length: 600 }, (_, i) => i).filter(i => row[i * 4] > 94 && row[i * 4] < 237)
    return { partial: partial.length, width: Math.max(...covered) - Math.min(...covered), expected, textureCount: layer.textures.length, error: gl.getError() }
  })
  expect(edge.error).toBe(0)
  expect(edge.expected).toBeGreaterThan(350)
  expect(edge.partial).toBeLessThanOrEqual(4)
  expect(Math.abs(edge.width - edge.expected)).toBeLessThan(3)
  // The side sprites' atlas and the overhead crowns'.
  expect(edge.textureCount).toBe(2)
  await page.screenshot({ path: testInfo.outputPath('close-up.png') })
  await page.getByRole('button', { name: 'All tree shapes' }).click()
  await settle(page)
  const gallery = await page.evaluate(async () => {
    const { map, crowns, layer } = (window as any).crownTest
    const canvas = map.getCanvas(), gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    const samples = crowns.map((crown: any) => {
      const anchor = map.project([crown.lng, crown.lat])
      const pixel = (x: number, y: number) => {
        const value = new Uint8Array(4)
        gl.readPixels(Math.round(anchor.x + x * 190), canvas.height - Math.round(anchor.y + y * 190), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, value)
        return Array.from(value)
      }
      return { form: crown.category, trunk: pixel(0, -0.1), leaf: pixel(0, crown.category === 'palm' ? -0.75 : -0.55), bloom: crown.category === 'ornamental' ? pixel(-0.1, -0.63) : null }
    })
    // Count actual draw submissions for the custom layer, excluding basemap.
    let draws = 0
    const original = gl.drawArrays.bind(gl)
    gl.drawArrays = (...args: any[]) => { draws++; return original(...args) }
    layer.render(gl, map.transform.customLayerMatrix())
    gl.drawArrays = original
    const clippedTips = Array.from(layer.sprites.cells.values() as Iterable<{ alpha: Uint8Array; size: number }>).some(cell => cell.alpha.slice(0, cell.size).some(alpha => alpha > 0))
    return { samples, draws, vertices: layer.count, clippedTips, error: gl.getError() }
  })
  expect(gallery.error).toBe(0)
  expect(gallery.draws).toBe(1)
  expect(gallery.vertices).toBe(9 * 6)
  expect(gallery.clippedTips).toBe(false)
  for (const sample of gallery.samples) {
    expect(sample.trunk.slice(0, 3), sample.form).toEqual([93, 64, 55])
    expect(sample.leaf[1], sample.form).toBeGreaterThan(sample.leaf[0])
    if (sample.bloom) expect(sample.bloom.slice(0, 3)).toEqual([248, 187, 208])
  }
  await page.screenshot({ path: testInfo.outputPath('all-shapes.png') })
})

test('from overhead a sprite is its crown: its outline the crown wide, centred on the trunk', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  // Straight above the tree, so it is seen from 90 degrees (past SPRITE_TOPDOWN_FULL_DEG).
  await page.evaluate(() => {
    const { map, crowns } = (window as any).crownTest
    map.jumpTo({ center: [crowns[0].lng, crowns[0].lat], pitch: 0 })
  })
  await settle(page)
  const disc = await page.evaluate(() => {
    const { map, crowns, layer } = (window as any).crownTest
    const canvas = map.getCanvas()
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    const point = map.project([crowns[0].lng, crowns[0].lat])
    const ratio = canvas.width / canvas.clientWidth
    const expected = 12 / (40075016.68557849 * Math.cos(crowns[0].lat * Math.PI / 180) / (512 * 2 ** map.getZoom())) * ratio
    // The green box around the trunk: a lobed outline reaches the crown's width at its lobes.
    const n = 200, pixels = new Uint8Array(n * n * 4)
    gl.readPixels(Math.round(point.x * ratio) - n / 2, canvas.height - Math.round(point.y * ratio) - n / 2, n, n, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    const xs: number[] = [], ys: number[] = []
    for (let i = 0; i < n * n; i++) if (pixels[i * 4 + 1] - pixels[i * 4] > 20) { xs.push(i % n); ys.push(Math.floor(i / n)) }
    return { width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys), expected, picked: layer.pick({ x: point.x, y: point.y })?.id, error: gl.getError() }
  })
  expect(disc.error).toBe(0)
  expect(Math.abs(disc.width - disc.expected)).toBeLessThan(5)
  expect(Math.abs(disc.height - disc.expected)).toBeLessThan(5)
  expect(disc.picked).toBe('measured')
})

test('late responses cannot restore filtered crowns or allocate resources after removal', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.layer.load = (view: any, options: any) => new Promise(resolve => { state.finish = (list: any[]) => resolve(state.batch(list, view, options)) })
    state.layer.invalidate()
  })
  await page.waitForFunction(() => !!(window as any).crownTest.finish)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.layer.invalidate()
    state.finish(state.crowns)
  })
  await expect.poll(() => page.evaluate(() => (window as any).crownTest.layer.count)).toBe(0)
  await page.waitForFunction(() => (window as any).crownTest.layer.inFlight)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.map.removeLayer(state.layer.id)
    state.finish(state.crowns)
  })
  await expect.poll(() => page.evaluate(() => (window as any).crownTest.layer.inFlight)).toBe(false)
  expect(await page.evaluate(() => (window as any).crownTest.layer.count)).toBe(0)
})

test('zooming out while loading cannot restore out-of-range sprites', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.layer.load = (view: any, options: any) => new Promise(resolve => { state.finish = (list: any[]) => resolve(state.batch(list, view, options)) })
    state.layer.invalidate()
  })
  await page.waitForFunction(() => !!(window as any).crownTest.finish)
  await page.getByRole('button', { name: 'Zoom out' }).click()
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.finish(state.crowns)
  })
  await expect.poll(() => page.evaluate(() => (window as any).crownTest.layer.inFlight)).toBe(false)
  expect(await page.evaluate(() => {
    const { layer } = (window as any).crownTest
    return { count: layer.count, trees: layer.trees.length }
  })).toEqual({ count: 0, trees: 0 })
})

test('sprite depth order follows the projected ground plane through rotation and pitch', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  const results = await page.evaluate(() => {
    const { map, layer, crowns, batch } = (window as any).crownTest
    const grid = Array.from({ length: 25 }, (_, i) => ({ ...crowns[0], id: `sort-${i}`,
      lng: crowns[0].lng + (i % 5) * 0.0001, lat: crowns[0].lat + Math.floor(i / 5) * 0.0001 }))
    const results: boolean[] = []
    for (const pitch of [0, 60, 80]) {
      for (const bearing of [0, 45, 90, -90, 180]) {
        map.jumpTo({ pitch, bearing })
        const camera = layer.camera()
        layer.setBatch(batch(grid, camera, { simplified: false, bearing: map.getBearing(), cells: layer.cellGeometry() }), camera)
        const y = layer.trees.map((tree: any) => map.project([tree.lng, tree.lat]).y)
        results.push(y.every((value: number, i: number) => !i || value >= y[i - 1] - 0.001))
      }
    }
    return results
  })
  expect(results.every(Boolean)).toBe(true)
})

test('a failed replacement query preserves the displayed trees', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  const before = await page.evaluate(() => (window as any).crownTest.layer.count)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.layer.load = async () => { state.failed = true; throw new Error('temporary query failure') }
    state.map.panBy([700, 0], { duration: 0 })
  })
  await page.waitForFunction(() => (window as any).crownTest.failed)
  expect(await page.evaluate(() => (window as any).crownTest.layer.count)).toBe(before)
})

test('GPU distance fading works while a replacement query is still pending', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await page.getByRole('button', { name: 'Mobile rings' }).click()
  await settle(page)
  await page.evaluate(() => {
    const state = (window as any).crownTest
    state.layer.load = () => new Promise(() => {})
  })
  await page.getByRole('button', { name: 'Distant camera' }).click()
  const frame = await page.evaluate(async () => {
    const { map, layer } = (window as any).crownTest
    await new Promise(resolve => { map.once('render', resolve); map.triggerRepaint() })
    const canvas = map.getCanvas()
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    const pixels = new Uint8Array(canvas.width * canvas.height * 4)
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    let green = 0
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 1] - pixels[i] > 15) green++
    return { retainedVertices: layer.count, green }
  })
  expect(frame.retainedVertices).toBeGreaterThan(0)
  expect(frame.green).toBe(0)
})

test('overhead, each form draws its own crown inside its width, on the desktop and the phone', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html')
  await settle(page)
  for (const mobile of [false, true]) {
    await page.evaluate(mobile => {
      const { map, crowns } = (window as any).crownTest
      const forms = ['broadleaf', 'conifer', 'palm', 'columnar', 'ornamental', 'spreading', 'weeping', 'multi_trunk', 'default']
      const center = map.getCenter()
      // One 12 m crown of each form on a 3 x 3 grid 15 m apart, all seen from nearly straight above.
      crowns.splice(0, crowns.length, ...forms.map((category, i) => ({
        id: category, lng: center.lng + (i % 3 - 1) * 15 / (111320 * Math.cos(center.lat * Math.PI / 180)), lat: center.lat + (Math.floor(i / 3) - 1) * 15 / 111320,
        width: 12, measured: true, color: '#4CAF50', category,
      })))
      if (mobile) document.querySelector<HTMLButtonElement>('#mobile')!.click()
      map.jumpTo({ center, zoom: 18.8, pitch: 0, bearing: 0 })
      ;(window as any).crownTest.layer.invalidate()
    }, mobile)
    await settle(page)
    const shapes = await page.evaluate(() => {
      const { map, crowns } = (window as any).crownTest
      const canvas = map.getCanvas()
      const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
      const ratio = canvas.width / canvas.clientWidth
      const expected = 12 / (40075016.68557849 * Math.cos(crowns[0].lat * Math.PI / 180) / (512 * 2 ** map.getZoom())) * ratio
      const n = Math.ceil(expected * 1.3)
      return crowns.map((crown: any) => {
        const point = map.project([crown.lng, crown.lat])
        const pixels = new Uint8Array(n * n * 4)
        gl.readPixels(Math.round(point.x * ratio) - n / 2, canvas.height - Math.round(point.y * ratio) - n / 2, n, n, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
        const xs: number[] = [], ys: number[] = []
        let covered = 0
        for (let i = 0; i < n * n; i++) {
          if (pixels[i * 4 + 1] - pixels[i * 4] > 12) { xs.push(i % n); ys.push(Math.floor(i / n)); covered++ }
        }
        return { form: crown.category, width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys), covered, expected, error: gl.getError() }
      })
    })
    for (const shape of shapes) {
      expect(shape.error).toBe(0)
      // Never wider than the crown, and reaching it but for a lobe's turn (three-lobed multi-trunk's box is the narrowest).
      expect(shape.width, `${shape.form} ${mobile ? 'phone' : 'desktop'}`).toBeLessThan(shape.expected + 3)
      expect(shape.width, `${shape.form} ${mobile ? 'phone' : 'desktop'}`).toBeGreaterThan(shape.expected * 0.84)
      expect(shape.height, `${shape.form} ${mobile ? 'phone' : 'desktop'}`).toBeLessThan(shape.expected + 3)
      expect(shape.height, `${shape.form} ${mobile ? 'phone' : 'desktop'}`).toBeGreaterThan(shape.expected * 0.84)
    }
    // The forms are drawn differently: a palm's fronds cover far less than a broadleaf's cloud,
    // and on the phone its outline and strokes mark more than a columnar crown's plain ring would.
    const covered = Object.fromEntries(shapes.map((shape: { form: string; covered: number }) => [shape.form, shape.covered]))
    if (!mobile) expect(covered.palm).toBeLessThan(covered.broadleaf * 0.6)
    expect(new Set(shapes.map((shape: { covered: number }) => shape.covered)).size).toBeGreaterThan(6)
  }
})

test('a software renderer keeps plain crown discs: no shape atlas, the crown as wide', async ({ page }) => {
  await page.goto('/renderer-tests/crowns.html?shapes=auto')
  await expect.poll(() => page.evaluate(() => {
    const layer = (window as any).crownTest?.layer
    return layer && !layer.inFlight && !layer.timer && layer.count > 0
  })).toBe(true)
  await page.evaluate(async () => {
    const { map, crowns } = (window as any).crownTest
    map.jumpTo({ center: [crowns[0].lng, crowns[0].lat], pitch: 0 })
  })
  await page.waitForTimeout(600)
  const disc = await page.evaluate(async () => {
    const { map, crowns, layer } = (window as any).crownTest
    await new Promise(resolve => { map.once('render', resolve); map.triggerRepaint() })
    const canvas = map.getCanvas()
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    const point = map.project([crowns[0].lng, crowns[0].lat])
    const ratio = canvas.width / canvas.clientWidth
    const expected = 12 / (40075016.68557849 * Math.cos(crowns[0].lat * Math.PI / 180) / (512 * 2 ** map.getZoom())) * ratio
    const pixels = new Uint8Array(200 * 4)
    gl.readPixels(Math.round(point.x * ratio) - 100, canvas.height - Math.round(point.y * ratio), 200, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    const green = Array.from({ length: 200 }, (_, i) => i).filter(i => pixels[i * 4 + 1] - pixels[i * 4] > 20)
    return { software: !layer.shapes, textures: layer.textures.length, width: Math.max(...green) - Math.min(...green), expected, error: gl.getError() }
  })
  expect(disc.error).toBe(0)
  expect(disc.software).toBe(true)
  expect(disc.textures).toBe(1)
  // A disc: the row through its trunk is the whole crown.
  expect(Math.abs(disc.width - disc.expected)).toBeLessThan(5)
})
