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

const phase = ref<MapPhase>('initializing')

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

  // --- Transitions ---------------------------------------------------------

  /** DuckDB init done, city parquet loading begins. */
  function startLoading() {
    // Allow from initializing or switching (switching restarts the load cycle).
    if (phase.value === 'initializing' || phase.value === 'switching') {
      phase.value = 'loading'
    }
  }

  /**
   * City tiles have loaded. On desktop, move to the intro animation;
   * on mobile (simplified) skip straight to ready.
   */
  function tilesLoaded(simplified: boolean) {
    if (phase.value === 'loading') {
      phase.value = simplified ? 'ready' : 'intro'
    } else if (phase.value === 'switching') {
      // After a city switch, tiles loaded means we're done.
      phase.value = 'ready'
    }
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
  function startCitySwitch() {
    phase.value = 'switching'
  }

  /**
   * A city switch has fully completed — data loaded AND tiles rendered.
   * Moves directly to ready (no intro on subsequent cities).
   */
  function citySwitchReady() {
    if (phase.value === 'switching') {
      phase.value = 'ready'
    }
  }

  /** Force transition to ready from any state (error recovery). */
  function forceReady() {
    phase.value = 'ready'
  }

  return {
    phase,
    chatReady,
    showLoadingOverlay,
    introAnimating,
    isSwitching,
    startLoading,
    tilesLoaded,
    introFinished,
    startCitySwitch,
    citySwitchReady,
    forceReady,
  }
}
