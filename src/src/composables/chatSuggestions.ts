/**
 * The example prompts each chat screen offers when the conversation is empty.
 *
 * They live here rather than in `ChatPanel.vue` because they are also the
 * agent's acceptance bar: `src/bench/chat-benchmark.test.ts` runs every one of
 * them through the real chat loop against the live resolver and the demo
 * model, and reports how often each finishes without a tool error. A prompt
 * the panel suggests is a prompt the agent is expected to complete, so adding
 * one here enrols it in the benchmark automatically -- the same arrangement
 * `dashboardQueryCatalog.ts` has with the chart configs.
 */

export const MAP_SUGGESTIONS = [
  'What can you do?',
  'What is the most common type of tree?',
  'Show me trees in bloom right now!',
  'Where is the biggest tree?',
]

export const SUMMARY_SUGGESTIONS = [
  'Filter the charts to local native trees',
  'Show only trees outside the hardiness zone',
  'What share of the city is concentrated in the top 5 species?',
  'Clear the analytics filters',
]

export const SPECIES_SUGGESTIONS = [
  'Set the genus to Quercus',
  'Filter this page to Acer rubrum',
  'What does the current species view show?',
  'Clear the species filter but keep the genus',
]
