/** @vitest-environment happy-dom */
// A city's dashboard must plan against that city's own Parquet.
//
// The browser downloads whatever the resolver's SQL names. `full_tree_info` is
// the 5.9M-row cross-city rollup; `ussfo_tree_info` is 206k rows. Both answer a
// `city = 'USSFO'` question, and the planner picks between them purely on what
// sources are in scope — so this is decided by the *shape of the model bundle*,
// not by anything in the query, and it changes silently.
//
// It changed silently once already. Giving `tree_enrichment.preql` a root
// datasource over the rollup (to declare an input the platform could order the
// enrichment job on) put a second tree source in the planner's scope, and it did
// not merely pick the slower path — the summary tree count under a nativeness
// cross-filter came back 2 where the fixtures say 3. `pnpm test:queries` caught
// the wrong number; nothing caught the source switch, which is the cause and the
// cheaper thing to detect.
//
// So: the per-city partitions live in `tree_info.preql`, `dashboard_context`
// imports it, and the enrichment job's own view of the rollup is kept in a
// separate file set (`raw/enrichment_refresh.preql`) that the frontend does not
// bundle. This asserts the outcome of that arrangement rather than its spelling.
//
// Network: compiles against the live resolver, like trilogy-smoketest. No SQL is
// executed — only the parquet names in the generated SQL are read.
import { describe, it, expect, vi } from 'vitest'

// happy-dom has no Worker; the dashboard bundle constructs one at import time.
vi.hoisted(() => {
  const globals = globalThis as { Worker?: unknown }
  globals.Worker ??= class {
    postMessage() {}
    terminate() {}
    addEventListener() {}
    removeEventListener() {}
  }
})

import { ALL_MODEL_SOURCES } from '../trilogyModels'
import {
  buildDashboardContextParameters,
  buildDashboardContextSource,
} from '../composables/dashboardContextSource'
import { summaryQueryCases, speciesQueryCases, type DashboardQueryCase } from './dashboardQueryCatalog'
import { postToResolverOrThrow } from './resolverFetch'
import { CITY_CONFIG, type CityCode } from '../composables/useMapData'

/** Tree parquets named by the generated SQL, without the version suffix. */
async function treeParquets(
  queryCase: DashboardQueryCase,
  city: CityCode | null,
): Promise<string[]> {
  // Retries a 5xx or a dropped connection; see resolverFetch for why, and why
  // the tests below carry a 120s timeout rather than 30s.
  const text = await postToResolverOrThrow('/generate_query', {
    query: queryCase.query,
    dialect: 'duckdb',
    full_model: {
      name: '',
      sources: [...ALL_MODEL_SOURCES, buildDashboardContextSource(city)],
    },
    imports: queryCase.imports,
    extra_filters: queryCase.filters,
    parameters: {
      ...buildDashboardContextParameters(city),
      ...(queryCase.parameters ?? {}),
    },
  })
  const { generated_sql: sql, error } = JSON.parse(text)
  if (error) throw new Error(`compile error for ${queryCase.id}: ${error}`)
  return [
    ...new Set(
      [...(sql as string).matchAll(/duckdb\/trees\/([a-z_]+)_v\d+\.parquet/g)].map((m) => m[1]),
    ),
  ]
}

// A representative slice rather than the whole catalog: the source choice is a
// property of the model bundle, so it is the same for every chart on a surface,
// and the full sweep already compiles all of them.
function sampleCases(city: CityCode | null): DashboardQueryCase[] {
  return [...summaryQueryCases(city).slice(0, 4), ...speciesQueryCases(city).slice(0, 4)]
}

describe('dashboard parquet pushdown', () => {
  for (const city of ['USSFO', 'FRPAR', 'GRATH'] as CityCode[]) {
    it(`plans ${city} against ${city.toLowerCase()}_tree_info, not the rollup`, async () => {
      for (const queryCase of sampleCases(city)) {
        const parquets = await treeParquets(queryCase, city)
        expect(parquets, `${queryCase.id} named no tree parquet`).not.toHaveLength(0)
        expect(
          parquets,
          `${queryCase.id} reads the cross-city rollup for a single city; the ` +
            `browser would download 5.9M rows instead of ${city}'s own file`,
        ).not.toContain('full_tree_info')
        expect(parquets).toContain(`${city.toLowerCase()}_tree_info`)
      }
    }, 120_000)
  }

  // The chat surface sends the agent's own PreQL with SUMMARY_DASHBOARD_IMPORTS
  // (`tree_enrichment` + `dashboard_context`), so the city is not baked into the
  // model the way a dashboard page bakes it — the agent writes the predicate
  // itself, and it may name a city other than the one on screen. Resolution has
  // to happen off that predicate.
  //
  // This is why `dashboard_context` imports `tree_info` explicitly. It used to
  // reach the per-city partitions transitively through `tree_enrichment`, which
  // is now species-only; without the explicit import a chat question about
  // Boston would scan the whole rollup.
  for (const city of ['USBOS', 'GRMLO'] as CityCode[]) {
    it(`resolves a chat query filtering to ${city} down to its parquet`, async () => {
      const parquets = await treeParquets(
        {
          id: `chat:${city}`,
          surface: 'summary',
          query: `SELECT species, count(tree_id) -> tree_count WHERE city = '${city}' ORDER BY tree_count desc LIMIT 10`,
          imports: [
            { name: 'tree_enrichment', alias: '' },
            { name: 'dashboard_context', alias: '' },
          ],
          filters: [],
          state: { city: null, genus: null, species: null, crossFilters: [] },
        },
        // No city context: the all-cities view, where the predicate is the only
        // thing that says which city is wanted.
        null,
      )
      expect(parquets).toContain(`${city.toLowerCase()}_tree_info`)
      expect(
        parquets,
        `a chat query filtered to ${city} scanned the cross-city rollup`,
      ).not.toContain('full_tree_info')
    }, 120_000)
  }

  it('plans the all-cities view against every city, or the rollup', async () => {
    // The other half of the contract: with no city there is no partition to
    // push down to. Two plans are complete: the rollup, or the union of every
    // city's own parquet. The planner used to pick the rollup; since the city
    // models moved their raw sources onto the shared raw_* concepts
    // (tree_dedup.preql) it picks the union, which downloads the same rows as
    // eighteen files instead of one. What must never pass is a strict subset:
    // while only some cities were converted, the resolver answered the
    // all-cities dot map from those cities alone and silently dropped the rest
    // (see upstream_repro/partition_subset_chosen), and a regression that
    // pointed every city at the rollup would still pass the tests above if
    // they were the only ones here.
    const every = Object.keys(CITY_CONFIG).map((c) => `${c.toLowerCase()}_tree_info`)
    for (const queryCase of sampleCases(null)) {
      const parquets = await treeParquets(queryCase, null)
      if (parquets.includes('full_tree_info')) continue
      expect(
        parquets.filter((p) => p !== 'full_tree_info').sort(),
        `${queryCase.id} read a strict subset of the cities and not the rollup`,
      ).toEqual(every.sort())
    }
  }, 120_000)
})
