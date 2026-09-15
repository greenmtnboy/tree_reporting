import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // dashboard-queries runs against the hosted resolver, so it has its own
    // config and CI job (vitest.queries.config.ts) — network, not runtime.
    // The chat benchmark spends the demo model budget and takes the better
    // part of an hour (vitest.chatbench.config.ts); it is run by hand.
    exclude: ['**/node_modules/**', '**/e2e/**', '**/dashboard-queries.test.ts', '**/bench/**'],
  },
})
