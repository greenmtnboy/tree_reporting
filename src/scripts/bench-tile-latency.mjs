// How long the map takes to settle after each zoom or pan, and how long its
// tiles trickle in while it does: the "popping" a user sees at the far zooms
// and the "snappiness" of a zoom in.
//
//   pnpm build && pnpm preview --port 6180      # in another terminal
//   MAP_BENCH_URL=http://localhost:6180/ MAP_BENCH_CITY=USBOS pnpm bench:tiles
//
// Each step jumps the camera (zN: to zoom N at the city centre; pan: 40% of
// the view sideways at the current zoom) and then waits until MapLibre has
// every tile for the view. Per step it prints the time to that settled
// state, the time to the map's next `idle`, how many tree tiles arrived and
// the span between the first and the last of them: a long span with many
// tiles is tiles popping in one after another. Run each build twice; tile
// timings move by a few hundred ms between identical runs.
//
// MAP_BENCH_LAYOUT=desktop (default) waits for the intro to land first.
import { execFileSync } from 'node:child_process'
import { chromium, devices } from '@playwright/test'
import process from 'node:process'

const base = process.env.MAP_BENCH_URL ?? 'http://localhost:6173/'
const city = process.env.MAP_BENCH_CITY ?? 'USBOS'
const layout = process.env.MAP_BENCH_LAYOUT ?? 'desktop'
const latitude = Number(process.env.MAP_BENCH_LAT ?? 42.3601)
const longitude = Number(process.env.MAP_BENCH_LNG ?? -71.0589)
const steps = (process.env.MAP_BENCH_STEPS ?? 'z11,pan,pan,z12,pan,z13,pan,z14,z15,pan,z16,z17,pan,z18').split(',').map((s) => s.trim()).filter(Boolean)
const executablePath = process.env.MAP_BENCH_CHROMIUM
const SETTLE_TIMEOUT_MS = 60_000

const browser = await chromium.launch({ executablePath, args: ['--no-sandbox', '--no-zygote'] })
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
  for (const line of out.split('\n')) {
    const [, rss] = line.trim().split(/\s+/)
    best = Math.max(best, Number(rss) || 0)
  }
  return Math.round(best / 1024)
}

const context = await browser.newContext(layout === 'mobile'
  ? { ...devices['iPhone 13'] }
  : { viewport: { width: 1400, height: 900 } })
await context.addInitScript(() => { localStorage.setItem('sf_trees_welcome_dismissed', '1') })
const page = await context.newPage()

// The worker's own [Perf] lines, stamped with seconds since navigation, so a
// slow step can be read against the batch or prewarm that was running.
const navigatedAt = Date.now()
const perfLines = []
function onConsole(msg) {
  const text = msg.text()
  if (!text.includes('[Perf]')) return
  perfLines.push(`${((Date.now() - navigatedAt) / 1000).toFixed(1).padStart(6)} s  ${text.slice(0, 160)}`)
}
// Worker console lines arrive on the page's console event too.
page.on('console', onConsole)

let peak = 0
const sampler = setInterval(() => { rendererPrivateMb().then((mb) => { peak = Math.max(peak, mb) }).catch(() => {}) }, 1000)

/** Jump the camera and wait for the view's tiles; timings are the page's own clock. */
async function runStep(step) {
  return page.evaluate(async ([step, lat, lng, timeoutMs]) => {
    const map = window.__treeMap
    const tiles = []
    const onData = (e) => { if (e.sourceId === 'trees' && e.tile) tiles.push(performance.now()) }
    map.on('sourcedata', onData)
    let idleAt = null
    map.once('idle', () => { idleAt = performance.now() })
    const t0 = performance.now()
    if (step === 'pan') {
      const b = map.getBounds()
      const c = map.getCenter()
      map.jumpTo({ center: [c.lng + (b.getEast() - b.getWest()) * 0.4, c.lat] })
    } else {
      map.jumpTo({ center: [lng, lat], zoom: Number(step.slice(1)) })
    }
    // Tiles for the new view are requested on the next render, so the loaded
    // flags still describe the old view until then.
    await new Promise((resolve) => map.once('render', resolve))
    let settledAt = null
    while (performance.now() - t0 < timeoutMs) {
      if (!map.isMoving() && map.areTilesLoaded() && map.isSourceLoaded('trees')) { settledAt = performance.now(); break }
      await new Promise((r) => setTimeout(r, 50))
    }
    // Let a late idle land, then stop listening.
    const idleDeadline = performance.now() + 3000
    while (idleAt === null && performance.now() < idleDeadline) await new Promise((r) => setTimeout(r, 50))
    map.off('sourcedata', onData)
    return {
      zoom: Math.round(map.getZoom() * 10) / 10,
      settledMs: settledAt === null ? null : Math.round(settledAt - t0),
      idleMs: idleAt === null ? null : Math.round(idleAt - t0),
      tiles: tiles.length,
      spanMs: tiles.length ? Math.round(tiles[tiles.length - 1] - tiles[0]) : 0,
      firstTileMs: tiles.length ? Math.round(tiles[0] - t0) : null,
    }
  }, [step, latitude, longitude, SETTLE_TIMEOUT_MS])
}

try {
  const started = Date.now()
  await page.goto(`${base.replace(/#.*$/, '')}#/?city=${city}&mode=explore`)
  await page.waitForFunction(
    (code) => document.querySelector('.tree-map')?.getAttribute('data-trees-loaded-for') === code,
    city,
    { timeout: 180_000 },
  )
  const loadedAfterMs = Date.now() - started
  if (layout !== 'mobile') {
    // The desktop intro flies out to the city view; measure from a still map.
    await page.waitForFunction(() => { const m = window.__treeMap; return !!m && m.getZoom() <= 13.55 }, null, { timeout: 90_000 }).catch(() => {})
  }
  await page.waitForFunction(() => { const m = window.__treeMap; return m && !m.isMoving() && m.areTilesLoaded() }, null, { timeout: 60_000 }).catch(() => {})
  await page.waitForTimeout(3000)
  console.log(`${layout} ${city}: trees loaded ${(loadedAfterMs / 1000).toFixed(1)} s after navigation; still at z${await page.evaluate(() => Math.round(window.__treeMap.getZoom() * 10) / 10)} after ${((Date.now() - started) / 1000).toFixed(1)} s; renderer ${await rendererPrivateMb()} MB`)
  console.log('step    zoom  settled   idle  tiles  first  span   renderer')
  const results = []
  for (const step of steps) {
    const r = await runStep(step)
    const mb = await rendererPrivateMb()
    results.push({ step, ...r, mb })
    const f = (v, w) => String(v ?? '-').padStart(w)
    console.log(`${step.padEnd(6)} ${f(r.zoom, 5)} ${f(r.settledMs, 8)} ${f(r.idleMs, 6)} ${f(r.tiles, 6)} ${f(r.firstTileMs, 6)} ${f(r.spanMs, 5)} ${f(mb, 7)} MB`)
  }
  const settled = results.map((r) => r.settledMs).filter((v) => v !== null)
  console.log(`--- total settle ${settled.reduce((a, b) => a + b, 0)} ms over ${settled.length} steps; renderer peak ${peak} MB`)
  if (process.env.MAP_BENCH_PERF_LINES !== '0' && perfLines.length) {
    console.log('--- [Perf] lines (seconds since navigation)')
    for (const line of perfLines) console.log(line)
  }
} finally {
  clearInterval(sampler)
  await browser.close()
}
