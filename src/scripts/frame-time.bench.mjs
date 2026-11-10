// Per-zoom render cost of the tree layers in Chromium: after the view settles,
// force repaints for a few seconds and report frames per second and the mean
// frame interval, plus the renderer main thread's script and task time over
// the settle (CDP Performance metrics). Headless Chromium draws WebGL with
// SwiftShader (software), so frame times are relative, not a desktop GPU's.
//
//   MAP_BENCH_URL=http://localhost:6180/ MAP_BENCH_QUERY=colorBy=tree_form node frame-time.mjs
import { chromium } from '@playwright/test'
import process from 'node:process'

const base = process.env.MAP_BENCH_URL ?? 'http://localhost:6180/'
const city = process.env.MAP_BENCH_CITY ?? 'USBOS'
const extraQuery = process.env.MAP_BENCH_QUERY ? `&${process.env.MAP_BENCH_QUERY}` : ''
const lat = 42.3601, lng = -71.0589
const zooms = (process.env.FRAME_ZOOMS ?? '11,12,13,14,15,16').split(',').map(Number)
// FRAME_PITCH tilts the camera (degrees): a pitched view holds the most trees under the sprites.
const pitch = Number(process.env.FRAME_PITCH ?? 0)
const REPAINT_MS = 3000

const browser = await chromium.launch({ executablePath: process.env.MAP_BENCH_CHROMIUM, args: ['--no-sandbox', '--no-zygote'] })
const context = await browser.newContext({ viewport: { width: 1400, height: 900 } })
await context.addInitScript(() => { localStorage.setItem('sf_trees_welcome_dismissed', '1') })
const page = await context.newPage()
const cdp = await context.newCDPSession(page)
await cdp.send('Performance.enable')
const metrics = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((m) => [m.name, m.value]))

try {
  await page.goto(`${base}#/?city=${city}&mode=explore${extraQuery}`)
  await page.waitForFunction((code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code, city, { timeout: 180_000 })
  await page.waitForFunction(() => { const m = window.__treeMap; return !!m && m.getZoom() <= 13.55 }, null, { timeout: 90_000 }).catch(() => {})
  await page.waitForFunction(() => { const m = window.__treeMap; return m && !m.isMoving() && m.areTilesLoaded() }, null, { timeout: 60_000 }).catch(() => {})
  await page.waitForTimeout(2000)
  const layers = await page.evaluate(() => window.__treeMap.getStyle().layers.filter((l) => l.id.startsWith('trees-heat-')).length)
  const resident = await page.evaluate(() => !!window.__treeMap.getLayer('trees-dots'))
  console.log(`${process.env.MAP_BENCH_QUERY ?? 'default'}: ${resident ? 'resident layer' : `${layers} heatmap layers`}`)
  console.log('zoom  settle_ms  main_script_ms  main_task_ms  features  frames/s  ms/frame')
  for (const z of zooms) {
    const before = await metrics()
    const settled = await page.evaluate(async ([lat, lng, z, pitch]) => {
      const map = window.__treeMap
      const t0 = performance.now()
      map.jumpTo({ center: [lng, lat], zoom: z, pitch })
      await new Promise((r) => map.once('render', r))
      while (performance.now() - t0 < 60_000) {
        if (!map.isMoving() && map.areTilesLoaded() && (!map.getSource('trees') || map.isSourceLoaded('trees'))) break
        await new Promise((r) => setTimeout(r, 50))
      }
      // A late idle, if one comes; the map may already be idle.
      await new Promise((r) => { const t = setTimeout(r, 3000); map.once('idle', () => { clearTimeout(t); r() }) })
      return Math.round(performance.now() - t0)
    }, [lat, lng, z, pitch])
    const after = await metrics()
    // One source layer per colour (workers/mvtLayers.ts).
    const features = await page.evaluate(() => {
      const map = window.__treeMap
      // The desktop's resident layer holds every tree (composables/treeDotsLayer.ts).
      const dots = map.getLayer('trees-dots')?.implementation
      if (dots) return dots.count
      const sourceLayers = new Set(map.getStyle().layers.flatMap((l) => (l.source === 'trees' && l['source-layer'] ? [l['source-layer']] : [])))
      return [...sourceLayers].reduce((n, sourceLayer) => n + map.querySourceFeatures('trees', { sourceLayer }).length, 0)
    })
    const frames = await page.evaluate(async (ms) => {
      const map = window.__treeMap
      let n = 0
      const onRender = () => { n += 1; map.triggerRepaint() }
      map.on('render', onRender)
      map.triggerRepaint()
      await new Promise((r) => setTimeout(r, ms))
      map.off('render', onRender)
      return n
    }, REPAINT_MS)
    const f = (v, w) => String(v).padStart(w)
    console.log(`z${f(z, 2)}  ${f(settled, 9)}  ${f(Math.round((after.ScriptDuration - before.ScriptDuration) * 1000), 14)}  ${f(Math.round((after.TaskDuration - before.TaskDuration) * 1000), 12)}  ${f(features, 8)}  ${f((frames / (REPAINT_MS / 1000)).toFixed(1), 8)}  ${f((REPAINT_MS / Math.max(1, frames)).toFixed(1), 8)}`)
  }
} finally {
  await browser.close()
}
