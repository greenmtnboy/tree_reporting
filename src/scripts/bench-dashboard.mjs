// What the summary dashboard costs a phone on first open: bytes per remote
// file, resolver round trips, renderer memory, and how long until its queries
// stop. Run against a preview or the deployed site:
//
//   DASH_BENCH_URL=http://localhost:6180/ DASH_BENCH_CITY=USBOS pnpm bench:dashboard
//
// DASH_BENCH_CITY='' opens the no-city (all cities) view. "Settled" is the
// first moment no data or resolver request has been in flight for
// DASH_BENCH_QUIET_MS (default 5000). Memory is the largest renderer's
// private bytes, read from the OS as in bench-map-memory.mjs.
import { chromium, devices } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import process from 'node:process'

const base = process.env.DASH_BENCH_URL ?? 'http://localhost:6180/'
const city = process.env.DASH_BENCH_CITY ?? 'USBOS'
const layout = process.env.DASH_BENCH_LAYOUT ?? 'mobile'
const quietMs = Number(process.env.DASH_BENCH_QUIET_MS ?? 5000)
const timeoutMs = Number(process.env.DASH_BENCH_TIMEOUT_MS ?? 180000)

const browser = await chromium.launch({ args: ['--no-sandbox', '--no-zygote'] })
const browserCdp = await browser.newBrowserCDPSession()

async function rendererPrivateMb() {
  const { processInfo } = await browserCdp.send('SystemInfo.getProcessInfo')
  const pids = processInfo.filter((p) => p.type === 'renderer').map((p) => p.id)
  if (pids.length === 0) return 0
  let best = 0
  if (process.platform === 'win32') {
    const script = `Get-Process -Id ${pids.join(',')} -ErrorAction SilentlyContinue | ForEach-Object { $_.PrivateMemorySize64 }`
    const out = execFileSync('powershell.exe', ['-NoProfile', '-Command', script], { encoding: 'utf8' })
    for (const line of out.split(/\r?\n/)) best = Math.max(best, Number(line) || 0)
    return Math.round(best / 1048576)
  }
  const out = execFileSync('ps', ['-o', 'pid=,rss=', '-p', pids.join(',')], { encoding: 'utf8' })
  for (const line of out.split('\n')) best = Math.max(best, Number(line.trim().split(/\s+/)[1]) || 0)
  return Math.round(best / 1024)
}

const context = await browser.newContext(layout === 'mobile'
  ? { ...devices['iPhone 13'] }
  : { viewport: { width: 1400, height: 900 } })
const page = await context.newPage()

// Data files and the resolver are what the dashboard waits on; the app's own
// assets are not counted towards "settled".
const isData = (url) => /\.parquet|generate_quer|trilogy-service/.test(url)
let inFlight = 0
let lastActivity = Date.now()
const bytes = new Map()
const start = Date.now()
page.on('request', (request) => {
  if (!isData(request.url())) return
  inFlight += 1
  lastActivity = Date.now()
})
const done = async (request, failed) => {
  if (!isData(request.url())) return
  inFlight -= 1
  lastActivity = Date.now()
  const key = request.url().split('?')[0]
  const entry = bytes.get(key) ?? { body: 0, requests: 0, failed: 0 }
  entry.requests += 1
  if (failed) entry.failed += 1
  else {
    try { entry.body += (await request.sizes()).responseBodySize } catch { /* gone */ }
  }
  bytes.set(key, entry)
}
page.on('requestfinished', (r) => done(r, false))
page.on('requestfailed', (r) => done(r, true))
const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)) })

// DASH_BENCH_CLEAR=1 then presses "Clear all" and measures the no-city view
// as a second step: the app has no URL for it, an empty `city` follows the map.
const url = `${base.replace(/\/?$/, '/')}#/summary${city ? `?city=${city}` : ''}`
let peak = 0
const sampler = setInterval(async () => { try { peak = Math.max(peak, await rendererPrivateMb()) } catch { /* closing */ } }, 500)

async function settle(label, stepStart) {
  while (Date.now() - stepStart < timeoutMs) {
    await page.waitForTimeout(250)
    if (inFlight <= 0 && Date.now() - lastActivity >= quietMs && bytes.size > 0) break
  }
  const settledS = ((lastActivity - stepStart) / 1000).toFixed(1)
  const settledMb = await rendererPrivateMb()
  const rows = [...bytes.entries()].sort((a, b) => b[1].body - a[1].body)
  const mib = (n) => (n / 1048576).toFixed(2)
  let total = 0
  console.log(`${label} (${layout})`)
  console.log(`settled after ${settledS} s, renderer ${settledMb} MB settled, ${peak} MB peak`)
  for (const [key, entry] of rows) {
    total += entry.body
    console.log(`  ${mib(entry.body).padStart(8)} MiB  ${String(entry.requests).padStart(4)} req${entry.failed ? ` (${entry.failed} failed)` : ''}  ${key.replace(/^https?:\/\/[^/]+/, '')}`)
  }
  console.log(`  ${mib(total).padStart(8)} MiB total`)
  if (errors.length) console.log(`console errors (${errors.length}):\n  ${errors.slice(0, 5).join('\n  ')}`)
}

await page.goto(url, { waitUntil: 'load' })
await settle(url, start)
if (process.env.DASH_BENCH_CLEAR === '1') {
  bytes.clear()
  errors.length = 0
  peak = 0
  const stepStart = Date.now()
  lastActivity = stepStart
  // The first-visit welcome overlay covers the page; dispatch past it.
  await page.locator('.clear-all-btn').dispatchEvent('click')
  await settle('no city, after Clear all', stepStart)
}
clearInterval(sampler)
await browser.close()
