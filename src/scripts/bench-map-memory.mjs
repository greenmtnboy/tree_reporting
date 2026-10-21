// Where the browser's memory goes while the map loads a city and zooms in.
// Run against a production preview or deployed URL, on Linux (it reads
// renderer RSS from `ps`). Serving the page cross-origin isolated
// (Cross-Origin-Opener-Policy: same-origin, Cross-Origin-Embedder-Policy:
// credentialless) also unlocks performance.measureUserAgentSpecificMemory,
// which splits that RSS by realm: the DuckDB worker's wasm heap, the page,
// the MapLibre workers. The DuckDB worker is the number to watch; iOS Safari
// reloads the page when the tab outgrows its process budget.
import { chromium } from '@playwright/test'
import { execSync } from 'node:child_process'
import process from 'node:process'

const base = process.env.MAP_BENCH_URL ?? 'http://localhost:6173/'
const city = process.env.MAP_BENCH_CITY ?? 'USBOS'
const latitude = Number(process.env.MAP_BENCH_LAT ?? 42.3601)
const longitude = Number(process.env.MAP_BENCH_LNG ?? -71.0589)
const zooms = (process.env.MAP_BENCH_ZOOMS ?? '13,15,16,17,18').split(',').map(Number)
const executablePath = process.env.MAP_BENCH_CHROMIUM

// Renderer processes host the page and its dedicated workers, so the largest
// one is the map tab. Direct children only, so another browser on the machine
// cannot be mistaken for it.
const browser = await chromium.launch({ executablePath, args: ['--no-sandbox', '--no-zygote'] })
function rendererRssMb() {
  const out = execSync('ps -eo pid=,rss=,args=').toString()
  let best = 0
  for (const line of out.split('\n')) {
    if (!line.includes('--type=renderer') || !line.includes(executablePath ?? 'chrom')) continue
    best = Math.max(best, Number(line.trim().split(/\s+/)[1]) / 1024)
  }
  return Math.round(best)
}

const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
const page = await context.newPage()
try {
  await page.goto(`${base.replace(/#.*$/, '')}#/?city=${city}&mode=explore`)
  await page.waitForFunction(() => performance.getEntriesByName('trees:explore:load').length > 0, null, { timeout: 180_000 })
  await page.waitForTimeout(8000)
  const isolated = await page.evaluate(() => crossOriginIsolated)
  if (!isolated) console.log('(not cross-origin isolated: renderer RSS only; serve with COOP/COEP headers for the per-realm breakdown)')
  async function report(label) {
    console.log(`--- ${label}: renderer RSS ${rendererRssMb()} MB`)
    if (!isolated) return
    const rows = await page.evaluate(async () => {
      const m = await performance.measureUserAgentSpecificMemory()
      return m.breakdown.filter(b => b.bytes > 1048576)
        .map(b => ({ mb: Math.round(b.bytes / 1048576), types: b.types.join('+'), scope: b.attribution.map(a => `${a.scope}:${(a.url || '').split('/').pop()?.slice(0, 40)}`).join(',') }))
        .sort((a, b) => b.mb - a.mb)
    })
    for (const row of rows) console.log(`   ${String(row.mb).padStart(5)} MB  ${row.types.padEnd(22)} ${row.scope}`)
  }
  await report('loaded')
  for (const zoom of zooms) {
    await page.evaluate(([lat, lng, zoom]) => window.__treeMap.jumpTo({ center: [lng, lat], zoom }), [latitude, longitude, zoom])
    await page.waitForFunction(() => { const m = window.__treeMap; return m && !m.isMoving() && m.areTilesLoaded() }, null, { timeout: 60_000 }).catch(() => {})
    await page.waitForTimeout(4000)
    await report(`settled at z${zoom}`)
  }
} finally {
  await browser.close()
}
