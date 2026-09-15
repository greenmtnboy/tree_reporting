// Merge every chat-benchmark-*.json in bench-results/ into one table: for each
// case, the measured rounds across all runs (budget refusals and unrun rounds
// dropped). A sweep on the demo token spans key windows, so one run rarely
// holds every prompt.  Usage: node src/bench/mergeRuns.mjs [bench-results]
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import process from 'node:process'

// A round is a measurement only if the model was offered the screen's own
// tools; a summary round offering publish_results ran on the map screen
// (the route mock once froze on its first route) and says nothing about the
// summary prompt. A budget refusal says nothing either, however it was scored.
// Nor does a round in which no model call ever returned an answer: that is
// the transport (a dropped connection, a request that never came back), and
// folding such rounds in misstates the prompt's clean rate.
const SCREEN_TOOL = { map: 'publish_results', summary: 'set_summary_filters', species: 'set_species_filters' }
const modelAnswered = (r) => Array.isArray(r.transcript) && r.transcript.some((call) => call.response && !call.error)
const isMeasurement = (r) =>
  r.verdict !== 'not_run' &&
  r.verdict !== 'budget_exhausted' &&
  !(r.loopError && /Key limit exceeded/.test(r.loopError)) &&
  Array.isArray(r.toolNames) && r.toolNames.includes(SCREEN_TOOL[r.screen]) &&
  modelAnswered(r)

const dir = process.argv[2] ?? 'bench-results'
const files = readdirSync(dir).filter((f) => /^chat-benchmark-.*\.json$/.test(f)).sort()
const byCase = new Map()
const order = []
for (const file of files) {
  const run = JSON.parse(readFileSync(join(dir, file), 'utf8'))
  for (const r of run.results) {
    if (!isMeasurement(r)) continue
    if (!byCase.has(r.caseId)) { byCase.set(r.caseId, []); order.push(r.caseId) }
    byCase.get(r.caseId).push({ ...r, file })
  }
}
const rank = (id) => ['map', 'summary', 'species'].indexOf(id.split('-')[0]) * 100 + Number(id.split('-')[1])
const lines = ['| case | prompt | measured | clean | tool err | no final | loop err | error rate | avg s | avg LLM calls |', '|---|---|---|---|---|---|---|---|---|---|']
let measured = 0, clean = 0
const errorsByCase = []
for (const id of [...order].sort((a, b) => rank(a) - rank(b))) {
  const rounds = byCase.get(id)
  const n = (v) => rounds.filter((r) => r.verdict === v).length
  measured += rounds.length; clean += n('clean')
  const avg = (k) => rounds.reduce((s, r) => s + r[k], 0) / rounds.length
  lines.push(`| ${id} | ${rounds[0].prompt} | ${rounds.length} | ${n('clean')} | ${n('tool_error')} | ${n('no_final_answer')} | ${n('loop_error')} | ${Math.round((100 * (rounds.length - n('clean'))) / rounds.length)}% | ${(avg('durationMs') / 1000).toFixed(1)} | ${avg('llmCalls').toFixed(1)} |`)
  const counts = new Map()
  for (const r of rounds) for (const e of r.errors) { const k = e.replace(/\s+/g, ' ').slice(0, 200); counts.set(k, (counts.get(k) ?? 0) + 1) }
  if (counts.size) errorsByCase.push([id, rounds[0].prompt, [...counts.entries()].sort((a, b) => b[1] - a[1])])
}
const out = [`# Chat benchmark, merged across ${files.length} runs`, '', `- rounds measured: ${measured}, clean: ${clean} (${Math.round((100 * clean) / measured)}%)`, '', ...lines, '', '## Errors by prompt', '']
for (const [id, prompt, errs] of errorsByCase) { out.push(`### ${id}: ${prompt}`, ''); for (const [m, c] of errs) out.push(`- (${c}) ${m}`); out.push('') }
writeFileSync(join(dir, 'chat-benchmark-merged.md'), out.join('\n'))
console.log(out.join('\n'))
