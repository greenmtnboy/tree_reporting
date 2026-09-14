/**
 * What the map-screen chat resolves its PreQL against.
 *
 * Two imports, not one. `tree_enrichment` is the species dimension only; the
 * tree rows live in `tree_info`, whose per-city `complete where city = 'X'`
 * partitions are also what let a single-city question plan against that city's
 * own parquet instead of the cross-city rollup. The summary and species screens
 * reach the same pair through `dashboard_context` (see
 * `summaryDashboardConfig.ts` and the import list inside
 * `dashboardContextSource.ts`); the map screen has no dashboard context, so it
 * imports `tree_info` directly.
 *
 * This is a constant rather than a literal at the call site because it was a
 * literal, and it silently went stale. `tree_enrichment.preql` used to import
 * `tree_info`, so importing enrichment alone brought the trees with it; when
 * enrichment became species-only (3d8eff0, "Split ingest into per-city
 * pipelines") `dashboardContextSource.ts` gained the explicit `import tree_info`
 * and this call site was missed. Every map-chat `run_query` and
 * `publish_results` then failed at the resolver with a 422 — "No datasource
 * exists for root concept local.tree_id", or "concepts split into 2
 * disconnected subgraphs: {city}; {tree_id}" once the query also filtered by
 * city. Nothing caught it: the chat integration test mocks `resolve_query`
 * wholesale, and the resolver-backed suites each declared their own import list
 * rather than the one the app sends. `dashboard-pushdown.test.ts` now compiles
 * against this exact array.
 */
export const MAP_CHAT_IMPORTS: Array<{ name: string; alias: string }> = [
  { name: 'tree_enrichment', alias: '' },
  { name: 'tree_info', alias: '' },
]
