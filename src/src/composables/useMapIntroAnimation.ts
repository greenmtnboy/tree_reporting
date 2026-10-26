import { ref, type Ref } from 'vue'
import maplibregl from 'maplibre-gl'

// --- Exported constants ---

export const INTRO_START_ZOOM = 18.5
export const INTRO_END_ZOOM = 13.5
const INTRO_DURATION_MS = 10_000
const INTRO_ROTATION_DEG = 240
/** Bearing every camera animation settles on, so each city lands north-up. */
export const INTRO_END_BEARING = 0
/** Bearing the map is created at so the intro's fixed sweep lands exactly on north. */
export const INTRO_START_BEARING = INTRO_END_BEARING - INTRO_ROTATION_DEG
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

/** Below this the tile layers carry the trees: the sprites fade out from z15.5. */
const INTRO_GATED_TOP_LEVEL = 15
/** The camera stops this far above a level it may not enter yet. */
const INTRO_GATE_LEAD_ZOOM = 0.05
/** Progress (eased, 0..1) over which the camera brakes into a gate. */
const INTRO_GATE_BRAKE = 0.04
/** How long the camera takes to get back to speed once a gate opens. */
const INTRO_GATE_RAMP_MS = 700
/** A level that is still not warm after this long is entered anyway. */
const INTRO_GATE_MAX_HOLD_MS = 15_000
/** Orbit the camera centre traces during the fly-out, in degrees. */
const INTRO_ORBIT_RADIUS_DEG = 0.0012
/**
 * MapLibre loads a little past the canvas corners: on Boston's start pose it
 * asked for one z14 column the corners alone leave out.
 */
const INTRO_FOOTPRINT_PAD_TILES = 0.25

export interface IntroLevelGate {
  level: number
  /** Eased progress the camera may not pass until `ready`. */
  stopAt: number
  ready: boolean
}

interface IntroWarmGate extends IntroLevelGate {
  range: TileRange | null
}

export interface IntroFlyOutState {
  /** Unpaced progress, 0..1; the camera's is its smoothstep. */
  local: number
  /** Share of full speed the camera moves at, 0..1. */
  rate: number
  held: { level: number; since: number } | null
}

export type IntroFlyOutEvent =
  | { kind: 'held'; level: number; heldMs: number }
  | { kind: 'released-cold'; level: number; heldMs: number }

const smoothstep = (v: number) => v * v * (3 - 2 * v)

/**
 * One frame of the fly-out's pacing: advances `state` by `dtMs` and returns
 * the camera's eased progress. Before a gate that is not ready the camera
 * brakes (speed proportional to the distance left) and so never passes its
 * `stopAt`; once it opens the speed ramps back up. A gate held longer than
 * INTRO_GATE_MAX_HOLD_MS is opened here.
 */
export function stepIntroFlyOut(
  state: IntroFlyOutState,
  gates: IntroLevelGate[],
  nowMs: number,
  dtMs: number,
): { t: number; events: IntroFlyOutEvent[] } {
  const events: IntroFlyOutEvent[] = []
  const t = smoothstep(state.local)
  const gate = gates.find(g => !g.ready && g.stopAt > t - 1e-6)
  const brake = gate ? Math.max(0, Math.min(1, (gate.stopAt - t) / INTRO_GATE_BRAKE)) : 1
  state.rate = Math.min(brake, state.rate + dtMs / INTRO_GATE_RAMP_MS)
  if (gate && state.rate < 0.05) {
    state.held ??= { level: gate.level, since: nowMs }
    if (nowMs - state.held.since > INTRO_GATE_MAX_HOLD_MS) {
      events.push({ kind: 'released-cold', level: gate.level, heldMs: Math.round(nowMs - state.held.since) })
      gate.ready = true
    }
  } else if (state.held) {
    events.push({ kind: 'held', level: state.held.level, heldMs: Math.round(nowMs - state.held.since) })
    state.held = null
  }
  state.local = Math.min(1, state.local + (state.rate * dtMs) / INTRO_DURATION_MS)
  return { t: smoothstep(state.local), events }
}

export interface IntroPose {
  center: [number, number]
  zoom: number
  bearing: number
}

/** The fly-out's camera at eased progress `t` (0 at the start pose, 1 at landing). */
export function introPoseAt(t: number, center: [number, number], startBearing: number, rotationDeg: number): IntroPose {
  const [baseLng, baseLat] = center
  const angle = t * Math.PI * 2
  // Start/end at exact center to avoid a visible hop
  const radiusDeg = INTRO_ORBIT_RADIUS_DEG * Math.sin(t * Math.PI)
  return {
    center: [
      baseLng + (Math.cos(angle) * radiusDeg) / Math.max(0.2, Math.cos((baseLat * Math.PI) / 180)),
      baseLat + Math.sin(angle) * radiusDeg,
    ],
    zoom: INTRO_START_ZOOM + (INTRO_END_ZOOM - INTRO_START_ZOOM) * t,
    bearing: startBearing + rotationDeg * t,
  }
}

/** Eased progress at which the fly-out reaches `zoom`. */
export function introProgressAtZoom(zoom: number): number {
  return Math.max(0, Math.min(1, (INTRO_START_ZOOM - zoom) / (INTRO_START_ZOOM - INTRO_END_ZOOM)))
}

function lngLatToMercator(lng: number, lat: number): [number, number] {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat))
  const latRad = (clamped * Math.PI) / 180
  return [(lng + 180) / 360, (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2]
}

/**
 * Every tile of level `level` the fly-out shows while its camera zoom is in
 * [level, level + 1). The camera's footprint is the start pose's, scaled by
 * the zoom and turned with the bearing (pitch and canvas stay fixed), sampled
 * along that stretch of the path. `cornersLngLat` are the canvas corners
 * unprojected at the start pose, whose bearing is `startBearing`.
 */
export function introLevelTileRange(
  level: number,
  cornersLngLat: readonly [number, number][],
  center: [number, number],
  startBearing: number,
  rotationDeg: number,
  samples = 16,
  padTiles = INTRO_FOOTPRINT_PAD_TILES,
): TileRange | null {
  const fromT = introProgressAtZoom(Math.min(INTRO_START_ZOOM, level + 1))
  const toT = introProgressAtZoom(Math.max(INTRO_END_ZOOM, level))
  if (toT < fromT) return null
  const [cx, cy] = lngLatToMercator(center[0], center[1])
  const b0 = (startBearing * Math.PI) / 180
  // Corner offsets in the screen's frame: x to the right, y up the screen.
  const screen = cornersLngLat.map(([lng, lat]) => {
    const [mx, my] = lngLatToMercator(lng, lat)
    const east = mx - cx
    const north = cy - my
    return [east * Math.cos(b0) - north * Math.sin(b0), east * Math.sin(b0) + north * Math.cos(b0)]
  })
  let minMx = Infinity, maxMx = -Infinity, minMy = Infinity, maxMy = -Infinity
  for (let i = 0; i <= samples; i += 1) {
    const pose = introPoseAt(fromT + ((toT - fromT) * i) / samples, center, startBearing, rotationDeg)
    const [px, py] = lngLatToMercator(pose.center[0], pose.center[1])
    const scale = Math.pow(2, INTRO_START_ZOOM - pose.zoom)
    const b = (pose.bearing * Math.PI) / 180
    for (const [sx, sy] of screen) {
      const mx = px + scale * (sx * Math.cos(b) + sy * Math.sin(b))
      const my = py - scale * (-sx * Math.sin(b) + sy * Math.cos(b))
      minMx = Math.min(minMx, mx); maxMx = Math.max(maxMx, mx)
      minMy = Math.min(minMy, my); maxMy = Math.max(maxMy, my)
    }
  }
  const n = Math.pow(2, level)
  const tile = (v: number) => Math.max(0, Math.min(n - 1, Math.floor(v * n)))
  const pad = padTiles / n
  return { minX: tile(minMx - pad), maxX: tile(maxMx + pad), minY: tile(minMy - pad), maxY: tile(maxMy + pad) }
}

export interface UseMapIntroAnimationOptions {
  map: Ref<maplibregl.Map | null>
  simplified: boolean
  /** Owned by the component; the composable sets it to false when animation ends. */
  introActive: Ref<boolean>
  /** Shared map of zoom → locked tile range used during intro prefetch. */
  introLockedRangeByZoom: Map<number, TileRange>
  /** Geographic center for the intro animation. */
  introCenter: Ref<[number, number]>
  setIntroComplete: () => void
  /** Lifecycle state machine callback: intro animation finished → ready. */
  onIntroFinished?: () => void
  setAutoTileFetchEnabled: (enabled: boolean) => void
  setVisibleTileRange: (z: number, minX: number, maxX: number, minY: number, maxY: number) => void
  prefetchVisibleTilesAtZoom: (z: number, range?: TileRange) => Promise<IntroPrefetchStatus>
  updateZoomLevel: () => void
  computeVisibleTileRangeForZoom: (z: number) => TileRange | null
  setMapInteractions: (enabled: boolean) => void
  /** Custom sprites have no MapLibre feature index. */
  renderedTreeSprites: () => readonly { lng: number; lat: number }[]
}

export function useMapIntroAnimation({
  map,
  introActive,
  introLockedRangeByZoom,
  introCenter,
  setIntroComplete,
  onIntroFinished,
  setAutoTileFetchEnabled,
  setVisibleTileRange,
  prefetchVisibleTilesAtZoom,
  updateZoomLevel,
  computeVisibleTileRangeForZoom,
  setMapInteractions,
  renderedTreeSprites,
}: UseMapIntroAnimationOptions) {
  const loadingMessage = ref('Counting our conifers...')

  let introStarted = false
  let introRafId: number | null = null
  let introCancelled = false
  let swoopCancelled = false

  const introPrefetchStatsByZoom = new Map<number, IntroPrefetchCounters>()

  function nowMs(): number {
    return typeof performance !== 'undefined' ? performance.now() : Date.now()
  }

  /**
   * Rotation to sweep so a spin starting at `startBearing` finishes facing north,
   * staying as close as possible to INTRO_ROTATION_DEG. The map is created at
   * INTRO_START_BEARING so this normally resolves to exactly INTRO_ROTATION_DEG,
   * but it also keeps the landing north-up if the camera starts anywhere else.
   */
  function introRotationFrom(startBearing: number): number {
    const naiveEnd = startBearing + INTRO_ROTATION_DEG
    const correction = ((((INTRO_END_BEARING - naiveEnd) % 360) + 540) % 360) - 180
    return INTRO_ROTATION_DEG + correction
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

  async function waitForViewportTreesRendered(timeoutMs = 6000): Promise<boolean> {
    if (!map.value) return false
    const startedAt = nowMs()
    return await new Promise<boolean>((resolve) => {
      if (!map.value) return resolve(false)
      let stableFrames = 0
      const tick = () => {
        if (!map.value || introCancelled) return resolve(false)
        const canvas = map.value.getCanvas()
        const width = Math.max(1, canvas.clientWidth)
        const height = Math.max(1, canvas.clientHeight)
        // Custom WebGL sprites have no MapLibre feature index. Check their
        // projected anchors, falling back to the vector circles at lower zoom.
        const visibleSprite = renderedTreeSprites().some(tree => {
          const point = map.value!.project([tree.lng, tree.lat])
          return point.x >= 0 && point.x <= width && point.y >= 0 && point.y <= height
        })
        const circles = !visibleSprite && map.value.getLayer('trees-circle')
          ? map.value.queryRenderedFeatures([[0, 0], [width, height]], { layers: ['trees-circle'] }).length
          : 0
        const frameReady = !!visibleSprite || (map.value.isSourceLoaded('trees') && circles >= VIEWPORT_TREE_MIN_FEATURES)
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

  /**
   * The fly-out from the start pose to landing. Tile fetching is paused while
   * it runs, so a level whose tiles are not in the worker's cache would draw
   * empty: the camera brakes to a hover above each level in `gates` until it
   * is warm, then picks up speed again. With every level warm in time this is
   * the plain eased 10 s sweep.
   */
  function runIntroFlyOut(
    startBearing: number,
    pitch: number,
    center: [number, number],
    rotationDeg: number,
    gates: IntroLevelGate[],
    onProgress?: (t: number) => void,
  ): Promise<void> {
    return new Promise((resolve) => {
      if (!map.value) return resolve()
      const state: IntroFlyOutState = { local: 0, rate: 1, held: null }
      let lastAt = nowMs()
      const step = () => {
        if (!map.value || introCancelled) {
          introRafId = null
          return resolve()
        }
        const now = nowMs()
        const { t: eased, events } = stepIntroFlyOut(state, gates, now, Math.min(100, now - lastAt))
        lastAt = now
        for (const e of events) {
          if (e.kind === 'released-cold') console.warn('[Perf] map:intro-gate:released-cold', { z: e.level, heldMs: e.heldMs })
          else console.info('[Perf] map:intro-gate:held', { z: e.level, heldMs: e.heldMs })
        }
        onProgress?.(eased)
        const pose = introPoseAt(eased, center, startBearing, rotationDeg)
        map.value.jumpTo({ ...pose, pitch })
        if (state.local < 1) {
          introRafId = requestAnimationFrame(step)
          return
        }
        introRafId = null
        resolve()
      }
      introRafId = requestAnimationFrame(step)
    })
  }

  /**
   * Warm the levels the fly-out will pass through, nearest first, over the
   * footprint it will show at each. Each gate opens when its level is in the
   * worker's cache.
   */
  function warmIntroLevels(startBearing: number, center: [number, number], rotationDeg: number): IntroWarmGate[] {
    if (!map.value) return []
    const canvas = map.value.getCanvas()
    const width = Math.max(1, canvas.clientWidth)
    const height = Math.max(1, canvas.clientHeight)
    const corners = ([[0, 0], [width, 0], [0, height], [width, height]] as [number, number][])
      .map(p => map.value!.unproject(p).toArray() as [number, number])
    const topLevel = Math.min(INTRO_GATED_TOP_LEVEL, Math.floor(INTRO_START_ZOOM) - 1)
    const gates: IntroWarmGate[] = []
    for (let level = topLevel; level >= Math.floor(INTRO_END_ZOOM); level -= 1) {
      gates.push({
        level,
        stopAt: introProgressAtZoom(level + 1 + INTRO_GATE_LEAD_ZOOM),
        range: introLevelTileRange(level, corners, center, startBearing, rotationDeg),
        ready: false,
      })
    }
    void (async () => {
      for (const gate of gates) {
        const startedAt = nowMs()
        try {
          if (introCancelled || !gate.range) continue
          // The detail zooms' range reports during the motion stay on this
          // footprint, so the worker batches it once instead of chasing the view.
          if (gate.level >= 15) introLockedRangeByZoom.set(gate.level, gate.range)
          const status = await prefetchVisibleTilesAtZoom(gate.level, gate.range)
          recordIntroPrefetchStatus(gate.level, status)
          console.info('[Perf] map:intro-level-warm', { z: gate.level, ...gate.range, status, ms: Math.round(nowMs() - startedAt) })
        } catch (err) {
          console.warn('[Perf] map:intro-prefetch:failed', { z: gate.level, err })
        } finally {
          gate.ready = true
        }
      }
    })()
    return gates
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
    const introRotationDeg = introRotationFrom(startBearing)
    const startPitch = map.value.getPitch()
    const introCenterSnapshot: [number, number] = [introCenter.value[0], introCenter.value[1]]
    let didRunIntroMotion = false

    try {
      // Re-anchor to the exact intro start pose, then wait for centered icons
      // before beginning motion.
      map.value.jumpTo({ center: introCenterSnapshot, zoom: INTRO_START_ZOOM, bearing: startBearing, pitch: startPitch })

      // Publish initial visible ranges before prefetching.
      updateZoomLevel()

      // Hard gate intro movement until detailed tiles are rendered at the starting viewpoint.
      const sourceStartZoom = Math.min(TREES_SOURCE_MAXZOOM, Math.round(INTRO_START_ZOOM))
      const startRange = computeVisibleTileRangeForZoom(sourceStartZoom)
      if (startRange) {
        introLockedRangeByZoom.set(sourceStartZoom, startRange)
        setVisibleTileRange(sourceStartZoom, startRange.minX, startRange.maxX, startRange.minY, startRange.maxY)
      }
      recordIntroPrefetchStatus(sourceStartZoom, await prefetchVisibleTilesAtZoom(sourceStartZoom, startRange ?? undefined))
      // The levels below are warmed behind it while the start pose settles,
      // and the fly-out waits above any that are not ready.
      const gates = warmIntroLevels(startBearing, introCenterSnapshot, introRotationDeg)

      // No source reload here. The tiles on screen were fetched with real data
      // before this ran (the intro starts on the trees source's loaded event),
      // and the prefetch only warms the worker cache for the next LOD. A URL
      // nonce bump would make MapLibre clear every tile and blank the sprite
      // layer, then redraw identical data a few hundred ms later.

      await waitForTreesMilestone(2500)
      const canStartMotion = await waitForViewportTreesRendered(6000)

      if (!introCancelled && canStartMotion) {
        loadingMessage.value = 'Tracking seed dispersion...'
        let didSetFinalPhase = false
        await runIntroFlyOut(
          startBearing, startPitch, introCenterSnapshot, introRotationDeg, gates,
          (globalT) => {
            if (!didSetFinalPhase && globalT >= 0.72) {
              didSetFinalPhase = true
              loadingMessage.value = 'Reticulating splines...'
            }
          },
        )
        didRunIntroMotion = true
      } else if (!introCancelled) {
        console.warn('[Perf] map:intro-gate:blocked-motion', { canStartMotion })
      }
    } finally {
      // If cancelIntro() was called, runGlobeSwoopTo has taken over camera control and owns
      // introActive / tile-fetch / interactions. Don't touch those — only do safe cleanup.
      if (!introCancelled) {
        setAutoTileFetchEnabled(true)
        if (map.value && didRunIntroMotion) {
          map.value.jumpTo({
            center: introCenterSnapshot,
            zoom: INTRO_END_ZOOM,
            bearing: INTRO_END_BEARING,
            pitch: startPitch,
          })
        }
        // Tiles requested during the motion that missed the worker cache were
        // answered with transient empties carrying a short expiry; MapLibre
        // re-requests those on its own, in place, once fetching is back on.
        // Deliberately no URL-nonce bump here: setTiles() makes MapLibre clear
        // every loaded tile first, so the city view blanked and redrew at landing.
        introActive.value = false
        setMapInteractions(true)
      }
      // Always mark intro as complete so the chat panel doesn't stay locked.
      setIntroComplete()
      // Notify the lifecycle state machine that the intro is done.
      // Only transition intro→ready when the intro wasn't cancelled by a city switch
      // (if cancelled, the switch handler owns the lifecycle transition).
      if (!introCancelled) onIntroFinished?.()
      introLockedRangeByZoom.clear()
      logIntroPrefetchSummary()
    }
  }

  function cancelIntro() {
    introCancelled = true
    swoopCancelled = true
    if (introRafId != null) {
      cancelAnimationFrame(introRafId)
      introRafId = null
    }
  }

  /** Allow the intro sequence to run again (e.g. after a city switch). */
  function resetIntro() {
    introStarted = false
    introCancelled = false
  }

  /**
   * Animate a globe-swoop transition to a new city:
   *   current view → slow zoom out to globe → slow arc across → slowly zoom into city at z=13.5.
   *
   * Auto tile fetching is disabled during Phase 1 (zoom-out) only. At the start of Phase 2
   * (the arc toward the new city) `onCrossingStart` is called so the caller can kick off DB
   * context rebuild and tile queries while the camera is still in flight — giving DuckDB the
   * full arc duration (~12 s) to prepare tiles before the camera lands.
   *
   * Because the DB is already initialised we land at city zoom directly (no INTRO_START_ZOOM).
   */
  async function runGlobeSwoopTo(targetCenter: [number, number], cityName: string, landingCenter?: [number, number]): Promise<void> {
    if (!map.value) return

    cancelIntro()
    // Reset only swoopCancelled — introCancelled must stay true so runIntroZoomOut's
    // pending async continuation recognises it was cancelled and does not jump back.
    swoopCancelled = false
    introActive.value = true
    setMapInteractions(false)
    setAutoTileFetchEnabled(false)
    loadingMessage.value = `Heading to ${cityName}…`

    const GLOBE_ZOOM = 3
    const ZOOM_OUT_MS = 7000
    const FLY_ACROSS_MS = 12000

    const cleanup = (cancelled: boolean) => {
      setAutoTileFetchEnabled(true)
      // NOTE: deliberately no forceTreesTileRefetchPass() here. The swoop lands on the
      // new city while the worker still holds the PREVIOUS city's context, so a refetch
      // at this point queries old-city data over the new city's viewport and yields an
      // all-empty pass. Its sourcedata events were being mistaken for "the new city's
      // tiles are loaded", flipping the lifecycle to ready on a blank map. The caller
      // (switchCity → setCityContext → query-revision watcher) bumps the tile nonce once
      // the new context is live, which flushes the empties cached during the swoop.
      if (cancelled) introActive.value = false
      setMapInteractions(true)
    }

    // Phase 1 — slow zoom out to globe (tile fetch disabled)
    await new Promise<void>((resolve) => {
      if (!map.value) return resolve()
      map.value.flyTo({ zoom: GLOBE_ZOOM, duration: ZOOM_OUT_MS, essential: true })
      const onEnd = () => { map.value?.off('moveend', onEnd); resolve() }
      map.value.once('moveend', onEnd)
    })

    if (swoopCancelled) { cleanup(true); return }

    loadingMessage.value = `Please remain seated while seatbelt light is on…`

    // Phase 2 — slow arc to new city; high curve dips through globe zoom then climbs to city zoom.
    await new Promise<void>((resolve) => {
      if (!map.value) return resolve()
      map.value.flyTo({
        center: landingCenter ?? targetCenter,
        zoom: 13.5,
        bearing: INTRO_END_BEARING,
        duration: FLY_ACROSS_MS,
        essential: true,
        curve: 1.6,
      })
      const onEnd = () => { map.value?.off('moveend', onEnd); resolve() }
      map.value.once('moveend', onEnd)
    })

    if (swoopCancelled) { cleanup(true); return }

    loadingMessage.value = 'Counting our conifers…'
    introActive.value = false
    cleanup(false)
  }

  return {
    loadingMessage,
    runIntroZoomOut,
    cancelIntro,
    resetIntro,
    runGlobeSwoopTo,
    recordIntroPrefetchStatus,
  }
}
