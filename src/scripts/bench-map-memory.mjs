// Where the browser's memory and bytes go while the map loads a city and
// zooms in. Run against a production preview or deployed URL:
//
//   pnpm preview --port 6180            # in one terminal, after pnpm build
//   MAP_BENCH_URL=http://localhost:6180/ MAP_BENCH_CITY=USNYC pnpm bench:memory
//
// Reports, per step, the renderer process's private memory (the page and
// its dedicated workers, DuckDB included, share one renderer) and, at the
// end, the bytes each remote file cost, counted from the worker's requests.
// MAP_BENCH_RANGES=<url substring> also prints each matching request as it
// finishes: seconds since start, method, Range header and size.
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
// Pans after the zoom walk, each a screen east at the walk's first zoom: the
// phone's window loads the new ground and drops the old (cityWindow.ts).
const pans = Number(process.env.MAP_BENCH_PANS ?? 0)
const executablePath = process.env.MAP_BENCH_CHROMIUM
// Serve the app's own responses cross-origin isolated, so the per-realm
// breakdown (performance.measureUserAgentSpecificMemory) is available without
// a special server. The worker ships no threaded DuckDB bundle, so isolation
// does not change which engine runs.
const isolate = process.env.MAP_BENCH_ISOLATE === '1'
const sampleMs = Number(process.env.MAP_BENCH_SAMPLE_MS ?? 250)
// Chat tool calls after the load, the same path an agent's call takes
// (compiled by the live resolver, run in the map's DuckDB worker): the chat
// reads the published parquets, not the phone's window, so its peaks land
// on the same heap. Needs the chat panel mounted once (it registers the
// handle), which the bench does by opening it. MAP_BENCH_CHAT=1 runs every
// step; a comma list of keys (aggregate,rows,publish,colour) runs those, so
// one step can be measured from a fresh tab.
const chatKeys = (process.env.MAP_BENCH_CHAT ?? '').split(',').filter(Boolean)
const ALL_CHAT_STEPS = [
  ['aggregate', 'run_query', 'aggregate', { query: `select species, count(tree_id) as trees where city = '${city}' order by trees desc limit 10;` }],
  ['rows', 'run_query', 'every row', { query: `select tree_id, species, latitude, longitude where city = '${city}';` }],
  ['publish', 'publish_results', 'every tree', { query: `select tree_id where city = '${city}';` }],
  ['colour', 'publish_results', 'every tree, coloured', { query: `select tree_id, case when diameter_at_breast_height >= 20 then '#FF69B4' else '#4169E1' end as override_color where city = '${city}';`, color_labels: [{ color: '#FF69B4', label: 'DBH 20+' }, { color: '#4169E1', label: 'DBH < 20' }] }],
]
const CHAT_STEPS = ALL_CHAT_STEPS.filter(([key]) => chatKeys.includes('1') || chatKeys.includes(key))

// The headless shell has no measureUserAgentSpecificMemory; isolated runs
// use the full browser's headless mode, which does.
const browser = await chromium.launch({ executablePath, channel: isolate && !executablePath ? 'chromium' : undefined, args: ['--no-sandbox', '--no-zygote'] })
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
const origin = new URL(base).origin
if (isolate) {
  await page.route(`${origin}/**`, async (route) => {
    const response = await route.fetch()
    await route.fulfill({
      response,
      headers: {
        ...response.headers(),
        'cross-origin-opener-policy': 'same-origin',
        'cross-origin-embedder-policy': 'credentialless',
      },
    })
  })
}

// The worker's [Perf] lines, with seconds since navigation, so a step's peak
// can be read against the queries that ran during it.
let navigatedAt = Date.now()
const perfLines = []
page.on('console', async (message) => {
  if (!message.text().startsWith('[Perf]')) return
  const at = ((Date.now() - navigatedAt) / 1000).toFixed(1).padStart(6)
  // An object argument prints as a handle; read its value instead.
  const parts = await Promise.all(message.args().map((arg) => arg.jsonValue().catch(() => String(arg))))
  const text = parts.map((part) => typeof part === 'string' ? part : JSON.stringify(part)).join(' ')
  perfLines.push(`${at} s  ${text.slice(0, 220)}`)
})

// Bytes per remote file. Playwright reports the requests of nested workers
// (the DuckDB worker is spawned by the pipeline worker) on the page.
const bytes = new Map()
const benchStart = Date.now()
page.on('requestfinished', async (request) => {
  try {
    const sizes = await request.sizes()
    const key = request.url().split('?')[0]
    const entry = bytes.get(key) ?? { body: 0, requests: 0 }
    entry.body += sizes.responseBodySize
    entry.requests += 1
    bytes.set(key, entry)
    if (process.env.MAP_BENCH_RANGES && key.includes(process.env.MAP_BENCH_RANGES)) {
      const range = (await request.allHeaders())['range'] ?? 'whole'
      console.log(`   range ${((Date.now() - benchStart) / 1000).toFixed(1)} s ${request.method()} ${range} ${(sizes.responseBodySize / 1e6).toFixed(2)} MB`)
    }
  } catch { /* the response was gone before it could be sized */ }
})

// Peak over the whole run and over the current step. A sample is skipped
// while the previous one is still reading the OS, so the interval is a floor.
let peak = 0
let stepPeak = 0
let stepStartedAt = Date.now()
/** This step's samples: [seconds since the step started, MB]. */
let stepSamples = []
let sampling = false
const sampler = setInterval(() => {
  if (sampling) return
  sampling = true
  rendererPrivateMb().then((mb) => {
    peak = Math.max(peak, mb)
    stepPeak = Math.max(stepPeak, mb)
    stepSamples.push([(Date.now() - stepStartedAt) / 1000, mb])
  }).catch(() => {}).finally(() => { sampling = false })
}, sampleMs)

/** The step's memory over time, one figure per second, and when it peaked. */
function stepTimeline() {
  const perSecond = []
  for (const [t, mb] of stepSamples) perSecond[Math.floor(t)] = Math.max(perSecond[Math.floor(t)] ?? 0, mb)
  const top = stepSamples.reduce((best, sample) => sample[1] > best[1] ? sample : best, [0, 0])
  return `peak at +${top[0].toFixed(1)} s; per second: ${perSecond.map((mb) => mb ?? '-').join(' ')}`
}

try {
  const started = Date.now()
  navigatedAt = started
  await page.goto(`${base.replace(/#.*$/, '')}#/?city=${city}&mode=explore`)
  await page.waitForFunction(() => performance.getEntriesByName('trees:explore:load').length > 0, null, { timeout: 180_000 })
  const loadedAfterMs = Date.now() - started
  await page.waitForTimeout(8000)
  const isolated = await page.evaluate(() => crossOriginIsolated && typeof performance.measureUserAgentSpecificMemory === 'function')
  if (!isolated) console.log('(no per-realm breakdown: renderer memory only; set MAP_BENCH_ISOLATE=1 for it)')
  async function report(label) {
    console.log(`--- ${label}: renderer private ${await rendererPrivateMb()} MB (step peak ${stepPeak} MB, peak so far ${peak} MB)`)
    console.log(`   ${stepTimeline()}`)
    for (const line of perfLines.splice(0)) console.log(`   ${line}`)
    if (isolated) await breakdown()
    // The breakdown allocates too; the next step's peak starts after it.
    stepPeak = 0
    stepSamples = []
    stepStartedAt = Date.now()
  }
  async function breakdown() {
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
  if (CHAT_STEPS.length > 0) {
    await page.locator('[data-testid="mobile-action-chat"], [aria-label$="chat"]').first().click()
    await page.waitForFunction(() => typeof window.__treeChatTool === 'function', null, { timeout: 30_000 })
    await page.locator('[data-testid="overlay-close"]').click().catch(() => {})
    for (const [, tool, label, input] of CHAT_STEPS) {
      const t0 = Date.now()
      const result = await page.evaluate(async ([tool, input]) => {
        const r = await window.__treeChatTool(tool, input)
        return { success: r.success, text: String(r.message ?? r.error ?? '').slice(0, 160) }
      }, [tool, input])
      await page.waitForTimeout(4000)
      console.log(`   ${tool} (${label}): ${((Date.now() - t0 - 4000) / 1000).toFixed(1)} s, ${result.success ? 'ok' : 'FAILED'}: ${result.text}`)
      await report(`chat ${tool}: ${label}`)
    }
  }
  for (const zoom of zooms) {
    // A block body: jumpTo returns the map, and an evaluate that returns it
    // has Playwright serialize the whole object graph (tile buffers as
    // base64), a 600-850 MB transient that is the bench's, not the page's.
    await page.evaluate(([lat, lng, zoom]) => { window.__treeMap.jumpTo({ center: [lng, lat], zoom }) }, [latitude, longitude, zoom])
    await page.waitForFunction(() => { const m = window.__treeMap; return m && !m.isMoving() && m.areTilesLoaded() }, null, { timeout: 60_000 }).catch(() => {})
    await page.waitForTimeout(4000)
    await report(`settled at z${zoom}`)
  }
  for (let i = 1; i <= pans; i += 1) {
    await page.evaluate(([zoom]) => {
      const map = window.__treeMap
      map.jumpTo({ zoom })
      const b = map.getBounds()
      const c = map.getCenter()
      map.jumpTo({ center: [c.lng + (b.getEast() - b.getWest()), c.lat] })
    }, [zooms[0] ?? 13])
    await page.waitForFunction(() => { const m = window.__treeMap; return m && !m.isMoving() && m.areTilesLoaded() }, null, { timeout: 60_000 }).catch(() => {})
    await page.waitForTimeout(4000)
    await report(`pan ${i} east at z${zooms[0] ?? 13}`)
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
