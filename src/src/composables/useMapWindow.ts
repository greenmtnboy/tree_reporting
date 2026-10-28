import type { WindowPolicy } from '../workers/cityWindow'
import { CELL_ZOOM } from '../workers/cityWindow'

/**
 * How much of a city a phone holds at once (workers/cityWindow.ts).
 *
 * A phone's map cannot zoom out past a floor, and holds only the trees of the
 * ground near the camera. The floor and the row budget come from measuring
 * Boston's densest square with every predicted tree (docs/MOBILE_LOAD_BUDGET.md):
 * a phone screen at z13 shows about 143k trees there, and a whole-file load
 * of 300k to 500k keeps the walk to z18 under 900 MB. The budget is the low
 * end because each window load still scans the unsorted city file and the
 * wasm heap keeps that transient; raise it once the projection is written in
 * Hilbert order (docs/TRILOGY_ORDER_BY_SPEC.md) and re-measured. Predictions
 * are being run for every city, so one floor serves them all.
 *
 * Two tiers. `normal` is a current phone on a first visit. `low` is a phone
 * that says it has little memory (`navigator.deviceMemory`, Android), or one
 * whose last city load never settled: Safari reloads a tab that outgrows its
 * budget, and allows the reloaded tab only 300 to 500 MB.
 */
export type MapWindowTier = 'normal' | 'low'

const TIERS: Record<MapWindowTier, { floorZoom: number; budgetRows: number }> = {
  normal: { floorZoom: 13, budgetRows: 300_000 },
  low: { floorZoom: 14, budgetRows: 150_000 },
}

/** Set while a city load is running; still set on the next visit means the tab was reloaded mid-load. */
const LOAD_PENDING_KEY = 'arborary:map-load-pending'
/** The tier a viewer chose from the banner, for this tab. */
const TIER_CHOICE_KEY = 'arborary:map-window-tier'
/** A pending flag older than this is a load the viewer walked away from, not a crash. */
const PENDING_STALE_MS = 6 * 60 * 60 * 1000

export type MapWindow = {
  policy: WindowPolicy
  tier: MapWindowTier
  /** Why the tier is `low`, for the banner; null when nothing needs saying. */
  reason: 'reloaded' | 'device' | null
}

function storage(kind: 'local' | 'session'): Storage | null {
  try {
    return kind === 'local' ? window.localStorage : window.sessionStorage
  } catch {
    return null
  }
}

/**
 * How many cells from the camera a tile may pull trees in: the screen at the
 * floor zoom, half its longer side plus a quarter-screen margin, in cells.
 * MapLibre draws a z14 cell 512 * 2^(zoom - 14) pixels wide.
 */
export function windowRadiusCells(floorZoom: number, width: number, height: number): number {
  const cellPx = 512 * 2 ** (floorZoom - CELL_ZOOM)
  return Math.ceil(((Math.max(width, height) / 2) * 1.25) / cellPx) + 1
}

/** The window for this phone, or null for a layout that holds the whole city. */
export function resolveMapWindow(isPhone: boolean): MapWindow | null {
  if (!isPhone || typeof window === 'undefined') return null
  const chosen = storage('session')?.getItem(TIER_CHOICE_KEY)
  const pending = Number(storage('local')?.getItem(LOAD_PENDING_KEY))
  const reloaded = Number.isFinite(pending) && pending > 0 && Date.now() - pending < PENDING_STALE_MS
  const deviceMemory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory
  const smallDevice = typeof deviceMemory === 'number' && deviceMemory <= 4

  // A reload mid-load wins over a choice: a viewer who asked for full detail
  // and was reloaded again goes back to the low tier rather than looping.
  let tier: MapWindowTier = 'normal'
  let reason: MapWindow['reason'] = null
  if (reloaded) {
    tier = 'low'
    reason = 'reloaded'
    storage('session')?.removeItem(TIER_CHOICE_KEY)
  } else if (chosen === 'normal' || chosen === 'low') {
    tier = chosen
  } else if (smallDevice) {
    tier = 'low'
    reason = 'device'
  }

  const { floorZoom, budgetRows } = TIERS[tier]
  return {
    tier,
    reason,
    policy: { floorZoom, budgetRows, radiusCells: windowRadiusCells(floorZoom, window.innerWidth, window.innerHeight) },
  }
}

/** Mark a city load as running; `markMapLoadSettled` clears it. */
export function markMapLoadStarted(): void {
  storage('local')?.setItem(LOAD_PENDING_KEY, String(Date.now()))
  // Leaving the page is not a crash; Safari's memory reload fires no pagehide.
  window.addEventListener('pagehide', markMapLoadSettled, { once: true })
}

export function markMapLoadSettled(): void {
  storage('local')?.removeItem(LOAD_PENDING_KEY)
}

/** The banner's "Try full detail": the normal tier for this tab, from a fresh page. */
export function chooseFullDetail(): void {
  storage('session')?.setItem(TIER_CHOICE_KEY, 'normal')
  markMapLoadSettled()
  window.location.reload()
}
