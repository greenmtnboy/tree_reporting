// Run with `pnpm dev --port 6174` in another terminal. Uses the deterministic
// renderer fixture, so this measures CPU/batch churn, not network or live SQL.
import { chromium } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import process from 'node:process'

const browser = await chromium.launch()
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } })
  await page.goto('http://localhost:6174/renderer-tests/crowns.html')
  await page.waitForFunction(() => window.crownTest?.layer.count > 0 && !window.crownTest.layer.inFlight)
  const results = await page.evaluate(async () => {
    const state = window.crownTest
    const pause = () => new Promise(resolve => setTimeout(resolve, 400))
    await pause()
    let before = state.calls
    for (let i = 0; i < 5; i++) {
      state.map.fire('sourcedata', { sourceId: 'trees', isSourceLoaded: true })
      await pause()
    }
    const tileEventQueries = state.calls - before
    before = state.calls
    for (let i = 0; i < 5; i++) {
      state.map.panBy([10, 0], { duration: 0 })
      await pause()
    }
    const smallPanQueries = state.calls - before
    const camera = state.layer.camera()
    const dense = Array.from({ length: 32768 }, (_, i) => ({
      ...state.crowns[i % 3], id: `bench-${i}`, category: 'broadleaf', dbh: 18,
      lng: camera.lng + (i % 181 - 90) * 0.00001,
      lat: camera.lat + (Math.floor(i / 181) - 90) * 0.00001,
    }))
    const batchMs = []
    for (let i = 0; i < 7; i++) {
      const start = performance.now()
      state.layer.setCrowns(dense, camera)
      batchMs.push(performance.now() - start)
    }
    const sorted = batchMs.slice(2).sort((a, b) => a - b)
    return { tileEventQueries, smallPanQueries, batchMs, medianBatchMs: sorted[2], gpuBufferBytes: state.layer.data.byteLength }
  })
  console.log(JSON.stringify(results, null, 2))
  await mkdir('bench-results', { recursive: true })
  await writeFile(`bench-results/map-renderer-${process.env.MAP_BENCH_LABEL ?? 'latest'}.json`, JSON.stringify(results, null, 2))
} finally {
  await browser.close()
}
