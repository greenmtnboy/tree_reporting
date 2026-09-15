/**
 * What the chat benchmark runs: every prompt the panel suggests, on the screen
 * that suggests it, in one fixed starting state per screen.
 *
 * The starting states are chosen so each prompt asks for a real change. The
 * species explorer opens on a genus *and* a species, so "clear the species
 * filter but keep the genus" has something to clear and "set the genus to
 * Quercus" something to replace; the summary page opens with no analytics
 * filters, so "clear the analytics filters" is the one prompt that is
 * legitimately a no-op and the benchmark still expects it to finish cleanly.
 */
import { MAP_SUGGESTIONS, SPECIES_SUGGESTIONS, SUMMARY_SUGGESTIONS } from '../composables/chatSuggestions'
import type { AppScreen } from '../composables/chatToolConfig'

export type ChatBenchScreen = Exclude<AppScreen, 'info'>

export type ChatBenchCase = {
  /** Stable id, `<screen>-<n>`: what the report and the prompt filter key on. */
  id: string
  screen: ChatBenchScreen
  prompt: string
}

function cases(screen: ChatBenchScreen, prompts: string[]): ChatBenchCase[] {
  return prompts.map((prompt, index) => ({ id: `${screen}-${index + 1}`, screen, prompt }))
}

export const CHAT_BENCH_CASES: ChatBenchCase[] = [
  ...cases('map', MAP_SUGGESTIONS),
  ...cases('summary', SUMMARY_SUGGESTIONS),
  ...cases('species', SPECIES_SUGGESTIONS),
]

export type BenchRoute = { path: string; name: string; query: Record<string, string> }

/** The route each screen's chat starts on, for the given city. */
export function startingRoute(screen: ChatBenchScreen, city: string): BenchRoute {
  switch (screen) {
    case 'map':
      return { path: '/', name: 'map', query: { city } }
    case 'summary':
      return { path: '/summary', name: 'summary', query: { city } }
    case 'species':
      return {
        path: '/species',
        name: 'species',
        query: { city, genus: 'Platanus', species: 'Platanus x hispanica' },
      }
  }
}
