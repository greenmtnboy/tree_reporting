// Serves dist/ for the benches the way the bucket answers: byte ranges
// exactly as GCS does (vite preview answers `bytes=0-0`, DuckDB-wasm's probe,
// with the whole file), and optionally a phone's link for the data files.
//
//   THROTTLE_MBPS=10 RTT_MS=70 node scripts/bench-serve.mjs dist 6180
//
// THROTTLE_MBPS / RTT_MS shape /local-trees/ only, as one shared link, so
// the app's own assets load at full speed. See docs/MOBILE_LOAD_BUDGET.md.
import { createServer } from 'node:http'
import { createReadStream, statSync } from 'node:fs'
import { join, extname, normalize } from 'node:path'
import process from 'node:process'

const root = process.argv[2]
const port = Number(process.argv[3] ?? 6180)
const bytesPerMs = Number(process.env.THROTTLE_MBPS ?? 0) * 1e6 / 8 / 1000
const rttMs = Number(process.env.RTT_MS ?? 0)
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png' }

let linkFreeAt = 0
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function send(res, file, opts, shaped) {
  if (!shaped || !bytesPerMs) return createReadStream(file, opts).pipe(res)
  const stream = createReadStream(file, { ...opts, highWaterMark: 64 * 1024 })
  for await (const chunk of stream) {
    const now = Date.now()
    linkFreeAt = Math.max(now, linkFreeAt) + chunk.length / bytesPerMs
    await sleep(linkFreeAt - now)
    if (!res.write(chunk)) await new Promise((r) => res.once('drain', r))
  }
  res.end()
}

createServer(async (req, res) => {
  let path = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  if (path.endsWith('/')) path += 'index.html'
  const shaped = path.startsWith('/local-trees/')
  let file = join(root, normalize(path))
  let size
  try { size = statSync(file).size } catch { file = join(root, 'index.html'); size = statSync(file).size }
  const headers = { 'Accept-Ranges': 'bytes', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache', 'Content-Type': types[extname(file)] ?? 'application/octet-stream' }
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? '')
  if (m) {
    const start = m[1] === '' ? size - Number(m[2]) : Number(m[1])
    const end = m[1] !== '' && m[2] !== '' ? Math.min(Number(m[2]), size - 1) : size - 1
    if (start > end || start >= size) { res.writeHead(416, { ...headers, 'Content-Range': `bytes */${size}` }); return res.end() }
    if (shaped && rttMs) await sleep(rttMs)
    res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${size}`, 'Content-Length': end - start + 1 })
    if (req.method === 'HEAD') return res.end()
    return send(res, file, { start, end }, shaped)
  }
  if (shaped && rttMs) await sleep(rttMs)
  res.writeHead(200, { ...headers, 'Content-Length': size })
  if (req.method === 'HEAD') return res.end()
  send(res, file, {}, shaped)
}).listen(port, () => console.log(`range server on ${port}`))
