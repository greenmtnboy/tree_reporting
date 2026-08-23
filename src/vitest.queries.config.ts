import { defineConfig } from 'vitest/config'

// The dashboard query suite compiles every chart against the hosted Trilogy
// resolver and runs the SQL, which takes tens of minutes and needs the network.
// It runs in its own job rather than in `pnpm test`, whose CI budget is ten
// minutes — see .github/workflows/dashboard-queries.yml.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/tests/dashboard-queries.test.ts'],
    // One long-running file; a failure lists every offending chart rather than
    // stopping at the first.
    testTimeout: 900_000,
  },
})
