/** The worker's init stages, in order, as it pushes them to the main thread. */
export type InitStage = 'engine' | 'guide' | 'world' | 'ready'

/**
 * The first Near Me load, as steps a phone user can watch: what the worker is
 * fetching (its init stages), then the bookmark's tables, then the trees
 * drawn. Each step shows a playful title with the plain thing it is doing
 * underneath.
 */
export type LoadStep = { key: string; title: string; detail: string }

export const NEARBY_LOAD_STEPS: readonly LoadStep[] = [
  { key: 'located', title: 'Found your patch of forest', detail: 'Location found' },
  { key: 'engine', title: 'Gathering seeds', detail: 'Fetching dependencies' },
  { key: 'guide', title: 'Opening the field guide', detail: 'Loading species data' },
  { key: 'world', title: 'Unfolding the forest map', detail: 'Reading the tree index' },
  { key: 'city', title: 'Wandering into the grove', detail: 'Loading local data' },
  { key: 'trees', title: 'Planting the trees around you', detail: 'Drawing trees' },
]

const STAGE_STEP: Record<InitStage, string> = {
  engine: 'engine',
  guide: 'guide',
  world: 'world',
  ready: 'city',
}

/**
 * The step under way. Before the worker reports anything it is fetching the
 * engine; once init is done, the bookmark is loading until its context has
 * answered, and after that the trees are being drawn.
 */
export function nearbyLoadStepIndex(state: { initStage: InitStage | null; cityContextPending: number; cityContextLoaded: boolean }): number {
  let key = STAGE_STEP[state.initStage ?? 'engine']
  if (key === 'city' && state.cityContextPending === 0 && state.cityContextLoaded) key = 'trees'
  return NEARBY_LOAD_STEPS.findIndex((step) => step.key === key)
}

/** What the corner leaf says while blocks are fetched, cycled over FETCH_DETAIL. */
export const FETCH_PHRASES: readonly string[] = [
  'Raking in more leaves…',
  'Branching out…',
  'Gathering acorns…',
  'Following the roots…',
]

/** The plain line under the corner leaf's phrase. */
export const FETCH_DETAIL = 'Fetching nearby trees'
