import { ref, computed } from 'vue'

/**
 * Centralised state machine for the map's loading lifecycle.
 *
 * Replaces the scattered boolean flags (ready, introComplete,
 * defaultQueryLoading, introActive, citySwitchInProgress) with a single
 * phase enum.  Every UI element that cares about "is the map ready?" reads
 * `chatReady` or `showLoadingOverlay` instead of combining flags itself.
 *
 * State diagram:
 *
 *   initializing → loading → intro → ready
 *                         └────────→ ready   (mobile / simplified)
 *   {any}        → switching → ready
 */

export type MapPhase =
  | 'initializing'  // DuckDB worker + tile protocol being registered
  | 'loading'       // City data loading (setCityContext + tile generation)
  | 'intro'         // Desktop intro zoom-out animation
  | 'ready'         // City loaded, UI fully interactive
  | 'switching'     // Globe swoop + new city load in progress

export interface MapTransitionSnapshot {
  id: number
  city: string
}

const phase = ref<MapPhase>('initializing')
const requestedCity = ref<string | null>(null)
const contextCity = ref<string | null>(null)
const renderedCity = ref<string | null>(null)
const transitionId = ref(0)
const manualCitySelectionReady = ref(false)

export function useMapLifecycle() {
  // --- Derived state -------------------------------------------------------

  /** Chat input should be enabled when (and only when) the map is ready. */
  const chatReady = computed(() => phase.value === 'ready')

  /** Show the full-screen loading overlay for everything except ready. */
  const showLoadingOverlay = computed(() => phase.value !== 'ready')

  /** True while the intro animation is running. */
  const introAnimating = computed(() => phase.value === 'intro')

  /** True while a city switch is in progress (prevents concurrent switches). */
  const isSwitching = computed(() => phase.value === 'switching')

  /** The city the map is currently trying to show. */
  const loadingCity = computed(() => requestedCity.value)

  /** The city whose query/DB context is currently committed. */
  const activeCity = computed(() => contextCity.value)

  /** True once the rendered map is confirmed to match the requested city. */
  const hasRenderedRequestedCity = computed(() =>
    requestedCity.value !== null && requestedCity.value === renderedCity.value,
  )

  /** True once the map/bootstrap flow can safely honor manual city selection. */
  const canManuallySelectCity = computed(() => manualCitySelectionReady.value)

  function snapshotFor(city: string): MapTransitionSnapshot {
    return { id: transitionId.value, city }
  }

  function currentSnapshot(city?: string): MapTransitionSnapshot | null {
    const snapshotCity = city ?? requestedCity.value
    return snapshotCity ? snapshotFor(snapshotCity) : null
  }

  function matches(snapshot: MapTransitionSnapshot): boolean {
    return requestedCity.value === snapshot.city && transitionId.value === snapshot.id
  }

  function initialize(city: string): MapTransitionSnapshot {
    requestedCity.value = city
    if (!contextCity.value) contextCity.value = city
    return snapshotFor(city)
  }

  function requestCity(city: string): MapTransitionSnapshot {
    if (requestedCity.value === city) return snapshotFor(city)
    requestedCity.value = city
    transitionId.value += 1
    return snapshotFor(city)
  }

  function activateCity(city: string): MapTransitionSnapshot {
    const snapshot = requestCity(city)
    contextCity.value = city
    if (renderedCity.value === city) {
      phase.value = 'ready'
    } else {
      phase.value = 'loading'
    }
    return snapshot
  }

  function setManualCitySelectionReady(ready: boolean) {
    manualCitySelectionReady.value = ready
  }

  // --- Transitions ---------------------------------------------------------

  /** DuckDB init done, city parquet loading begins. */
  function startLoading(snapshot?: MapTransitionSnapshot) {
    const nextCity = snapshot?.city ?? requestedCity.value
    const nextSnapshot = nextCity ? snapshotFor(nextCity) : null
    if (!nextSnapshot) return null
    requestedCity.value = nextSnapshot.city
    phase.value = 'loading'
    return nextSnapshot
  }

  /** Commit the requested city as the active DuckDB/query context. */
  function commitContextCity(snapshot: MapTransitionSnapshot): boolean {
    if (!matches(snapshot)) return false
    contextCity.value = snapshot.city
    return true
  }

  /**
   * City tiles have loaded. On desktop, move to the intro animation;
   * on mobile (simplified) skip straight to ready.
   */
  function tilesLoaded(snapshot: MapTransitionSnapshot, simplified: boolean): boolean {
    if (!matches(snapshot)) return false
    renderedCity.value = snapshot.city
    if (phase.value === 'loading') {
      phase.value = simplified ? 'ready' : 'intro'
    } else if (phase.value === 'switching') {
      // After a city switch, tiles loaded means we're done.
      phase.value = 'ready'
    }
    return true
  }

  /** Intro animation finished — unlock the UI. */
  function introFinished() {
    if (phase.value === 'intro') {
      phase.value = 'ready'
    }
  }

  /**
   * User initiated a city switch. This can happen from ANY state —
   * including during the intro animation or even during an earlier switch
   * (the earlier switch is effectively cancelled).
   */
  function startCitySwitch(city: string) {
    const snapshot = requestCity(city)
    phase.value = 'switching'
    return snapshot
  }

  /**
   * A city switch has fully completed — data loaded AND tiles rendered.
   * Moves directly to ready (no intro on subsequent cities).
   */
  function citySwitchReady(snapshot: MapTransitionSnapshot): boolean {
    if (!matches(snapshot)) return false
    renderedCity.value = snapshot.city
    if (phase.value === 'switching') {
      phase.value = 'ready'
    }
    return true
  }

  /** Force transition to ready from any state (error recovery). */
  function forceReady(snapshot?: MapTransitionSnapshot | null) {
    if (snapshot && matches(snapshot)) {
      renderedCity.value = snapshot.city
      contextCity.value = snapshot.city
    }
    phase.value = 'ready'
  }

  return {
    phase,
    requestedCity,
    contextCity,
    renderedCity,
    transitionId,
    chatReady,
    showLoadingOverlay,
    introAnimating,
    isSwitching,
    loadingCity,
    activeCity,
    hasRenderedRequestedCity,
    canManuallySelectCity,
    initialize,
    requestCity,
    activateCity,
    setManualCitySelectionReady,
    currentSnapshot,
    matches,
    startLoading,
    commitContextCity,
    tilesLoaded,
    introFinished,
    startCitySwitch,
    citySwitchReady,
    forceReady,
  }
}
