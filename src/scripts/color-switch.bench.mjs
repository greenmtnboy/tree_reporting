// What a "Color by" change does to a settled desktop map: whether the map is
// rebuilt, whether its trees blank, and how long until the new colours are
// on screen. Samples the trees every animation frame from the change: the
// vector source's features, or the resident layer's count where the desktop
// draws from memory (composables/treeDotsLayer.ts), whose recolour logs
// `[Perf] map:dots:recolor` when the new colours are uploaded.
//
//   pnpm build && node scripts/bench-serve.mjs dist 6180    # in another terminal
//   MAP_BENCH_URL=http://localhost:6180/ node scripts/color-switch.bench.mjs
//
// COLOR_SEQUENCE picks the schemes to step through (default
// tree_form,dbh,foliage); MAP_BENCH_ZOOM the zoom to switch at.
import { chromium } from '@playwright/test'
import process from 'node:process'

const base = process.env.MAP_BENCH_URL ?? 'http://localhost:6180/'
const city = process.env.MAP_BENCH_CITY ?? 'USBOS'
const lat = 42.3601, lng = -71.0589
const zoom = Number(process.env.MAP_BENCH_ZOOM ?? 13)
const sequence = (process.env.COLOR_SEQUENCE ?? 'tree_form,dbh,foliage').split(',')
const shots = process.env.COLOR_SHOTS_DIR

const browser = await chromium.launch({ executablePath: process.env.MAP_BENCH_CHROMIUM, args: ['--no-sandbox', '--no-zygote'] })
const context = await browser.newContext({ viewport: { width: 1400, height: 900 } })
await context.addInitScript(() => { localStorage.setItem('sf_trees_welcome_dismissed', '1') })
const page = await context.newPage()
// The resident layer's recolour, stamped on the page clock.
page.on('console', (msg) => { if (msg.text().includes('map:dots:recolor')) void page.evaluate(() => { window.__recolorAt = performance.now() }).catch(() => {}) })

try {
  await page.goto(`${base}#/?city=${city}&mode=explore`)
  await page.waitForFunction((code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code, city, { timeout: 180_000 })
  await page.waitForFunction(() => { const m = window.__treeMap; return !!m && m.getZoom() <= 13.55 }, null, { timeout: 90_000 }).catch(() => {})
  await page.evaluate(async ([lat, lng, zoom]) => {
    const map = window.__treeMap
    map.jumpTo({ center: [lng, lat], zoom })
    const t0 = performance.now()
    await new Promise((r) => map.once('render', r))
    while (performance.now() - t0 < 60_000 && !(map.areTilesLoaded() && map.isSourceLoaded('trees'))) await new Promise((r) => setTimeout(r, 50))
    window.__benchMapId = map
  }, [lat, lng, zoom])
  await page.waitForTimeout(4000)

  console.log(`z${zoom}: step         same_map  min_features  blank_ms  overlay  layers_rebuilt  new_colours_ms  settled_ms`)
  for (const scheme of sequence) {
    const result = await page.evaluate(async (scheme) => {
      const map = window.__treeMap
      const dots = map.getLayer('trees-dots')?.implementation
      const count = () => {
        if (dots) return dots.count
        const layers = new Set(map.getStyle().layers.flatMap((l) => (l.source === 'trees' && l['source-layer'] ? [l['source-layer']] : [])))
        return [...layers].reduce((n, sourceLayer) => n + map.querySourceFeatures('trees', { sourceLayer }).length, 0)
      }
      const layerKey = () => map.getStyle().layers.filter((l) => l.id.startsWith('trees-')).map((l) => l.id).join(',')
      const before = count()
      const beforeLayers = layerKey()
      window.__recolorAt = null
      let removed = 0
      const origRemove = map.removeLayer.bind(map)
      map.removeLayer = (id) => { if (String(id).startsWith('trees-')) removed += 1; return origRemove(id) }
      const select = document.querySelector('select[aria-label="Color trees by"]')
      const t0 = performance.now()
      select.value = scheme
      select.dispatchEvent(new Event('change'))
      let minFeatures = before, blankFrom = null, blankMs = 0, overlay = false, settledAt = null, coloursAt = null
      while (performance.now() - t0 < 30_000) {
        await new Promise((r) => requestAnimationFrame(r))
        const n = count()
        minFeatures = Math.min(minFeatures, n)
        if (n < before * 0.5) { if (blankFrom === null) blankFrom = performance.now() }
        else if (blankFrom !== null) { blankMs += performance.now() - blankFrom; blankFrom = null }
        if (document.querySelector('.map-refreshing')) overlay = true
        if (dots) {
          if (window.__recolorAt !== null) { coloursAt = settledAt = window.__recolorAt; break }
          continue
        }
        const now = layerKey()
        if (coloursAt === null && now !== beforeLayers) coloursAt = performance.now()
        if (performance.now() - t0 > 500 && map.areTilesLoaded() && map.isSourceLoaded('trees') && !map.isMoving() && n >= before * 0.5) { settledAt = performance.now(); break }
      }
      if (blankFrom !== null) blankMs += performance.now() - blankFrom
      map.removeLayer = origRemove
      return {
        sameMap: window.__benchMapId === window.__treeMap,
        before, minFeatures, blankMs: Math.round(blankMs), overlay, removed,
        coloursMs: coloursAt === null ? null : Math.round(coloursAt - t0),
        settledMs: settledAt === null ? null : Math.round(settledAt - t0),
      }
    }, scheme)
    if (shots) await page.screenshot({ path: `${shots}/after-${scheme}.png` })
    const f = (v, w) => String(v ?? '-').padStart(w)
    console.log(`     ${scheme.padEnd(12)} ${f(result.sameMap, 8)} ${f(`${result.minFeatures}/${result.before}`, 13)} ${f(result.blankMs, 9)} ${f(result.overlay, 8)} ${f(result.removed, 15)} ${f(result.coloursMs, 15)} ${f(result.settledMs, 11)}`)
    await page.waitForTimeout(2000)
  }
} finally {
  await browser.close()
}
