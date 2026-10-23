// Where the browser's memory and bytes go while the map loads a city and
// zooms in. Run against a production preview or deployed URL:
//
//   pnpm preview --port 6180            # in one terminal, after pnpm build
//   MAP_BENCH_URL=http://localhost:6180/ MAP_BENCH_CITY=USNYC pnpm bench:memory
//
// Reports, per step, the renderer process's private memory (the page and
// its dedicated workers, DuckDB included, share one renderer) and, at the
// end, the bytes each remote file cost, counted from the worker's requests.
// Renderer memory is read from the OS: `ps` on Linux, Get-Process on
// Windows, with the renderer pids taken from the browser's own process list
// so another browser on the machine cannot be mistaken for it. Serving the
// page cross-origin isolated (Cross-Origin-Opener-Policy: same-origin,
// Cross-Origin-Embedder-Policy: credentialless) also unlocks
// performance.measureUserAgentSpecificMemory, which splits that figure by
// realm. The DuckDB worker is the number to watch; iOS Safari reloads the
// page when the tab outgrows its process budget. See docs/MOBILE_LOAD_BUDGET.md.
import { chromium, devices } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import process from 'node:process'

const base = process.env.MAP_BENCH_URL ?? 'http://localhost:6173/'
const city = process.env.MAP_BENCH_CITY ?? 'USBOS'
const layout = process.env.MAP_BENCH_LAYOUT ?? 'mobile'
const latitude = Number(process.env.MAP_BENCH_LAT ?? 42.3601)
const longitude = Number(process.env.MAP_BENCH_LNG ?? -71.0589)
const zooms = (process.env.MAP_BENCH_ZOOMS ?? '13,15,16,17,18').split(',').map(Number).filter(Number.isFinite)
const executablePath = process.env.MAP_BENCH_CHROMIUM

const browser = await chromium.launch({ executablePath, args: ['--no-sandbox', '--no-zygote'] })
const browserCdp = await browser.newBrowserCDPSession()

async function rendererPids() {
  const { processInfo } = await browserCdp.send('SystemInfo.getProcessInfo')
  return processInfo.filter((p) => p.type === 'renderer').map((p) => p.id)
}

/** Private memory of the largest renderer, in MB: the map tab. */
async function rendererPrivateMb() {
  const pids = await rendererPids()
  if (pids.length === 0) return 0
  let best = 0
  if (process.platform === 'win32') {
    const script = `Get-Process -Id ${pids.join(',')} -ErrorAction SilentlyContinue | ForEach-Object { $_.PrivateMemorySize64 }`
    const out = execFileSync('powershell.exe', ['-NoProfile', '-Command', script], { encoding: 'utf8' })
    for (const line of out.split(/\r?\n/)) best = Math.max(best, Number(line) || 0)
    return Math.round(best / 1048576)
  }
  const out = execFileSync('ps', ['-o', 'pid=,rss=', '-p', pids.join(',')], { encoding: 'utf8' })
  for (const line of out.split('\n')) {
    const [, rss] = line.trim().split(/\s+/)
    best = Math.max(best, Number(rss) || 0)
  }
  return Math.round(best / 1024)
}

const context = await browser.newContext(layout === 'mobile'
  ? { ...devices['iPhone 13'] }
  : { viewport: { width: 1400, height: 900 } })
const page = await context.newPage()

// Bytes per remote file. Playwright reports the requests of nested workers
// (the DuckDB worker is spawned by the pipeline worker) on the page.
const bytes = new Map()
page.on('requestfinished', async (request) => {
  try {
    const sizes = await request.sizes()
    const key = request.url().split('?')[0]
    const entry = bytes.get(key) ?? { body: 0, requests: 0 }
    entry.body += sizes.responseBodySize
    entry.requests += 1
    bytes.set(key, entry)
  } catch { /* the response was gone before it could be sized */ }
})

let peak = 0
const sampler = setInterval(() => { rendererPrivateMb().then((mb) => { peak = Math.max(peak, mb) }).catch(() => {}) }, 1000)

try {
  const started = Date.now()
  await page.goto(`${base.replace(/#.*$/, '')}#/?city=${city}&mode=explore`)
  await page.waitForFunction(() => performance.getEntriesByName('trees:explore:load').length > 0, null, { timeout: 180_000 })
  const loadedAfterMs = Date.now() - started
  await page.waitForTimeout(8000)
  const isolated = await page.evaluate(() => crossOriginIsolated)
  if (!isolated) console.log('(not cross-origin isolated: renderer memory only; serve with COOP/COEP headers for the per-realm breakdown)')
  async function report(label) {
    console.log(`--- ${label}: renderer private ${await rendererPrivateMb()} MB (peak so far ${peak} MB)`)
    if (!isolated) return
    const rows = await page.evaluate(async () => {
      const m = await performance.measureUserAgentSpecificMemory()
      return m.breakdown.filter(b => b.bytes > 1048576)
        .map(b => ({ mb: Math.round(b.bytes / 1048576), types: b.types.join('+'), scope: b.attribution.map(a => `${a.scope}:${(a.url || '').split('/').pop()?.slice(0, 40)}`).join(',') }))
        .sort((a, b) => b.mb - a.mb)
    })
    for (const row of rows) console.log(`   ${String(row.mb).padStart(5)} MB  ${row.types.padEnd(22)} ${row.scope}`)
  }
  console.log(`${layout} ${city}: trees loaded ${(loadedAfterMs / 1000).toFixed(1)} s after navigation`)
  await report('loaded')
  for (const zoom of zooms) {
    await page.evaluate(([lat, lng, zoom]) => window.__treeMap.jumpTo({ center: [lng, lat], zoom }), [latitude, longitude, zoom])
    await page.waitForFunction(() => { const m = window.__treeMap; return m && !m.isMoving() && m.areTilesLoaded() }, null, { timeout: 60_000 }).catch(() => {})
    await page.waitForTimeout(4000)
    await report(`settled at z${zoom}`)
  }
  const rows = [...bytes.entries()].map(([url, v]) => ({ url, mb: v.body / 1e6, requests: v.requests })).sort((a, b) => b.mb - a.mb)
  const remote = rows.filter((r) => !r.url.startsWith(base.replace(/\/$/, ''))).reduce((sum, r) => sum + r.mb, 0)
  console.log(`--- bytes: ${remote.toFixed(1)} MB from remote hosts`)
  for (const row of rows.slice(0, 12)) {
    console.log(`   ${row.mb.toFixed(2).padStart(8)} MB  ${String(row.requests).padStart(3)} req  ${row.url.replace('https://storage.googleapis.com/trilogy_public_models/duckdb/', 'gcs:').slice(0, 100)}`)
  }
} finally {
  clearInterval(sampler)
  await browser.close()
}
