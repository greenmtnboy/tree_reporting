// What a phone waits for between tapping Near Me and seeing trees: every
// request in order (when it started, how long it took, its Range and size),
// the worker's [Perf] lines, and the time to the first drawn trees.
//
//   pnpm preview --port 6190            # in one terminal, after pnpm build
//   MAP_BENCH_URL=http://localhost:6190/ pnpm bench:nearby
//
// MAP_BENCH_LAT / MAP_BENCH_LNG place the phone (default: Boston's South End).
// MAP_BENCH_NET=4g|3g|none shapes every request, the workers' included: each
// is held for the round trip and its body's transfer time at the profile's
// rate, with six requests in flight at most as a browser allows one host.
// MAP_BENCH_CPU=<n> slows the page's main thread n times (not the workers:
// DuckDB runs unthrottled, so a phone's query time is longer than shown).
// MAP_BENCH_ENTRY_MS=<n> opens the entry screen, waits n ms as a viewer
// reading it does, then taps Near Me; the times are then also given from the
// tap. Without it the page opens straight into Near Me.
// MAP_BENCH_WATERFALL=0 prints the totals alone.
import { chromium, devices } from '@playwright/test'
import process from 'node:process'
import { gzipSync } from 'node:zlib'

const base = process.env.MAP_BENCH_URL ?? 'http://localhost:6173/'
const latitude = Number(process.env.MAP_BENCH_LAT ?? 42.3412)
const longitude = Number(process.env.MAP_BENCH_LNG ?? -71.0745)
const net = process.env.MAP_BENCH_NET ?? 'none'
const cpu = Number(process.env.MAP_BENCH_CPU ?? 1)
const waterfall = process.env.MAP_BENCH_WATERFALL !== '0'
const entryMs = Number(process.env.MAP_BENCH_ENTRY_MS ?? 0)
const settleMs = Number(process.env.MAP_BENCH_SETTLE_MS ?? 6000)
const PROFILES = { '4g': { mbps: 9, rttMs: 85 }, '3g': { mbps: 1.6, rttMs: 300 }, none: null }
const profile = PROFILES[net]
if (profile === undefined) throw new Error(`MAP_BENCH_NET is one of ${Object.keys(PROFILES).join(', ')}`)

const browser = await chromium.launch({ executablePath: process.env.MAP_BENCH_CHROMIUM, args: ['--no-sandbox', '--no-zygote'] })
const context = await browser.newContext({
  ...devices['iPhone 13 Mini'],
  geolocation: { latitude, longitude, accuracy: 20 },
  permissions: ['geolocation'],
})
const page = await context.newPage()

let t0 = Date.now()
const at = () => (Date.now() - t0) / 1000

// Shape the network in the route handler: the only place that sees the
// requests of a worker's worker (DuckDB's) as well as the page's.
// What a response costs on the wire. `route.fetch` hands back the decoded
// body, so a compressed response is charged its Content-Length; a preview
// server sends scripts and wasm uncompressed where the deployed site gzips
// them, so those are charged their gzipped size.
const gzipped = new Map()
function wireBytes(url, response, body) {
  const headers = response.headers()
  if (headers['content-encoding'] && Number(headers['content-length']) > 0) return Number(headers['content-length'])
  if (!headers['content-encoding'] && /\.(wasm|js|css|html|json)(\?|$)/.test(url) && new URL(url).origin === new URL(base).origin) {
    if (!gzipped.has(url)) gzipped.set(url, gzipSync(body, { level: 6 }).byteLength)
    return gzipped.get(url)
  }
  return body.byteLength
}
if (profile) {
  const perHost = new Map()
  const acquire = async (host) => {
    const slot = perHost.get(host) ?? { active: 0, waiting: [] }
    perHost.set(host, slot)
    if (slot.active >= 6) await new Promise((resolve) => slot.waiting.push(resolve))
    slot.active += 1
    return () => { slot.active -= 1; slot.waiting.shift()?.() }
  }
  await context.route('**/*', async (route) => {
    const release = await acquire(new URL(route.request().url()).host)
    try {
      const started = Date.now()
      const response = await route.fetch()
      const body = await response.body()
      const wanted = profile.rttMs + (wireBytes(route.request().url(), response, body) * 8) / (profile.mbps * 1e6) * 1000
      const wait = wanted - (Date.now() - started)
      if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait))
      await route.fulfill({ response, body })
    } catch {
      await route.abort().catch(() => {})
    } finally {
      release()
    }
  })
}
if (cpu > 1) {
  const cdp = await context.newCDPSession(page)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: cpu })
}

const perfLines = []
page.on('console', async (message) => {
  const text = message.text()
  if (!text.startsWith('[Perf]') && message.type() !== 'error') return
  const stamp = at()
  const parts = await Promise.all(message.args().map((arg) => arg.jsonValue().catch(() => String(arg))))
  perfLines.push([stamp, parts.map((part) => typeof part === 'string' ? part : JSON.stringify(part)).join(' ').slice(0, 260)])
})

const started = new Map()
const requests = []
page.on('request', (request) => started.set(request, at()))
page.on('requestfinished', async (request) => {
  const begin = started.get(request) ?? at()
  const end = at()
  let size = 0
  try { size = (await request.sizes()).responseBodySize } catch { /* gone */ }
  const headers = await request.allHeaders().catch(() => ({}))
  requests.push({ begin, end, method: request.method(), range: headers.range ?? '', size, url: request.url() })
})
page.on('requestfailed', (request) => {
  requests.push({ begin: started.get(request) ?? at(), end: at(), method: request.method(), range: 'FAILED', size: 0, url: request.url() })
})

function shortUrl(url) {
  const u = new URL(url)
  const tail = u.pathname.split('/').slice(-2).join('/')
  return `${u.host.replace(/^www\./, '').slice(0, 28)} …/${tail.slice(-58)}`
}

try {
  t0 = Date.now()
  let tapAt = 0
  if (entryMs > 0) {
    await page.goto(base.replace(/#.*$/, ''))
    const nearMe = page.getByRole('button', { name: 'Near Me' })
    await nearMe.waitFor({ timeout: 60_000 })
    await page.waitForTimeout(entryMs)
    tapAt = at()
    await nearMe.click()
  } else {
    await page.goto(`${base.replace(/#.*$/, '')}#/?mode=nearby`)
  }
  await page.waitForFunction(() => performance.getEntriesByName('trees:nearby:load').length > 0, null, { timeout: 240_000 })
  const firstTrees = at()
  // Trees on screen, not the tile source's first event: rendered tree features.
  const drawn = await page.waitForFunction(() => {
    const map = window.__treeMap
    if (!map) return false
    const layers = map.getStyle().layers.filter((l) => l.source === 'trees').map((l) => l.id)
    return layers.length > 0 && map.queryRenderedFeatures(undefined, { layers }).length > 0
  }, null, { timeout: 120_000 }).then(() => at()).catch(() => null)
  await page.waitForTimeout(settleMs)
  const marks = await page.evaluate(() => ({
    location: performance.getEntriesByName('trees:location')[0]?.duration ?? null,
    mapStart: performance.getEntriesByName('trees:nearby:start')[0]?.startTime ?? null,
    load: performance.getEntriesByName('trees:nearby:load')[0]?.duration ?? null,
    zoom: window.__treeMap?.getZoom(),
    rendered: (() => {
      const map = window.__treeMap
      const layers = map.getStyle().layers.filter((l) => l.source === 'trees').map((l) => l.id)
      return map.queryRenderedFeatures(undefined, { layers }).length
    })(),
  }))

  console.log(`near me at ${latitude}, ${longitude}; network ${net}${profile ? ` (${profile.mbps} Mbps, ${profile.rttMs} ms)` : ''}; main thread x${cpu}`)
  console.log(`  location fix            ${marks.location === null ? '-' : (marks.location / 1000).toFixed(2)} s`)
  console.log(`  map mounted at          ${marks.mapStart === null ? '-' : (marks.mapStart / 1000).toFixed(2)} s`)
  console.log(`  tree source first event ${firstTrees.toFixed(2)} s after navigation (${marks.load === null ? '-' : (marks.load / 1000).toFixed(2)} s after the map mounted)`)
  console.log(`  trees on screen         ${drawn === null ? 'never' : `${drawn.toFixed(2)} s`} (${marks.rendered} features at z${marks.zoom?.toFixed(1)})`)
  if (entryMs > 0) console.log(`  Near Me tapped at       ${tapAt.toFixed(2)} s, after ${entryMs} ms on the entry screen: trees ${drawn === null ? 'never' : `${(drawn - tapAt).toFixed(2)} s`} after the tap`)

  const until = drawn ?? firstTrees
  const groups = new Map()
  for (const r of requests) {
    const u = new URL(r.url)
    const kind = /world\/v1\/blocks\//.test(u.pathname) ? 'world blocks'
      : /world\/v1\/vocab/.test(u.pathname) ? 'world vocab'
        : /world\/v1\/manifest/.test(u.pathname) ? 'world manifest'
          : /_tree_browser_v/.test(u.pathname) ? 'CITY projection (tree_browser)'
            : /_tree_info_v|full_tree_info/.test(u.pathname) ? 'CITY full tree_info'
              : /tree_enrichment/.test(u.pathname) ? 'species enrichment'
                : /landmark/.test(u.pathname) ? 'landmarks'
                  : /ecoregion/.test(u.pathname) ? 'ecoregion'
                    : /duckdb.*\.wasm|\.duckdb_extension/.test(u.pathname) ? 'duckdb wasm + extensions'
                      : u.host === new URL(base).host ? 'app bundle'
                        : u.host
    const g = groups.get(kind) ?? { before: 0, beforeBytes: 0, after: 0, afterBytes: 0 }
    if (r.begin <= until) { g.before += 1; g.beforeBytes += r.size } else { g.after += 1; g.afterBytes += r.size }
    groups.set(kind, g)
  }
  console.log(`\n  requests before trees were on screen (${until.toFixed(1)} s), and after:`)
  let total = 0, totalBytes = 0
  for (const [kind, g] of [...groups].sort((a, b) => b[1].beforeBytes - a[1].beforeBytes)) {
    total += g.before; totalBytes += g.beforeBytes
    console.log(`  ${kind.padEnd(34)} ${String(g.before).padStart(4)} req ${(g.beforeBytes / 1e6).toFixed(2).padStart(7)} MB   | after ${String(g.after).padStart(3)} req ${(g.afterBytes / 1e6).toFixed(2).padStart(6)} MB`)
  }
  console.log(`  ${'total'.padEnd(34)} ${String(total).padStart(4)} req ${(totalBytes / 1e6).toFixed(2).padStart(7)} MB`)

  if (waterfall) {
    console.log('\n  waterfall (start s, duration ms, method, bytes, range, url); [Perf] lines inline:')
    const rows = [
      ...requests.map((r) => [r.begin, `${r.begin.toFixed(2).padStart(7)} ${String(Math.round((r.end - r.begin) * 1000)).padStart(6)} ms ${r.method.padEnd(4)} ${String(r.size).padStart(9)} ${r.range.padEnd(24)} ${shortUrl(r.url)}`]),
      ...perfLines.map(([stamp, text]) => [stamp, `${stamp.toFixed(2).padStart(7)}        >> ${text}`]),
    ].sort((a, b) => a[0] - b[0])
    for (const [, line] of rows) console.log(`  ${line}`)
  }
} finally {
  await browser.close()
}
