// Run against a production preview or deployed URL. Each round gets a fresh
// browser context; reload measures HTTP-cache reuse but recreates DuckDB.
import { chromium } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import process from 'node:process'

const base = process.env.MAP_BENCH_URL ?? 'http://localhost:6173/'
const city = process.env.MAP_BENCH_CITY ?? 'USBOS'
const latitude = Number(process.env.MAP_BENCH_LAT ?? 42.3601)
const longitude = Number(process.env.MAP_BENCH_LNG ?? -71.0589)
const rounds = Number(process.env.MAP_BENCH_ROUNDS ?? 3)
const browser = await chromium.launch()
const results = []
try {
  for (let round = 1; round <= rounds; round++) {
    // Alternate the order to reduce network/order bias.
    for (const mode of round % 2 ? ['nearby', 'explore'] : ['explore', 'nearby']) {
      const context = await browser.newContext({
        viewport: { width: 390, height: 844 }, permissions: ['geolocation'],
        geolocation: { latitude, longitude },
      })
      const page = await context.newPage()
      for (const cache of ['cold', 'reload']) {
        const url = `${base.replace(/#.*$/, '')}#/?city=${city}&mode=${mode}`
        if (cache === 'cold') await page.goto(url)
        else await page.reload()
        await page.waitForFunction(mode => performance.getEntriesByName(`trees:${mode}:load`).length > 0, mode, { timeout: 120_000 })
        await page.waitForFunction(() => {
          const map = window.__treeMap
          return map?.queryRenderedFeatures({ layers: ['trees-circle', 'trees-heat'].filter(id => map.getLayer(id)) }).length > 0
        }, null, { timeout: 30_000 })
        const result = await page.evaluate(() => ({
          navigationToVisibleTreesMs: Math.round(performance.now()),
          timings: performance.getEntriesByType('measure').filter(e => e.name.startsWith('trees:')).map(e => ({ name: e.name, ms: Math.round(e.duration) })),
          zoom: window.__treeMap.getZoom(),
          center: window.__treeMap.getCenter(),
        }))
        results.push({ round, mode, cache, ...result })
        console.log(JSON.stringify(results.at(-1)))
      }
      await context.close()
    }
  }
} finally {
  await browser.close()
  await mkdir('bench-results', { recursive: true })
  await writeFile('bench-results/map-load.json', JSON.stringify({ base, city, latitude, longitude, results }, null, 2))
}
