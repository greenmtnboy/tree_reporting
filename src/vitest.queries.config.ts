import { defineConfig } from 'vitest/config'

// The dashboard query suite compiles every chart against the hosted Trilogy
// resolver and runs the SQL. It is its own config and its own CI job because it
// needs the network, not because it is slow — it compiles the whole 820-query
// catalog in about 70 seconds by batching through /generate_queries. See
// the `dashboard-queries` job in .github/workflows/ci.yml, which gates PRs on it.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/tests/dashboard-queries.test.ts'],
    // The compile happens once in a beforeAll and carries its own budget; a
    // failure lists every offending chart rather than stopping at the first.
    testTimeout: 120_000,
    hookTimeout: 900_000,
    // The whole run is one long hook, so intercepted console output would be
    // held until it finished — turning a slow resolver into a silent hang with
    // nothing in the CI log to distinguish it from one. The per-batch progress
    // lines are the only live signal this suite has; let them through.
    disableConsoleIntercept: true,
  },
})
