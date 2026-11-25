import { describe, expect, it } from 'vitest'
import { ALL_MODEL_SOURCES } from '../trilogyModels'
import { MAP_CHAT_IMPORTS, MAP_RULE_IMPORTS } from '../composables/chatModelImports'

// What the resolver is sent with every dashboard and chat request. It parses
// all of it each time, so the bundle is the app's consumption models only;
// these checks are offline and read the text, the resolver-backed suites
// (trilogy-smoketest, dashboard-pushdown) are what prove the plans.

const aliases = new Set(ALL_MODEL_SOURCES.map((source) => source.alias))

function importsOf(contents: string): string[] {
  return [...contents.matchAll(/^import\s+([\w.]+)(?:\s+as\s+\w+)?\s*;/gm)].map((m) => m[1])
}

describe('the resolver model bundle', () => {
  it('resolves every import it makes, so no model is missing from the bundle', () => {
    const missing = ALL_MODEL_SOURCES.flatMap((source) =>
      importsOf(source.contents)
        .filter((name) => !name.startsWith('std.') && !aliases.has(name))
        .map((name) => `${source.alias} imports ${name}`),
    )
    expect(missing).toEqual([])
  })

  it('resolves every import the chat and the dashboards ask for', () => {
    // dashboard_context is built per city at request time and sent beside the bundle.
    const asked = [...MAP_CHAT_IMPORTS, ...MAP_RULE_IMPORTS].map((entry) => entry.name)
    expect(asked.filter((name) => !aliases.has(name))).toEqual([])
  })

  it('carries no city build model and nothing that derives a published column', () => {
    // A city build model brings its raw partitions, portal scripts and the
    // dedup / position / height derivations into every request; the published
    // parquet already holds what they derive.
    const build = ALL_MODEL_SOURCES.filter(
      (source) =>
        source.alias.includes('.') ||
        ['tree_dedup', 'tree_position', 'tree_height', 'community_tree_info', 'satellite_tree_info', 'landmark_common'].includes(
          source.alias,
        ),
    ).map((source) => source.alias)
    expect(build).toEqual([])
  })

  it('reads published parquets in tree_info, with no merge and no watermark', () => {
    const treeInfo = ALL_MODEL_SOURCES.find((source) => source.alias === 'tree_info')?.contents ?? ''
    expect(treeInfo).not.toMatch(/^merge /m)
    expect(treeInfo).not.toMatch(/^\s*freshness by/m)
    expect(treeInfo).not.toMatch(/_published_data_updated_through/)
    expect(treeInfo).not.toMatch(/file `\.\/.*\.py`/)
  })
})
