import { ref, type Ref } from 'vue'
import maplibregl from 'maplibre-gl'

// --- Exported constants ---

export const INTRO_START_ZOOM = 18.5
export const INTRO_END_ZOOM = 13.5

// --- Internal constants ---

const INTRO_CENTER: [number, number] = [-122.4194, 37.7749]
const INTRO_DURATION_MS = 10_000
const INTRO_ROTATION_DEG = 240
const CENTER_ICON_GRID_RADIUS_PX = 96
const CENTER_ICON_GRID_SIZE = 3
const CENTER_ICON_GRID_MIN_POPULATED_CELLS = 5
const CENTER_ICON_GRID_MIN_TOTAL_ICONS = 8
const VIEWPORT_TREE_MIN_FEATURES = 1
const VIEWPORT_TREE_STABLE_FRAMES = 2
const TREES_SOURCE_MAXZOOM = 16

export type IntroPrefetchStatus = 'executed' | 'deduped' | 'skipped'

type IntroPrefetchCounters = { requested: number; executed: number; deduped: number; skipped: number }

export interface TileRange {
  minX: number
  maxX: number
  minY: number
  maxY: number
}

export interface UseMapIntroAnimationOptions {
  map: Ref<maplibregl.Map | null>
  simplified: boolean
  /** Owned by the component; the composable sets it to false when animation ends. */
  introActive: Ref<boolean>
  /** Shared map of zoom → locked tile range used during intro prefetch. */
  introLockedRangeByZoom: Map<number, TileRange>
  setIntroComplete: () => void
  setAutoTileFetchEnabled: (enabled: boolean) => void
  setVisibleTileRange: (z: number, minX: number, maxX: number, minY: number, maxY: number) => void
  prefetchVisibleDetailTilesAtZoom: (z: number, range?: TileRange) => Promise<IntroPrefetchStatus>
  requestTreesSourceReload: () => void
  forceTreesTileRefetchPass: () => void
  updateZoomLevel: () => void
  computeVisibleTileRangeForZoom: (z: number) => TileRange | null
  setMapInteractions: (enabled: boolean) => void
}

export function useMapIntroAnimation({
  map,
  introActive,
  introLockedRangeByZoom,
  setIntroComplete,
  setAutoTileFetchEnabled,
  setVisibleTileRange,
  prefetchVisibleDetailTilesAtZoom,
  requestTreesSourceReload,
  forceTreesTileRefetchPass,
  updateZoomLevel,
  computeVisibleTileRangeForZoom,
  setMapInteractions,
}: UseMapIntroAnimationOptions) {
  const loadingMessage = ref('Counting our conifers...')

  let introStarted = false
  let introRafId: number | null = null
  let introCancelled = false

  const introPrefetchStatsByZoom = new Map<number, IntroPrefetchCounters>()

  function nowMs(): number {
    return typeof performance !== 'undefined' ? performance.now() : Date.now()
  }

  function resetIntroPrefetchStats() {
    introPrefetchStatsByZoom.clear()
  }

  function recordIntroPrefetchStatus(z: number, status: IntroPrefetchStatus) {
    const current = introPrefetchStatsByZoom.get(z) ?? { requested: 0, executed: 0, deduped: 0, skipped: 0 }
    current.requested += 1
    if (status === 'executed') current.executed += 1
    else if (status === 'deduped') current.deduped += 1
    else current.skipped += 1
    introPrefetchStatsByZoom.set(z, current)
  }

  function logIntroPrefetchSummary() {
    const rows = Array.from(introPrefetchStatsByZoom.entries())
      .sort((a, b) => b[0] - a[0])
      .map(([z, s]) => ({ z, ...s }))
    const totals = rows.reduce(
      (acc, row) => {
        acc.requested += row.requested
        acc.executed += row.executed
        acc.deduped += row.deduped
        acc.skipped += row.skipped
        return acc
      },
      { requested: 0, executed: 0, deduped: 0, skipped: 0 },
    )
    console.info('[Perf] map:intro-prefetch-summary', { totals, byZoom: rows })
  }

  async function waitForTreesMilestone(timeoutMs = 2500): Promise<void> {
    if (!map.value) return
    if (map.value.isSourceLoaded('trees')) return
    await new Promise<void>((resolve) => {
      if (!map.value) return resolve()
      let done = false
      const finish = () => {
        if (done) return
        done = true
        map.value?.off('sourcedata', onSourceData)
        clearTimeout(timer)
        resolve()
      }
      const onSourceData = (e: any) => {
        if (e?.sourceId === 'trees' && e?.isSourceLoaded) finish()
      }
      const timer = setTimeout(finish, timeoutMs)
      map.value.on('sourcedata', onSourceData)
    })
  }

  async function waitForInitialDetailedTrees(timeoutMs = 5000): Promise<boolean> {
    if (!map.value) return false
    const startedAt = nowMs()
    return await new Promise<boolean>((resolve) => {
      if (!map.value) return resolve(false)
      const tick = () => {
        if (!map.value) return resolve(false)
        const iconCount = map.value.queryRenderedFeatures(undefined, { layers: ['trees-icon'] }).length
        if (iconCount > 0) return resolve(true)
        if (nowMs() - startedAt >= timeoutMs) return resolve(false)
        requestAnimationFrame(tick)
      }
      tick()
    })
  }

  async function waitForCenteredDetailedTrees(targetCenter: [number, number], timeoutMs = 5000): Promise<boolean> {
    if (!map.value) return false
    const startedAt = nowMs()
    return await new Promise<boolean>((resolve) => {
      if (!map.value) return resolve(false)
      const tick = () => {
        if (!map.value) return resolve(false)
        const centerPx = map.value.project(targetCenter)
        const gridSize = Math.max(1, CENTER_ICON_GRID_SIZE)
        const radiusPx = CENTER_ICON_GRID_RADIUS_PX
        const minX = centerPx.x - radiusPx
        const minY = centerPx.y - radiusPx
        const cellSize = (radiusPx * 2) / gridSize
        const centerIndex = Math.floor(gridSize / 2)
        let totalIcons = 0
        let populatedCells = 0
        let centerCellIcons = 0
        for (let row = 0; row < gridSize; row += 1) {
          for (let col = 0; col < gridSize; col += 1) {
            const cellMinX = minX + col * cellSize
            const cellMinY = minY + row * cellSize
            const cellIcons = map.value.queryRenderedFeatures(
              [[cellMinX, cellMinY], [cellMinX + cellSize, cellMinY + cellSize]],
              { layers: ['trees-icon'] },
            ).length
            totalIcons += cellIcons
            if (cellIcons > 0) populatedCells += 1
            if (row === centerIndex && col === centerIndex) centerCellIcons = cellIcons
          }
        }
        if (centerCellIcons > 0 && populatedCells >= CENTER_ICON_GRID_MIN_POPULATED_CELLS && totalIcons >= CENTER_ICON_GRID_MIN_TOTAL_ICONS) return resolve(true)
        if (nowMs() - startedAt >= timeoutMs) return resolve(false)
        requestAnimationFrame(tick)
      }
      tick()
    })
  }

  async function waitForViewportTreesRendered(timeoutMs = 6000): Promise<boolean> {
    if (!map.value) return false
    const startedAt = nowMs()
    return await new Promise<boolean>((resolve) => {
      if (!map.value) return resolve(false)
      let stableFrames = 0
      const tick = () => {
        if (!map.value) return resolve(false)
        const canvas = map.value.getCanvas()
        const width = Math.max(1, canvas.clientWidth)
        const height = Math.max(1, canvas.clientHeight)
        const features = map.value.queryRenderedFeatures(
          [[0, 0], [width, height]],
          { layers: ['trees-icon', 'trees-circle'] },
        )
        const frameReady = map.value.isSourceLoaded('trees') && features.length >= VIEWPORT_TREE_MIN_FEATURES
        if (frameReady) {
          stableFrames += 1
          if (stableFrames >= VIEWPORT_TREE_STABLE_FRAMES) return resolve(true)
        } else {
          stableFrames = 0
        }
        if (nowMs() - startedAt >= timeoutMs) return resolve(false)
        requestAnimationFrame(tick)
      }
      tick()
    })
  }

  function runIntroZoomSegment(
    fromZoom: number,
    toZoom: number,
    fromT: number,
    toT: number,
    durationMs: number,
    startBearing: number,
    pitch: number,
    center: [number, number],
    rotationDeg: number,
    onProgress?: (globalT: number) => void,
  ): Promise<void> {
    return new Promise((resolve) => {
      if (!map.value) return resolve()
      const [baseLng, baseLat] = center
      const segmentStart = nowMs()
      const step = () => {
        if (!map.value || introCancelled) {
          introRafId = null
          return resolve()
        }
        const local = Math.max(0, Math.min(1, (nowMs() - segmentStart) / durationMs))
        const easedLocal = local * local * (3 - 2 * local)
        const globalT = fromT + (toT - fromT) * easedLocal
        onProgress?.(globalT)
        const zoom = fromZoom + (toZoom - fromZoom) * easedLocal
        const bearing = startBearing + rotationDeg * globalT
        const angle = globalT * Math.PI * 2
        // Start/end at exact center to avoid a visible hop
        const radiusDeg = 0.0012 * Math.sin(globalT * Math.PI)
        const lng = baseLng + (Math.cos(angle) * radiusDeg) / Math.max(0.2, Math.cos((baseLat * Math.PI) / 180))
        const lat = baseLat + Math.sin(angle) * radiusDeg
        map.value.jumpTo({ center: [lng, lat], zoom, bearing, pitch })
        if (local < 1) {
          introRafId = requestAnimationFrame(step)
          return
        }
        introRafId = null
        resolve()
      }
      introRafId = requestAnimationFrame(step)
    })
  }

  async function runIntroZoomOut() {
    if (!map.value || introStarted) return
    introStarted = true
    introCancelled = false
    introActive.value = true
    resetIntroPrefetchStats()
    setAutoTileFetchEnabled(false)
    setMapInteractions(false)

    const startBearing = map.value.getBearing()
    const startPitch = map.value.getPitch()
    const introCenter: [number, number] = [INTRO_CENTER[0], INTRO_CENTER[1]]
    let didRunIntroMotion = false

    try {
      // Re-anchor to the exact intro start pose, then wait for centered icons
      // before beginning motion.
      map.value.jumpTo({ center: introCenter, zoom: INTRO_START_ZOOM, bearing: startBearing, pitch: startPitch })

      // Publish initial visible ranges before prefetching.
      updateZoomLevel()

      // Hard gate intro movement until detailed tiles are rendered at the starting viewpoint.
      const sourceStartZoom = Math.min(TREES_SOURCE_MAXZOOM, Math.round(INTRO_START_ZOOM))
      const prefetchZooms = Array.from(new Set([sourceStartZoom, Math.max(15, sourceStartZoom - 1)]))
      for (const z of prefetchZooms) {
        if (z < 15) continue
        const range = computeVisibleTileRangeForZoom(z)
        if (range) {
          introLockedRangeByZoom.set(z, range)
          setVisibleTileRange(z, range.minX, range.maxX, range.minY, range.maxY)
        }
        const status = await prefetchVisibleDetailTilesAtZoom(z, range ?? undefined)
        recordIntroPrefetchStatus(z, status)
      }

      // During intro auto-fetch is disabled; force a source reload so MapLibre
      // re-requests viewport tiles and consumes freshly prefetched cached data.
      requestTreesSourceReload()
      forceTreesTileRefetchPass()

      await waitForTreesMilestone(2500)
      const initialReady = await waitForInitialDetailedTrees(5000)
      const centeredReady = await waitForCenteredDetailedTrees(introCenter, 5000)
      const viewportReady = await waitForViewportTreesRendered(6000)
      const canStartMotion = initialReady || centeredReady || viewportReady

      if (!introCancelled && canStartMotion) {
        loadingMessage.value = 'Tracking seed dispersion...'
        let didSetFinalPhase = false
        await runIntroZoomSegment(
          INTRO_START_ZOOM, INTRO_END_ZOOM, 0, 1,
          INTRO_DURATION_MS, startBearing, startPitch,
          introCenter, INTRO_ROTATION_DEG,
          (globalT) => {
            if (!didSetFinalPhase && globalT >= 0.72) {
              didSetFinalPhase = true
              loadingMessage.value = 'Reticulating splines...'
            }
          },
        )
        didRunIntroMotion = true
      } else if (!introCancelled) {
        console.warn('[Perf] map:intro-gate:blocked-motion', { canStartMotion, initialReady, centeredReady, viewportReady })
      }
    } finally {
      setAutoTileFetchEnabled(true)
      if (map.value && didRunIntroMotion) {
        map.value.jumpTo({
          center: introCenter,
          zoom: INTRO_END_ZOOM,
          bearing: startBearing + INTRO_ROTATION_DEG,
          pitch: startPitch,
        })
      }
      introActive.value = false
      setIntroComplete()
      introLockedRangeByZoom.clear()
      logIntroPrefetchSummary()
      setMapInteractions(true)
    }
  }

  function cancelIntro() {
    introCancelled = true
    if (introRafId != null) {
      cancelAnimationFrame(introRafId)
      introRafId = null
    }
  }

  return {
    loadingMessage,
    runIntroZoomOut,
    cancelIntro,
    recordIntroPrefetchStatus,
  }
}
